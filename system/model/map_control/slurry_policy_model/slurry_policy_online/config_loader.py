from __future__ import annotations

import copy
import importlib
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, Optional, Tuple


class OnlineConfigurationError(ValueError):
    pass


def _default_config_module() -> ModuleType:
    try:
        return importlib.import_module("slurry_policy_config")
    except ModuleNotFoundError:
        package = (__package__ or "").rsplit(".", 1)[0]
        if package:
            return importlib.import_module(package + ".slurry_policy_config")
        raise


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _load_module(spec: Optional[str]) -> ModuleType:
    if not spec:
        return _default_config_module()
    path = Path(spec)
    if path.exists():
        module_name = "slurry_policy_online_external_config_%s" % abs(hash(path.resolve()))
        module_spec = importlib.util.spec_from_file_location(module_name, str(path))
        if module_spec is None or module_spec.loader is None:
            raise OnlineConfigurationError("无法加载配置文件: %s" % path)
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
        return module
    return importlib.import_module(spec)


def load_online_config(config_spec: Optional[str] = None) -> Tuple[dict, dict, dict]:
    default = _default_config_module()
    override = _load_module(config_spec)
    for name in ("PLANT_CONFIG", "TRAINING_CONFIG", "ONLINE_POLICY_CONFIG"):
        if not hasattr(default, name):
            raise OnlineConfigurationError("默认配置缺少 %s" % name)
        if not hasattr(override, name):
            raise OnlineConfigurationError("在线配置缺少 %s" % name)

    plant = _deep_merge(default.PLANT_CONFIG, override.PLANT_CONFIG)
    training = _deep_merge(default.TRAINING_CONFIG, override.TRAINING_CONFIG)
    online = _deep_merge(default.ONLINE_POLICY_CONFIG, override.ONLINE_POLICY_CONFIG)
    validate_online_config(plant, training, online)
    return plant, training, online


def validate_online_config(plant: dict, training: dict, online: dict) -> None:
    paths = plant.get("paths", {})
    for key in ("output_root", "active_policy_version_file", "online_runtime_dir"):
        if not str(paths.get(key, "")).strip():
            raise OnlineConfigurationError("PLANT_CONFIG.paths.%s 不能为空" % key)

    target = online.get("so2_control", {})
    allowed = target.get("allowed_target_range")
    if not isinstance(allowed, (list, tuple)) or len(allowed) != 2:
        raise OnlineConfigurationError("so2_control.allowed_target_range 必须为 [min,max]")
    if float(allowed[0]) >= float(allowed[1]):
        raise OnlineConfigurationError("allowed_target_range 范围无效")
    if float(target.get("target_deadband", 0)) < 0:
        raise OnlineConfigurationError("target_deadband 不能小于0")
    if float(target.get("maximum_effective_target_change_per_minute", 0)) <= 0:
        raise OnlineConfigurationError("maximum_effective_target_change_per_minute 必须大于0")
    safe_max = float(plant["outlet_so2_safe_range"][1])
    emission_limit = target.get("emission_limit")
    emission_limit = safe_max if emission_limit is None else float(emission_limit)
    if float(allowed[1]) > emission_limit:
        raise OnlineConfigurationError("allowed_target_range 上限不能超过排放安全上限")
    warning = float(target.get("emission_warning_margin", 0))
    emergency = float(target.get("emission_emergency_margin", 0))
    if warning <= 0 or emergency < 0 or emergency > warning:
        raise OnlineConfigurationError("排放 warning/emergency margin 配置无效")

    regular = online.get("regular_control", {})
    small = float(regular.get("small_error_threshold", 0))
    medium = float(regular.get("medium_error_threshold", 0))
    if small <= 0 or medium < small:
        raise OnlineConfigurationError("regular_control 误差阈值无效")
    allowed_magnitudes = {"HOLD", "MICRO", "SMALL", "MEDIUM", "STRONG"}
    progressive = regular.get("progressive_action", {})
    if str(progressive.get("initial_max_magnitude", "SMALL")).upper() not in allowed_magnitudes:
        raise OnlineConfigurationError("progressive_action.initial_max_magnitude 无效")
    for value in regular.get("maximum_magnitude_by_level", {}).values():
        if str(value).upper() not in allowed_magnitudes:
            raise OnlineConfigurationError("maximum_magnitude_by_level 含无效动作幅度")

    stability = online.get("action_stability", {})
    for key in (
        "minimum_action_interval_minutes",
        "reverse_action_lock_minutes",
        "recommendation_feedback_timeout_seconds",
    ):
        if float(stability.get(key, 0)) < 0:
            raise OnlineConfigurationError("action_stability.%s 不能小于0" % key)
    if int(stability.get("maximum_actions_per_hour", 0)) < 1:
        raise OnlineConfigurationError("maximum_actions_per_hour 必须至少为1")
    for key in ("condition_switch_hold_cycles", "model_reload_hold_cycles"):
        if int(stability.get(key, 0)) < 0:
            raise OnlineConfigurationError("action_stability.%s 不能小于0" % key)

    fast = online.get("fast_policy", {})
    if float(fast.get("minimum_transient_safe_ratio", 0.0)) < 0 or float(
        fast.get("minimum_transient_safe_ratio", 0.0)
    ) > 1:
        raise OnlineConfigurationError("fast_policy.minimum_transient_safe_ratio 必须位于 [0,1]")

    execution = online.get("execution_limits", {})
    caps = execution.get("maximum_single_valve_delta_by_magnitude", {})
    steps = execution.get("rule_step_by_magnitude", {})
    for magnitude in ("MICRO", "SMALL", "MEDIUM", "STRONG"):
        if float(caps.get(magnitude, 0)) <= 0:
            raise OnlineConfigurationError("缺少正值动作上限: %s" % magnitude)
        if float(steps.get(magnitude, 0)) <= 0:
            raise OnlineConfigurationError("缺少正规则步长: %s" % magnitude)

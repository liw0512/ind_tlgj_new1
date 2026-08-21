from __future__ import annotations

import copy
from typing import Any

from .exceptions import ConfigurationError


POLICY_SEMANTICS_VERSION = "ACTUAL_SUPPLY_FLOW_V1"


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """递归合并配置字典；列表和普通值由 override 整体替换。"""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def enabled_towers(plant: dict[str, Any]) -> list[dict[str, Any]]:
    return [tower for tower in plant["towers"] if tower.get("enabled", True)]


def validate_plant_config(plant: dict[str, Any]) -> None:
    paths = plant.get("paths")
    if not isinstance(paths, dict):
        raise ConfigurationError("PLANT_CONFIG.paths 必须为字典")
    if not paths.get("output_root"):
        raise ConfigurationError("PLANT_CONFIG.paths.output_root 不能为空")
    if not paths.get("condition_snapshots_dir"):
        raise ConfigurationError("PLANT_CONFIG.paths.condition_snapshots_dir 不能为空")
    safe_range = plant.get("outlet_so2_safe_range")
    if not isinstance(safe_range, (list, tuple)) or len(safe_range) != 2:
        raise ConfigurationError("outlet_so2_safe_range 必须为 [min,max]")
    if float(safe_range[0]) >= float(safe_range[1]):
        raise ConfigurationError("outlet_so2_safe_range 范围无效")

    towers = enabled_towers(plant)
    if not towers:
        raise ConfigurationError("至少需要配置一座 enabled=True 的塔")

    tower_ids: set[str] = set()
    ph_columns: set[str] = set()
    flow_columns: set[str] = set()
    for tower in towers:
        tower_id = str(tower.get("tower_id", "")).strip()
        if not tower_id or tower_id in tower_ids:
            raise ConfigurationError(f"塔 ID 为空或重复: {tower_id!r}")
        tower_ids.add(tower_id)

        ph_column = str(tower.get("ph_column", "")).strip()
        if not ph_column or ph_column in ph_columns:
            raise ConfigurationError(f"塔 {tower_id} 的 ph_column 为空或重复")
        ph_columns.add(ph_column)

        ph_range = tower.get("ph_safe_range")
        if not isinstance(ph_range, (list, tuple)) or len(ph_range) != 2:
            raise ConfigurationError(f"塔 {tower_id} 的 ph_safe_range 必须为 [min,max]")
        if float(ph_range[0]) >= float(ph_range[1]):
            raise ConfigurationError(f"塔 {tower_id} 的 pH 安全范围无效")
        if float(tower.get("ph_guard_band", 0.0)) < 0:
            raise ConfigurationError(f"塔 {tower_id} 的 ph_guard_band 不能小于0")

        supply_flows = tower.get("supply_flows", []) or []
        if not supply_flows:
            raise ConfigurationError(f"塔 {tower_id} 至少配置一个供浆流量测点")
        for meter in supply_flows:
            column = str(meter.get("column", "")).strip()
            if not column or column in flow_columns:
                raise ConfigurationError(f"供浆流量字段为空或重复: {column!r}")
            flow_columns.add(column)

def validate_training_config(training: dict[str, Any]) -> None:
    # 第二模块策略语义固定为实际供浆流量动作。
    state = training.setdefault("state", {})
    state.setdefault("policy_state_mode", "COARSE_TOWER")
    training["policy_semantics_version"] = POLICY_SEMANTICS_VERSION

    progress = training.get("progress", {})
    if int(progress.get("bar_width", 32)) < 10:
        raise ConfigurationError("progress.bar_width 不能小于10")
    if float(progress.get("min_interval_seconds", 0.20)) < 0:
        raise ConfigurationError("progress.min_interval_seconds 不能小于0")

    episode = training["episode"]
    positive_keys = [
        "baseline_minutes",
        "action_detection_window_minutes",
        "max_action_duration_minutes",
        "action_end_stable_minutes",
        "response_window_minutes",
        "incremental_context_tail_minutes",
    ]
    for key in positive_keys:
        if float(episode[key]) <= 0:
            raise ConfigurationError(f"episode.{key} 必须大于0")
    if float(episode.get("response_delay_minutes", 0)) < 0:
        raise ConfigurationError("episode.response_delay_minutes 不能小于0")
    if float(episode.get("minimum_window_coverage_ratio", 0)) <= 0 or float(
        episode.get("minimum_window_coverage_ratio", 0)
    ) > 1:
        raise ConfigurationError("minimum_window_coverage_ratio 必须位于 (0,1]")

    weights = training["reliability"]["weights"]
    total = sum(float(v) for v in weights.values())
    if abs(total - 1.0) > 1e-6:
        raise ConfigurationError(f"reliability.weights 权重之和必须为1，当前为 {total}")
    alignment = training.get("version_alignment", {})
    if not bool(alignment.get("follow_condition_snapshot_version", True)):
        raise ConfigurationError("V1.8B 必须跟随第一模块 condition snapshot 版本")
    for key in (
        "allow_condition_version_jump",
        "fail_on_unresolved_valid_episode",
        "fail_on_unresolved_invalid_episode",
        "strict_input_mapping_check",
    ):
        if key in alignment and not isinstance(alignment[key], bool):
            raise ConfigurationError(f"version_alignment.{key} 必须为布尔值")

    performance = training.get("performance", {})
    for key in (
        "read_only_required_columns",
        "skip_sort_when_already_ordered",
        "record_stage_timings",
    ):
        if key in performance and not isinstance(performance[key], bool):
            raise ConfigurationError(f"performance.{key} 必须为布尔值")
    output = training.get("output", {})
    for key in (
        "write_pickle_only_when_profiles_exist",
        "write_episode_pickle",
        "prefer_episode_pickle_for_incremental_read",
        "write_full_episode_csv",
        "write_context_tail_pickle",
        "write_context_tail_csv",
    ):
        if key in output and not isinstance(output[key], bool):
            raise ConfigurationError(f"output.{key} 必须为布尔值")
    if not bool(output.get("write_episode_pickle", True)) and not bool(
        output.get("write_full_episode_csv", True)
    ):
        raise ConfigurationError("episode pickle 与完整 episode CSV 不能同时关闭")
    if not bool(output.get("write_context_tail_pickle", True)) and not bool(
        output.get("write_context_tail_csv", True)
    ):
        raise ConfigurationError("context_tail pickle 与 CSV 不能同时关闭")

from __future__ import annotations

import copy
import math
from typing import Any

from .exceptions import ConfigurationError


POLICY_SEMANTICS_VERSION = "TOWER_LEVEL_V3_PUMP_GATED"


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


def all_valves(plant: dict[str, Any]) -> list[dict[str, Any]]:
    valves: list[dict[str, Any]] = []
    for tower in enabled_towers(plant):
        for valve in tower.get("valves", []):
            item = copy.deepcopy(valve)
            item["tower_id"] = tower["tower_id"]
            valves.append(item)
    return valves


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
    valve_ids: set[str] = set()
    valve_columns: set[str] = set()
    pump_ids: set[str] = set()
    pump_current_columns: set[str] = set()
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

        valves = tower.get("valves", [])
        if not valves:
            raise ConfigurationError(f"塔 {tower_id} 至少配置一个供浆阀")
        tower_valve_ids: set[str] = set()
        for valve in valves:
            valve_id = str(valve.get("valve_id", "")).strip()
            column = str(valve.get("column", "")).strip()
            if not valve_id or valve_id in valve_ids:
                raise ConfigurationError(f"阀门 ID 为空或重复: {valve_id!r}")
            if not column or column in valve_columns:
                raise ConfigurationError(f"阀门字段为空或重复: {column!r}")
            valve_ids.add(valve_id)
            tower_valve_ids.add(valve_id)
            valve_columns.add(column)
            lo = float(valve["min_opening"])
            hi = float(valve["max_opening"])
            if lo >= hi:
                raise ConfigurationError(f"阀门 {valve_id} 开度范围无效")
            threshold = float(valve.get("action_threshold", 0))
            if threshold <= 0:
                raise ConfigurationError(f"阀门 {valve_id} action_threshold 必须大于0")
            if threshold > hi - lo:
                raise ConfigurationError(
                    f"阀门 {valve_id} action_threshold 不能大于开度量程"
                )

        supply_pumps = tower.get("supply_pumps", []) or []
        if not isinstance(supply_pumps, list):
            raise ConfigurationError(f"塔 {tower_id} 的 supply_pumps 必须为列表")
        served_by_any_pump: set[str] = set()
        for pump in supply_pumps:
            if not isinstance(pump, dict):
                raise ConfigurationError(f"塔 {tower_id} 的 supply_pumps 每项必须为字典")
            pump_id = str(pump.get("pump_id", "")).strip()
            current_column = str(pump.get("current_column", "")).strip()
            if not pump_id or pump_id in pump_ids:
                raise ConfigurationError(f"供浆泵 ID 为空或重复: {pump_id!r}")
            if not current_column or current_column in pump_current_columns:
                raise ConfigurationError(
                    f"供浆泵 {pump_id} 的 current_column 为空或重复"
                )
            try:
                run_threshold = float(pump.get("run_current_threshold"))
            except (TypeError, ValueError, OverflowError):
                raise ConfigurationError(
                    f"供浆泵 {pump_id} 的 run_current_threshold 必须为数值"
                )
            if not math.isfinite(run_threshold) or run_threshold < 0:
                raise ConfigurationError(
                    f"供浆泵 {pump_id} 的 run_current_threshold 必须为有限非负数"
                )
            served = pump.get("served_valve_ids")
            if not isinstance(served, list) or not served:
                raise ConfigurationError(
                    f"供浆泵 {pump_id} 的 served_valve_ids 必须为非空列表"
                )
            normalized_served = [str(value).strip() for value in served]
            if any(not value for value in normalized_served):
                raise ConfigurationError(
                    f"供浆泵 {pump_id} 的 served_valve_ids 不能包含空值"
                )
            if len(set(normalized_served)) != len(normalized_served):
                raise ConfigurationError(
                    f"供浆泵 {pump_id} 的 served_valve_ids 不能重复"
                )
            unknown = sorted(set(normalized_served) - tower_valve_ids)
            if unknown:
                raise ConfigurationError(
                    f"供浆泵 {pump_id} 只能服务同塔已配置阀门，未知阀门={unknown}"
                )
            served_by_any_pump.update(normalized_served)
            pump_ids.add(pump_id)
            pump_current_columns.add(current_column)

        # 一旦某塔配置了 supply_pumps，就认为泵电流可用性是该塔的硬执行约束。
        # 因此每个阀必须至少有一台配置泵为其提供浆液。一个泵可服务多个阀，
        # 一个阀也可由多台泵共同服务（任一泵运行即认为该阀供浆路径可用）。
        if supply_pumps:
            unserved = sorted(tower_valve_ids - served_by_any_pump)
            if unserved:
                raise ConfigurationError(
                    f"塔 {tower_id} 已启用供浆泵拓扑，但阀门未被任何泵服务: {unserved}"
                )


def validate_training_config(training: dict[str, Any]) -> None:
    # 塔级策略语义统一由代码冻结。V3 在 V2 基础上增加“供浆泵电流阈值→
    # 0/1状态→阀门可用性”的在线硬约束，并将同一判定用于离线 episode 有效性。
    state = training.setdefault("state", {})
    state.setdefault("policy_state_mode", "COARSE_TOWER")
    training["policy_semantics_version"] = POLICY_SEMANTICS_VERSION
    mode = str(state.get("policy_state_mode", "COARSE_TOWER")).upper()
    if mode not in {"COARSE_TOWER", "LEGACY_DETAILED"}:
        raise ConfigurationError(
            "state.policy_state_mode 仅支持 COARSE_TOWER 或 LEGACY_DETAILED"
        )

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
        "hold_episode_minutes",
        "hold_stride_minutes",
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
    attribution = training.get("condition_attribution", {})
    if not bool(attribution.get("enabled", True)):
        raise ConfigurationError("V1.8B condition_attribution.enabled 必须为 True")
    if int(attribution.get("max_load_grid_offset", -1)) < 0:
        raise ConfigurationError("condition_attribution.max_load_grid_offset 不能小于0")
    if int(attribution.get("max_inlet_so2_grid_offset", -1)) < 0:
        raise ConfigurationError("condition_attribution.max_inlet_so2_grid_offset 不能小于0")
    coverage = float(attribution.get("minimum_neighborhood_coverage_ratio", 0))
    if not 0 < coverage <= 1:
        raise ConfigurationError("minimum_neighborhood_coverage_ratio 必须位于 (0,1]")
    if str(attribution.get("action_anchor_mode", "")).upper() != "ACTION_START":
        raise ConfigurationError("V1.8B action_anchor_mode 当前固定为 ACTION_START")
    if str(attribution.get("hold_anchor_mode", "")).upper() != "MAJORITY_CONDITION":
        raise ConfigurationError("V1.8B hold_anchor_mode 当前固定为 MAJORITY_CONDITION")
    if str(attribution.get("nearby_evidence_weight_mode", "")).upper() != "COVERAGE_RATIO":
        raise ConfigurationError("V1.8B nearby_evidence_weight_mode 当前固定为 COVERAGE_RATIO")
    if bool(attribution.get("grid_change_alone_is_transient", False)):
        raise ConfigurationError("V1.8B 不允许仅因 grid 变化直接判快变")
    if bool(attribution.get("condition_label_change_alone_is_transient", False)):
        raise ConfigurationError("V1.8B 不允许仅因 condition_label 变化直接判快变")

    neighbor = training.get("neighbor_policy", {})
    if str(neighbor.get("distance_weight_mode", "LINEAR_AXIS")).upper() not in {
        "LINEAR_AXIS", "INVERSE_DISTANCE", "UNIFORM"
    }:
        raise ConfigurationError("neighbor_policy.distance_weight_mode 不受支持")
    minimum_mapping = float(neighbor.get("minimum_mapping_weight", 0.10))
    if not 0 <= minimum_mapping <= 1:
        raise ConfigurationError("neighbor_policy.minimum_mapping_weight 必须位于 [0,1]")

    prior = training.get("plant_action_prior", {})
    for key in ("maximum_single_condition_share", "maximum_single_grid_share",
                "minimum_cross_grid_direction_consistency", "global_only_evidence_weight"):
        value = float(prior.get(key, 0))
        if not 0 <= value <= 1:
            raise ConfigurationError(f"plant_action_prior.{key} 必须位于 [0,1]")
    for key in ("minimum_source_conditions", "minimum_source_grids",
                "minimum_events_per_source_grid"):
        if int(prior.get(key, 0)) < 1:
            raise ConfigurationError(f"plant_action_prior.{key} 必须至少为1")

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
        "categorical_groupby_keys",
        "record_stage_timings",
    ):
        if key in performance and not isinstance(performance[key], bool):
            raise ConfigurationError(f"performance.{key} 必须为布尔值")
    batch_size = performance.get("neighbor_target_condition_batch_size", 64)
    if not isinstance(batch_size, int) or batch_size <= 0:
        raise ConfigurationError(
            "performance.neighbor_target_condition_batch_size 必须为正整数"
        )
    max_rows = performance.get("neighbor_max_expanded_rows_per_batch", 500000)
    if not isinstance(max_rows, int) or max_rows < 0:
        raise ConfigurationError(
            "performance.neighbor_max_expanded_rows_per_batch 必须为非负整数"
        )

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

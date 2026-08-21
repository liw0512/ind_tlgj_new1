"""第二模块固定接口字段与决策片段输出结构。

第一模块输出的 condition/grid/version 字段名是稳定接口；所有随厂变化的原始
过程字段与工况轴统一从 ``system/model/config/plant_config.py`` 读取。训练时再将
工况轴冻结进 effective config，保证活动 snapshot 不受后续配置编辑影响。
"""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
from typing import Any

from system.model.config.standard_fields import OUTLET_SO2_COLUMN, TIME_COLUMN


def _load_site_plant_config() -> dict[str, Any]:
    """Load the one authoritative plant config without depending on cwd."""
    try:
        from system.model.config.plant_config import PLANT_CONFIG
        return copy.deepcopy(PLANT_CONFIG)
    except ImportError:
        config_path = (
            Path(__file__).resolve().parents[3]
            / "config"
            / "plant_config.py"
        )
        if not config_path.is_file():
            raise ImportError(f"plant_config.py not found: {config_path}")
        spec = importlib.util.spec_from_file_location(
            "slurry_policy_site_plant_config", config_path
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load plant config: {config_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        value = getattr(module, "PLANT_CONFIG", None)
        if not isinstance(value, dict):
            raise ImportError("plant_config.PLANT_CONFIG is unavailable")
        return copy.deepcopy(value)


_SITE_PLANT_CONFIG = _load_site_plant_config()

CONDITION_SNAPSHOT_VERSION_COLUMN = "condition_snapshot_version"
GRID_ID_COLUMN = "grid_id"
BASE_CONDITION_ID_COLUMN = "base_condition_id"
CONDITION_LABEL_COLUMN = "condition_label"
POLICY_REGION_ID_COLUMN = "policy_region_id"
REGION_STATUS_COLUMN = "region_status"
REGION_MEMBER_COUNT_COLUMN = "region_member_count"
COVERAGE_STATUS_COLUMN = "coverage_status"
CONDITION_STATE_KEY_COLUMN = "state_key"
CONDITION_EXPERIENCE_SOURCE_COLUMN = "condition_experience_source"
CONDITION_VALID_COLUMN = "condition_valid"
OUT_OF_RANGE_CLIPPED_COLUMN = "out_of_range_clipped"
CLIP_AXIS_COLUMN = "clip_axis"
CONDITION_REASON_COLUMN = "condition_reason"

REQUIRED_CONDITION_COLUMNS = (
    CONDITION_SNAPSHOT_VERSION_COLUMN,
    GRID_ID_COLUMN,
    CONDITION_LABEL_COLUMN,
    POLICY_REGION_ID_COLUMN,
    CONDITION_STATE_KEY_COLUMN,
    CONDITION_VALID_COLUMN,
    OUT_OF_RANGE_CLIPPED_COLUMN,
)

AUDIT_CONDITION_COLUMNS = (
    BASE_CONDITION_ID_COLUMN,
    REGION_STATUS_COLUMN,
    REGION_MEMBER_COUNT_COLUMN,
    COVERAGE_STATUS_COLUMN,
    CONDITION_EXPERIENCE_SOURCE_COLUMN,
    CLIP_AXIS_COLUMN,
    CONDITION_REASON_COLUMN,
)

ALL_CONDITION_COLUMNS = REQUIRED_CONDITION_COLUMNS + AUDIT_CONDITION_COLUMNS


def time_column(plant: dict[str, Any] | None = None) -> str:
    """Return the fixed post-preprocessor timestamp field."""
    del plant
    return TIME_COLUMN


def _configured_condition_axes() -> list[dict[str, Any]]:
    """Read current condition axes directly from the central plant config."""
    axes = _SITE_PLANT_CONFIG.get("condition_axes")
    if not isinstance(axes, (list, tuple)):
        raise ImportError("plant_config.PLANT_CONFIG.condition_axes is unavailable")
    return [copy.deepcopy(dict(item)) for item in axes]


def condition_axis_specs(training: dict[str, Any]) -> list[dict[str, Any]]:
    """Return frozen policy axes, or derive them from the central plant config."""
    raw = training.get("_condition_axes")
    if not isinstance(raw, (list, tuple)) or not raw:
        raw = _configured_condition_axes()

    result: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        column = str(item.get("column", "")).strip()
        if column:
            result.append(dict(item, column=column))
    if len(result) not in {1, 2}:
        raise ValueError(
            "policy condition axes must contain exactly 1 or 2 fields; "
            f"got {len(result)}"
        )
    if len({item["column"] for item in result}) != len(result):
        raise ValueError("policy condition axes contain duplicate columns")
    return result


def condition_axis_columns(training: dict[str, Any]) -> tuple[str, ...]:
    return tuple(item["column"] for item in condition_axis_specs(training))


def freeze_condition_axes(training: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with the current central condition axes frozen into it."""
    result = copy.deepcopy(training)
    result["_condition_axes"] = condition_axis_specs(result)
    return result


def episode_output_columns(plant: dict[str, Any]) -> list[str]:
    columns = [
        "episode_id",
        "condition_label",
        "anchor_condition_label",
        "anchor_grid_id",
        "original_condition_label",
        "original_condition_snapshot_version",
        "previous_condition_label",
        "current_condition_label",
        "current_condition_snapshot_version",
        "condition_remapped",
        "start_condition_label",
        "end_condition_label",
        "condition_label_path",
        "condition_label_change_count",
        "condition_snapshot_version",
        "policy_region_id",
        "region_status",
        "region_member_count",
        "start_grid_id",
        "end_grid_id",
        "grid_transition_path",
        "grid_change_count",
        "max_first_axis_grid_offset",
        "max_second_axis_grid_offset",
        "valid_grid_point_count",
        "neighborhood_inside_point_count",
        "neighborhood_coverage_ratio",
        "attribution_source",
        "training_route",
        "evidence_weight",
        "supply_pump_state_changed",
        "supply_pump_changed_columns",
        "base_condition_id",
        "coverage_status",
        "condition_state_key",
        "condition_experience_source",
        "out_of_range_clipped",
        "clip_axis",
        "condition_reason",
        "condition_valid",
        "episode_type",
        "action_semantics",
        "action_start_time",
        "action_end_time",
        "response_end_time",
        "action_family",
        "action_direction",
        "action_magnitude_value",
        "action_magnitude",
        "action_id",
        "active_tower_ids",
        "flow_event_tower_id",
        "flow_event_start_time",
        "flow_event_end_time",
        "flow_event_baseline_flow",
        "flow_event_final_flow",
        "flow_event_peak_flow",
        "flow_event_trough_flow",
        "flow_event_peak_delta_flow",
        "flow_event_final_delta_flow",
        "flow_event_max_abs_delta_flow",
        "flow_event_extra_slurry_volume",
        "flow_event_deficit_slurry_volume",
        "flow_event_signed_slurry_volume",
        "flow_event_active_duration_minutes",
        "flow_event_time_to_extreme_minutes",
        "flow_event_time_from_extreme_to_end_minutes",
        "flow_event_baseline_noise_sigma",
        "flow_event_trigger_deadband",
        "flow_event_transition_count",
        "flow_event_complete",
        "flow_shape",
        "flow_direction",
        "flow_persistent_ratio",
        "flow_return_ratio",
        "flow_overshoot_delta_flow",
        "flow_overshoot_ratio",
        "flow_return_tolerance",
        "flow_crosses_baseline",
        "flow_temporary_plateau",
        "flow_temporary_plateau_count",
        "flow_execution_profile",
        "flow_classification_reason",
        "flow_context",
        "flow_learning_eligible",
        "flow_circulation_change",
        "flow_major_process_transition",
        "flow_context_reason",
        "flow_effect_baseline_start_time",
        "flow_effect_response_start_time",
        "flow_effect_outlet_so2_direction",
        "flow_effect_response_outlet_so2_min",
        "flow_effect_response_outlet_so2_max",
        "flow_effect_tower_ph_direction",
        "flow_effect_response_tower_ph_min",
        "flow_effect_response_tower_ph_max",
        "flow_effect_complete",
        "flow_effect_reason",
        "flow_timing_first_effect_time",
        "flow_timing_observed_response_delay_minutes",
        "flow_timing_extreme_effect_time",
        "flow_timing_time_to_extreme_minutes",
        "flow_timing_stable_time",
        "flow_timing_time_to_stable_minutes",
        "flow_timing_settled",
    ]

    for tower in plant.get("towers", []):
        if not tower.get("enabled", True):
            continue
        tower_id = str(tower["tower_id"])
        columns.extend(
            [
                f"before_ph__{tower_id}",
                f"after_ph__{tower_id}",
                f"delta_ph__{tower_id}",
                f"post_ph_range__{tower_id}",
                f"post_ph_std__{tower_id}",
                f"ph_below_limit__{tower_id}",
                f"ph_above_limit__{tower_id}",
                f"ph_out_of_range__{tower_id}",
            ]
        )
    columns.extend(
        [
            "before_condition_axis_1",
            "before_condition_axis_2",
            "before_outlet_so2",
            "after_condition_axis_1",
            "after_condition_axis_2",
            "after_outlet_so2",
            "delta_outlet_so2",
            "before_condition_axis_1_rate",
            "before_condition_axis_2_rate",
            "before_outlet_so2_rate",
            "after_outlet_so2_rate",
            "outlet_so2_rate_reduction",
            "episode_condition_axis_1_rate",
            "episode_condition_axis_2_rate",
            "disturbance_mode",
            "fast_change_mode",
            "fast_change_direction",
            "fast_change_exact_trend_mode",
            "fast_change_severity",
            "fast_change_effect_risk_level",
            "fast_change_overall_risk_level",
            "fast_change_effect_state",
            "post_outlet_so2_median",
            "post_outlet_so2_p25",
            "post_outlet_so2_p75",
            "post_outlet_so2_std",
            "post_outlet_so2_range",
            "post_outlet_so2_peak",
            "post_outlet_so2_safe_ratio",
            "post_outlet_so2_warning_ratio",
            "post_outlet_so2_emergency_ratio",
            "outlet_so2_sign_changes",
            "outlet_so2_out_of_range",
            "outlet_so2_over_hard_max",
            "is_transient",
            "continuous_segment_id",
            "event_date",
            "source_files",
            "policy_state_key",
            "policy_state_key_no_grid",
            "baseline_coverage_ratio",
            "response_coverage_ratio",
            "followup_action_in_response",
            "valid",
            "invalid_reason",
            "short_reverse_action",
            "so2_effect_direction",
            "so2_effect_strength",
            "stable_response",
            "oscillation_detected",
        ]
    )
    return list(dict.fromkeys(columns))

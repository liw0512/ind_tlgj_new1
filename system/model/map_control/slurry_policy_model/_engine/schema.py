"""第二模块固定接口字段与决策片段输出结构。

第一模块输出的 condition/grid/version 字段名是稳定接口；用于划分工况的原始
过程字段不再在第二模块写死为 ``jzfh`` / ``yyq_SO2``。正式训练时，P4PC/第二
模块会从当前 condition_snapshot.json 中读取 ``condition_axes`` 并注入有效训练
配置，因此换厂只需要修改第一模块 ``condition_config.CONDITION_AXES``。
"""
from __future__ import annotations

from typing import Any, Iterable


OUTLET_SO2_COLUMN = "jyq_SO2"

# 仅用于读取旧第二模块 effective_config 的兼容默认值。新训练不会把这两个字段
# 当成固定必要字段。
LEGACY_CONDITION_AXIS_COLUMNS = ("jzfh", "yyq_SO2")

# 第一模块固定追加的详细工况字段。
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


def time_column(plant: dict[str, Any]) -> str:
    """返回输入 CSV 的原始时间列名。"""
    return str(plant.get("time_column", "date")).strip() or "date"


def condition_axis_specs(training: dict[str, Any]) -> list[dict[str, Any]]:
    """返回由第一模块 snapshot 注入的工况轴定义。

    新训练必须由 slurry_policy_core 在读取 condition snapshot 后注入
    ``_condition_axes``。兼容读取历史 effective_config 时，若缺少该字段则退回
    旧电厂两轴字段，避免旧快照连读取都失败；旧语义是否允许增量仍由版本/配置
    兼容校验决定。
    """
    raw = training.get("_condition_axes")
    if isinstance(raw, (list, tuple)) and raw:
        result = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            column = str(item.get("column", "")).strip()
            if column:
                result.append(dict(item, column=column))
        if result:
            return result
    return [{"column": column} for column in LEGACY_CONDITION_AXIS_COLUMNS]


def condition_axis_columns(training: dict[str, Any]) -> tuple[str, ...]:
    return tuple(item["column"] for item in condition_axis_specs(training))


def episode_output_columns(plant: dict[str, Any]) -> list[str]:
    """返回稳定的决策片段 CSV 表头。"""
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
        "action_start_time",
        "action_end_time",
        "response_end_time",
        "action_family",
        "action_direction",
        "action_magnitude_value",
        "action_magnitude",
        "action_id",
        "active_valve_ids",
        "active_tower_ids",
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
        for valve in tower.get("valves", []):
            valve_id = str(valve["valve_id"])
            columns.extend(
                [
                    f"before_valve__{valve_id}",
                    f"after_valve__{valve_id}",
                    f"delta_valve__{valve_id}",
                    f"normalized_delta_valve__{valve_id}",
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
            "episode_condition_axis_1_rate",
            "episode_condition_axis_2_rate",
            "disturbance_mode",
            "post_outlet_so2_median",
            "post_outlet_so2_p25",
            "post_outlet_so2_p75",
            "post_outlet_so2_std",
            "post_outlet_so2_range",
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

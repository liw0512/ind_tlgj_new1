"""第二模块固定输入字段与决策片段输出结构。

第一模块输出字段已经稳定，因此第二模块直接读取固定字段名，不再要求每个厂
重复配置“逻辑字段 -> CSV 字段”映射。V1.8B 将 ``condition_label`` 作为
人工审查和工况级聚合的主展示标识，同时继续保留 ``grid_id`` 作为局部经验
与永久追溯主键。
"""
from __future__ import annotations

from typing import Any


# 第一模块输出 CSV 中固定使用的过程字段。
LOAD_COLUMN = "jzfh"
INLET_SO2_COLUMN = "yyq_SO2"
OUTLET_SO2_COLUMN = "jyq_SO2"

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

# V1.8B 按锚点 condition_label 建立工况级输出，因此 condition_label 为必要字段。
REQUIRED_CONDITION_COLUMNS = (
    CONDITION_SNAPSHOT_VERSION_COLUMN,
    GRID_ID_COLUMN,
    CONDITION_LABEL_COLUMN,
    POLICY_REGION_ID_COLUMN,
    CONDITION_STATE_KEY_COLUMN,
    CONDITION_VALID_COLUMN,
    OUT_OF_RANGE_CLIPPED_COLUMN,
)

# 其余字段主要用于人工审计和目录说明。
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


def episode_output_columns(plant: dict[str, Any]) -> list[str]:
    """返回稳定的决策片段 CSV 表头。

    工况身份字段放在动作字段之前，便于直接用 Excel 审查。初次数据即使一个
    有效事件都没有，也会写出完整表头，供第一次增量训练安全继承。
    """
    columns = [
        "episode_id",
        # 人工审查优先字段。
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
        "max_load_grid_offset",
        "max_inlet_so2_grid_offset",
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
        # 决策片段和动作身份。
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
            "before_load",
            "before_inlet_so2",
            "before_outlet_so2",
            "after_load",
            "after_inlet_so2",
            "after_outlet_so2",
            "delta_outlet_so2",
            "before_load_rate",
            "before_inlet_so2_rate",
            "before_outlet_so2_rate",
            "episode_load_rate",
            "episode_inlet_so2_rate",
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

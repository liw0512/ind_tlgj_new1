"""湿法脱硫供浆历史动作响应模型——算法配置。

厂级物理/信号参数不再在本文件重复维护。工况轴、SO2安全范围、
单塔/双塔、pH、阀门、供浆泵及 pump->valve 拓扑统一来自：
``system/model/config/plant_config.py``。

本文件只负责第二模块自身的训练、响应、可靠性、在线控制和执行限幅参数。
项目路径按当前仓库根目录自动生成，不需要因为 F:/tlgj、F:/tlgj_new 或部署目录
变化而修改配置。
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from system.model.config.plant_config import PLANT_CONFIG as SITE_PLANT_CONFIG


CONDITION_ROOT = PROJECT_ROOT / "system" / "model" / "map_control" / "condition_model"
MODEL_CSV_ROOT = PROJECT_ROOT / "system" / "model" / "map_control" / "model_csv"
POLICY_OUTPUT_ROOT = PROJECT_ROOT / "files" / "slurry_policy_model_output"

# 第二模块使用中央厂级配置的深拷贝，并只补充本模块运行所需的项目路径。
# towers / valves / supply_pumps / pH / SO2 等都不在这里再写一份；标准过程字段名固定。
PLANT_CONFIG = copy.deepcopy(SITE_PLANT_CONFIG)
PLANT_CONFIG["paths"] = {
    "default_initial_input": str(MODEL_CSV_ROOT / "Initial_train_after_condition.csv"),
    "default_incremental_input": str(MODEL_CSV_ROOT / "Incremental_train_after_condition.csv"),
    "output_root": str(POLICY_OUTPUT_ROOT),
    "condition_snapshots_dir": str(CONDITION_ROOT / "snapshots"),
    "active_policy_version_file": str(POLICY_OUTPUT_ROOT / "active_version.json"),
    "online_runtime_dir": str(PROJECT_ROOT / "files" / "slurry_policy_online_runtime"),
}

_SAFE_SO2_LO, _SAFE_SO2_HI = [
    float(value) for value in PLANT_CONFIG["outlet_so2_safe_range"]
]
# 保持原来 0~35 时每 5 一个状态边界；安全范围变化时自动等距缩放，不再重复写 35。
_OUTLET_SO2_STATE_EDGES = [
    _SAFE_SO2_LO + (_SAFE_SO2_HI - _SAFE_SO2_LO) * index / 7.0
    for index in range(8)
]


TRAINING_CONFIG = {
    "progress": {
        "enabled": True,
        "bar_width": 32,
        "min_interval_seconds": 0.20,
        "show_elapsed": True,
    },

    "performance": {
        "read_only_required_columns": True,
        "skip_sort_when_already_ordered": True,
        "categorical_groupby_keys": True,
        "record_stage_timings": True,
        "neighbor_target_condition_batch_size": 64,
        "neighbor_max_expanded_rows_per_batch": 500000,
    },

    "io": {
        "csv_encoding": "utf-8-sig",
        "timestamp_format": None,
        "drop_duplicate_timestamp_keep": "last",
        "strict_required_columns": True,
    },

    "preprocessing": {
        "valve_rolling_median_points": 3,
        "max_continuous_gap_seconds": 180,
        "coerce_numeric": True,
    },

    "episode": {
        "baseline_minutes": 5.0,
        "action_detection_window_minutes": 2.0,
        "max_action_duration_minutes": 20.0,
        "action_end_stable_minutes": 1.5,
        "action_merge_gap_minutes": 1.0,
        "response_delay_minutes": 3.0,
        "response_window_minutes": 10.0,
        "invalidate_followup_action_in_response": True,
        "hold_action_guard_minutes": 3.0,
        "hold_episode_minutes": 15.0,
        "hold_stride_minutes": 15.0,
        "max_hold_episodes_per_segment": 48,
        "minimum_window_coverage_ratio": 0.70,
        "incremental_context_tail_minutes": 60.0,
        "short_reverse_action_minutes": 20.0,
    },

    "condition_attribution": {
        "enabled": True,
        "action_anchor_mode": "ACTION_START",
        "hold_anchor_mode": "MAJORITY_CONDITION",
        # 旧键名仅为内部兼容：分别表示第一、第二工况轴允许的基础格偏移。
        "max_load_grid_offset": 2,
        "max_inlet_so2_grid_offset": 3,
        "minimum_neighborhood_coverage_ratio": 0.60,
        "grid_change_alone_is_transient": False,
        "condition_label_change_alone_is_transient": False,
        "nearby_evidence_weight_mode": "COVERAGE_RATIO",
    },

    "neighbor_policy": {
        "enabled": True,
        "include_same_condition": True,
        "include_global_only": False,
        "distance_weight_mode": "LINEAR_AXIS",
        "minimum_mapping_weight": 0.10,
    },

    "plant_action_prior": {
        "enabled": True,
        "global_only_evidence_weight": 0.50,
        "minimum_source_conditions": 3,
        "minimum_source_grids": 3,
        "minimum_events_per_source_grid": 2,
        "maximum_single_condition_share": 0.60,
        "maximum_single_grid_share": 0.50,
        "minimum_cross_grid_direction_consistency": 0.70,
    },

    "disturbance": {
        "mode": "auto",
        "trend_window_minutes": 5.0,
        "auto_slow_quantile": 0.75,
        "auto_fast_quantile": 0.92,
        # fixed 模式历史兼容键；仅当工况轴仍为旧 jzfh/yyq_SO2 时使用。
        "load_slow_rate": 1.0,
        "load_fast_rate": 3.0,
        "inlet_so2_slow_rate": 20.0,
        "inlet_so2_fast_rate": 60.0,
        "minimum_load_slow_rate": 0.10,
        "minimum_load_fast_rate": 0.30,
        "minimum_inlet_so2_slow_rate": 1.0,
        "minimum_inlet_so2_fast_rate": 3.0,
        # 任意新工况轴在 auto 模式下按自身 grid step 比例给最小阈值。
        "minimum_axis_slow_step_ratio": 0.01,
        "minimum_axis_fast_step_ratio": 0.03,
    },

    "state": {
        "outlet_so2_edges": _OUTLET_SO2_STATE_EDGES,
        "outlet_so2_trend_slow_rate": 0.20,
        "outlet_so2_trend_fast_rate": 0.80,
        "valve_opening_edges": [0.0, 0.25, 0.50, 0.75, 1.0],
        "valve_balance_threshold": 0.10,
        "include_condition_state_key": True,
        "include_disturbance_mode": True,
    },

    "action_magnitude": {
        "mode": "auto",
        "micro_max": 0.006,
        "small_quantile": 0.40,
        "medium_quantile": 0.75,
        "minimum_events_per_family": 8,
        "default_fixed_bins": {
            "small_max": 0.025,
            "medium_max": 0.060,
        },
        "family_fixed_bins": {},
    },

    "response": {
        "so2_direction_deadband": 0.50,
        "ph_direction_deadband": 0.02,
        "effect_strength_mode": "auto",
        "effect_strength_small_quantile": 0.40,
        "effect_strength_medium_quantile": 0.75,
        "effect_strength_fixed_bins": {
            "weak_max": 1.0,
            "small_max": 2.5,
            "medium_max": 5.0,
        },
        "stable_so2_range_max": None,
        "oscillation_diff_deadband": 0.30,
        "max_oscillation_sign_changes": 4,
    },

    "validity": {
        "require_condition_valid": True,
        "allow_out_of_range_clipped": True,
        "invalidate_supply_pump_state_change": True,
        "keep_safety_violation_episodes": True,
    },

    "reliability": {
        "reference_event_count": 20,
        "reference_segment_count": 10,
        "reference_day_count": 10,
        "weights": {
            "support": 0.25,
            "direction_consistency": 0.25,
            "response_stability": 0.20,
            "safety_history": 0.20,
            "time_coverage": 0.10,
        },
        "minimum_supported_events": 5,
        "minimum_supported_segments": 3,
        "minimum_supported_days": 2,
    },

    "version_alignment": {
        "follow_condition_snapshot_version": True,
        "allow_condition_version_jump": True,
        "fail_on_unresolved_valid_episode": True,
        "fail_on_unresolved_invalid_episode": False,
        "strict_input_mapping_check": True,
    },

    "output": {
        "version_directory_name_from_condition_snapshot": True,
        "max_versions_to_keep": 5,
        "write_pickle_only_when_profiles_exist": True,
        "write_episode_pickle": True,
        "prefer_episode_pickle_for_incremental_read": True,
        "write_full_episode_csv": True,
        "write_context_tail_pickle": True,
        "write_context_tail_csv": True,
        "json_indent": 2,
    },
}


ONLINE_POLICY_CONFIG = {
    "model_loading": {
        "require_active_version_file": True,
        "allow_latest_snapshot_fallback": False,
        "verify_manifest_hashes": True,
        "reload_check_interval_seconds": 30.0,
        "require_integrated_version_pair": True,
        "preserve_runtime_state_on_external_switch": True,
        "condition_policy_cache_size": 8,
    },

    "so2_control": {
        "default_target": 20.0,
        "target_source_mode": "RUNTIME_WITH_CONFIG_FALLBACK",
        "allowed_target_range": [5.0, 30.0],
        "target_deadband": 1.0,
        # None = 永远继承中央 PLANT_CONFIG.outlet_so2_safe_range 上限。
        "emission_limit": None,
        "emission_warning_margin": 5.0,
        "emission_emergency_margin": 1.0,
        "target_transition_enabled": True,
        "maximum_effective_target_change_per_minute": 1.0,
        "hold_cycles_after_target_change": 1,
        "decrease_slurry_more_conservative": True,
        "minimum_low_side_error_for_decrease": 2.0,
    },

    "regular_control": {
        "small_error_threshold": 2.0,
        "medium_error_threshold": 5.0,
        "progressive_action": {
            "enabled": True,
            "initial_max_magnitude": "SMALL",
            "medium_after_completed_matching_actions": 1,
            "strong_after_completed_matching_actions": 2,
            "strong_only_warning_or_emergency": True,
        },
        "maximum_magnitude_by_level": {
            "TARGET_HOLD": "HOLD",
            "TARGET_SMALL": "SMALL",
            "TARGET_MEDIUM": "MEDIUM",
            "TARGET_LARGE": "STRONG",
            "WARNING": "STRONG",
            "EMERGENCY": "STRONG",
        },
    },

    "profile_acceptance": {
        "local_allowed_status": ["SUPPORTED"],
        "neighbor_allowed_status": ["SUPPORTED"],
        "transient_allowed_status": ["SUPPORTED"],
        "plant_prior_allowed_status": ["SUPPORTED"],
        "minimum_direction_consistency": 0.55,
        "minimum_safety_history_score": 80.0,
        "minimum_reliability_total_score": 45.0,
        "minimum_stable_response_ratio": 0.50,
        "allow_mixed_action": False,
        "allow_rebalance_action": False,
    },

    "action_stability": {
        "wait_for_actual_execution_feedback": True,
        "recommendation_feedback_timeout_seconds": 90.0,
        "minimum_action_interval_minutes": 3.0,
        "reverse_action_lock_minutes": 10.0,
        "maximum_actions_per_hour": 6,
        "response_delay_minutes": None,
        "response_window_minutes": None,
        "block_normal_actions_while_waiting_effect": True,
        "condition_switch_hold_cycles": 1,
        "model_reload_hold_cycles": 1,
    },

    "fast_mode": {
        "minimum_hold_minutes": 4.0,
        "exit_stable_cycles": 4,
        "recovery_hold_minutes": 2.0,
        "block_economic_slurry_decrease": True,
        "allow_regular_policy_fallback": False,
    },

    "execution_limits": {
        "valve_limit_margin": 0.5,
        "maximum_single_valve_delta_by_magnitude": {
            "MICRO": 1.0,
            "SMALL": 2.0,
            "MEDIUM": 4.0,
            "STRONG": 6.0,
        },
        "rule_step_by_magnitude": {
            "MICRO": 0.6,
            "SMALL": 1.2,
            "MEDIUM": 2.5,
            "STRONG": 4.0,
        },
        "minimum_command_delta": 0.5,
        "preferred_increase_action_families": [],
        "preferred_decrease_action_families": [],
    },

    "logging": {
        "enabled": True,
        "decision_log_filename": "online_decisions.jsonl",
        "execution_log_filename": "online_executions.jsonl",
        "runtime_state_filename": "online_runtime_state.json",
    },
}

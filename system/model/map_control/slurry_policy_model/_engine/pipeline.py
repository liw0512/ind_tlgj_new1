from __future__ import annotations

import copy
from typing import Any, Callable

import pandas as pd

from .aggregator import aggregate_all_levels
from .calibration import (
    assign_action_magnitude_labels,
    assign_response_labels,
    calibrate_action_magnitude_bins,
    calibrate_disturbance_thresholds,
    calibrate_response_settings,
)
from .data_loader import assign_continuous_segments, load_input_data
from .episode_extractor import extract_decision_episodes
from .schema import freeze_condition_axes
from .signal_processing import add_clean_valve_columns
from .tower_policy_projection import project_tower_policy_deltas


ProgressCallback = Callable[[float, str], None]
POLICY_SEMANTICS_VERSION = "TOWER_LEVEL_V3_PUMP_GATED"


def _emit_range(
    progress: ProgressCallback | None,
    start: float,
    end: float,
) -> ProgressCallback | None:
    if not progress:
        return None
    return lambda value, message: progress(
        start + (end - start) * min(1.0, max(0.0, float(value))), message
    )


def _normalized_training_semantics(training: dict[str, Any]) -> dict[str, Any]:
    """Attach canonical policy semantics and freeze condition-axis metadata."""
    result = freeze_condition_axes(training)
    result.setdefault("state", {})
    result["state"].setdefault("policy_state_mode", "COARSE_TOWER")
    result["policy_semantics_version"] = POLICY_SEMANTICS_VERSION
    return result


def _pump_topology_signature(plant: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the policy-relevant pump/current/valve topology in stable order."""

    result: list[dict[str, Any]] = []
    for tower in plant.get("towers", []):
        if not tower.get("enabled", True):
            continue
        pumps = []
        for pump in tower.get("supply_pumps", []) or []:
            pumps.append(
                {
                    "pump_id": str(pump.get("pump_id", "")),
                    "current_column": str(pump.get("current_column", "")),
                    "run_current_threshold": float(
                        pump.get("run_current_threshold", 0.0)
                    ),
                    "served_valve_ids": sorted(
                        str(value)
                        for value in (pump.get("served_valve_ids", []) or [])
                    ),
                }
            )
        result.append(
            {
                "tower_id": str(tower.get("tower_id", "")),
                "supply_pumps": sorted(
                    pumps,
                    key=lambda item: (
                        item["pump_id"],
                        item["current_column"],
                    ),
                ),
            }
        )
    return sorted(result, key=lambda item: item["tower_id"])


def _validate_previous_semantics(
    previous_effective_config: dict[str, Any] | None,
    plant: dict[str, Any],
    training: dict[str, Any],
) -> None:
    if not previous_effective_config:
        return
    mode = str(
        training.get("state", {}).get("policy_state_mode", "COARSE_TOWER")
    ).upper()
    if mode == "LEGACY_DETAILED":
        return

    previous_training = previous_effective_config.get("training", {}) or {}
    previous_mode = str(
        previous_training.get("state", {}).get("policy_state_mode", "")
    ).upper()
    previous_semantics = str(
        previous_training.get("policy_semantics_version", "")
    ).upper()
    if (
        previous_mode != "COARSE_TOWER"
        or previous_semantics != POLICY_SEMANTICS_VERSION
    ):
        raise ValueError(
            "上一版第二模块仍是旧策略语义，不能直接增量混入 "
            f"{POLICY_SEMANTICS_VERSION}。请先用完整历史数据重新执行一次初次训练；"
            "新基线建立后，后续版本可继续正常增量训练。"
        )

    # 工况轴属于 episode 定义的一部分。字段/顺序变化后旧 episode 的扰动与
    # 邻域含义已经不同，不能直接增量混用。
    previous_axes = previous_training.get("_condition_axes")
    current_axes = training.get("_condition_axes")
    if previous_axes is not None and previous_axes != current_axes:
        raise ValueError(
            "第一模块 condition axes 已变化，旧第二模块 episode 不能直接增量继承。"
            "请基于完整历史数据重新执行一次初次训练。"
        )

    # 泵电流阈值和 pump→valve 拓扑会改变 episode 有效性以及在线可执行阀门，
    # 因此属于模型语义的一部分。任何变化都必须重新初次训练，不能与旧 episode
    # 混合增量。
    previous_plant = previous_effective_config.get("plant", {}) or {}
    if _pump_topology_signature(previous_plant) != _pump_topology_signature(plant):
        raise ValueError(
            "供浆泵电流阈值或 pump→valve 拓扑已变化，旧第二模块 episode 不能直接"
            "增量继承。请基于完整历史数据重新执行一次初次训练。"
        )


def prepare_raw_data(
    input_specs: list[str] | str,
    plant: dict[str, Any],
    training: dict[str, Any],
    progress: ProgressCallback | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    # load_input_data 也会从同一 condition_config 读取轴；这里提前冻结，确保本次
    # 训练从 CSV 读取到最终 effective_config 始终使用完全相同的字段定义。
    training = freeze_condition_axes(training)
    df, warnings = load_input_data(
        input_specs,
        plant,
        training,
        progress=_emit_range(progress, 0.00, 0.72),
    )
    if progress:
        progress(0.78, "划分连续运行数据段")
    df = assign_continuous_segments(df, plant, training)
    if progress:
        segment_count = (
            int(df["continuous_segment_id"].nunique()) if not df.empty else 0
        )
        progress(0.86, f"连续运行段划分完成，共 {segment_count} 段")
        progress(0.90, "执行阀位短窗口中位数去抖")
    df = add_clean_valve_columns(df, plant, training)
    if progress:
        progress(1.0, f"原始数据预处理完成，共 {len(df)} 行")
    return df, warnings


def run_episode_pipeline(
    raw_df: pd.DataFrame,
    plant: dict[str, Any],
    training: dict[str, Any],
    previous_effective_config: dict[str, Any] | None = None,
    recalibrate: bool = False,
    aggregate_results: bool = True,
    progress: ProgressCallback | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any], dict[str, Any]]:
    training = _normalized_training_semantics(training)
    _validate_previous_semantics(previous_effective_config, plant, training)

    if progress:
        progress(0.01, "准备工况轴扰动阈值")
    if previous_effective_config and not recalibrate:
        effective_disturbance = copy.deepcopy(
            previous_effective_config["disturbance"]
        )
        if progress:
            progress(0.06, "沿用上一版工况轴扰动阈值")
    else:
        effective_disturbance = calibrate_disturbance_thresholds(
            raw_df, plant, training
        )
        if progress:
            progress(0.06, "完成工况轴扰动阈值标定")

    episodes, _actions = extract_decision_episodes(
        raw_df,
        plant,
        training,
        effective_disturbance,
        progress=_emit_range(progress, 0.06, 0.68),
    )

    if progress:
        progress(0.70, "准备动作幅度与响应强度参数")
    if previous_effective_config and not recalibrate:
        effective_action = copy.deepcopy(
            previous_effective_config["action_magnitude"]
        )
        effective_response = copy.deepcopy(
            previous_effective_config["response"]
        )
        if progress:
            progress(0.76, "沿用上一版动作幅度和响应强度参数")
    else:
        effective_action = calibrate_action_magnitude_bins(episodes, training)
        effective_response = calibrate_response_settings(episodes, training)
        if progress:
            progress(0.76, "完成动作幅度和响应强度参数标定")

    if not episodes.empty:
        if progress:
            progress(0.79, "标记动作幅度与历史响应标签")
        episodes = assign_action_magnitude_labels(episodes, effective_action)
        episodes = assign_response_labels(episodes, effective_response)
        valid = episodes[episodes["valid"]].copy()
        invalid = episodes[~episodes["valid"]].copy()
        valid = project_tower_policy_deltas(valid, plant)
    else:
        valid = pd.DataFrame()
        invalid = pd.DataFrame()
    if progress:
        progress(
            0.84,
            f"决策片段校验完成：VALID={len(valid)}，INVALID={len(invalid)}",
        )

    if aggregate_results:
        aggregated = aggregate_all_levels(
            valid,
            plant,
            training,
            progress=_emit_range(progress, 0.84, 1.00),
        )
    else:
        aggregated = {
            "conditions": {},
            "condition_grids": {},
            "neighbor_state": {},
            "plant_action_prior": {},
            "transients": {},
        }
        if progress:
            progress(1.0, "新增决策片段提取完成，等待与旧经验合并")

    effective = {
        "plant": copy.deepcopy(plant),
        "training": copy.deepcopy(training),
        "disturbance": effective_disturbance,
        "action_magnitude": effective_action,
        "response": effective_response,
    }
    return valid, invalid, effective, aggregated

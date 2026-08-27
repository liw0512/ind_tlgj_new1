from __future__ import annotations

import copy
from typing import Any, Callable

import pandas as pd

from .data_loader import assign_continuous_segments, load_input_data
from .episode_extractor import extract_decision_episodes
from .historical_evidence import enrich_historical_episode_frame
from .schema import freeze_condition_axes
from .signal_processing import add_clean_supply_flow_columns


ProgressCallback = Callable[[float, str], None]
POLICY_SEMANTICS_VERSION = "ACTUAL_SUPPLY_FLOW_V1"


def _emit_range(progress: ProgressCallback | None, start: float, end: float) -> ProgressCallback | None:
    if not progress:
        return None
    return lambda value, message: progress(
        start + (end - start) * min(1.0, max(0.0, float(value))), message
    )


def _normalized_training_semantics(training: dict[str, Any]) -> dict[str, Any]:
    result = freeze_condition_axes(training)
    result.setdefault("state", {})
    result["state"].setdefault("policy_state_mode", "COARSE_TOWER")
    result["policy_semantics_version"] = POLICY_SEMANTICS_VERSION
    return result


def _flow_topology_signature(plant: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for tower in plant.get("towers", []):
        if not tower.get("enabled", True):
            continue
        meters = sorted(
            str(item.get("column", ""))
            for item in (tower.get("supply_flows", []) or [])
        )
        result.append({
            "tower_id": str(tower.get("tower_id", "")),
            "supply_flow_columns": meters,
        })
    return sorted(result, key=lambda item: item["tower_id"])


def _validate_previous_semantics(previous_effective_config: dict[str, Any] | None, plant: dict[str, Any], training: dict[str, Any]) -> None:
    if not previous_effective_config:
        return
    previous_training = previous_effective_config.get("training", {}) or {}
    previous_semantics = str(previous_training.get("policy_semantics_version", "")).upper()
    if previous_semantics != POLICY_SEMANTICS_VERSION:
        raise ValueError(
            "上一版第二模块仍是旧策略语义，不能直接增量混入 "
            f"{POLICY_SEMANTICS_VERSION}。请先用完整历史数据重新执行一次初次训练；"
            "新基线建立后，后续版本可继续正常增量训练。"
        )
    previous_axes = previous_training.get("_condition_axes")
    current_axes = training.get("_condition_axes")
    if previous_axes is not None and previous_axes != current_axes:
        raise ValueError("第一模块 condition axes 已变化，旧第二模块 episode 不能直接增量继承。请重新初次训练。")
    previous_plant = previous_effective_config.get("plant", {}) or {}
    if _flow_topology_signature(previous_plant) != _flow_topology_signature(plant):
        raise ValueError("供浆流量测点拓扑已变化，请重新初次训练。")


def prepare_raw_data(input_specs: list[str] | str, plant: dict[str, Any], training: dict[str, Any], progress: ProgressCallback | None = None) -> tuple[pd.DataFrame, list[str]]:
    training = freeze_condition_axes(training)
    df, warnings = load_input_data(input_specs, plant, training, progress=_emit_range(progress, 0.00, 0.72))
    if progress:
        progress(0.78, "划分连续运行数据段")
    df = assign_continuous_segments(df, plant, training)
    if progress:
        segment_count = int(df["continuous_segment_id"].nunique()) if not df.empty else 0
        progress(0.86, f"连续运行段划分完成，共 {segment_count} 段")
        progress(0.90, "执行供浆流量短窗口中位数去抖")
    df = add_clean_supply_flow_columns(df, plant, training)
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
        progress(0.02, "读取上游 fast_change_mode 因果标签")

    episodes, _actions = extract_decision_episodes(
        raw_df, plant, training, progress=_emit_range(progress, 0.02, 0.62)
    )
    if not episodes.empty:
        if progress:
            progress(0.64, "生成MFAC历史剂量指标并执行LOCAL/DYNAMIC/SAFETY证据分流")
        episodes = enrich_historical_episode_frame(
            episodes,
            raw_df,
            plant,
            routing_config=training.get("mfac_historical_evidence", {}),
        )

    if progress:
        progress(0.70, "准备供浆流量动作响应参数")
    if previous_effective_config and not recalibrate:
        effective_action = copy.deepcopy(
            previous_effective_config.get("action_magnitude", {})
        )
        effective_response = copy.deepcopy(
            previous_effective_config.get("response", training.get("response", {}))
        )
    else:
        # 流量动作的峰值、最终值、持续时间和响应时间直接在
        # supply_flow_prototype 中按分布学习。
        effective_action = {"semantics": "ACTUAL_SUPPLY_FLOW_V1"}
        effective_response = copy.deepcopy(training.get("response", {}))

    if not episodes.empty:
        valid = episodes[episodes["valid"]].copy()
        invalid = episodes[~episodes["valid"]].copy()
    else:
        valid = pd.DataFrame()
        invalid = pd.DataFrame()
    if progress:
        local_gain_count = (
            int(valid["mfac_local_gain_eligible"].fillna(False).sum())
            if not valid.empty and "mfac_local_gain_eligible" in valid.columns
            else 0
        )
        dynamic_count = (
            int(valid["mfac_dynamic_evidence_eligible"].fillna(False).sum())
            if not valid.empty and "mfac_dynamic_evidence_eligible" in valid.columns
            else 0
        )
        safety_count = (
            int(valid["mfac_safety_evidence"].fillna(False).sum())
            if not valid.empty and "mfac_safety_evidence" in valid.columns
            else 0
        )
        progress(
            0.84,
            "决策片段校验完成：VALID=%d，INVALID=%d，LOCAL_GAIN=%d，DYNAMIC=%d，SAFETY=%d"
            % (len(valid), len(invalid), local_gain_count, dynamic_count, safety_count),
        )

    aggregated = {
        "conditions": {},
    }
    if progress:
        progress(1.0, "供浆流量动作提取完成，等待生成MFAC历史证据")

    effective = {
        "plant": copy.deepcopy(plant),
        "training": copy.deepcopy(training),
        "action_magnitude": effective_action,
        "response": effective_response,
        "mfac_historical_evidence": copy.deepcopy(
            training.get("mfac_historical_evidence", {})
        ),
    }
    return valid, invalid, effective, aggregated

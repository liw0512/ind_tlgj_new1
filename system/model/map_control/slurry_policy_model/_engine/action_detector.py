from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
import pandas as pd

from .config_loader import all_valves, enabled_towers
from .schema import time_column
from .time_index import TimeWindowIndexer
from .utils import median_or_nan


@dataclass
class RawAction:
    start_time: pd.Timestamp
    end_time: pd.Timestamp
    before_values: dict[str, float]
    after_values: dict[str, float]
    delta_values: dict[str, float]
    normalized_delta_values: dict[str, float]
    action_family: str
    action_direction: str
    action_magnitude_value: float
    active_valve_ids: list[str]
    active_tower_ids: list[str]


def _clean_col(valve: dict[str, Any]) -> str:
    return f"__clean_valve__{valve['valve_id']}"


def _classify_action(
    delta: dict[str, float], plant: dict[str, Any], training: dict[str, Any]
) -> tuple[str, str, float, list[str], list[str], dict[str, float]]:
    """把分阀历史操作归并为塔级供浆动作。

    设计原则：
    - 每个阀门的原始 delta 继续保留，便于审计和后续执行分配；
    - 动作主键不再区分“只动阀1/平衡/不平衡”等操作习惯；
    - 同一座塔的动作统一归为 ``TOWER:<tower>|SUPPLY``；
    - 动作幅度使用该塔全部配置阀门的归一化净变化均值，避免阀门数量越多
      动作幅度天然越大的问题；
    - 多塔同时动作仍单独保留为联合历史，但在线 NORMAL 模式默认不采用。
    """
    del training  # 保留签名兼容旧调用；塔级分类不再依赖阀门平衡阈值。

    valves = all_valves(plant)
    active: list[dict[str, Any]] = []
    normalized: dict[str, float] = {}
    for valve in valves:
        value = float(delta.get(valve["valve_id"], 0.0))
        span = float(valve["max_opening"]) - float(valve["min_opening"])
        normalized[valve["valve_id"]] = value / span if span > 0 else 0.0
        if abs(value) >= float(valve["action_threshold"]):
            active.append(valve)

    if not active:
        return "HOLD", "HOLD", 0.0, [], [], normalized

    active_valve_ids = [str(v["valve_id"]) for v in active]
    active_tower_ids = sorted({str(v["tower_id"]) for v in active})

    tower_equivalent: dict[str, float] = {}
    for tower in enabled_towers(plant):
        tower_id = str(tower["tower_id"])
        values = [
            float(normalized.get(str(valve["valve_id"]), 0.0))
            for valve in tower.get("valves", [])
        ]
        tower_equivalent[tower_id] = float(np.mean(values)) if values else 0.0

    active_equivalent = [tower_equivalent[tower_id] for tower_id in active_tower_ids]
    eps = 1e-12
    if all(value > eps for value in active_equivalent):
        direction = "INCREASE"
    elif all(value < -eps for value in active_equivalent):
        direction = "DECREASE"
    else:
        direction = "MIXED"

    # 单塔时 magnitude 就是该塔等效归一化动作；多塔历史仅用于独立联合经验，
    # 采用各塔等效动作绝对值之和，不把具体阀门数量带入幅度定义。
    magnitude = float(sum(abs(value) for value in active_equivalent))

    if len(active_tower_ids) > 1:
        family = "MULTI_TOWER:" + "+".join(active_tower_ids) + "|COMBINED"
    else:
        family = f"TOWER:{active_tower_ids[0]}|SUPPLY"
    return family, direction, magnitude, active_valve_ids, active_tower_ids, normalized


def detect_actions(
    df: pd.DataFrame,
    plant: dict[str, Any],
    training: dict[str, Any],
    progress: Callable[[float, str], None] | None = None,
) -> list[RawAction]:
    ts_col = time_column(plant)
    valves = all_valves(plant)
    if df.empty:
        if progress:
            progress(1.0, "没有可检测的数据")
        return []

    baseline_minutes = float(training["episode"]["baseline_minutes"])
    detection_minutes = float(training["episode"]["action_detection_window_minutes"])
    stable_minutes = float(training["episode"]["action_end_stable_minutes"])
    max_duration = float(training["episode"]["max_action_duration_minutes"])
    merge_gap = float(training["episode"]["action_merge_gap_minutes"])

    indexer = TimeWindowIndexer(df, ts_col)
    time_values = pd.DatetimeIndex(df[ts_col])
    actions: list[RawAction] = []
    i = 0
    n = len(df)
    report_step = max(1, n // 100)
    if progress:
        progress(0.0, f"开始扫描 {n} 行阀位数据")
    while i < n:
        if progress and (i % report_step == 0 or i == n - 1):
            progress(i / max(n, 1), f"扫描阀位动作 {i}/{n}，已发现 {len(actions)} 个")
        current_time = time_values[i]
        anchor_start = current_time - pd.Timedelta(minutes=detection_minutes)
        anchor_window = indexer.slice(anchor_start, current_time)
        if len(anchor_window) < 2:
            i += 1
            continue
        before = {v["valve_id"]: median_or_nan(anchor_window[_clean_col(v)]) for v in valves}
        current = {v["valve_id"]: float(df.iloc[i][_clean_col(v)]) for v in valves}
        triggered = any(
            pd.notna(current[v["valve_id"]])
            and pd.notna(before[v["valve_id"]])
            and abs(current[v["valve_id"]] - before[v["valve_id"]])
            >= float(v["action_threshold"])
            for v in valves
        )
        if not triggered:
            i += 1
            continue

        # 回退到最早超过噪声死区的点，尽量恢复动作真实起点。
        start_idx = i
        earliest_index = indexer.left(anchor_start)
        for k in range(i - 1, earliest_index - 1, -1):
            # 不再要求每个厂额外配置“测点噪声死区”。
            # 回溯动作起点时，内部使用 action_threshold 的 25% 作为起点敏感阈值；
            # 真正认定动作仍必须达到完整 action_threshold。
            deviated = any(
                abs(float(df.iloc[k][_clean_col(v)]) - before[v["valve_id"]])
                > 0.25 * float(v["action_threshold"])
                for v in valves
                if pd.notna(df.iloc[k][_clean_col(v)]) and pd.notna(before[v["valve_id"]])
            )
            if deviated:
                start_idx = k

        start_time = time_values[start_idx]
        baseline_window = indexer.slice(
            start_time - pd.Timedelta(minutes=baseline_minutes), start_time
        )
        before = {v["valve_id"]: median_or_nan(baseline_window[_clean_col(v)]) for v in valves}

        j = i
        found_end = False
        while j < n:
            t = time_values[j]
            if t - start_time > pd.Timedelta(minutes=max_duration):
                break
            stable_start = t - pd.Timedelta(minutes=stable_minutes)
            stable_window = indexer.slice(stable_start, t)
            if len(stable_window) >= 2 and t > start_time:
                stable = True
                for valve in valves:
                    values = pd.to_numeric(stable_window[_clean_col(valve)], errors="coerce").dropna()
                    # 动作结束稳定判据与动作阈值统一，不再单独配置 hold_deadband。
                    if values.empty or float(values.max() - values.min()) > float(valve["action_threshold"]):
                        stable = False
                        break
                if stable:
                    after = {
                        v["valve_id"]: median_or_nan(stable_window[_clean_col(v)]) for v in valves
                    }
                    delta = {
                        v["valve_id"]: after[v["valve_id"]] - before[v["valve_id"]]
                        for v in valves
                    }
                    if any(
                        abs(delta[v["valve_id"]]) >= float(v["action_threshold"])
                        for v in valves
                    ):
                        found_end = True
                        break
            j += 1

        if not found_end:
            i += 1
            continue

        end_time = time_values[j]
        family, direction, magnitude, active_valves, active_towers, normalized = _classify_action(
            delta, plant, training
        )
        action = RawAction(
            start_time=start_time,
            end_time=end_time,
            before_values=before,
            after_values=after,
            delta_values=delta,
            normalized_delta_values=normalized,
            action_family=family,
            action_direction=direction,
            action_magnitude_value=magnitude,
            active_valve_ids=active_valves,
            active_tower_ids=active_towers,
        )

        if actions and action.start_time - actions[-1].end_time <= pd.Timedelta(minutes=merge_gap):
            previous = actions.pop()
            merged_before = previous.before_values
            merged_after = action.after_values
            merged_delta = {key: merged_after[key] - merged_before[key] for key in merged_before}
            family, direction, magnitude, active_valves, active_towers, normalized = _classify_action(
                merged_delta, plant, training
            )
            action = RawAction(
                start_time=previous.start_time,
                end_time=action.end_time,
                before_values=merged_before,
                after_values=merged_after,
                delta_values=merged_delta,
                normalized_delta_values=normalized,
                action_family=family,
                action_direction=direction,
                action_magnitude_value=magnitude,
                active_valve_ids=active_valves,
                active_tower_ids=active_towers,
            )
        actions.append(action)
        i = j + 1
    if progress:
        progress(1.0, f"动作扫描完成，共发现 {len(actions)} 个 ACTION")
    return actions

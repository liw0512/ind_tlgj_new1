from __future__ import annotations

from typing import Any

import numpy as np

from .config_loader import enabled_towers


def ph_band(value: float, safe_range: list[float], guard: float) -> str:
    if np.isnan(value):
        return "UNKNOWN"
    lo, hi = map(float, safe_range)
    mid = (lo + hi) / 2.0
    if value < lo:
        return "BELOW_LIMIT"
    if value < lo + guard:
        return "LOW_MARGIN"
    if value < mid:
        return "NORMAL_LOW"
    if value <= hi - guard:
        return "NORMAL_HIGH"
    if value <= hi:
        return "HIGH_MARGIN"
    return "ABOVE_LIMIT"


def interval_band(value: float, edges: list[float], prefix: str) -> str:
    if np.isnan(value):
        return f"{prefix}_UNKNOWN"
    if value < edges[0]:
        return f"{prefix}_BELOW_{edges[0]:g}"
    for left, right in zip(edges[:-1], edges[1:]):
        if left <= value < right:
            return f"{prefix}_{left:g}_{right:g}"
    if value <= edges[-1]:
        return f"{prefix}_{edges[-2]:g}_{edges[-1]:g}"
    return f"{prefix}_OVER_{edges[-1]:g}"


def trend_band(rate: float, slow: float, fast: float) -> str:
    if np.isnan(rate):
        return "UNKNOWN"
    if rate >= fast:
        return "RISING_FAST"
    if rate >= slow:
        return "RISING"
    if rate <= -fast:
        return "FALLING_FAST"
    if rate <= -slow:
        return "FALLING"
    return "STABLE"


def normalized_opening(value: float, lo: float, hi: float) -> float:
    if np.isnan(value) or hi <= lo:
        return float("nan")
    return min(1.0, max(0.0, (value - lo) / (hi - lo)))


def _coarse_policy_state(row: dict[str, Any]) -> tuple[str, str]:
    """V2 塔级策略状态。

    condition_label 已经作为聚合外层主键，因此这里不再把 pH、SO2 区间、
    阀位区间和多阀平衡状态再次离散组合。这样可以显著减少三个月历史数据
    被切成大量稀疏小桶的问题。

    pH、当前 SO2、当前阀位仍完整保留为连续实时量，在线候选过滤/排序时使用；
    它们只是“不再成为经验桶主键”，并不是被忽略。
    """
    grid = str(row.get("anchor_grid_id", row.get("start_grid_id", "UNKNOWN")))
    disturbance = str(row.get("disturbance_mode", "UNKNOWN"))
    state = "TRANSIENT" if "FAST" in disturbance else "REGULAR"
    return f"GRID={grid}|{state}", state


def _legacy_detailed_policy_state(
    row: dict[str, Any], plant: dict[str, Any], training: dict[str, Any]
) -> tuple[str, str]:
    """保留旧版细状态逻辑，供显式 LEGACY_DETAILED 配置兼容。"""
    pieces: list[str] = []
    pieces_without_grid: list[str] = []

    grid = str(row.get("anchor_grid_id", row.get("start_grid_id", "UNKNOWN")))
    pieces.append(f"GRID={grid}")

    for tower in enabled_towers(plant):
        tower_id = tower["tower_id"]
        value = float(row.get(f"before_ph__{tower_id}", float("nan")))
        band = ph_band(value, tower["ph_safe_range"], float(tower["ph_guard_band"]))
        token = f"PH:{tower_id}={band}"
        pieces.append(token)
        pieces_without_grid.append(token)

    outlet = float(row.get("before_outlet_so2", float("nan")))
    so2_token = interval_band(outlet, list(map(float, training["state"]["outlet_so2_edges"])), "SO2")
    pieces.append(so2_token)
    pieces_without_grid.append(so2_token)

    trend = trend_band(
        float(row.get("before_outlet_so2_rate", float("nan"))),
        float(training["state"]["outlet_so2_trend_slow_rate"]),
        float(training["state"]["outlet_so2_trend_fast_rate"]),
    )
    trend_token = f"SO2_TREND={trend}"
    pieces.append(trend_token)
    pieces_without_grid.append(trend_token)

    opening_edges = list(map(float, training["state"]["valve_opening_edges"]))
    balance_threshold = float(training["state"]["valve_balance_threshold"])
    for tower in enabled_towers(plant):
        normalized_values: list[float] = []
        for valve in tower["valves"]:
            value = float(row.get(f"before_valve__{valve['valve_id']}", float("nan")))
            normalized_values.append(
                normalized_opening(value, float(valve["min_opening"]), float(valve["max_opening"]))
            )
        clean = [v for v in normalized_values if not np.isnan(v)]
        mean = float(np.mean(clean)) if clean else float("nan")
        spread = float(max(clean) - min(clean)) if clean else float("nan")
        opening = interval_band(mean, opening_edges, f"OPEN:{tower['tower_id']}")
        allocation = "BALANCED" if len(clean) <= 1 or spread <= balance_threshold else "UNBALANCED"
        token = f"{opening}|ALLOC:{tower['tower_id']}={allocation}"
        pieces.append(token)
        pieces_without_grid.append(token)

    condition_state = str(row.get("condition_state_key", ""))
    if training["state"].get("include_condition_state_key", True) and condition_state:
        token = f"COND={condition_state}"
        pieces.append(token)
        pieces_without_grid.append(token)

    disturbance = str(row.get("disturbance_mode", "UNKNOWN"))
    if training["state"].get("include_disturbance_mode", True):
        token = f"DIST={disturbance}"
        pieces.append(token)
        pieces_without_grid.append(token)

    return "|".join(pieces), "|".join(pieces_without_grid)


def build_policy_state(
    row: dict[str, Any], plant: dict[str, Any], training: dict[str, Any]
) -> tuple[str, str]:
    mode = str(training.get("state", {}).get("policy_state_mode", "COARSE_TOWER")).upper()
    if mode == "LEGACY_DETAILED":
        return _legacy_detailed_policy_state(row, plant, training)
    return _coarse_policy_state(row)

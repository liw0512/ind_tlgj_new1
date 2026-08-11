from __future__ import annotations

from typing import Any


def _level(rate: float, slow: float, fast: float, prefix: str) -> str:
    magnitude = abs(rate)
    if magnitude < slow:
        return "STEADY"
    direction = "RISE" if rate > 0 else "DROP"
    speed = "FAST" if magnitude >= fast else "SLOW"
    return f"{prefix}_{direction}_{speed}"


def classify_disturbance(
    load_rate: float, inlet_so2_rate: float, effective_disturbance: dict[str, Any]
) -> str:
    load_mode = _level(
        load_rate,
        float(effective_disturbance["load_slow_rate"]),
        float(effective_disturbance["load_fast_rate"]),
        "LOAD",
    )
    so2_mode = _level(
        inlet_so2_rate,
        float(effective_disturbance["inlet_so2_slow_rate"]),
        float(effective_disturbance["inlet_so2_fast_rate"]),
        "INLET_SO2",
    )
    if load_mode == "STEADY" and so2_mode == "STEADY":
        return "STEADY"
    if load_mode == "STEADY":
        return so2_mode
    if so2_mode == "STEADY":
        return load_mode

    load_dir = "RISE" if "RISE" in load_mode else "DROP"
    so2_dir = "RISE" if "RISE" in so2_mode else "DROP"
    fast = "FAST" in load_mode or "FAST" in so2_mode
    if load_dir == so2_dir:
        return f"LOAD_AND_SO2_{load_dir}_{'FAST' if fast else 'SLOW'}"
    return "MIXED_DISTURBANCE"


def is_fast_disturbance(mode: str) -> bool:
    return "FAST" in str(mode)

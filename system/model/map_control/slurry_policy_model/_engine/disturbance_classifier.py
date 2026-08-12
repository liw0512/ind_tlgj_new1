from __future__ import annotations

from typing import Any


def _level(rate: float, slow: float, fast: float, prefix: str) -> str:
    magnitude = abs(float(rate))
    if magnitude < float(slow):
        return "STEADY"
    direction = "RISE" if float(rate) > 0 else "DROP"
    speed = "FAST" if magnitude >= float(fast) else "SLOW"
    return f"{prefix}_{direction}_{speed}"


def _generic_thresholds(effective: dict[str, Any]) -> list[dict[str, Any]]:
    values = effective.get("axis_thresholds")
    if isinstance(values, (list, tuple)) and values:
        result = []
        for index, item in enumerate(values, start=1):
            value = dict(item or {})
            result.append(
                {
                    "axis_index": int(value.get("axis_index", index)),
                    "column": str(value.get("column", f"AXIS{index}")),
                    "slow_rate": float(value["slow_rate"]),
                    "fast_rate": float(value["fast_rate"]),
                }
            )
        return result

    # Read-only compatibility for policy snapshots trained before configurable
    # condition axes were introduced.
    return [
        {
            "axis_index": 1,
            "column": "jzfh",
            "slow_rate": float(effective.get("load_slow_rate", 1.0)),
            "fast_rate": float(effective.get("load_fast_rate", 3.0)),
            "legacy_prefix": "LOAD",
        },
        {
            "axis_index": 2,
            "column": "yyq_SO2",
            "slow_rate": float(effective.get("inlet_so2_slow_rate", 20.0)),
            "fast_rate": float(effective.get("inlet_so2_fast_rate", 60.0)),
            "legacy_prefix": "INLET_SO2",
        },
    ]


def classify_disturbance(
    first_axis_rate: float,
    second_axis_rate: float | None,
    effective_disturbance: dict[str, Any],
) -> str:
    """Classify one/two condition-axis trends.

    New snapshots use AXIS1/AXIS2 labels so the same transient library works for
    arbitrary plant variables.  Old snapshots lacking ``axis_thresholds`` keep
    their historical LOAD/INLET_SO2 labels and remain readable online.
    """
    thresholds = _generic_thresholds(effective_disturbance)
    rates = [float(first_axis_rate)]
    if len(thresholds) >= 2:
        rates.append(float(second_axis_rate or 0.0))

    modes: list[str] = []
    legacy = "axis_thresholds" not in effective_disturbance
    for index, threshold in enumerate(thresholds[: len(rates)], start=1):
        prefix = (
            str(threshold.get("legacy_prefix"))
            if legacy and threshold.get("legacy_prefix")
            else f"AXIS{index}"
        )
        modes.append(
            _level(
                rates[index - 1],
                threshold["slow_rate"],
                threshold["fast_rate"],
                prefix,
            )
        )

    active = [mode for mode in modes if mode != "STEADY"]
    if not active:
        return "STEADY"
    if len(active) == 1:
        return active[0]

    directions = ["RISE" if "RISE" in mode else "DROP" for mode in active]
    fast = any("FAST" in mode for mode in active)
    speed = "FAST" if fast else "SLOW"
    if len(set(directions)) == 1:
        if legacy:
            return f"LOAD_AND_SO2_{directions[0]}_{speed}"
        return f"AXIS1_AND_AXIS2_{directions[0]}_{speed}"

    # Keep FAST visible for opposite-direction mixed disturbances.  This also
    # fixes the old bug where a fast mixed event was accidentally routed into
    # REGULAR because the returned label lost the word FAST.
    return f"MIXED_DISTURBANCE_{speed}"


def is_fast_disturbance(mode: str) -> bool:
    return "FAST" in str(mode).upper()

from __future__ import annotations

from typing import Any


def calculate_reliability(
    event_count: int,
    segment_count: int,
    day_count: int,
    direction_consistency: float,
    stable_ratio: float,
    safety_violation_ratio: float,
    config: dict[str, Any],
) -> dict[str, float]:
    support_event = min(1.0, event_count / max(1, int(config["reference_event_count"])))
    support_segment = min(1.0, segment_count / max(1, int(config["reference_segment_count"])))
    support = 100.0 * min(support_event, support_segment)
    consistency = 100.0 * max(0.0, min(1.0, direction_consistency))
    stability = 100.0 * max(0.0, min(1.0, stable_ratio))
    safety = 100.0 * max(0.0, min(1.0, 1.0 - safety_violation_ratio))
    coverage = 100.0 * min(1.0, day_count / max(1, int(config["reference_day_count"])))
    components = {
        "support_score": support,
        "consistency_score": consistency,
        "stability_score": stability,
        "safety_history_score": safety,
        "coverage_score": coverage,
    }
    weights = config["weights"]
    total = (
        support * float(weights["support"])
        + consistency * float(weights["direction_consistency"])
        + stability * float(weights["response_stability"])
        + safety * float(weights["safety_history"])
        + coverage * float(weights["time_coverage"])
    )
    components["total_score"] = total
    return components


def profile_status(
    event_count: int, segment_count: int, day_count: int, config: dict[str, Any]
) -> str:
    if event_count == 0:
        return "NO_DATA"
    if (
        event_count >= int(config["minimum_supported_events"])
        and segment_count >= int(config["minimum_supported_segments"])
        and day_count >= int(config["minimum_supported_days"])
    ):
        return "SUPPORTED"
    return "LOW_SUPPORT"

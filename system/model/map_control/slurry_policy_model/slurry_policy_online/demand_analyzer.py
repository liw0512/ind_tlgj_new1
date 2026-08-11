from __future__ import annotations

from typing import List

from .types import ControlDemand


MAGNITUDE_ORDER = {"HOLD": 0, "MICRO": 1, "SMALL": 2, "MEDIUM": 3, "STRONG": 4}


def analyze_demand(
    current_so2: float,
    commanded_target: float,
    effective_target: float,
    target_changed: bool,
    plant_config: dict,
    online_config: dict,
) -> ControlDemand:
    so2_cfg = online_config["so2_control"]
    regular = online_config["regular_control"]
    emission_limit = so2_cfg.get("emission_limit")
    if emission_limit is None:
        emission_limit = float(plant_config["outlet_so2_safe_range"][1])
    emission_limit = float(emission_limit)
    warning_threshold = emission_limit - float(so2_cfg["emission_warning_margin"])
    emergency_threshold = emission_limit - float(so2_cfg["emission_emergency_margin"])
    error = float(current_so2) - float(effective_target)
    abs_error = abs(error)
    deadband = float(so2_cfg["target_deadband"])
    reasons: List[str] = []

    if current_so2 >= emergency_threshold:
        safety = "EMERGENCY"
        level = "EMERGENCY"
        desired = "SO2_DOWN"
        acceptable = ["DECREASE"]
        reasons.append("SO2_EMERGENCY_ZONE")
    elif current_so2 >= warning_threshold:
        safety = "WARNING"
        level = "WARNING"
        desired = "SO2_DOWN"
        acceptable = ["DECREASE", "NEUTRAL"]
        reasons.append("SO2_WARNING_ZONE")
    elif abs_error <= deadband:
        safety = "NORMAL"
        level = "TARGET_HOLD"
        desired = "SO2_HOLD"
        acceptable = ["NEUTRAL"]
        reasons.append("SO2_INSIDE_TARGET_DEADBAND")
    elif error > 0:
        safety = "NORMAL"
        desired = "SO2_DOWN"
        acceptable = ["DECREASE"]
        if abs_error <= float(regular["small_error_threshold"]):
            level = "TARGET_SMALL"
        elif abs_error <= float(regular["medium_error_threshold"]):
            level = "TARGET_MEDIUM"
        else:
            level = "TARGET_LARGE"
        reasons.append("SO2_ABOVE_TARGET")
    else:
        safety = "NORMAL"
        conservative = bool(so2_cfg.get("decrease_slurry_more_conservative", True))
        minimum_error = float(so2_cfg.get("minimum_low_side_error_for_decrease", 0.0))
        if conservative and abs_error < minimum_error:
            level = "TARGET_HOLD"
            desired = "SO2_HOLD"
            acceptable = ["NEUTRAL"]
            reasons.append("SO2_LOW_BUT_DECREASE_GUARD_ACTIVE")
        else:
            level = "TARGET_SMALL" if abs_error <= float(regular["small_error_threshold"]) else "TARGET_MEDIUM"
            desired = "SO2_UP"
            acceptable = ["INCREASE", "NEUTRAL"]
            reasons.append("SO2_BELOW_TARGET")

    max_magnitude = str(regular["maximum_magnitude_by_level"][level]).upper()
    if target_changed:
        reasons.append("TARGET_COMMAND_CHANGED")
    return ControlDemand(
        commanded_target=commanded_target,
        effective_target=effective_target,
        current_so2=current_so2,
        error=error,
        demand_level=level,
        desired_so2_response=desired,
        acceptable_effect_directions=acceptable,
        maximum_action_magnitude=max_magnitude,
        safety_level=safety,
        target_changed=target_changed,
        reason_codes=reasons,
    )

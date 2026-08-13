from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

from .demand_analyzer import MAGNITUDE_ORDER
from .types import ControlDemand


@dataclass
class FastActionEnvelope:
    control_mode: str
    fast_direction: str
    allowed_slurry_directions: List[str]
    acceptable_effect_directions: List[str]
    maximum_action_magnitude: str
    preferred_effect_direction: str
    allow_preemptive_increase: bool = False
    risk_escalation: bool = False
    reason_codes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _max_magnitude(left: str, right: str) -> str:
    return max(
        (str(left).upper(), str(right).upper()),
        key=lambda value: MAGNITUDE_ORDER.get(value, 0),
    )


def build_fast_action_envelope(
    fast: Dict[str, Any], demand: ControlDemand, online: dict
) -> FastActionEnvelope:
    cfg = online.get("fast_policy", {})
    mode = str(fast.get("fast_change_mode", "REGULAR")).upper()
    direction = str(fast.get("fast_change_direction", "NONE")).upper()
    effect_state = str(fast.get("fast_change_effect_state", "UNKNOWN")).upper()
    effect_risk = str(fast.get("fast_change_effect_risk_level", "LOW")).upper()
    outlet_trend = str(fast.get("fast_change_outlet_so2_trend", "STABLE")).upper()
    exact = str(fast.get("fast_change_exact_trend_mode", "STEADY")).upper()
    reasons: List[str] = []

    acceptable = list(demand.acceptable_effect_directions)
    allowed_slurry = ["HOLD", "INCREASE", "DECREASE"]
    maximum = str(demand.maximum_action_magnitude).upper()
    preferred = acceptable[0] if acceptable else "NEUTRAL"
    allow_preemptive = False
    risk_escalation = bool(
        mode in {"FAST_CHANGE", "FAST_RECOVERY"}
        and (effect_risk in {"HIGH", "EMERGENCY"} or outlet_trend == "RISING_FAST")
    )

    if demand.safety_level in {"WARNING", "EMERGENCY"}:
        allowed_slurry = ["HOLD", "INCREASE"]
        preferred = "DECREASE"
        if "DECREASE" not in acceptable:
            acceptable.insert(0, "DECREASE")
        reasons.append("FAST_ENVELOPE_EMISSION_GUARD")
    elif mode == "FAST_CHANGE":
        allowed_slurry = ["HOLD", "INCREASE"]
        if direction == "RISE":
            reasons.append("FAST_RISE_BLOCKS_ECONOMIC_DECREASE")
            allow_preemptive = bool(cfg.get("allow_preemptive_increase", True))
            if effect_state in {"ABOVE_TARGET", "ABOVE_TARGET_FAR"}:
                maximum = _max_magnitude(maximum, "SMALL" if effect_state == "ABOVE_TARGET" else "MEDIUM")
                preferred = "DECREASE"
            elif effect_state == "TARGET_BAND" and allow_preemptive:
                cap = str(cfg.get("target_band_preemptive_max_magnitude", "SMALL")).upper()
                if outlet_trend == "RISING_FAST" and "AND" in exact:
                    cap = str(cfg.get("combined_rise_max_magnitude", "MEDIUM")).upper()
                maximum = _max_magnitude(maximum, cap)
                if "DECREASE" not in acceptable:
                    acceptable.append("DECREASE")
                if outlet_trend in {"RISING", "RISING_FAST"} or "AND" in exact:
                    acceptable = ["DECREASE"] + [x for x in acceptable if x != "DECREASE"]
                    preferred = "DECREASE"
                    reasons.append("FAST_RISE_PREEMPTIVE_INCREASE_PREFERRED")
            elif effect_state in {"BELOW_TARGET", "BELOW_TARGET_FAR"}:
                if allow_preemptive and (outlet_trend in {"RISING", "RISING_FAST"} or "AND" in exact):
                    maximum = _max_magnitude(maximum, "SMALL")
                    if "DECREASE" not in acceptable:
                        acceptable.append("DECREASE")
                    preferred = "DECREASE" if outlet_trend == "RISING_FAST" else preferred
                    reasons.append("FAST_RISE_LOW_SO2_PROTECTIVE_OPTION")
                else:
                    maximum = "HOLD"
                    acceptable = ["NEUTRAL"]
                    preferred = "NEUTRAL"
        elif direction == "DROP":
            reasons.append("FAST_DROP_HOLDS_ECONOMIC_DECREASE")
            if effect_state in {"BELOW_TARGET", "BELOW_TARGET_FAR", "TARGET_BAND"}:
                maximum = "HOLD"
                acceptable = ["NEUTRAL"]
                preferred = "NEUTRAL"
        else:
            reasons.append("FAST_MIXED_CONSERVATIVE")
            if effect_state in {"BELOW_TARGET", "BELOW_TARGET_FAR", "TARGET_BAND"}:
                maximum = "HOLD"
                acceptable = ["NEUTRAL"]
                preferred = "NEUTRAL"
    elif mode == "FAST_RECOVERY":
        if direction == "DROP" and effect_state in {"BELOW_TARGET", "BELOW_TARGET_FAR"} and outlet_trend in {"STABLE", "FALLING", "FALLING_FAST"}:
            allowed_slurry = ["HOLD", "DECREASE", "INCREASE"]
            cap = str(cfg.get("recovery_drop_max_decrease_magnitude", "SMALL")).upper()
            maximum = cap if MAGNITUDE_ORDER.get(maximum, 0) > MAGNITUDE_ORDER.get(cap, 0) else maximum
            reasons.append("FAST_DROP_RECOVERY_ECONOMIC_DECREASE_ALLOWED")
        else:
            allowed_slurry = ["HOLD", "INCREASE"]
            if effect_state in {"TARGET_BAND", "BELOW_TARGET", "BELOW_TARGET_FAR"}:
                maximum = "HOLD"
                acceptable = ["NEUTRAL"]
            reasons.append("FAST_RECOVERY_CONSERVATIVE")

    acceptable = list(dict.fromkeys(acceptable))
    return FastActionEnvelope(
        control_mode=mode,
        fast_direction=direction,
        allowed_slurry_directions=allowed_slurry,
        acceptable_effect_directions=acceptable,
        maximum_action_magnitude=maximum,
        preferred_effect_direction=preferred,
        allow_preemptive_increase=allow_preemptive,
        risk_escalation=risk_escalation,
        reason_codes=reasons,
    )


def apply_fast_action_envelope(demand: ControlDemand, envelope: FastActionEnvelope) -> ControlDemand:
    return ControlDemand(
        commanded_target=demand.commanded_target,
        effective_target=demand.effective_target,
        current_so2=demand.current_so2,
        error=demand.error,
        demand_level=demand.demand_level,
        desired_so2_response=demand.desired_so2_response,
        acceptable_effect_directions=list(envelope.acceptable_effect_directions),
        maximum_action_magnitude=envelope.maximum_action_magnitude,
        safety_level=demand.safety_level,
        target_changed=demand.target_changed,
        reason_codes=list(demand.reason_codes) + list(envelope.reason_codes),
    )

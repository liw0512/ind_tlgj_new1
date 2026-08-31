from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class IdentifiabilityLevel(str, Enum):
    IDENTIFIABLE = "IDENTIFIABLE"
    WEAKLY_IDENTIFIABLE = "WEAKLY_IDENTIFIABLE"
    UNIDENTIFIABLE = "UNIDENTIFIABLE"


@dataclass(frozen=True)
class IdentifiabilityAssessment:
    level: IdentifiabilityLevel
    weight: float
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "identifiability": self.level.value,
            "identification_weight": float(self.weight),
            "identification_reason_codes": list(self.reason_codes),
        }


def _bool(row: Mapping[str, Any], key: str, default: bool = False) -> bool:
    value = row.get(key, default)
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "t"}


def _number(row: Mapping[str, Any], key: str) -> float | None:
    value = row.get(key)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def assess_episode_identifiability(
    row: Mapping[str, Any],
    *,
    minimum_abs_delta_flow: float = 0.5,
) -> IdentifiabilityAssessment:
    """Assess whether a historical flow event can identify process dynamics.

    This deliberately does *not* ask whether the historical operator action was
    good.  Under-action and over-action can both be informative.  The hard
    blockers are missing/invalid execution or process evidence.  Measured
    external disturbances and FAST/condition changes are not automatically
    discarded because the V2 dynamic model explicitly includes those causal
    disturbance channels.
    """

    hard: list[str] = []
    soft: list[str] = []

    if not _bool(row, "flow_event_complete", True):
        hard.append("FLOW_EVENT_INCOMPLETE")
    if not _bool(row, "flow_learning_eligible", True):
        hard.append("FLOW_SIGNAL_NOT_LEARNABLE")
    if not _bool(row, "flow_effect_complete", True):
        hard.append("RESPONSE_WINDOW_INCOMPLETE")

    delta = _number(row, "flow_event_max_abs_delta_flow")
    if delta is None:
        delta = _number(row, "flow_event_final_delta_flow")
    if delta is None:
        hard.append("ACTUAL_DELTA_FLOW_MISSING")
    elif abs(delta) < float(minimum_abs_delta_flow):
        hard.append("EXCITATION_TOO_SMALL")

    # Pump/circulation topology changes alter the manipulated-path physics and
    # are not treated as ordinary measured disturbances in V1 identification.
    if _bool(row, "flow_circulation_change", False):
        hard.append("CIRCULATION_CONFIGURATION_CHANGED")
    if _bool(row, "supply_pump_state_changed", False):
        hard.append("SUPPLY_PUMP_STATE_CHANGED")

    if not _bool(row, "condition_valid", True):
        soft.append("CONDITION_CONTEXT_INVALID_OR_OOR")
    if _bool(row, "out_of_range_clipped", False):
        soft.append("CONDITION_OUT_OF_RANGE_CLIPPED")

    # A second supply-flow action inside the observation window makes isolated
    # step-response attribution weaker, but it remains usable by the later ARX
    # path where the full Q_actual history is an explicit input.
    if _bool(row, "followup_action_in_response", False):
        soft.append("FOLLOWUP_FLOW_ACTION_IN_RESPONSE")

    # Major load/yyq disturbances are intentionally *not* hard blockers.  They
    # are modeled through plant_config.condition_axes.  Mark them for weighting
    # and audit so clean isolated events can still receive higher confidence.
    if _bool(row, "flow_major_process_transition", False):
        soft.append("MEASURED_PROCESS_DISTURBANCE_PRESENT")
    if _bool(row, "is_transient", False):
        soft.append("TRANSIENT_CONTEXT")

    # Safety/effect quality describes the historical action outcome, not
    # identifiability.  Keep it out of hard blockers by design.
    if _bool(row, "ph_out_of_range", False) or any(
        _bool(row, key, False)
        for key in row.keys()
        if str(key).startswith("ph_out_of_range__")
    ):
        soft.append("HISTORICAL_PH_UNSAFE_OUTCOME")
    if _bool(row, "outlet_so2_over_hard_max", False):
        soft.append("HISTORICAL_SO2_UNSAFE_OUTCOME")

    if hard:
        return IdentifiabilityAssessment(
            IdentifiabilityLevel.UNIDENTIFIABLE,
            0.0,
            tuple(dict.fromkeys(hard + soft)),
        )
    if soft:
        # Deliberately non-zero: these events can still inform a multivariable
        # causal time-series model, but should not dominate clean excitation.
        return IdentifiabilityAssessment(
            IdentifiabilityLevel.WEAKLY_IDENTIFIABLE,
            0.35,
            tuple(dict.fromkeys(soft)),
        )
    return IdentifiabilityAssessment(
        IdentifiabilityLevel.IDENTIFIABLE,
        1.0,
        ("CLEAN_CAUSAL_EXCITATION",),
    )

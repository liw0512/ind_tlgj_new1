# -*- coding: utf-8 -*-
"""Strict learning-eligibility gate for Scheme 2 MFAC V1.

Scheme 1 intentionally learns useful slurry actions under multiple disturbance
routes.  Scheme 2 has a different objective: estimate one local CFDL-like
``delta SO2 / delta Q`` sensitivity.  Its bootstrap sample gate is therefore
stricter and initially accepts only isolated, sustained STEP events.
"""

from dataclasses import asdict, dataclass, field
import math
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple


@dataclass(frozen=True)
class MFACEligibilityConfig:
    """Algorithm gate configuration.

    Plant-specific magnitude thresholds are deliberately optional.  Missing
    required evidence results in ``INSUFFICIENT_EVIDENCE`` rather than silently
    approving the event.
    """

    allowed_shapes: Tuple[str, ...] = ("STEP",)
    allowed_disturbance_classes: Tuple[str, ...] = ("STEADY",)
    require_scheme1_valid: bool = True
    require_effect_complete: bool = True
    require_flow_context_eligible: bool = True
    reject_followup_action: bool = True
    reject_circulation_change: bool = True
    reject_major_process_transition: bool = True
    reject_equipment_change: bool = True
    require_condition_context_stable: bool = True
    require_target_stable: bool = True
    require_qbase_stable: bool = True
    require_negative_phi: bool = True
    min_abs_delta_q: Optional[float] = None
    max_abs_qbase_drift: Optional[float] = None
    max_relative_qbase_drift: Optional[float] = None


@dataclass(frozen=True)
class MFACEligibilityDecision:
    eligible: bool
    decision: str
    reasons: Tuple[str, ...] = ()
    metrics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["reasons"] = list(self.reasons)
        return value


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y", "on"}:
        return True
    if text in {"false", "0", "no", "n", "off", ""}:
        return False
    return default


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _upper(value: Any) -> str:
    return str(value or "").strip().upper()


class StrictMFACEligibilityGate:
    """Evaluate whether a completed observed action may update MFAC ``phi``."""

    def __init__(self, config: MFACEligibilityConfig | None = None) -> None:
        self.config = config or MFACEligibilityConfig()

    def evaluate(self, evidence: Mapping[str, Any]) -> MFACEligibilityDecision:
        reasons: list[str] = []
        insufficient: list[str] = []
        config = self.config

        shape = _upper(evidence.get("flow_shape"))
        if shape not in {item.upper() for item in config.allowed_shapes}:
            reasons.append(f"FLOW_SHAPE_NOT_ALLOWED:{shape or 'UNKNOWN'}")

        disturbance = _upper(evidence.get("flow_disturbance_class"))
        if disturbance not in {
            item.upper() for item in config.allowed_disturbance_classes
        }:
            reasons.append(
                f"DISTURBANCE_CLASS_NOT_ALLOWED:{disturbance or 'UNKNOWN'}"
            )

        if config.require_scheme1_valid and not _bool(
            evidence.get("scheme1_valid"), False
        ):
            reasons.append("SCHEME1_EPISODE_INVALID")
        if config.require_effect_complete and not _bool(
            evidence.get("effect_complete"), False
        ):
            reasons.append("EFFECT_PROFILE_INCOMPLETE")
        if config.require_flow_context_eligible and not _bool(
            evidence.get("flow_context_eligible"), False
        ):
            reasons.append("SCHEME1_FLOW_CONTEXT_CONFOUNDED")
        if config.reject_followup_action and _bool(
            evidence.get("followup_action_in_response"), False
        ):
            reasons.append("FOLLOWUP_ACTION_IN_RESPONSE")
        if config.reject_circulation_change and _bool(
            evidence.get("circulation_changed"), False
        ):
            reasons.append("CIRCULATION_CHANGED")
        if config.reject_major_process_transition and _bool(
            evidence.get("major_process_transition"), False
        ):
            reasons.append("MAJOR_PROCESS_TRANSITION")
        if config.reject_equipment_change and _bool(
            evidence.get("equipment_changed"), False
        ):
            reasons.append("EQUIPMENT_CHANGED")

        if config.require_condition_context_stable:
            if evidence.get("context_stability_evidence_available") is not True:
                insufficient.append("MISSING_CONTEXT_STABILITY_EVIDENCE")
            elif _bool(evidence.get("condition_context_changed"), False):
                reasons.append("MFAC_CONTEXT_CHANGED")

        if config.require_target_stable:
            if evidence.get("target_evidence_available") is not True:
                insufficient.append("MISSING_TARGET_STABILITY_EVIDENCE")
            elif _bool(evidence.get("target_changed"), False):
                reasons.append("SO2_TARGET_CHANGED")

        qbase_before = _finite(evidence.get("qbase_before"))
        qbase_after = _finite(evidence.get("qbase_after"))
        qbase_drift = _finite(evidence.get("qbase_drift"))
        if config.require_qbase_stable:
            if evidence.get("qbase_evidence_available") is not True:
                insufficient.append("MISSING_QBASE_STABILITY_EVIDENCE")
            else:
                if qbase_drift is None and qbase_before is not None and qbase_after is not None:
                    qbase_drift = qbase_after - qbase_before
                if qbase_drift is None:
                    insufficient.append("QBASE_DRIFT_UNAVAILABLE")
                else:
                    if (
                        config.max_abs_qbase_drift is not None
                        and abs(qbase_drift) > float(config.max_abs_qbase_drift)
                    ):
                        reasons.append("QBASE_ABSOLUTE_DRIFT_TOO_LARGE")
                    if config.max_relative_qbase_drift is not None:
                        if qbase_before is None:
                            insufficient.append("QBASE_BEFORE_UNAVAILABLE")
                        else:
                            relative = abs(qbase_drift) / max(abs(qbase_before), 1e-9)
                            if relative > float(config.max_relative_qbase_drift):
                                reasons.append("QBASE_RELATIVE_DRIFT_TOO_LARGE")

        delta_q = _finite(evidence.get("delta_q_actual"))
        delta_so2 = _finite(evidence.get("delta_so2"))
        if delta_q is None:
            insufficient.append("DELTA_Q_UNAVAILABLE")
        elif abs(delta_q) <= 1e-12:
            reasons.append("DELTA_Q_ZERO")
        elif (
            config.min_abs_delta_q is not None
            and abs(delta_q) < float(config.min_abs_delta_q)
        ):
            reasons.append("DELTA_Q_BELOW_MINIMUM")

        if delta_so2 is None:
            insufficient.append("DELTA_SO2_UNAVAILABLE")

        phi_event: Optional[float] = None
        if delta_q is not None and abs(delta_q) > 1e-12 and delta_so2 is not None:
            phi_event = delta_so2 / delta_q
            if config.require_negative_phi and phi_event >= 0.0:
                reasons.append("PHI_DIRECTION_NOT_NEGATIVE")

        metrics = {
            "flow_shape": shape,
            "flow_disturbance_class": disturbance,
            "delta_q_actual": delta_q,
            "delta_so2": delta_so2,
            "phi_event": phi_event,
            "qbase_before": qbase_before,
            "qbase_after": qbase_after,
            "qbase_drift": qbase_drift,
        }

        if reasons:
            return MFACEligibilityDecision(
                eligible=False,
                decision="REJECTED",
                reasons=tuple(reasons + insufficient),
                metrics=metrics,
            )
        if insufficient:
            return MFACEligibilityDecision(
                eligible=False,
                decision="INSUFFICIENT_EVIDENCE",
                reasons=tuple(insufficient),
                metrics=metrics,
            )
        return MFACEligibilityDecision(
            eligible=True,
            decision="ELIGIBLE",
            reasons=(),
            metrics=metrics,
        )

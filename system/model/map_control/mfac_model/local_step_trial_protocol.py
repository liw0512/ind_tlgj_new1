# -*- coding: utf-8 -*-
"""Manual-only closed-loop protocol for controlled LOCAL_GAIN identification.

The protocol deliberately reuses the existing actual-flow tracking and
independent SO2/pH response events.  It does not issue a command, replace the
normal MFAC target, write DCS, or update online ``phi``.  A successful trial is
only an evidence candidate until a second explicit human evidence review
promotes it to a canonical ``ActionResponseEvent``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import math
from typing import Any, Dict, Optional, Tuple

from .local_step_identification import LocalStepIdentificationProposal
from .mfac_schema import ActionResponseEvent
from .ph_arbitration import PHResidualArbitrationConfig
from .ph_response import PHResponseEvent
from .process_response import ProcessResponseEvent


LOCAL_STEP_TRIAL_PROTOCOL_VERSION = (
    "SCHEME2_LOCAL_STEP_TRIAL_PROTOCOL_V1_MANUAL_REVIEW"
)


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if hasattr(value, "to_pydatetime"):
        converted = value.to_pydatetime()
        if isinstance(converted, datetime):
            return converted
    text = str(value or "").strip()
    if not text:
        raise ValueError("timestamp is required")
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _elapsed_seconds(start: Any, end: Any) -> Optional[float]:
    try:
        return max(0.0, (_timestamp(end) - _timestamp(start)).total_seconds())
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class LocalStepTrialProtocolConfig:
    """Reviewed evidence and safety limits; no site defaults are provided."""

    max_sample_gap_seconds: float
    max_abs_step_error_m3_h: float
    max_abs_qbase_drift: float
    max_relative_qbase_drift: float
    max_abs_inlet_so2_change: float
    min_abs_delta_so2: float
    min_abs_delta_ph: float
    minimum_so2_observation_seconds: float
    minimum_ph_observation_seconds: float
    outlet_so2_abort_headroom_to_safe_max: float

    def __post_init__(self) -> None:
        positive = (
            "max_sample_gap_seconds",
            "max_abs_step_error_m3_h",
            "max_abs_qbase_drift",
            "max_relative_qbase_drift",
            "max_abs_inlet_so2_change",
            "min_abs_delta_so2",
            "min_abs_delta_ph",
            "minimum_so2_observation_seconds",
            "minimum_ph_observation_seconds",
        )
        for name in positive:
            value = _finite(getattr(self, name))
            if value is None or value <= 0.0:
                raise ValueError("%s must be finite and > 0" % name)
        headroom = _finite(self.outlet_so2_abort_headroom_to_safe_max)
        if headroom is None or headroom < 0.0:
            raise ValueError(
                "outlet_so2_abort_headroom_to_safe_max must be finite and >= 0"
            )


@dataclass(frozen=True)
class LocalStepTrialPlan:
    trial_id: str
    proposal_id: str
    reviewer_id: str
    approval_time: str
    condition_snapshot_version: str
    mfac_context_id: str
    pretrial_actual_supply_flow: float
    pretrial_qbase_effective: float
    pretrial_ph: float
    pretrial_outlet_so2: float
    approved_test_target_supply_flow: float
    approved_step_up_m3_h: float
    manual_return_target_supply_flow: float
    manual_execution_required: bool = True
    automatic_execution_allowed: bool = False
    dcs_write_enabled: bool = False
    learning_permission: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = LOCAL_STEP_TRIAL_PROTOCOL_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def approve_local_step_proposal(
    proposal: LocalStepIdentificationProposal,
    *,
    human_approved: bool,
    reviewer_id: str,
    approval_time: Any,
) -> LocalStepTrialPlan:
    """Convert one proposal into an approved *manual* trial plan.

    This function records approval only.  It has no actuator or DCS side effect.
    """
    if not bool(human_approved):
        raise ValueError("explicit human approval is required")
    if proposal.status != "REVIEW_CANDIDATE":
        raise ValueError("only REVIEW_CANDIDATE proposals can be approved")
    reviewer = str(reviewer_id or "").strip()
    if not reviewer:
        raise ValueError("reviewer_id is required")
    approved_at = _timestamp(approval_time).isoformat()
    required = {
        "actual_supply_flow": proposal.actual_supply_flow,
        "qbase_effective": proposal.qbase_effective,
        "ph_value": proposal.ph_value,
        "outlet_so2": proposal.outlet_so2,
        "proposed_test_target_supply_flow": proposal.proposed_test_target_supply_flow,
        "step_up_m3_h": proposal.step_up_m3_h,
    }
    parsed: Dict[str, float] = {}
    for name, value in required.items():
        number = _finite(value)
        if number is None:
            raise ValueError("proposal is missing %s" % name)
        parsed[name] = number
    metadata = dict(proposal.metadata or {})
    snapshot = str(metadata.get("condition_snapshot_version") or "").strip()
    context = str(metadata.get("mfac_context_id") or "").strip()
    if not snapshot or not context:
        raise ValueError("proposal is missing condition/context binding")
    return LocalStepTrialPlan(
        trial_id="TRIAL-%s" % proposal.proposal_id,
        proposal_id=proposal.proposal_id,
        reviewer_id=reviewer,
        approval_time=approved_at,
        condition_snapshot_version=snapshot,
        mfac_context_id=context,
        pretrial_actual_supply_flow=parsed["actual_supply_flow"],
        pretrial_qbase_effective=parsed["qbase_effective"],
        pretrial_ph=parsed["ph_value"],
        pretrial_outlet_so2=parsed["outlet_so2"],
        approved_test_target_supply_flow=parsed[
            "proposed_test_target_supply_flow"
        ],
        approved_step_up_m3_h=parsed["step_up_m3_h"],
        manual_return_target_supply_flow=parsed["actual_supply_flow"],
        metadata={
            "manual_approval_recorded": True,
            "normal_algorithm_target_replaced": False,
            "automatic_dcs_adapter": False,
            "proposal_metadata": metadata,
        },
    )


@dataclass
class LocalStepTrialSafetyDecision:
    status: str
    trial_id: str
    abort_recommended: bool
    reasons: Tuple[str, ...] = ()
    ph_min_observed: Optional[float] = None
    ph_max_observed: Optional[float] = None
    outlet_so2_max_observed: Optional[float] = None
    sample_count: int = 0
    manual_return_target_supply_flow: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = LOCAL_STEP_TRIAL_PROTOCOL_VERSION

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["reasons"] = list(self.reasons)
        return value


class LocalStepTrialSafetyMonitor:
    """Observe a manually executed trial and recommend aborts without actuating."""

    dcs_write_enabled = False
    automatic_execution_allowed = False

    def __init__(
        self,
        plan: LocalStepTrialPlan,
        config: LocalStepTrialProtocolConfig,
        ph_envelope: PHResidualArbitrationConfig,
        *,
        outlet_so2_safe_max: float,
    ) -> None:
        self.plan = plan
        self.config = config
        self.ph_envelope = ph_envelope
        safe_max = _finite(outlet_so2_safe_max)
        if safe_max is None:
            raise ValueError("outlet_so2_safe_max must be finite")
        headroom = float(config.outlet_so2_abort_headroom_to_safe_max)
        if headroom >= safe_max:
            raise ValueError("SO2 abort headroom must be smaller than safe maximum")
        self.outlet_so2_abort_threshold = safe_max - headroom
        self._last_timestamp: Optional[datetime] = None
        self._ph_min: Optional[float] = None
        self._ph_max: Optional[float] = None
        self._so2_max: Optional[float] = None
        self._sample_count = 0
        self._aborted = False
        self._abort_reasons: Tuple[str, ...] = ()

    def update(
        self,
        *,
        timestamp: Any,
        ph_value: Any,
        outlet_so2: Any,
        qbase_drift_from_pretrial: Any,
        inlet_so2_change_from_pretrial: Any,
        condition_snapshot_version: str,
        mfac_context_id: str,
        data_quality_ok: bool = True,
        fast_active: bool = False,
        equipment_changed: bool = False,
        unexpected_target_change: bool = False,
    ) -> LocalStepTrialSafetyDecision:
        if self._aborted:
            return self._decision("ABORT_RECOMMENDED", self._abort_reasons)

        reasons = []
        try:
            now = _timestamp(timestamp)
        except (TypeError, ValueError):
            now = None
            reasons.append("INVALID_TIMESTAMP")

        if now is not None and self._last_timestamp is not None:
            gap = (now - self._last_timestamp).total_seconds()
            if gap < 0.0 or gap > float(self.config.max_sample_gap_seconds):
                reasons.append("SAMPLE_GAP")
        if now is not None:
            self._last_timestamp = now

        ph = _finite(ph_value)
        so2 = _finite(outlet_so2)
        qbase_drift = _finite(qbase_drift_from_pretrial)
        inlet_change = _finite(inlet_so2_change_from_pretrial)
        self._sample_count += 1

        if not bool(data_quality_ok):
            reasons.append("DATA_QUALITY_INVALID")
        if bool(fast_active):
            reasons.append("FAST_ACTIVE")
        if bool(equipment_changed):
            reasons.append("EQUIPMENT_CHANGED")
        if bool(unexpected_target_change):
            reasons.append("UNEXPECTED_TARGET_CHANGE")
        if str(condition_snapshot_version or "") != self.plan.condition_snapshot_version:
            reasons.append("CONDITION_SNAPSHOT_CHANGED")
        if str(mfac_context_id or "") != self.plan.mfac_context_id:
            reasons.append("MFAC_CONTEXT_CHANGED")

        if ph is None:
            reasons.append("PH_INVALID")
        else:
            self._ph_min = ph if self._ph_min is None else min(self._ph_min, ph)
            self._ph_max = ph if self._ph_max is None else max(self._ph_max, ph)
            if not (
                float(self.ph_envelope.operating_min)
                <= ph
                <= float(self.ph_envelope.operating_max)
            ):
                reasons.append("PH_LEFT_OPERATING_ENVELOPE")

        if so2 is None:
            reasons.append("OUTLET_SO2_INVALID")
        else:
            self._so2_max = so2 if self._so2_max is None else max(self._so2_max, so2)
            if so2 >= self.outlet_so2_abort_threshold:
                reasons.append("OUTLET_SO2_ABORT_HEADROOM_REACHED")

        if (
            qbase_drift is None
            or abs(qbase_drift) > float(self.config.max_abs_qbase_drift)
        ):
            reasons.append("QBASE_DRIFT_TOO_LARGE")
        if (
            inlet_change is None
            or abs(inlet_change) > float(self.config.max_abs_inlet_so2_change)
        ):
            reasons.append("INLET_SO2_CHANGE_TOO_LARGE")

        reasons = tuple(dict.fromkeys(reasons))
        if reasons:
            self._aborted = True
            self._abort_reasons = reasons
            return self._decision("ABORT_RECOMMENDED", reasons)
        return self._decision("CONTINUE", ())

    def _decision(
        self,
        status: str,
        reasons: Tuple[str, ...],
    ) -> LocalStepTrialSafetyDecision:
        return LocalStepTrialSafetyDecision(
            status=status,
            trial_id=self.plan.trial_id,
            abort_recommended=status == "ABORT_RECOMMENDED",
            reasons=tuple(reasons),
            ph_min_observed=self._ph_min,
            ph_max_observed=self._ph_max,
            outlet_so2_max_observed=self._so2_max,
            sample_count=self._sample_count,
            manual_return_target_supply_flow=(
                self.plan.manual_return_target_supply_flow
                if status == "ABORT_RECOMMENDED"
                else None
            ),
            metadata={
                "manual_action_only": True,
                "normal_algorithm_target_replaced": False,
                "dcs_write_enabled": False,
            },
        )


@dataclass
class LocalStepTrialOutcome:
    status: str
    trial_id: str
    tracking_event_id: str
    eligible_for_local_gain_promotion: bool
    reasons: Tuple[str, ...] = ()
    delta_q_actual: Optional[float] = None
    delta_so2: Optional[float] = None
    delta_ph: Optional[float] = None
    phi_so2_event: Optional[float] = None
    phi_ph_event: Optional[float] = None
    q_before: Optional[float] = None
    q_after: Optional[float] = None
    qbase_before: Optional[float] = None
    qbase_after: Optional[float] = None
    qbase_drift: Optional[float] = None
    so2_before: Optional[float] = None
    so2_after: Optional[float] = None
    ph_before: Optional[float] = None
    ph_after: Optional[float] = None
    action_start_time: str = ""
    actual_flow_reached_time: str = ""
    so2_response_start_time: str = ""
    so2_response_end_time: str = ""
    ph_response_start_time: str = ""
    ph_response_end_time: str = ""
    learning_permission: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = LOCAL_STEP_TRIAL_PROTOCOL_VERSION

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["reasons"] = list(self.reasons)
        return value


def evaluate_local_step_trial(
    plan: LocalStepTrialPlan,
    config: LocalStepTrialProtocolConfig,
    so2_response: ProcessResponseEvent,
    ph_response: PHResponseEvent,
    safety: LocalStepTrialSafetyDecision,
) -> LocalStepTrialOutcome:
    """Evaluate one manually executed trial; never grants runtime learning."""
    reasons = []
    insufficient = []
    if safety.abort_recommended:
        reasons.append("TRIAL_ABORT_RECOMMENDED")
    if so2_response.status != "COMPLETED":
        reasons.append("SO2_RESPONSE_NOT_COMPLETED:%s" % so2_response.status)
    if ph_response.status != "COMPLETED":
        reasons.append("PH_RESPONSE_NOT_COMPLETED:%s" % ph_response.status)
    if (
        not so2_response.tracking_event_id
        or so2_response.tracking_event_id != ph_response.tracking_event_id
    ):
        reasons.append("DUAL_RESPONSE_TRACKING_EVENT_MISMATCH")

    for response, prefix in ((so2_response, "SO2"), (ph_response, "PH")):
        if response.condition_snapshot_version != plan.condition_snapshot_version:
            reasons.append("%s_CONDITION_SNAPSHOT_MISMATCH" % prefix)
        if response.mfac_context_id != plan.mfac_context_id:
            reasons.append("%s_MFAC_CONTEXT_MISMATCH" % prefix)
        if bool(response.fast_overlap):
            reasons.append("%s_FAST_OVERLAP" % prefix)
        if bool(response.condition_changed):
            reasons.append("%s_CONDITION_CHANGED" % prefix)
        if bool(response.target_changed):
            reasons.append("%s_SO2_TARGET_CHANGED" % prefix)
        if not bool(response.data_quality_ok):
            reasons.append("%s_DATA_QUALITY_INVALID" % prefix)

    delta_q_so2 = _finite(so2_response.delta_q_actual)
    delta_q_ph = _finite(ph_response.delta_q_actual)
    if delta_q_so2 is None or delta_q_ph is None:
        insufficient.append("DELTA_Q_UNAVAILABLE")
        delta_q = delta_q_so2 if delta_q_so2 is not None else delta_q_ph
    else:
        delta_q = 0.5 * (delta_q_so2 + delta_q_ph)
        if abs(delta_q_so2 - delta_q_ph) > float(config.max_abs_step_error_m3_h):
            reasons.append("DUAL_RESPONSE_DELTA_Q_MISMATCH")
    if delta_q is not None:
        if delta_q <= 0.0:
            reasons.append("IDENTIFICATION_STEP_DIRECTION_NOT_POSITIVE")
        if abs(delta_q - plan.approved_step_up_m3_h) > float(
            config.max_abs_step_error_m3_h
        ):
            reasons.append("ACTUAL_STEP_DIFFERS_FROM_APPROVED_STEP")

    delta_so2 = _finite(so2_response.delta_so2)
    delta_ph = _finite(ph_response.delta_ph)
    if delta_so2 is None:
        insufficient.append("DELTA_SO2_UNAVAILABLE")
    elif abs(delta_so2) < float(config.min_abs_delta_so2):
        reasons.append("SO2_EFFECT_BELOW_MINIMUM")
    if delta_ph is None:
        insufficient.append("DELTA_PH_UNAVAILABLE")
    elif abs(delta_ph) < float(config.min_abs_delta_ph):
        reasons.append("PH_EFFECT_BELOW_MINIMUM")

    phi_so2 = None
    phi_ph = None
    if delta_q is not None and abs(delta_q) > 1e-12:
        if delta_so2 is not None:
            phi_so2 = delta_so2 / delta_q
            if phi_so2 >= 0.0:
                reasons.append("PHI_SO2_DIRECTION_NOT_NEGATIVE")
        if delta_ph is not None:
            phi_ph = delta_ph / delta_q
            if phi_ph <= 0.0:
                reasons.append("PHI_PH_DIRECTION_NOT_POSITIVE")

    qbase_drift_values = [
        value
        for value in (
            _finite(so2_response.qbase_drift),
            _finite(ph_response.qbase_drift),
        )
        if value is not None
    ]
    qbase_drift = max(qbase_drift_values, key=abs) if qbase_drift_values else None
    qbase_before = _finite(so2_response.qbase_before)
    qbase_after = _finite(so2_response.qbase_after)
    if qbase_drift is None:
        insufficient.append("QBASE_DRIFT_UNAVAILABLE")
    else:
        if abs(qbase_drift) > float(config.max_abs_qbase_drift):
            reasons.append("QBASE_ABSOLUTE_DRIFT_TOO_LARGE")
        if qbase_before is None:
            insufficient.append("QBASE_BEFORE_UNAVAILABLE")
        else:
            relative = abs(qbase_drift) / max(abs(qbase_before), 1e-9)
            if relative > float(config.max_relative_qbase_drift):
                reasons.append("QBASE_RELATIVE_DRIFT_TOO_LARGE")

    inlet_change = _finite(so2_response.inlet_so2_change)
    if inlet_change is None:
        insufficient.append("INLET_SO2_CHANGE_UNAVAILABLE")
    elif abs(inlet_change) > float(config.max_abs_inlet_so2_change):
        reasons.append("INLET_SO2_CHANGE_TOO_LARGE")

    so2_observation = _elapsed_seconds(
        so2_response.actual_flow_reached_time,
        so2_response.response_end_time,
    )
    ph_observation = _elapsed_seconds(
        ph_response.actual_flow_reached_time,
        ph_response.response_end_time,
    )
    if so2_observation is None:
        insufficient.append("SO2_OBSERVATION_DURATION_UNAVAILABLE")
    elif so2_observation < float(config.minimum_so2_observation_seconds):
        reasons.append("SO2_OBSERVATION_TOO_SHORT")
    if ph_observation is None:
        insufficient.append("PH_OBSERVATION_DURATION_UNAVAILABLE")
    elif ph_observation < float(config.minimum_ph_observation_seconds):
        reasons.append("PH_OBSERVATION_TOO_SHORT")

    if safety.ph_min_observed is None or safety.ph_max_observed is None:
        insufficient.append("PH_TRIAL_ENVELOPE_UNAVAILABLE")

    reasons = list(dict.fromkeys(reasons))
    insufficient = [item for item in dict.fromkeys(insufficient) if item not in reasons]
    eligible = not reasons and not insufficient
    status = "LOCAL_GAIN_EVIDENCE_CANDIDATE" if eligible else (
        "REJECTED" if reasons else "INSUFFICIENT_EVIDENCE"
    )
    all_reasons = tuple(reasons + insufficient)
    return LocalStepTrialOutcome(
        status=status,
        trial_id=plan.trial_id,
        tracking_event_id=str(so2_response.tracking_event_id or ""),
        eligible_for_local_gain_promotion=eligible,
        reasons=all_reasons,
        delta_q_actual=delta_q,
        delta_so2=delta_so2,
        delta_ph=delta_ph,
        phi_so2_event=phi_so2,
        phi_ph_event=phi_ph,
        q_before=_finite(so2_response.q_before),
        q_after=_finite(so2_response.q_after),
        qbase_before=qbase_before,
        qbase_after=qbase_after,
        qbase_drift=qbase_drift,
        so2_before=_finite(so2_response.so2_before),
        so2_after=_finite(so2_response.so2_after),
        ph_before=_finite(ph_response.ph_before),
        ph_after=_finite(ph_response.ph_after),
        action_start_time=str(so2_response.target_change_time or ""),
        actual_flow_reached_time=str(so2_response.actual_flow_reached_time or ""),
        so2_response_start_time=str(so2_response.response_start_time or ""),
        so2_response_end_time=str(so2_response.response_end_time or ""),
        ph_response_start_time=str(ph_response.response_start_time or ""),
        ph_response_end_time=str(ph_response.response_end_time or ""),
        learning_permission=False,
        metadata={
            "manual_evidence_review_required": True,
            "automatic_online_adaptation_allowed": False,
            "safety_decision": safety.to_dict(),
            "so2_response_event_id": so2_response.response_event_id,
            "ph_response_event_id": ph_response.response_event_id,
            "so2_observation_seconds": so2_observation,
            "ph_observation_seconds": ph_observation,
        },
    )


def promote_local_step_evidence(
    plan: LocalStepTrialPlan,
    outcome: LocalStepTrialOutcome,
    *,
    human_evidence_approved: bool,
    reviewer_id: str,
    condition_label: str,
    base_condition_id: str,
    grid_id: str = "",
    policy_region_id: str = "",
) -> ActionResponseEvent:
    """Promote a successful trial after a second explicit evidence review."""
    if not bool(human_evidence_approved):
        raise ValueError("explicit human evidence approval is required")
    reviewer = str(reviewer_id or "").strip()
    if not reviewer:
        raise ValueError("reviewer_id is required")
    if outcome.trial_id != plan.trial_id:
        raise ValueError("trial outcome does not belong to plan")
    if not outcome.eligible_for_local_gain_promotion:
        raise ValueError("trial outcome is not eligible for LOCAL_GAIN promotion")
    if outcome.phi_so2_event is None or outcome.phi_so2_event >= 0.0:
        raise ValueError("reviewed SO2 local gain must remain negative")
    if outcome.phi_ph_event is None or outcome.phi_ph_event <= 0.0:
        raise ValueError("reviewed pH local gain must remain positive")

    return ActionResponseEvent(
        event_id="MFAC-LOCAL-GAIN-%s" % plan.trial_id,
        condition_snapshot_version=plan.condition_snapshot_version,
        condition_label=str(condition_label or ""),
        base_condition_id=str(base_condition_id or ""),
        grid_id=str(grid_id or ""),
        policy_region_id=str(policy_region_id or ""),
        mfac_context_id=plan.mfac_context_id,
        action_start_time=outcome.action_start_time,
        action_reached_time=outcome.actual_flow_reached_time,
        response_start_time=outcome.so2_response_start_time,
        response_end_time=max(
            outcome.so2_response_end_time,
            outcome.ph_response_end_time,
        ),
        action_source="MANUAL_LOCAL_STEP_IDENTIFICATION_REVIEWED",
        q_before=outcome.q_before,
        q_after=outcome.q_after,
        delta_q_actual=outcome.delta_q_actual,
        qbase_before=outcome.qbase_before,
        qbase_after=outcome.qbase_after,
        qbase_drift=outcome.qbase_drift,
        so2_before=outcome.so2_before,
        so2_after=outcome.so2_after,
        delta_so2=outcome.delta_so2,
        ph_before=outcome.ph_before,
        ph_after=outcome.ph_after,
        delta_ph=outcome.delta_ph,
        fast_overlap=False,
        equipment_changed=False,
        target_changed=False,
        condition_changed=False,
        data_quality_ok=True,
        learning_eligible=True,
        reject_reason="",
        phi_event=outcome.phi_so2_event,
        quality_score=None,
        metadata={
            "evidence_role": "LOCAL_GAIN",
            "identification_trial_id": plan.trial_id,
            "manual_execution": True,
            "manual_evidence_review_approved": True,
            "evidence_reviewer_id": reviewer,
            "phi_ph_event": outcome.phi_ph_event,
            "operator_action_imitation": False,
            "automatic_online_adaptation_allowed": False,
            "offline_bootstrap_evidence_allowed": True,
            "trial_outcome": outcome.to_dict(),
        },
    )


__all__ = [
    "LOCAL_STEP_TRIAL_PROTOCOL_VERSION",
    "LocalStepTrialProtocolConfig",
    "LocalStepTrialPlan",
    "approve_local_step_proposal",
    "LocalStepTrialSafetyDecision",
    "LocalStepTrialSafetyMonitor",
    "LocalStepTrialOutcome",
    "evaluate_local_step_trial",
    "promote_local_step_evidence",
]

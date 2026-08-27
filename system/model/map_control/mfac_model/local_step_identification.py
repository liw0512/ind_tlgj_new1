# -*- coding: utf-8 -*-
"""Manual-only proposal gate for controlled Scheme-2 LOCAL_GAIN identification.

Historical pulse data does not support the low-flow staircase shape needed by
MFAC. This module defines when a small positive actual-flow step may be proposed
for human review. It has no execution API, never writes DCS, and never grants
learning permission by itself.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import math
from typing import Any, Dict, Optional, Tuple

from .continuous_target import ContinuousTargetConfig
from .ph_arbitration import PHResidualArbitrationConfig


LOCAL_STEP_IDENTIFICATION_VERSION = (
    "SCHEME2_LOCAL_STEP_IDENTIFICATION_V1_MANUAL_ONLY"
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


@dataclass(frozen=True)
class LocalStepIdentificationConfig:
    """Reviewed test-design limits; no plant-specific defaults are provided."""

    step_up_m3_h: float
    ph_lower_margin_inside_operating: float
    ph_upper_margin_inside_operating: float
    outlet_so2_headroom_to_safe_max: float
    min_quiet_seconds: float
    min_candidate_interval_seconds: float
    max_abs_actual_minus_qbase: float
    max_abs_qbase_drift: float
    max_abs_inlet_so2_change: float
    max_outlet_so2_baseline_range: float

    def __post_init__(self) -> None:
        positive = (
            "step_up_m3_h",
            "min_quiet_seconds",
            "min_candidate_interval_seconds",
            "max_abs_actual_minus_qbase",
            "max_abs_qbase_drift",
            "max_abs_inlet_so2_change",
            "max_outlet_so2_baseline_range",
        )
        nonnegative = (
            "ph_lower_margin_inside_operating",
            "ph_upper_margin_inside_operating",
            "outlet_so2_headroom_to_safe_max",
        )
        for name in positive:
            value = _finite(getattr(self, name))
            if value is None or value <= 0.0:
                raise ValueError("%s must be finite and > 0" % name)
        for name in nonnegative:
            value = _finite(getattr(self, name))
            if value is None or value < 0.0:
                raise ValueError("%s must be finite and >= 0" % name)


@dataclass
class LocalStepIdentificationProposal:
    status: str
    proposal_id: str
    proposed_test_target_supply_flow: Optional[float]
    step_up_m3_h: Optional[float]
    actual_supply_flow: Optional[float]
    qbase_effective: Optional[float]
    ph_value: Optional[float]
    outlet_so2: Optional[float]
    reasons: Tuple[str, ...] = ()
    manual_review_required: bool = True
    manual_execution_required: bool = True
    automatic_execution_allowed: bool = False
    dcs_write_enabled: bool = False
    learning_permission: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = LOCAL_STEP_IDENTIFICATION_VERSION

    @property
    def eligible_for_manual_test_review(self) -> bool:
        return self.status == "REVIEW_CANDIDATE"

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["reasons"] = list(self.reasons)
        value["eligible_for_manual_test_review"] = (
            self.eligible_for_manual_test_review
        )
        return value


class LocalStepIdentificationGate:
    """Fail-closed proposal gate. This class cannot execute the proposed step."""

    dcs_write_enabled = False
    automatic_execution_allowed = False

    def __init__(
        self,
        config: LocalStepIdentificationConfig,
        target_bounds: ContinuousTargetConfig,
        ph_envelope: PHResidualArbitrationConfig,
        *,
        outlet_so2_safe_max: float,
    ) -> None:
        self.config = config
        self.target_bounds = target_bounds
        self.ph_envelope = ph_envelope
        safe_so2 = _finite(outlet_so2_safe_max)
        if safe_so2 is None:
            raise ValueError("outlet_so2_safe_max must be finite")
        self.outlet_so2_safe_max = safe_so2

        ph_min = (
            float(ph_envelope.operating_min)
            + float(config.ph_lower_margin_inside_operating)
        )
        ph_max = (
            float(ph_envelope.operating_max)
            - float(config.ph_upper_margin_inside_operating)
        )
        if ph_min >= ph_max:
            raise ValueError("identification pH margins consume the operating range")
        if (
            float(config.outlet_so2_headroom_to_safe_max)
            >= self.outlet_so2_safe_max
        ):
            raise ValueError("SO2 headroom must be smaller than safe maximum")
        self.identification_ph_min = ph_min
        self.identification_ph_max = ph_max
        self.identification_so2_max = (
            self.outlet_so2_safe_max
            - float(config.outlet_so2_headroom_to_safe_max)
        )

    def propose(
        self,
        *,
        timestamp: Any,
        request_enabled: bool,
        actual_supply_flow: Any,
        qbase_effective: Any,
        ph_value: Any,
        outlet_so2: Any,
        condition_snapshot_version: str,
        mfac_context_id: str,
        seconds_since_last_supply_action: Any,
        seconds_since_last_identification: Any,
        qbase_drift: Any,
        inlet_so2_change: Any,
        outlet_so2_baseline_range: Any,
        fast_active: bool = False,
        data_quality_ok: bool = True,
        equipment_changed: bool = False,
    ) -> LocalStepIdentificationProposal:
        reasons = []
        try:
            now = _timestamp(timestamp)
        except (TypeError, ValueError):
            now = None
            reasons.append("INVALID_TIMESTAMP")

        actual = _finite(actual_supply_flow)
        qbase = _finite(qbase_effective)
        ph = _finite(ph_value)
        so2 = _finite(outlet_so2)
        quiet = _finite(seconds_since_last_supply_action)
        since_identification = _finite(seconds_since_last_identification)
        qbase_change = _finite(qbase_drift)
        inlet_change = _finite(inlet_so2_change)
        so2_range = _finite(outlet_so2_baseline_range)

        if not bool(request_enabled):
            reasons.append("IDENTIFICATION_REQUEST_DISABLED")
        if not bool(data_quality_ok):
            reasons.append("DATA_QUALITY_INVALID")
        if bool(fast_active):
            reasons.append("FAST_ACTIVE")
        if bool(equipment_changed):
            reasons.append("EQUIPMENT_CHANGED")
        if not str(condition_snapshot_version or "").strip():
            reasons.append("CONDITION_SNAPSHOT_UNAVAILABLE")
        if not str(mfac_context_id or "").strip():
            reasons.append("MFAC_CONTEXT_UNAVAILABLE")
        if actual is None:
            reasons.append("ACTUAL_FLOW_INVALID")
        if qbase is None:
            reasons.append("QBASE_INVALID")
        if ph is None:
            reasons.append("PH_INVALID")
        elif not self.identification_ph_min <= ph <= self.identification_ph_max:
            reasons.append("PH_OUTSIDE_IDENTIFICATION_BAND")
        if so2 is None:
            reasons.append("OUTLET_SO2_INVALID")
        elif so2 > self.identification_so2_max:
            reasons.append("OUTLET_SO2_HEADROOM_INSUFFICIENT")
        if quiet is None or quiet < float(self.config.min_quiet_seconds):
            reasons.append("RECENT_SUPPLY_ACTION_NOT_SETTLED")
        if (
            since_identification is None
            or since_identification < float(self.config.min_candidate_interval_seconds)
        ):
            reasons.append("IDENTIFICATION_INTERVAL_NOT_ELAPSED")
        if qbase_change is None or abs(qbase_change) > float(self.config.max_abs_qbase_drift):
            reasons.append("QBASE_NOT_STABLE")
        if inlet_change is None or abs(inlet_change) > float(self.config.max_abs_inlet_so2_change):
            reasons.append("INLET_SO2_NOT_STABLE")
        if so2_range is None or so2_range > float(self.config.max_outlet_so2_baseline_range):
            reasons.append("OUTLET_SO2_BASELINE_NOT_STABLE")
        if (
            actual is not None
            and qbase is not None
            and abs(actual - qbase) > float(self.config.max_abs_actual_minus_qbase)
        ):
            reasons.append("ACTUAL_FLOW_NOT_NEAR_QBASE")

        proposed = None
        if actual is not None:
            proposed = actual + float(self.config.step_up_m3_h)
            if proposed > float(self.target_bounds.hard_max_supply_flow):
                reasons.append("PROPOSED_STEP_EXCEEDS_PLANT_MAX")
            if proposed < float(self.target_bounds.hard_min_supply_flow):
                reasons.append("PROPOSED_STEP_BELOW_PLANT_MIN")

        reasons = list(dict.fromkeys(reasons))
        accepted = not reasons
        proposal_id = ""
        if now is not None:
            proposal_id = "LOCAL-STEP-%s-%s" % (
                str(mfac_context_id or "UNKNOWN"),
                now.isoformat(),
            )
        return LocalStepIdentificationProposal(
            status="REVIEW_CANDIDATE" if accepted else "BLOCKED",
            proposal_id=proposal_id,
            proposed_test_target_supply_flow=proposed if accepted else None,
            step_up_m3_h=(float(self.config.step_up_m3_h) if accepted else None),
            actual_supply_flow=actual,
            qbase_effective=qbase,
            ph_value=ph,
            outlet_so2=so2,
            reasons=tuple(reasons),
            manual_review_required=True,
            manual_execution_required=True,
            automatic_execution_allowed=False,
            dcs_write_enabled=False,
            learning_permission=False,
            metadata={
                "condition_snapshot_version": str(condition_snapshot_version or ""),
                "mfac_context_id": str(mfac_context_id or ""),
                "identification_ph_band": [
                    self.identification_ph_min,
                    self.identification_ph_max,
                ],
                "identification_so2_max": self.identification_so2_max,
                "normal_algorithm_target_replaced": False,
                "actual_flow_used_as_normal_algorithm_target": False,
                "proposal_semantics": "MANUAL_CONTROLLED_LOCAL_STEP_ONLY",
            },
        )


__all__ = [
    "LOCAL_STEP_IDENTIFICATION_VERSION",
    "LocalStepIdentificationConfig",
    "LocalStepIdentificationProposal",
    "LocalStepIdentificationGate",
]

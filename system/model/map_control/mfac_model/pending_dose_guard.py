# -*- coding: utf-8 -*-
"""Shadow-only pending pH model for recent slurry-flow changes.

``phi_ph`` is a step sensitivity (delta pH / delta flow), not a volume gain.
Recent actual-flow changes are superposed through a calibrated step-response
rise.  The guard predicts only the *future response that has not yet been
realized*.

A critical semantic boundary is deliberate here: pulse half-decay/full recovery
is not a PendingDoseGuard control parameter.  A permanent positive step does
not decay merely because a timer expires; a later negative flow step produces
the recovery by superposition.  Once an individual delta-Q contribution has
reached its response peak, its future incremental effect is zero and it can be
removed from the pending set safely.  Historical recovery/quiet-time evidence
belongs to identification/session gating, not to this pending-response model.

Delivered volume is audit only and never dose debt.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import math
from typing import Any, Dict, List, Optional, Tuple

from .mfac_schema import MFACRuntimeState
from .ph_arbitration import PHResidualArbitrationConfig


PENDING_DOSE_GUARD_SEMANTICS_VERSION = (
    "SCHEME2_PENDING_DOSE_GUARD_V3_PENDING_ONLY_NO_RECOVERY_MEMORY"
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
class PendingDoseGuardConfig:
    flow_change_deadband: float
    response_onset_seconds: float
    response_peak_seconds: float
    max_sample_gap_seconds: float
    audit_volume_window_seconds: Optional[float] = None
    min_confidence: float = 0.0
    # Deprecated compatibility input.  It has no pending-control authority.
    # If audit_volume_window_seconds is omitted, this legacy value may be used
    # only as the recent-volume audit window.
    response_memory_seconds: Optional[float] = None

    def __post_init__(self) -> None:
        parsed: Dict[str, float] = {}
        for name in (
            "flow_change_deadband",
            "response_onset_seconds",
            "response_peak_seconds",
            "max_sample_gap_seconds",
            "min_confidence",
        ):
            value = _finite(getattr(self, name))
            if value is None:
                raise ValueError("%s must be finite" % name)
            parsed[name] = value
        if parsed["flow_change_deadband"] <= 0.0:
            raise ValueError("flow_change_deadband must be > 0")
        if parsed["response_onset_seconds"] < 0.0:
            raise ValueError("response_onset_seconds must be >= 0")
        if parsed["response_peak_seconds"] <= parsed["response_onset_seconds"]:
            raise ValueError("response_peak_seconds must be > response_onset_seconds")
        if parsed["max_sample_gap_seconds"] <= 0.0:
            raise ValueError("max_sample_gap_seconds must be > 0")
        if not 0.0 <= parsed["min_confidence"] <= 1.0:
            raise ValueError("min_confidence must be within [0, 1]")

        for name in ("audit_volume_window_seconds", "response_memory_seconds"):
            value = getattr(self, name)
            if value is None:
                continue
            number = _finite(value)
            if number is None or number <= 0.0:
                raise ValueError("%s must be finite and > 0 when provided" % name)

    @property
    def effective_audit_volume_window_seconds(self) -> float:
        value = self.audit_volume_window_seconds
        if value is None:
            value = self.response_memory_seconds
        if value is None:
            value = self.response_peak_seconds
        return max(float(self.response_peak_seconds), float(value))


@dataclass
class PendingDoseGuardDecision:
    status: str
    current_ph: Optional[float]
    current_actual_flow: Optional[float]
    phi_ph_live: Optional[float]
    confidence_ph_live: Optional[float]
    pending_equivalent_delta_q: float
    pending_delta_ph: Optional[float]
    predicted_ph_after_pending: Optional[float]
    recent_slurry_volume_m3: Optional[float]
    active_contribution_count: int
    pending_up_equivalent_delta_q: float = 0.0
    pending_down_equivalent_delta_q: float = 0.0
    predicted_ph_upper: Optional[float] = None
    predicted_ph_lower: Optional[float] = None
    predicted_peak_horizon_seconds: Optional[float] = None
    predicted_trough_horizon_seconds: Optional[float] = None
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = PENDING_DOSE_GUARD_SEMANTICS_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class PendingDoseGuard:
    def __init__(
        self,
        config: PendingDoseGuardConfig,
        ph_envelope: PHResidualArbitrationConfig,
    ) -> None:
        self.config = config
        self.ph_envelope = ph_envelope
        self._last_timestamp: Optional[datetime] = None
        self._last_flow: Optional[float] = None
        self._contributions: List[tuple[datetime, float]] = []
        self._samples: List[tuple[datetime, float]] = []
        self._last_reset_reason = ""

    def reset(self, reason: str = "RESET") -> None:
        self._last_timestamp = None
        self._last_flow = None
        self._contributions.clear()
        self._samples.clear()
        self._last_reset_reason = str(reason)

    def _response_fraction(self, age_seconds: float) -> float:
        onset = float(self.config.response_onset_seconds)
        peak = float(self.config.response_peak_seconds)
        if age_seconds <= onset:
            return 0.0
        if age_seconds >= peak:
            return 1.0
        return max(0.0, min(1.0, (age_seconds - onset) / (peak - onset)))

    def _prune(self, now: datetime) -> None:
        # Contributions older than peak have zero *future incremental* effect.
        # They must not be retained merely to emulate a vague recovery memory.
        peak = float(self.config.response_peak_seconds)
        self._contributions = [
            item
            for item in self._contributions
            if 0.0 <= (now - item[0]).total_seconds() < peak
        ]
        audit_window = float(self.config.effective_audit_volume_window_seconds)
        self._samples = [
            item
            for item in self._samples
            if 0.0 <= (now - item[0]).total_seconds() <= audit_window
        ]

    def _recent_volume(self) -> Optional[float]:
        if len(self._samples) < 2:
            return None
        volume = 0.0
        for (lt, lf), (rt, rf) in zip(self._samples[:-1], self._samples[1:]):
            hours = (rt - lt).total_seconds() / 3600.0
            if hours >= 0.0:
                volume += 0.5 * (max(0.0, lf) + max(0.0, rf)) * hours
        return float(volume)

    def _future_equivalent_range(
        self,
        now: datetime,
    ) -> Tuple[float, float, float, float, List[Dict[str, Any]]]:
        """Return future max/min equivalent step change relative to *now*.

        The response rise is piecewise linear, so future extrema can only occur
        at the current instant or when a still-pending contribution reaches
        onset/peak.  Contributions that already reached peak are pruned because
        their future incremental response is exactly zero.
        """
        onset = float(self.config.response_onset_seconds)
        peak = float(self.config.response_peak_seconds)
        ages: List[Tuple[datetime, float, float, float]] = []
        horizons = {0.0}
        details: List[Dict[str, Any]] = []
        for event_time, delta_q in self._contributions:
            age = max(0.0, (now - event_time).total_seconds())
            current_fraction = self._response_fraction(age)
            ages.append((event_time, float(delta_q), age, current_fraction))
            for breakpoint in (onset, peak):
                horizon = breakpoint - age
                if horizon > 0.0:
                    horizons.add(float(horizon))
            details.append({
                "timestamp": event_time.isoformat(),
                "age_seconds": age,
                "delta_q_actual": float(delta_q),
                "response_fraction_now": current_fraction,
            })

        values: List[Tuple[float, float]] = []
        for horizon in sorted(horizons):
            future_eq = 0.0
            for _event_time, delta_q, age, current_fraction in ages:
                future_fraction = self._response_fraction(age + horizon)
                future_eq += delta_q * (future_fraction - current_fraction)
            values.append((horizon, float(future_eq)))
        if not values:
            return 0.0, 0.0, 0.0, 0.0, details
        peak_horizon, max_eq = max(values, key=lambda item: item[1])
        trough_horizon, min_eq = min(values, key=lambda item: item[1])
        return max_eq, min_eq, peak_horizon, trough_horizon, details

    def update(
        self,
        *,
        timestamp: Any,
        actual_supply_flow_feedback: Any,
        ph_value: Any,
        state: Optional[MFACRuntimeState],
        data_quality_ok: bool = True,
    ) -> PendingDoseGuardDecision:
        try:
            now = _timestamp(timestamp)
        except (TypeError, ValueError):
            return self._decision("INVALID_INPUT", None, _finite(ph_value), state, "INVALID_TIMESTAMP")
        flow = _finite(actual_supply_flow_feedback)
        ph = _finite(ph_value)
        if not bool(data_quality_ok) or flow is None:
            return self._decision("INVALID_INPUT", flow, ph, state, "INVALID_ACTUAL_FLOW_OR_DATA_QUALITY")

        if self._last_timestamp is not None:
            gap = (now - self._last_timestamp).total_seconds()
            if gap < 0.0 or gap > float(self.config.max_sample_gap_seconds):
                self.reset("SAMPLE_GAP")
        if self._last_flow is None:
            self._last_flow = flow
        else:
            delta_q = flow - self._last_flow
            if abs(delta_q) >= float(self.config.flow_change_deadband):
                self._contributions.append((now, float(delta_q)))
                self._last_flow = flow

        self._last_timestamp = now
        self._samples.append((now, flow))
        self._prune(now)

        max_eq, min_eq, peak_horizon, trough_horizon, details = (
            self._future_equivalent_range(now)
        )
        selected_eq = max_eq if abs(max_eq) >= abs(min_eq) else min_eq
        phi_ph = _finite(state.phi_ph_live) if state is not None else None
        confidence = _finite(state.confidence_ph_live) if state is not None else None
        common_metadata = {
            "contributions": details,
            "last_reset_reason": self._last_reset_reason,
            "recent_volume_semantics": "AUDIT_ONLY_NOT_CONTROL_DEBT",
            "future_extrema_method": "PIECEWISE_LINEAR_STEP_BREAKPOINTS",
            "pending_control_horizon_seconds": float(self.config.response_peak_seconds),
            "recovery_memory_used_for_pending_control": False,
            "audit_volume_window_seconds": float(
                self.config.effective_audit_volume_window_seconds
            ),
            "legacy_response_memory_seconds": self.config.response_memory_seconds,
        }
        if (
            ph is None or phi_ph is None or phi_ph <= 0.0
            or confidence is None or confidence < float(self.config.min_confidence)
        ):
            return self._decision(
                "MODEL_UNAVAILABLE", flow, ph, state,
                "PH_MODEL_UNAVAILABLE_OR_LOW_CONFIDENCE",
                pending_equivalent_delta_q=selected_eq,
                pending_up_equivalent_delta_q=max_eq,
                pending_down_equivalent_delta_q=min_eq,
                predicted_peak_horizon_seconds=peak_horizon,
                predicted_trough_horizon_seconds=trough_horizon,
                recent_slurry_volume_m3=self._recent_volume(),
                metadata=common_metadata,
            )

        predicted_upper = ph + float(phi_ph) * max_eq
        predicted_lower = ph + float(phi_ph) * min_eq
        upper_guard = float(self.ph_envelope.safe_max) - float(self.ph_envelope.guard_band)
        lower_guard = float(self.ph_envelope.safe_min) + float(self.ph_envelope.guard_band)
        if predicted_upper >= upper_guard:
            status, reason = "LIMIT_POSITIVE", "PENDING_PH_REACHES_UPPER_GUARD"
            selected_eq = max_eq
            predicted = predicted_upper
        elif predicted_upper > float(self.ph_envelope.operating_max):
            status, reason = "WATCH_HIGH", "PENDING_PH_EXCEEDS_OPERATING_MAX"
            selected_eq = max_eq
            predicted = predicted_upper
        elif predicted_lower <= lower_guard:
            status, reason = "LIMIT_NEGATIVE", "PENDING_PH_REACHES_LOWER_GUARD"
            selected_eq = min_eq
            predicted = predicted_lower
        elif predicted_lower < float(self.ph_envelope.operating_min):
            status, reason = "WATCH_LOW", "PENDING_PH_BELOW_OPERATING_MIN"
            selected_eq = min_eq
            predicted = predicted_lower
        else:
            status, reason = "CLEAR", "PENDING_PH_WITHIN_OPERATING_ENVELOPE"
            if abs(max_eq) >= abs(min_eq):
                selected_eq, predicted = max_eq, predicted_upper
            else:
                selected_eq, predicted = min_eq, predicted_lower

        return self._decision(
            status, flow, ph, state, reason,
            pending_equivalent_delta_q=selected_eq,
            pending_delta_ph=float(phi_ph) * selected_eq,
            predicted_ph_after_pending=predicted,
            pending_up_equivalent_delta_q=max_eq,
            pending_down_equivalent_delta_q=min_eq,
            predicted_ph_upper=predicted_upper,
            predicted_ph_lower=predicted_lower,
            predicted_peak_horizon_seconds=peak_horizon,
            predicted_trough_horizon_seconds=trough_horizon,
            recent_slurry_volume_m3=self._recent_volume(),
            metadata=common_metadata,
        )

    def _decision(
        self,
        status: str,
        flow: Optional[float],
        ph: Optional[float],
        state: Optional[MFACRuntimeState],
        reason: str,
        *,
        pending_equivalent_delta_q: float = 0.0,
        pending_delta_ph: Optional[float] = None,
        predicted_ph_after_pending: Optional[float] = None,
        recent_slurry_volume_m3: Optional[float] = None,
        pending_up_equivalent_delta_q: float = 0.0,
        pending_down_equivalent_delta_q: float = 0.0,
        predicted_ph_upper: Optional[float] = None,
        predicted_ph_lower: Optional[float] = None,
        predicted_peak_horizon_seconds: Optional[float] = None,
        predicted_trough_horizon_seconds: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> PendingDoseGuardDecision:
        return PendingDoseGuardDecision(
            status=str(status),
            current_ph=ph,
            current_actual_flow=flow,
            phi_ph_live=_finite(state.phi_ph_live) if state is not None else None,
            confidence_ph_live=(
                _finite(state.confidence_ph_live) if state is not None else None
            ),
            pending_equivalent_delta_q=float(pending_equivalent_delta_q),
            pending_delta_ph=pending_delta_ph,
            predicted_ph_after_pending=predicted_ph_after_pending,
            recent_slurry_volume_m3=recent_slurry_volume_m3,
            active_contribution_count=len(self._contributions),
            pending_up_equivalent_delta_q=float(pending_up_equivalent_delta_q),
            pending_down_equivalent_delta_q=float(pending_down_equivalent_delta_q),
            predicted_ph_upper=predicted_ph_upper,
            predicted_ph_lower=predicted_ph_lower,
            predicted_peak_horizon_seconds=predicted_peak_horizon_seconds,
            predicted_trough_horizon_seconds=predicted_trough_horizon_seconds,
            reason=str(reason),
            metadata=dict(metadata or {}),
        )


__all__ = [
    "PENDING_DOSE_GUARD_SEMANTICS_VERSION",
    "PendingDoseGuardConfig",
    "PendingDoseGuardDecision",
    "PendingDoseGuard",
]

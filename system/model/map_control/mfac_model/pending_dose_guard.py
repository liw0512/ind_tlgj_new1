# -*- coding: utf-8 -*-
"""Shadow-only memory model for delayed pH response to recent slurry-flow changes.

``phi_ph`` is a step sensitivity (delta pH / delta flow), not a volume gain.
This module therefore tracks recent actual-flow increments and estimates the
part of their delayed pH response that has not appeared yet.  Delivered slurry
volume is exposed for audit only; it is never treated as a quantity that must be
"paid back" by later control moves.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import math
from typing import Any, Dict, List, Optional

from .mfac_schema import MFACRuntimeState
from .ph_arbitration import PHResidualArbitrationConfig


PENDING_DOSE_GUARD_SEMANTICS_VERSION = "SCHEME2_PENDING_DOSE_GUARD_V1_SHADOW"


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
    response_memory_seconds: float
    max_sample_gap_seconds: float
    min_confidence: float = 0.0

    def __post_init__(self) -> None:
        parsed: Dict[str, float] = {}
        for name in (
            "flow_change_deadband",
            "response_onset_seconds",
            "response_peak_seconds",
            "response_memory_seconds",
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
        if parsed["response_memory_seconds"] < parsed["response_peak_seconds"]:
            raise ValueError("response_memory_seconds must be >= response_peak_seconds")
        if parsed["max_sample_gap_seconds"] <= 0.0:
            raise ValueError("max_sample_gap_seconds must be > 0")
        if not 0.0 <= parsed["min_confidence"] <= 1.0:
            raise ValueError("min_confidence must be within [0, 1]")


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
        memory = float(self.config.response_memory_seconds)
        self._contributions = [
            item for item in self._contributions
            if 0.0 <= (now - item[0]).total_seconds() <= memory
        ]
        self._samples = [
            item for item in self._samples
            if 0.0 <= (now - item[0]).total_seconds() <= memory
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

        pending_eq = 0.0
        realized_eq = 0.0
        details: List[Dict[str, Any]] = []
        for event_time, delta_q in self._contributions:
            age = max(0.0, (now - event_time).total_seconds())
            fraction = self._response_fraction(age)
            pending_eq += float(delta_q) * (1.0 - fraction)
            realized_eq += float(delta_q) * fraction
            details.append({
                "timestamp": event_time.isoformat(),
                "age_seconds": age,
                "delta_q_actual": float(delta_q),
                "response_fraction": fraction,
            })

        phi_ph = _finite(state.phi_ph_live) if state is not None else None
        confidence = _finite(state.confidence_ph_live) if state is not None else None
        common_metadata = {
            "realized_equivalent_delta_q": realized_eq,
            "contributions": details,
            "last_reset_reason": self._last_reset_reason,
            "recent_volume_semantics": "AUDIT_ONLY_NOT_CONTROL_DEBT",
        }
        if (
            ph is None or phi_ph is None or phi_ph <= 0.0
            or confidence is None or confidence < float(self.config.min_confidence)
        ):
            return self._decision(
                "MODEL_UNAVAILABLE", flow, ph, state,
                "PH_MODEL_UNAVAILABLE_OR_LOW_CONFIDENCE",
                pending_equivalent_delta_q=pending_eq,
                recent_slurry_volume_m3=self._recent_volume(),
                metadata=common_metadata,
            )

        pending_delta_ph = float(phi_ph) * pending_eq
        predicted = ph + pending_delta_ph
        upper_guard = float(self.ph_envelope.safe_max) - float(self.ph_envelope.guard_band)
        lower_guard = float(self.ph_envelope.safe_min) + float(self.ph_envelope.guard_band)
        if predicted >= upper_guard:
            status, reason = "LIMIT_POSITIVE", "PENDING_PH_REACHES_UPPER_GUARD"
        elif predicted > float(self.ph_envelope.operating_max):
            status, reason = "WATCH_HIGH", "PENDING_PH_EXCEEDS_OPERATING_MAX"
        elif predicted <= lower_guard:
            status, reason = "LIMIT_NEGATIVE", "PENDING_PH_REACHES_LOWER_GUARD"
        elif predicted < float(self.ph_envelope.operating_min):
            status, reason = "WATCH_LOW", "PENDING_PH_BELOW_OPERATING_MIN"
        else:
            status, reason = "CLEAR", "PENDING_PH_WITHIN_OPERATING_ENVELOPE"
        return self._decision(
            status, flow, ph, state, reason,
            pending_equivalent_delta_q=pending_eq,
            pending_delta_ph=pending_delta_ph,
            predicted_ph_after_pending=predicted,
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
            reason=str(reason),
            metadata=dict(metadata or {}),
        )


__all__ = [
    "PENDING_DOSE_GUARD_SEMANTICS_VERSION",
    "PendingDoseGuardConfig",
    "PendingDoseGuardDecision",
    "PendingDoseGuard",
]

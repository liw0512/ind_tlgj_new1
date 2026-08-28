# -*- coding: utf-8 -*-
"""Event-driven positive-direction pH sensitivity and confidence adaptation.

The pH channel learns ``phi_ph = delta pH / delta Q_actual`` independently from
SO2.  It never creates an additive slurry-flow command; its learned state is
consumed only by pH residual arbitration.

Clean physical-direction events update both ``phi_ph`` and pH confidence.  A
clean event with the wrong physical direction cannot drive ``phi_ph`` negative;
it only lowers pH confidence.  Confounded/data-quality-invalid events change
neither phi nor confidence.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any, Dict, Optional

from .mfac_schema import MFACRuntimeState
from .online_confidence import (
    OnlineConfidenceConfig,
    update_online_confidence,
)
from .ph_response import PHResponseEvent


PH_ONLINE_ADAPTATION_SEMANTICS_VERSION = "SCHEME2_PH_ONLINE_ADAPTATION_V2_CONFIDENCE"


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


@dataclass(frozen=True)
class PHOnlineAdaptationConfig:
    eta: float
    mu: float
    phi_lower_bound: float
    phi_upper_bound: float
    max_single_update_abs: float
    min_abs_delta_q: float = 1e-12
    max_abs_qbase_drift: Optional[float] = None
    confidence_reference_event_count: float = 5.0

    def __post_init__(self) -> None:
        eta = _finite(self.eta)
        mu = _finite(self.mu)
        lower = _finite(self.phi_lower_bound)
        upper = _finite(self.phi_upper_bound)
        max_update = _finite(self.max_single_update_abs)
        min_delta_q = _finite(self.min_abs_delta_q)
        confidence_reference = _finite(self.confidence_reference_event_count)
        if eta is None or eta <= 0.0:
            raise ValueError("eta must be finite and > 0")
        if mu is None or mu <= 0.0:
            raise ValueError("mu must be finite and > 0")
        if lower is None or upper is None or lower >= upper:
            raise ValueError("phi bounds must be finite with lower < upper")
        if lower <= 0.0:
            raise ValueError("phi_lower_bound must remain positive")
        if max_update is None or max_update <= 0.0:
            raise ValueError("max_single_update_abs must be finite and > 0")
        if min_delta_q is None or min_delta_q < 0.0:
            raise ValueError("min_abs_delta_q must be finite and >= 0")
        if self.max_abs_qbase_drift is not None:
            drift = _finite(self.max_abs_qbase_drift)
            if drift is None or drift < 0.0:
                raise ValueError("max_abs_qbase_drift must be finite and >= 0")
        if confidence_reference is None or confidence_reference <= 0.0:
            raise ValueError("confidence_reference_event_count must be finite and > 0")


@dataclass
class PHOnlineAdaptationResult:
    updated: bool
    reason: str
    old_phi: Optional[float]
    new_phi: Optional[float]
    applied_update: float = 0.0
    event_id: str = ""
    runtime_state: Optional[MFACRuntimeState] = None
    old_confidence: Optional[float] = None
    new_confidence: Optional[float] = None
    phi_updated: bool = False
    confidence_updated: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = PH_ONLINE_ADAPTATION_SEMANTICS_VERSION

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["runtime_state"] = (
            self.runtime_state.to_dict() if self.runtime_state is not None else None
        )
        return value


class PHOnlineAdapter:
    """Update only the pH portion of one matching MFAC runtime state."""

    def __init__(self, config: PHOnlineAdaptationConfig) -> None:
        self.config = config
        self.confidence_config = OnlineConfidenceConfig(
            reference_event_count=float(config.confidence_reference_event_count)
        )

    def update(
        self,
        state: MFACRuntimeState,
        event: PHResponseEvent,
    ) -> PHOnlineAdaptationResult:
        old_phi = _finite(state.phi_ph_live)
        old_confidence = _finite(state.confidence_ph_live)
        if old_phi is None:
            return self._reject("NO_PH_LIVE_PHI", state, event, None)
        if old_confidence is None or not 0.0 <= old_confidence <= 1.0:
            return self._reject("INVALID_PH_LIVE_CONFIDENCE", state, event, old_phi)
        if state.condition_snapshot_version != event.condition_snapshot_version:
            return self._reject("SNAPSHOT_VERSION_MISMATCH", state, event, old_phi)
        if state.mfac_context_id != event.mfac_context_id:
            return self._reject("MFAC_CONTEXT_MISMATCH", state, event, old_phi)
        if state.ph_last_event_id and state.ph_last_event_id == event.response_event_id:
            return self._reject("DUPLICATE_EVENT", state, event, old_phi)
        if event.status != "COMPLETED":
            return self._reject("PH_RESPONSE_NOT_COMPLETE", state, event, old_phi)
        if event.fast_overlap or event.condition_changed or event.target_changed:
            return self._reject("PH_RESPONSE_CONFOUNDED", state, event, old_phi)
        if not event.data_quality_ok:
            return self._reject("PH_DATA_QUALITY_INVALID", state, event, old_phi)

        delta_q = _finite(event.delta_q_actual)
        delta_ph = _finite(event.delta_ph)
        if delta_q is None or abs(delta_q) <= float(self.config.min_abs_delta_q):
            return self._reject("INVALID_DELTA_Q", state, event, old_phi)
        if delta_ph is None:
            return self._reject("INVALID_DELTA_PH", state, event, old_phi)
        if self.config.max_abs_qbase_drift is not None:
            drift = _finite(event.qbase_drift)
            if drift is None:
                return self._reject("QBASE_DRIFT_UNAVAILABLE", state, event, old_phi)
            if abs(drift) > float(self.config.max_abs_qbase_drift):
                return self._reject("QBASE_DRIFT_TOO_LARGE", state, event, old_phi)

        lower = float(self.config.phi_lower_bound)
        upper = float(self.config.phi_upper_bound)
        if old_phi < lower or old_phi > upper or old_phi <= 0.0:
            return self._reject("PH_LIVE_PHI_OUTSIDE_PHYSICAL_BOUNDS", state, event, old_phi)

        phi_event = delta_ph / delta_q
        direction_ok = math.isfinite(phi_event) and phi_event > 0.0
        predicted_response = old_phi * delta_q
        confidence_update, metadata = update_online_confidence(
            current_confidence=old_confidence,
            metadata=state.metadata,
            metadata_key="online_confidence_ph",
            observed_response=delta_ph,
            predicted_response=predicted_response,
            direction_ok=direction_ok,
            quality_weight=1.0,
            config=self.confidence_config,
        )
        event_time = (
            event.response_end_time
            or event.response_start_time
            or event.actual_flow_reached_time
        )

        if not direction_ok:
            metadata["last_ph_online_adaptation"] = {
                "event_id": event.response_event_id,
                "delta_q_actual": delta_q,
                "delta_ph": delta_ph,
                "phi_ph_event": phi_event,
                "prediction_residual": delta_ph - predicted_response,
                "applied_update": 0.0,
                "phi_update_rejected": True,
                "confidence_update": confidence_update.to_dict(),
            }
            new_state = self._state(
                state,
                phi=old_phi,
                confidence=confidence_update.new_confidence,
                metadata=metadata,
                valid_event_count=int(state.ph_valid_event_count),
                last_event_id=event.response_event_id,
                last_update_time=event_time,
            )
            return PHOnlineAdaptationResult(
                updated=True,
                reason="CONFIDENCE_DOWNGRADED_PHYSICAL_CONFLICT",
                old_phi=old_phi,
                new_phi=old_phi,
                applied_update=0.0,
                event_id=event.response_event_id,
                runtime_state=new_state,
                old_confidence=old_confidence,
                new_confidence=confidence_update.new_confidence,
                phi_updated=False,
                confidence_updated=True,
                metadata={
                    "phi_ph_event": phi_event,
                    "physical_direction_ok": False,
                    "confidence_update": confidence_update.to_dict(),
                },
            )

        prediction_residual = delta_ph - predicted_response
        raw_update = (
            float(self.config.eta)
            * delta_q
            * prediction_residual
            / (float(self.config.mu) + delta_q * delta_q)
        )
        limit = abs(float(self.config.max_single_update_abs))
        bounded_update = max(-limit, min(limit, raw_update))
        candidate = max(lower, min(upper, old_phi + bounded_update))
        if not math.isfinite(candidate) or candidate <= 0.0:
            return self._reject("PH_PHYSICAL_DIRECTION_VIOLATION", state, event, old_phi)

        metadata["last_ph_online_adaptation"] = {
            "event_id": event.response_event_id,
            "delta_q_actual": delta_q,
            "delta_ph": delta_ph,
            "phi_ph_event": phi_event,
            "prediction_residual": prediction_residual,
            "raw_update": raw_update,
            "applied_update": candidate - old_phi,
            "confidence_update": confidence_update.to_dict(),
        }
        new_state = self._state(
            state,
            phi=candidate,
            confidence=confidence_update.new_confidence,
            metadata=metadata,
            valid_event_count=int(state.ph_valid_event_count) + 1,
            last_event_id=event.response_event_id,
            last_update_time=event_time,
        )
        return PHOnlineAdaptationResult(
            updated=True,
            reason="UPDATED",
            old_phi=old_phi,
            new_phi=candidate,
            applied_update=candidate - old_phi,
            event_id=event.response_event_id,
            runtime_state=new_state,
            old_confidence=old_confidence,
            new_confidence=confidence_update.new_confidence,
            phi_updated=True,
            confidence_updated=True,
            metadata={
                "phi_ph_event": phi_event,
                "prediction_residual": prediction_residual,
                "raw_update": raw_update,
                "confidence_update": confidence_update.to_dict(),
            },
        )

    @staticmethod
    def _state(
        state: MFACRuntimeState,
        *,
        phi: float,
        confidence: float,
        metadata: Dict[str, Any],
        valid_event_count: int,
        last_event_id: str,
        last_update_time: str,
    ) -> MFACRuntimeState:
        return MFACRuntimeState(
            condition_snapshot_version=state.condition_snapshot_version,
            mfac_context_id=state.mfac_context_id,
            phi_live=state.phi_live,
            confidence_live=state.confidence_live,
            bias_live=state.bias_live,
            valid_event_count=state.valid_event_count,
            last_event_id=state.last_event_id,
            last_update_time=state.last_update_time,
            phi_ph_live=float(phi),
            confidence_ph_live=float(confidence),
            ph_valid_event_count=int(valid_event_count),
            ph_last_event_id=str(last_event_id),
            ph_last_update_time=str(last_update_time or ""),
            metadata=metadata,
            semantics_version=state.semantics_version,
        )

    @staticmethod
    def _reject(
        reason: str,
        state: MFACRuntimeState,
        event: PHResponseEvent,
        old_phi: Optional[float],
    ) -> PHOnlineAdaptationResult:
        return PHOnlineAdaptationResult(
            updated=False,
            reason=reason,
            old_phi=old_phi,
            new_phi=old_phi,
            applied_update=0.0,
            event_id=event.response_event_id,
            runtime_state=state,
            old_confidence=_finite(state.confidence_ph_live),
            new_confidence=_finite(state.confidence_ph_live),
            phi_updated=False,
            confidence_updated=False,
        )

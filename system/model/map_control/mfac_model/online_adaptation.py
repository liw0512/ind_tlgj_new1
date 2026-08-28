# -*- coding: utf-8 -*-
"""Event-driven online SO2 MFAC phi and confidence adaptation.

Only complete ``learning_eligible`` ActionResponseEvents may affect the SO2
channel.  Clean events with the expected physical direction update both phi and
confidence.  A clean event with the wrong physical direction never pushes phi
across zero; it only lowers the channel confidence.  Confounded/ineligible
events change neither phi nor confidence.

Dual-response pH state is preserved byte-for-byte when SO2 state is updated so
the two channels cannot overwrite each other.
"""

from dataclasses import asdict, dataclass, field
import math
from typing import Any, Dict, Optional

from .mfac_schema import ActionResponseEvent, MFACRuntimeState
from .online_confidence import (
    OnlineConfidenceConfig,
    update_online_confidence,
)


ONLINE_ADAPTATION_SEMANTICS_VERSION = "SCHEME2_ONLINE_ADAPTATION_V2_CONFIDENCE"


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


@dataclass(frozen=True)
class MFACOnlineAdaptationConfig:
    eta: float
    mu: float
    phi_lower_bound: float
    phi_upper_bound: float
    max_single_update_abs: float
    min_abs_delta_q: float = 1e-12
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
        if upper >= 0.0:
            raise ValueError("phi_upper_bound must remain negative")
        if max_update is None or max_update <= 0.0:
            raise ValueError("max_single_update_abs must be finite and > 0")
        if min_delta_q is None or min_delta_q < 0.0:
            raise ValueError("min_abs_delta_q must be finite and >= 0")
        if confidence_reference is None or confidence_reference <= 0.0:
            raise ValueError("confidence_reference_event_count must be finite and > 0")


@dataclass
class MFACOnlineAdaptationResult:
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
    semantics_version: str = ONLINE_ADAPTATION_SEMANTICS_VERSION

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["runtime_state"] = (
            self.runtime_state.to_dict() if self.runtime_state is not None else None
        )
        return value


class MFACOnlineAdapter:
    """Apply one canonical SO2 event to one matching live MFAC context state."""

    def __init__(self, config: MFACOnlineAdaptationConfig) -> None:
        self.config = config
        self.confidence_config = OnlineConfidenceConfig(
            reference_event_count=float(config.confidence_reference_event_count)
        )

    def update(
        self,
        state: MFACRuntimeState,
        event: ActionResponseEvent,
    ) -> MFACOnlineAdaptationResult:
        old_phi = _finite(state.phi_live)
        old_confidence = _finite(state.confidence_live)
        if old_phi is None:
            return self._reject("INVALID_LIVE_PHI", state, event, None)
        if old_confidence is None or not 0.0 <= old_confidence <= 1.0:
            return self._reject("INVALID_LIVE_CONFIDENCE", state, event, old_phi)
        if state.condition_snapshot_version != event.condition_snapshot_version:
            return self._reject("SNAPSHOT_VERSION_MISMATCH", state, event, old_phi)
        if state.mfac_context_id != event.mfac_context_id:
            return self._reject("MFAC_CONTEXT_MISMATCH", state, event, old_phi)
        if state.last_event_id and state.last_event_id == event.event_id:
            return self._reject("DUPLICATE_EVENT", state, event, old_phi)
        if not event.learning_eligible:
            return self._reject("EVENT_NOT_LEARNING_ELIGIBLE", state, event, old_phi)

        delta_q = _finite(event.delta_q_actual)
        delta_so2 = _finite(event.delta_so2)
        if delta_q is None or abs(delta_q) <= float(self.config.min_abs_delta_q):
            return self._reject("INVALID_DELTA_Q", state, event, old_phi)
        if delta_so2 is None:
            return self._reject("INVALID_DELTA_SO2", state, event, old_phi)

        lower = float(self.config.phi_lower_bound)
        upper = float(self.config.phi_upper_bound)
        if old_phi < lower or old_phi > upper or old_phi >= 0.0:
            return self._reject("LIVE_PHI_OUTSIDE_PHYSICAL_BOUNDS", state, event, old_phi)

        quality = _finite(event.quality_score)
        quality_weight = 1.0 if quality is None else min(1.0, max(0.0, quality))
        if quality_weight <= 0.0:
            return self._reject("ZERO_QUALITY_WEIGHT", state, event, old_phi)

        predicted_response = old_phi * delta_q
        phi_event = delta_so2 / delta_q
        direction_ok = math.isfinite(phi_event) and phi_event < 0.0
        confidence_update, metadata = update_online_confidence(
            current_confidence=old_confidence,
            metadata=state.metadata,
            metadata_key="online_confidence_so2",
            observed_response=delta_so2,
            predicted_response=predicted_response,
            direction_ok=direction_ok,
            quality_weight=quality_weight,
            config=self.confidence_config,
        )

        event_time = (
            event.response_end_time
            or event.response_start_time
            or event.action_reached_time
            or event.action_start_time
        )
        if not direction_ok:
            metadata["last_online_adaptation"] = {
                "event_id": event.event_id,
                "delta_q_actual": delta_q,
                "delta_so2": delta_so2,
                "phi_event": phi_event,
                "prediction_residual": delta_so2 - predicted_response,
                "applied_update": 0.0,
                "quality_weight": quality_weight,
                "channel": "SO2",
                "phi_update_rejected": True,
                "confidence_update": confidence_update.to_dict(),
            }
            new_state = self._state(
                state,
                phi=old_phi,
                confidence=confidence_update.new_confidence,
                metadata=metadata,
                valid_event_count=int(state.valid_event_count),
                last_event_id=event.event_id,
                last_update_time=event_time,
            )
            return MFACOnlineAdaptationResult(
                updated=True,
                reason="CONFIDENCE_DOWNGRADED_PHYSICAL_CONFLICT",
                old_phi=old_phi,
                new_phi=old_phi,
                applied_update=0.0,
                event_id=event.event_id,
                runtime_state=new_state,
                old_confidence=old_confidence,
                new_confidence=confidence_update.new_confidence,
                phi_updated=False,
                confidence_updated=True,
                metadata={
                    "phi_event": phi_event,
                    "physical_direction_ok": False,
                    "confidence_update": confidence_update.to_dict(),
                },
            )

        prediction_residual = delta_so2 - predicted_response
        raw_update = (
            float(self.config.eta)
            * quality_weight
            * delta_q
            * prediction_residual
            / (float(self.config.mu) + delta_q * delta_q)
        )
        limit = abs(float(self.config.max_single_update_abs))
        bounded_update = max(-limit, min(limit, raw_update))
        candidate = max(lower, min(upper, old_phi + bounded_update))

        if not math.isfinite(candidate) or candidate >= 0.0:
            return self._reject("PHYSICAL_DIRECTION_VIOLATION", state, event, old_phi)

        metadata["last_online_adaptation"] = {
            "event_id": event.event_id,
            "delta_q_actual": delta_q,
            "delta_so2": delta_so2,
            "phi_event": phi_event,
            "prediction_residual": prediction_residual,
            "raw_update": raw_update,
            "applied_update": candidate - old_phi,
            "quality_weight": quality_weight,
            "channel": "SO2",
            "confidence_update": confidence_update.to_dict(),
        }
        new_state = self._state(
            state,
            phi=candidate,
            confidence=confidence_update.new_confidence,
            metadata=metadata,
            valid_event_count=int(state.valid_event_count) + 1,
            last_event_id=event.event_id,
            last_update_time=event_time,
        )
        return MFACOnlineAdaptationResult(
            updated=True,
            reason="UPDATED",
            old_phi=old_phi,
            new_phi=candidate,
            applied_update=candidate - old_phi,
            event_id=event.event_id,
            runtime_state=new_state,
            old_confidence=old_confidence,
            new_confidence=confidence_update.new_confidence,
            phi_updated=True,
            confidence_updated=True,
            metadata={
                "phi_event": phi_event,
                "prediction_residual": prediction_residual,
                "raw_update": raw_update,
                "quality_weight": quality_weight,
                "confidence_update": confidence_update.to_dict(),
                "channel": "SO2",
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
            phi_live=float(phi),
            confidence_live=float(confidence),
            bias_live=state.bias_live,
            valid_event_count=int(valid_event_count),
            last_event_id=str(last_event_id),
            last_update_time=str(last_update_time or ""),
            phi_ph_live=state.phi_ph_live,
            confidence_ph_live=state.confidence_ph_live,
            ph_valid_event_count=state.ph_valid_event_count,
            ph_last_event_id=state.ph_last_event_id,
            ph_last_update_time=state.ph_last_update_time,
            metadata=metadata,
            semantics_version=state.semantics_version,
        )

    @staticmethod
    def _reject(
        reason: str,
        state: MFACRuntimeState,
        event: ActionResponseEvent,
        old_phi: Optional[float],
    ) -> MFACOnlineAdaptationResult:
        return MFACOnlineAdaptationResult(
            updated=False,
            reason=reason,
            old_phi=old_phi,
            new_phi=old_phi,
            applied_update=0.0,
            event_id=event.event_id,
            runtime_state=state,
            old_confidence=_finite(state.confidence_live),
            new_confidence=_finite(state.confidence_live),
            phi_updated=False,
            confidence_updated=False,
        )

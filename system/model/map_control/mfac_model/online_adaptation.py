# -*- coding: utf-8 -*-
"""Event-driven online MFAC phi adaptation.

Only complete ``learning_eligible`` ActionResponseEvents may update live phi.
The adapter is deliberately not wired into the production main loop here; the
runtime activation gate remains a separate responsibility.
"""

from dataclasses import asdict, dataclass, field
import math
from typing import Any, Dict, Optional

from .mfac_schema import ActionResponseEvent, MFACRuntimeState


ONLINE_ADAPTATION_SEMANTICS_VERSION = "SCHEME2_ONLINE_ADAPTATION_V1"


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

    def __post_init__(self) -> None:
        eta = _finite(self.eta)
        mu = _finite(self.mu)
        lower = _finite(self.phi_lower_bound)
        upper = _finite(self.phi_upper_bound)
        max_update = _finite(self.max_single_update_abs)
        min_delta_q = _finite(self.min_abs_delta_q)
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


@dataclass
class MFACOnlineAdaptationResult:
    updated: bool
    reason: str
    old_phi: Optional[float]
    new_phi: Optional[float]
    applied_update: float = 0.0
    event_id: str = ""
    runtime_state: Optional[MFACRuntimeState] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = ONLINE_ADAPTATION_SEMANTICS_VERSION

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["runtime_state"] = (
            self.runtime_state.to_dict() if self.runtime_state is not None else None
        )
        return value


class MFACOnlineAdapter:
    """Apply one canonical event to one matching live MFAC context state."""

    def __init__(self, config: MFACOnlineAdaptationConfig) -> None:
        self.config = config

    def update(
        self,
        state: MFACRuntimeState,
        event: ActionResponseEvent,
    ) -> MFACOnlineAdaptationResult:
        old_phi = _finite(state.phi_live)
        if old_phi is None:
            return self._reject("INVALID_LIVE_PHI", state, event, None)
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
        prediction_residual = delta_so2 - old_phi * delta_q
        raw_update = (
            float(self.config.eta)
            * quality_weight
            * delta_q
            * prediction_residual
            / (float(self.config.mu) + delta_q * delta_q)
        )
        limit = abs(float(self.config.max_single_update_abs))
        bounded_update = max(-limit, min(limit, raw_update))
        candidate = old_phi + bounded_update
        candidate = max(lower, min(upper, candidate))

        if not math.isfinite(candidate) or candidate >= 0.0:
            return self._reject("PHYSICAL_DIRECTION_VIOLATION", state, event, old_phi)

        metadata = dict(state.metadata or {})
        metadata["last_online_adaptation"] = {
            "event_id": event.event_id,
            "delta_q_actual": delta_q,
            "delta_so2": delta_so2,
            "prediction_residual": prediction_residual,
            "raw_update": raw_update,
            "applied_update": candidate - old_phi,
            "quality_weight": quality_weight,
        }
        new_state = MFACRuntimeState(
            condition_snapshot_version=state.condition_snapshot_version,
            mfac_context_id=state.mfac_context_id,
            phi_live=candidate,
            confidence_live=state.confidence_live,
            bias_live=state.bias_live,
            valid_event_count=int(state.valid_event_count) + 1,
            last_event_id=event.event_id,
            last_update_time=(
                event.response_end_time
                or event.response_start_time
                or event.action_reached_time
                or event.action_start_time
            ),
            metadata=metadata,
            semantics_version=state.semantics_version,
        )
        return MFACOnlineAdaptationResult(
            updated=True,
            reason="UPDATED",
            old_phi=old_phi,
            new_phi=candidate,
            applied_update=candidate - old_phi,
            event_id=event.event_id,
            runtime_state=new_state,
            metadata={
                "prediction_residual": prediction_residual,
                "raw_update": raw_update,
                "quality_weight": quality_weight,
            },
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
        )

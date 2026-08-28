# -*- coding: utf-8 -*-
"""pH arbitration for the SO2-led Scheme 2 residual controller.

SO2 remains the only control-producing MFAC channel.  pH never creates an
additive slurry-flow command; it can only pass, scale, or block the *increment*
from the currently held SO2 residual to the newly desired SO2 residual.

The important semantic boundary is::

    requested_delta_residual = desired_residual - held_residual
    final_residual = held_residual + scale * requested_delta_residual

This prevents a SCALE decision from accidentally shrinking or reversing an
already-held residual.  Optional pending pH extrema may be supplied by the
PendingDoseGuard so the new increment is judged from the future pH base that is
already in flight, rather than current pH alone.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any, Dict, Optional

from .mfac_schema import MFACRuntimeState
from .residual_control import MFACResidualDecision


PH_ARBITRATION_SEMANTICS_VERSION = (
    "SCHEME2_PH_ARBITRATION_V2_INCREMENTAL_PENDING_AWARE"
)


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


@dataclass(frozen=True)
class PHResidualArbitrationConfig:
    operating_min: float
    operating_max: float
    safe_min: float
    safe_max: float
    guard_band: float
    min_confidence: float = 0.0

    def __post_init__(self) -> None:
        values = {
            "operating_min": self.operating_min,
            "operating_max": self.operating_max,
            "safe_min": self.safe_min,
            "safe_max": self.safe_max,
            "guard_band": self.guard_band,
            "min_confidence": self.min_confidence,
        }
        parsed: Dict[str, float] = {}
        for name, value in values.items():
            number = _finite(value)
            if number is None:
                raise ValueError("%s must be finite" % name)
            parsed[name] = number
        if parsed["safe_min"] >= parsed["safe_max"]:
            raise ValueError("safe_min must be < safe_max")
        if parsed["operating_min"] >= parsed["operating_max"]:
            raise ValueError("operating_min must be < operating_max")
        if not (
            parsed["safe_min"] <= parsed["operating_min"]
            < parsed["operating_max"] <= parsed["safe_max"]
        ):
            raise ValueError("operating range must lie within safe range")
        if parsed["guard_band"] < 0.0:
            raise ValueError("guard_band must be >= 0")
        if not 0.0 <= parsed["min_confidence"] <= 1.0:
            raise ValueError("min_confidence must be within [0, 1]")


@dataclass
class PHResidualArbitrationDecision:
    status: str
    source_residual: float
    residual_scale: float
    final_residual: float
    held_residual: float = 0.0
    requested_delta_residual: float = 0.0
    allowed_delta_residual: float = 0.0
    ph_value: Optional[float] = None
    phi_ph_live: Optional[float] = None
    confidence_ph_live: Optional[float] = None
    predicted_ph: Optional[float] = None
    pending_base_ph: Optional[float] = None
    pending_predicted_ph_upper: Optional[float] = None
    pending_predicted_ph_lower: Optional[float] = None
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = PH_ARBITRATION_SEMANTICS_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class PHResidualArbiter:
    """Constrain the incremental change of an SO2 residual.

    ``source_residual`` is the absolute SO2 desired residual.  ``held_residual``
    is the residual already in force.  pH arbitrates only their difference.
    """

    def __init__(self, config: PHResidualArbitrationConfig) -> None:
        self.config = config

    def arbitrate(
        self,
        *,
        ph_value: Any,
        state: Optional[MFACRuntimeState],
        so2_residual: MFACResidualDecision,
        arbitration_enabled: bool,
        held_residual: Any = 0.0,
        pending_predicted_ph_upper: Any = None,
        pending_predicted_ph_lower: Any = None,
    ) -> PHResidualArbitrationDecision:
        source = _finite(so2_residual.candidate_residual)
        source_value = 0.0 if source is None else source
        held = _finite(held_residual)
        held_value = 0.0 if held is None else held
        requested_delta = source_value - held_value
        pending_upper = _finite(pending_predicted_ph_upper)
        pending_lower = _finite(pending_predicted_ph_lower)

        if not bool(arbitration_enabled):
            return self._decision(
                "ARBITRATION_DISABLED",
                source_value,
                held_value,
                requested_delta,
                1.0,
                ph_value=_finite(ph_value),
                state=state,
                reason="PH_ARBITRATION_DISABLED",
                pending_base_ph=_finite(ph_value),
                pending_upper=pending_upper,
                pending_lower=pending_lower,
            )
        if so2_residual.status != "CALCULATED" or source is None:
            return self._decision(
                "SOURCE_NOT_CALCULATED",
                source_value,
                held_value,
                0.0,
                0.0,
                ph_value=_finite(ph_value),
                state=state,
                reason=so2_residual.status,
                pending_base_ph=_finite(ph_value),
                pending_upper=pending_upper,
                pending_lower=pending_lower,
            )

        ph = _finite(ph_value)
        if ph is None:
            return self._decision(
                "BLOCK",
                source_value,
                held_value,
                requested_delta,
                0.0,
                ph_value=None,
                state=state,
                reason="INVALID_PH_INPUT",
                pending_base_ph=None,
                pending_upper=pending_upper,
                pending_lower=pending_lower,
            )
        if ph < float(self.config.safe_min) or ph > float(self.config.safe_max):
            return self._decision(
                "BLOCK",
                source_value,
                held_value,
                requested_delta,
                0.0,
                ph_value=ph,
                state=state,
                reason="PH_OUTSIDE_SAFE_RANGE",
                pending_base_ph=ph,
                pending_upper=pending_upper,
                pending_lower=pending_lower,
            )

        # Direction protection is based on the new increment, not the absolute
        # residual.  Reducing a positive held residual is therefore allowed at
        # high pH, while increasing it is blocked.
        if requested_delta > 0.0 and ph >= float(self.config.operating_max):
            return self._decision(
                "BLOCK",
                source_value,
                held_value,
                requested_delta,
                0.0,
                ph_value=ph,
                state=state,
                reason="POSITIVE_INCREMENT_WORSENS_HIGH_PH",
                pending_base_ph=ph,
                pending_upper=pending_upper,
                pending_lower=pending_lower,
            )
        if requested_delta < 0.0 and ph <= float(self.config.operating_min):
            return self._decision(
                "BLOCK",
                source_value,
                held_value,
                requested_delta,
                0.0,
                ph_value=ph,
                state=state,
                reason="NEGATIVE_INCREMENT_WORSENS_LOW_PH",
                pending_base_ph=ph,
                pending_upper=pending_upper,
                pending_lower=pending_lower,
            )

        phi_ph = _finite(state.phi_ph_live) if state is not None else None
        confidence = (
            _finite(state.confidence_ph_live) if state is not None else None
        )
        if (
            phi_ph is None
            or phi_ph <= 0.0
            or confidence is None
            or confidence < float(self.config.min_confidence)
        ):
            return self._decision(
                "PASS_CURRENT_PH_ONLY",
                source_value,
                held_value,
                requested_delta,
                1.0,
                ph_value=ph,
                state=state,
                reason="PH_MODEL_UNAVAILABLE_OR_LOW_CONFIDENCE",
                pending_base_ph=ph,
                pending_upper=pending_upper,
                pending_lower=pending_lower,
            )

        if requested_delta > 0.0:
            pending_base = pending_upper if pending_upper is not None else ph
        elif requested_delta < 0.0:
            pending_base = pending_lower if pending_lower is not None else ph
        else:
            pending_base = ph

        predicted_delta = float(phi_ph) * requested_delta
        predicted_ph = float(pending_base) + predicted_delta
        safe_upper_guard = float(self.config.safe_max) - float(self.config.guard_band)
        safe_lower_guard = float(self.config.safe_min) + float(self.config.guard_band)

        if requested_delta > 0.0 and float(pending_base) >= safe_upper_guard:
            return self._decision(
                "BLOCK",
                source_value,
                held_value,
                requested_delta,
                0.0,
                ph_value=ph,
                state=state,
                predicted_ph=predicted_ph,
                pending_base_ph=pending_base,
                pending_upper=pending_upper,
                pending_lower=pending_lower,
                reason="PENDING_UPPER_PH_GUARD_BAND",
            )
        if requested_delta < 0.0 and float(pending_base) <= safe_lower_guard:
            return self._decision(
                "BLOCK",
                source_value,
                held_value,
                requested_delta,
                0.0,
                ph_value=ph,
                state=state,
                predicted_ph=predicted_ph,
                pending_base_ph=pending_base,
                pending_upper=pending_upper,
                pending_lower=pending_lower,
                reason="PENDING_LOWER_PH_GUARD_BAND",
            )

        scale = 1.0
        reason = "WITHIN_PH_OPERATING_ENVELOPE"
        if requested_delta > 0.0 and predicted_ph > float(self.config.operating_max):
            allowed = max(0.0, float(self.config.operating_max) - float(pending_base))
            needed = max(abs(predicted_delta), 1e-12)
            scale = min(1.0, allowed / needed)
            reason = "SCALE_INCREMENT_TO_PH_OPERATING_MAX"
        elif requested_delta < 0.0 and predicted_ph < float(self.config.operating_min):
            allowed = max(0.0, float(pending_base) - float(self.config.operating_min))
            needed = max(abs(predicted_delta), 1e-12)
            scale = min(1.0, allowed / needed)
            reason = "SCALE_INCREMENT_TO_PH_OPERATING_MIN"

        status = "PASS" if scale >= 1.0 - 1e-12 else "SCALE"
        return self._decision(
            status,
            source_value,
            held_value,
            requested_delta,
            scale,
            ph_value=ph,
            state=state,
            predicted_ph=predicted_ph,
            pending_base_ph=pending_base,
            pending_upper=pending_upper,
            pending_lower=pending_lower,
            reason=reason,
        )

    def _decision(
        self,
        status: str,
        source: float,
        held: float,
        requested_delta: float,
        scale: float,
        *,
        ph_value: Optional[float],
        state: Optional[MFACRuntimeState],
        reason: str,
        predicted_ph: Optional[float] = None,
        pending_base_ph: Optional[float] = None,
        pending_upper: Optional[float] = None,
        pending_lower: Optional[float] = None,
    ) -> PHResidualArbitrationDecision:
        bounded_scale = max(0.0, min(1.0, float(scale)))
        phi_ph = _finite(state.phi_ph_live) if state is not None else None
        confidence = (
            _finite(state.confidence_ph_live) if state is not None else None
        )
        allowed_delta = float(requested_delta) * bounded_scale
        final_residual = float(held) + allowed_delta
        return PHResidualArbitrationDecision(
            status=status,
            source_residual=float(source),
            residual_scale=bounded_scale,
            final_residual=final_residual,
            held_residual=float(held),
            requested_delta_residual=float(requested_delta),
            allowed_delta_residual=float(allowed_delta),
            ph_value=ph_value,
            phi_ph_live=phi_ph,
            confidence_ph_live=confidence,
            predicted_ph=predicted_ph,
            pending_base_ph=pending_base_ph,
            pending_predicted_ph_upper=pending_upper,
            pending_predicted_ph_lower=pending_lower,
            reason=reason,
            metadata={
                "control_semantics": "SO2_RESIDUAL_INCREMENT_THEN_PH_ARBITRATION",
                "residual_scale_applies_to": "CANDIDATE_MINUS_HELD",
                "pending_ph_base_used": pending_base_ph is not None and (
                    pending_upper is not None or pending_lower is not None
                ),
                "additive_ph_residual": False,
            },
        )

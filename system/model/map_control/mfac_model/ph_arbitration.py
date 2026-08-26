# -*- coding: utf-8 -*-
"""pH arbitration for the SO2-led Scheme 2 residual controller.

SO2 remains the only control-producing MFAC channel.  pH never creates an
additive slurry-flow command; it can only pass, scale, or block the SO2 residual
candidate according to current pH and the learned positive ``phi_ph``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any, Dict, Optional

from .mfac_schema import MFACRuntimeState
from .residual_control import MFACResidualDecision


PH_ARBITRATION_SEMANTICS_VERSION = "SCHEME2_PH_ARBITRATION_V1"


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
    ph_value: Optional[float] = None
    phi_ph_live: Optional[float] = None
    confidence_ph_live: Optional[float] = None
    predicted_ph: Optional[float] = None
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = PH_ARBITRATION_SEMANTICS_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class PHResidualArbiter:
    """Constrain an SO2 residual without producing a second control command."""

    def __init__(self, config: PHResidualArbitrationConfig) -> None:
        self.config = config

    def arbitrate(
        self,
        *,
        ph_value: Any,
        state: Optional[MFACRuntimeState],
        so2_residual: MFACResidualDecision,
        arbitration_enabled: bool,
    ) -> PHResidualArbitrationDecision:
        source = _finite(so2_residual.candidate_residual)
        source_value = 0.0 if source is None else source
        if not bool(arbitration_enabled):
            return self._decision(
                "ARBITRATION_DISABLED",
                source_value,
                1.0,
                ph_value=_finite(ph_value),
                state=state,
                reason="PH_ARBITRATION_DISABLED",
            )
        if so2_residual.status != "CALCULATED" or source is None:
            return self._decision(
                "SOURCE_NOT_CALCULATED",
                source_value,
                0.0,
                ph_value=_finite(ph_value),
                state=state,
                reason=so2_residual.status,
            )

        ph = _finite(ph_value)
        if ph is None:
            return self._decision(
                "BLOCK",
                source_value,
                0.0,
                ph_value=None,
                state=state,
                reason="INVALID_PH_INPUT",
            )
        if ph < float(self.config.safe_min) or ph > float(self.config.safe_max):
            return self._decision(
                "BLOCK",
                source_value,
                0.0,
                ph_value=ph,
                state=state,
                reason="PH_OUTSIDE_SAFE_RANGE",
            )

        # Even without a learned pH model, current pH and the known physical
        # direction protect against commands that would move farther outside
        # the configured operating range.
        if source_value > 0.0 and ph >= float(self.config.operating_max):
            return self._decision(
                "BLOCK",
                source_value,
                0.0,
                ph_value=ph,
                state=state,
                reason="POSITIVE_RESIDUAL_WORSENS_HIGH_PH",
            )
        if source_value < 0.0 and ph <= float(self.config.operating_min):
            return self._decision(
                "BLOCK",
                source_value,
                0.0,
                ph_value=ph,
                state=state,
                reason="NEGATIVE_RESIDUAL_WORSENS_LOW_PH",
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
                1.0,
                ph_value=ph,
                state=state,
                reason="PH_MODEL_UNAVAILABLE_OR_LOW_CONFIDENCE",
            )

        predicted_delta = phi_ph * source_value
        predicted_ph = ph + predicted_delta
        safe_upper_guard = float(self.config.safe_max) - float(self.config.guard_band)
        safe_lower_guard = float(self.config.safe_min) + float(self.config.guard_band)

        if source_value > 0.0 and ph >= safe_upper_guard:
            return self._decision(
                "BLOCK",
                source_value,
                0.0,
                ph_value=ph,
                state=state,
                predicted_ph=predicted_ph,
                reason="UPPER_PH_GUARD_BAND",
            )
        if source_value < 0.0 and ph <= safe_lower_guard:
            return self._decision(
                "BLOCK",
                source_value,
                0.0,
                ph_value=ph,
                state=state,
                predicted_ph=predicted_ph,
                reason="LOWER_PH_GUARD_BAND",
            )

        scale = 1.0
        reason = "WITHIN_PH_OPERATING_ENVELOPE"
        if source_value > 0.0 and predicted_ph > float(self.config.operating_max):
            allowed = max(0.0, float(self.config.operating_max) - ph)
            needed = max(abs(predicted_delta), 1e-12)
            scale = min(1.0, allowed / needed)
            reason = "SCALE_TO_PH_OPERATING_MAX"
        elif source_value < 0.0 and predicted_ph < float(self.config.operating_min):
            allowed = max(0.0, ph - float(self.config.operating_min))
            needed = max(abs(predicted_delta), 1e-12)
            scale = min(1.0, allowed / needed)
            reason = "SCALE_TO_PH_OPERATING_MIN"

        status = "PASS" if scale >= 1.0 - 1e-12 else "SCALE"
        return self._decision(
            status,
            source_value,
            scale,
            ph_value=ph,
            state=state,
            predicted_ph=predicted_ph,
            reason=reason,
        )

    def _decision(
        self,
        status: str,
        source: float,
        scale: float,
        *,
        ph_value: Optional[float],
        state: Optional[MFACRuntimeState],
        reason: str,
        predicted_ph: Optional[float] = None,
    ) -> PHResidualArbitrationDecision:
        bounded_scale = max(0.0, min(1.0, float(scale)))
        phi_ph = _finite(state.phi_ph_live) if state is not None else None
        confidence = (
            _finite(state.confidence_ph_live) if state is not None else None
        )
        final_residual = float(source) * bounded_scale
        return PHResidualArbitrationDecision(
            status=status,
            source_residual=float(source),
            residual_scale=bounded_scale,
            final_residual=final_residual,
            ph_value=ph_value,
            phi_ph_live=phi_ph,
            confidence_ph_live=confidence,
            predicted_ph=predicted_ph,
            reason=reason,
            metadata={
                "control_semantics": "SO2_RESIDUAL_THEN_PH_ARBITRATION",
                "additive_ph_residual": False,
            },
        )

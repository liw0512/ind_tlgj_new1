# -*- coding: utf-8 -*-
"""MFAC residual calculation and non-accumulating hold semantics."""

from dataclasses import asdict, dataclass, field
import math
from typing import Any, Dict, Optional

from .mfac_schema import MFACRuntimeState


RESIDUAL_CONTROL_SEMANTICS_VERSION = "SCHEME2_RESIDUAL_CONTROL_V1"


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


@dataclass(frozen=True)
class MFACResidualConfig:
    rho: float
    lambda_regularization: float
    max_abs_residual: float
    min_confidence: float

    def __post_init__(self) -> None:
        rho = _finite(self.rho)
        regularization = _finite(self.lambda_regularization)
        max_abs = _finite(self.max_abs_residual)
        confidence = _finite(self.min_confidence)
        if rho is None or rho <= 0.0:
            raise ValueError("rho must be finite and > 0")
        if regularization is None or regularization <= 0.0:
            raise ValueError("lambda_regularization must be finite and > 0")
        if max_abs is None or max_abs <= 0.0:
            raise ValueError("max_abs_residual must be finite and > 0")
        if confidence is None or not 0.0 <= confidence <= 1.0:
            raise ValueError("min_confidence must be within [0, 1]")


@dataclass
class MFACResidualDecision:
    status: str
    candidate_residual: float
    so2_error: Optional[float] = None
    phi_live: Optional[float] = None
    confidence_live: Optional[float] = None
    hard_clipped: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = RESIDUAL_CONTROL_SEMANTICS_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class MFACResidualController:
    """Calculate one absolute residual candidate from current SO2 error.

    The controller never accumulates its previous result.  Repeated accumulation
    is prevented by ``MFACResidualHoldManager`` which either replaces the held
    residual when an update is allowed or holds the previous value unchanged.
    """

    def __init__(self, config: MFACResidualConfig) -> None:
        self.config = config

    def compute(
        self,
        *,
        so2_target: Any,
        outlet_so2: Any,
        state: Optional[MFACRuntimeState],
        control_enabled: bool,
    ) -> MFACResidualDecision:
        if not bool(control_enabled):
            return MFACResidualDecision(
                status="CONTROL_DISABLED",
                candidate_residual=0.0,
            )
        if state is None:
            return MFACResidualDecision(
                status="NO_RUNTIME_STATE",
                candidate_residual=0.0,
            )

        target = _finite(so2_target)
        outlet = _finite(outlet_so2)
        phi = _finite(state.phi_live)
        confidence = _finite(state.confidence_live)
        if target is None or outlet is None:
            return MFACResidualDecision(
                status="INVALID_SO2_INPUT",
                candidate_residual=0.0,
                phi_live=phi,
                confidence_live=confidence,
            )
        if phi is None or phi >= 0.0:
            return MFACResidualDecision(
                status="INVALID_PHI",
                candidate_residual=0.0,
                phi_live=phi,
                confidence_live=confidence,
            )
        if confidence is None or confidence < float(self.config.min_confidence):
            return MFACResidualDecision(
                status="LOW_CONFIDENCE",
                candidate_residual=0.0,
                phi_live=phi,
                confidence_live=confidence,
            )

        error = target - outlet
        denominator = float(self.config.lambda_regularization) + phi * phi
        raw_residual = float(self.config.rho) * phi * error / denominator
        limit = abs(float(self.config.max_abs_residual))
        candidate = max(-limit, min(limit, raw_residual))
        return MFACResidualDecision(
            status="CALCULATED",
            candidate_residual=candidate,
            so2_error=error,
            phi_live=phi,
            confidence_live=confidence,
            hard_clipped=candidate != raw_residual,
            metadata={"raw_residual": raw_residual},
        )


@dataclass
class MFACResidualHoldDecision:
    held_residual: float
    status: str
    source_candidate_status: str = ""
    semantics_version: str = RESIDUAL_CONTROL_SEMANTICS_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class MFACResidualHoldManager:
    """Hold or replace residual without repeated per-cycle accumulation."""

    def __init__(self, initial_residual: float = 0.0) -> None:
        value = _finite(initial_residual)
        if value is None:
            raise ValueError("initial_residual must be finite")
        self._held_residual = value

    @property
    def held_residual(self) -> float:
        return self._held_residual

    def update(
        self,
        decision: MFACResidualDecision,
        *,
        allow_update: bool,
        reset: bool = False,
    ) -> MFACResidualHoldDecision:
        if reset:
            self._held_residual = 0.0
            return MFACResidualHoldDecision(
                held_residual=0.0,
                status="RESET",
                source_candidate_status=decision.status,
            )

        if not bool(allow_update):
            return MFACResidualHoldDecision(
                held_residual=self._held_residual,
                status="HOLD_WAITING_RESPONSE",
                source_candidate_status=decision.status,
            )

        if decision.status != "CALCULATED":
            return MFACResidualHoldDecision(
                held_residual=self._held_residual,
                status="HOLD_INVALID_CANDIDATE",
                source_candidate_status=decision.status,
            )

        candidate = _finite(decision.candidate_residual)
        if candidate is None:
            return MFACResidualHoldDecision(
                held_residual=self._held_residual,
                status="HOLD_INVALID_CANDIDATE",
                source_candidate_status=decision.status,
            )

        self._held_residual = candidate
        return MFACResidualHoldDecision(
            held_residual=self._held_residual,
            status="UPDATED",
            source_candidate_status=decision.status,
        )

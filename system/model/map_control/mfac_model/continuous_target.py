# -*- coding: utf-8 -*-
"""Continuous algorithm-target publication for Scheme 2 MFAC.

This module owns only the algorithm target contract.  It deliberately does not
track actual slurry-flow feedback, infer process response, update ``phi``, or
write DCS commands.  Those responsibilities belong to later runtime stages.

Core contract::

    Q_target_algorithm = clip(Qbase + residual_mfac_hold, hard_min, hard_max)

When current inputs are invalid, the publisher holds the last *valid algorithm
calculation*.  Before the first valid calculation it may use an explicit plant
startup/DCS setpoint fallback.  Actual slurry-flow feedback is intentionally not
part of this API and must never be used as a substitute algorithm target.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any, Dict, Optional


CONTINUOUS_TARGET_SEMANTICS_VERSION = "SCHEME2_CONTINUOUS_TARGET_V1"
COUNTERFACTUAL_SHADOW = "COUNTERFACTUAL_SHADOW"
ONLINE_SHADOW = "ONLINE_SHADOW"


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


@dataclass(frozen=True)
class ContinuousTargetConfig:
    hard_min_supply_flow: float = 0.0
    hard_max_supply_flow: float = 70.0

    def __post_init__(self) -> None:
        lower = _finite(self.hard_min_supply_flow)
        upper = _finite(self.hard_max_supply_flow)
        if lower is None or upper is None:
            raise ValueError("supply-flow hard bounds must be finite")
        if lower >= upper:
            raise ValueError("hard_min_supply_flow must be < hard_max_supply_flow")


@dataclass
class ContinuousTargetDecision:
    algorithm_target_supply_flow: Optional[float]
    algorithm_target_valid: bool
    algorithm_target_status: str
    algorithm_target_timestamp: str = ""
    qbase_effective: Optional[float] = None
    residual_mfac_hold: Optional[float] = None
    hard_clipped: bool = False
    replay_semantics: str = ONLINE_SHADOW
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = CONTINUOUS_TARGET_SEMANTICS_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ContinuousTargetPublisher:
    """Publish one bounded Scheme-2 algorithm target per decision cycle.

    ``startup_setpoint_target`` is an explicit DCS target readback or plant
    startup setpoint.  It must never be populated from actual slurry-flow
    feedback.  The API intentionally has no ``actual_flow`` argument so that
    historical/operator actual flow cannot silently leak into the algorithm
    target calculation.
    """

    def __init__(
        self,
        config: ContinuousTargetConfig | None = None,
        *,
        startup_setpoint_target: Optional[float] = None,
    ) -> None:
        self.config = config or ContinuousTargetConfig()
        self._last_valid_algorithm_target: Optional[float] = None
        self._startup_setpoint_target = self._bounded_optional(
            startup_setpoint_target,
            field_name="startup_setpoint_target",
        )

    @property
    def last_valid_algorithm_target(self) -> Optional[float]:
        return self._last_valid_algorithm_target

    @property
    def startup_setpoint_target(self) -> Optional[float]:
        return self._startup_setpoint_target

    def restore_last_valid_algorithm_target(self, value: float) -> None:
        """Restore a persisted algorithm target without inventing a new one."""
        number = _finite(value)
        if number is None:
            raise ValueError("restored algorithm target must be finite")
        lower, upper = self._bounds()
        if number < lower or number > upper:
            raise ValueError("restored algorithm target is outside hard bounds")
        self._last_valid_algorithm_target = number

    def publish(
        self,
        qbase_effective: Any,
        residual_mfac_hold: Any = 0.0,
        *,
        inputs_valid: bool = True,
        timestamp: str = "",
        replay_semantics: str = ONLINE_SHADOW,
    ) -> ContinuousTargetDecision:
        qbase = _finite(qbase_effective)
        residual = _finite(residual_mfac_hold)
        current_inputs_valid = bool(inputs_valid) and qbase is not None and residual is not None

        if current_inputs_valid:
            raw_target = qbase + residual
            target, hard_clipped = self._clip(raw_target)
            self._last_valid_algorithm_target = target
            return ContinuousTargetDecision(
                algorithm_target_supply_flow=target,
                algorithm_target_valid=True,
                algorithm_target_status="CALCULATED",
                algorithm_target_timestamp=str(timestamp or ""),
                qbase_effective=qbase,
                residual_mfac_hold=residual,
                hard_clipped=hard_clipped,
                replay_semantics=str(replay_semantics or ONLINE_SHADOW),
                metadata={"raw_algorithm_target_supply_flow": raw_target},
            )

        if self._last_valid_algorithm_target is not None:
            return ContinuousTargetDecision(
                algorithm_target_supply_flow=self._last_valid_algorithm_target,
                algorithm_target_valid=False,
                algorithm_target_status="HOLD_LAST_INVALID_INPUT",
                algorithm_target_timestamp=str(timestamp or ""),
                qbase_effective=qbase,
                residual_mfac_hold=residual,
                replay_semantics=str(replay_semantics or ONLINE_SHADOW),
                metadata={"invalid_input": True},
            )

        if self._startup_setpoint_target is not None:
            return ContinuousTargetDecision(
                algorithm_target_supply_flow=self._startup_setpoint_target,
                algorithm_target_valid=False,
                algorithm_target_status="STARTUP_FALLBACK_INVALID_INPUT",
                algorithm_target_timestamp=str(timestamp or ""),
                qbase_effective=qbase,
                residual_mfac_hold=residual,
                replay_semantics=str(replay_semantics or ONLINE_SHADOW),
                metadata={
                    "invalid_input": True,
                    "startup_fallback_source": "EXPLICIT_SETPOINT_TARGET",
                },
            )

        return ContinuousTargetDecision(
            algorithm_target_supply_flow=None,
            algorithm_target_valid=False,
            algorithm_target_status="NO_VALID_TARGET",
            algorithm_target_timestamp=str(timestamp or ""),
            qbase_effective=qbase,
            residual_mfac_hold=residual,
            replay_semantics=str(replay_semantics or ONLINE_SHADOW),
            metadata={"invalid_input": True},
        )

    def _bounds(self) -> tuple[float, float]:
        return (
            float(self.config.hard_min_supply_flow),
            float(self.config.hard_max_supply_flow),
        )

    def _clip(self, value: float) -> tuple[float, bool]:
        lower, upper = self._bounds()
        bounded = max(lower, min(upper, float(value)))
        return bounded, bounded != float(value)

    def _bounded_optional(
        self,
        value: Optional[float],
        *,
        field_name: str,
    ) -> Optional[float]:
        if value is None:
            return None
        number = _finite(value)
        if number is None:
            raise ValueError(f"{field_name} must be finite when provided")
        bounded, _ = self._clip(number)
        return bounded

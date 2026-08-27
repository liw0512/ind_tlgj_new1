# -*- coding: utf-8 -*-
"""Shadow-only staircase planner for Scheme-2 MFAC slurry demand.

The planner shapes *how* a raw slurry demand could be approached. It never
creates a second controller and never writes DCS. The current production target
remains owned by the existing MFAC runtime until this planner is separately
validated and approved.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import math
from typing import Any, Dict, Optional

from .continuous_target import ContinuousTargetConfig
from .pending_dose_guard import PendingDoseGuardDecision


FLOW_TRAJECTORY_PLANNER_SEMANTICS_VERSION = "SCHEME2_FLOW_TRAJECTORY_PLANNER_V1_SHADOW"


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
class FlowTrajectoryPlannerConfig:
    max_step_up: float
    max_step_down: float
    min_hold_seconds: float
    demand_deadband: float = 0.0

    def __post_init__(self) -> None:
        for name in ("max_step_up", "max_step_down"):
            value = _finite(getattr(self, name))
            if value is None or value <= 0.0:
                raise ValueError("%s must be finite and > 0" % name)
        hold = _finite(self.min_hold_seconds)
        if hold is None or hold < 0.0:
            raise ValueError("min_hold_seconds must be finite and >= 0")
        deadband = _finite(self.demand_deadband)
        if deadband is None or deadband < 0.0:
            raise ValueError("demand_deadband must be finite and >= 0")


@dataclass
class FlowTrajectoryPlan:
    status: str
    raw_demand: Optional[float]
    previous_planned_target: Optional[float]
    planned_target: Optional[float]
    planned_delta_q: float
    pending_response_status: str = ""
    hold_remaining_seconds: float = 0.0
    reason: str = ""
    shadow_only: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = FLOW_TRAJECTORY_PLANNER_SEMANTICS_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class FlowTrajectoryPlanner:
    def __init__(
        self,
        config: FlowTrajectoryPlannerConfig,
        target_bounds: ContinuousTargetConfig,
    ) -> None:
        self.config = config
        self.target_bounds = target_bounds
        self._planned_target: Optional[float] = None
        self._last_change_time: Optional[datetime] = None

    @property
    def planned_target(self) -> Optional[float]:
        return self._planned_target

    def reset(self) -> None:
        self._planned_target = None
        self._last_change_time = None

    def _clip(self, value: float) -> float:
        return min(
            float(self.target_bounds.hard_max_supply_flow),
            max(float(self.target_bounds.hard_min_supply_flow), float(value)),
        )

    def plan(
        self,
        *,
        timestamp: Any,
        raw_demand: Any,
        raw_demand_valid: bool,
        current_algorithm_target: Any = None,
        pending_response: Optional[PendingDoseGuardDecision] = None,
    ) -> FlowTrajectoryPlan:
        try:
            now = _timestamp(timestamp)
        except (TypeError, ValueError):
            return self._result(
                "INVALID_INPUT", None, None, 0.0,
                reason="INVALID_TIMESTAMP",
                pending_response=pending_response,
            )
        demand = _finite(raw_demand)
        anchor = _finite(current_algorithm_target)
        if not bool(raw_demand_valid) or demand is None:
            return self._result(
                "HOLD_INVALID_DEMAND",
                demand,
                self._planned_target,
                0.0,
                reason="RAW_DEMAND_INVALID",
                pending_response=pending_response,
            )

        demand = self._clip(demand)
        if self._planned_target is None:
            seed = anchor if anchor is not None else demand
            self._planned_target = self._clip(seed)
            self._last_change_time = now
            return self._result(
                "INITIALIZED",
                demand,
                self._planned_target,
                0.0,
                reason="SHADOW_PLANNER_INITIALIZED_FROM_CURRENT_TARGET",
                pending_response=pending_response,
            )

        previous = float(self._planned_target)
        gap = demand - previous
        if abs(gap) <= float(self.config.demand_deadband):
            self._planned_target = demand
            return self._result(
                "AT_DEMAND", demand, previous, demand - previous,
                reason="DEMAND_WITHIN_DEADBAND",
                pending_response=pending_response,
            )

        pending_status = (
            str(pending_response.status) if pending_response is not None else ""
        )
        if gap > 0.0 and pending_status in {"LIMIT_POSITIVE", "WATCH_HIGH"}:
            return self._result(
                "HOLD_PENDING_PH", demand, previous, 0.0,
                reason="PENDING_PH_LIMITS_ADDITIONAL_SLURRY",
                pending_response=pending_response,
            )
        if gap < 0.0 and pending_status in {"LIMIT_NEGATIVE", "WATCH_LOW"}:
            return self._result(
                "HOLD_PENDING_PH", demand, previous, 0.0,
                reason="PENDING_PH_LIMITS_ADDITIONAL_REDUCTION",
                pending_response=pending_response,
            )

        elapsed = (
            (now - self._last_change_time).total_seconds()
            if self._last_change_time is not None
            else float("inf")
        )
        hold_required = float(self.config.min_hold_seconds)
        if elapsed < hold_required:
            return self._result(
                "HOLD_MIN_DURATION", demand, previous, 0.0,
                reason="MINIMUM_HOLD_NOT_ELAPSED",
                pending_response=pending_response,
                hold_remaining_seconds=max(0.0, hold_required - elapsed),
            )

        if gap > 0.0:
            step = min(gap, float(self.config.max_step_up))
            status = "STEP_UP"
        else:
            step = max(gap, -float(self.config.max_step_down))
            status = "STEP_DOWN"
        candidate = self._clip(previous + step)
        applied_delta = candidate - previous
        self._planned_target = candidate
        if abs(applied_delta) > 1e-12:
            self._last_change_time = now
        return self._result(
            status, demand, previous, applied_delta,
            reason="STEP_TOWARD_RAW_DEMAND",
            pending_response=pending_response,
        )

    def _result(
        self,
        status: str,
        demand: Optional[float],
        previous: Optional[float],
        delta: float,
        *,
        reason: str,
        pending_response: Optional[PendingDoseGuardDecision],
        hold_remaining_seconds: float = 0.0,
    ) -> FlowTrajectoryPlan:
        return FlowTrajectoryPlan(
            status=str(status),
            raw_demand=demand,
            previous_planned_target=previous,
            planned_target=self._planned_target,
            planned_delta_q=float(delta),
            pending_response_status=(
                str(pending_response.status) if pending_response is not None else ""
            ),
            hold_remaining_seconds=float(hold_remaining_seconds),
            reason=str(reason),
            shadow_only=True,
            metadata={
                "algorithm_target_replaced": False,
                "actual_flow_used_as_target_fallback": False,
                "dose_debt_semantics": False,
            },
        )


__all__ = [
    "FLOW_TRAJECTORY_PLANNER_SEMANTICS_VERSION",
    "FlowTrajectoryPlannerConfig",
    "FlowTrajectoryPlan",
    "FlowTrajectoryPlanner",
]

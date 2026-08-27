# -*- coding: utf-8 -*-
"""Add delayed-response memory and staircase planning without changing target ownership."""

from __future__ import annotations

from typing import Any, Optional, Tuple

from .flow_trajectory_planner import (
    FlowTrajectoryPlanner,
    FlowTrajectoryPlannerConfig,
)
from .pending_dose_guard import PendingDoseGuard, PendingDoseGuardConfig
from .runtime_coordinator import (
    Scheme2RuntimeCoordinator,
    Scheme2RuntimeCoordinatorConfig,
    Scheme2RuntimeCycleResult,
)
from .runtime_store import Scheme2RuntimeStore


TRAJECTORY_SHADOW_COORDINATOR_VERSION = "SCHEME2_TRAJECTORY_SHADOW_COORDINATOR_V1"


class Scheme2TrajectoryShadowCoordinator(Scheme2RuntimeCoordinator):
    """Run the existing coordinator, then append non-authoritative trajectory advice."""

    def __init__(
        self,
        config: Scheme2RuntimeCoordinatorConfig,
        runtime_store: Scheme2RuntimeStore,
        *,
        pending_dose_config: PendingDoseGuardConfig,
        trajectory_planner_config: FlowTrajectoryPlannerConfig,
        runtime_state=None,
        initial_residual_mfac_hold: float = 0.0,
        startup_setpoint_target: Optional[float] = None,
    ) -> None:
        super().__init__(
            config,
            runtime_store,
            runtime_state=runtime_state,
            initial_residual_mfac_hold=initial_residual_mfac_hold,
            startup_setpoint_target=startup_setpoint_target,
        )
        if config.ph_arbitration is None:
            raise ValueError("trajectory shadow requires pH arbitration envelope")
        self.pending_dose_guard = PendingDoseGuard(
            pending_dose_config,
            config.ph_arbitration,
        )
        self.trajectory_planner = FlowTrajectoryPlanner(
            trajectory_planner_config,
            config.continuous_target,
        )
        self._trajectory_context_key: Optional[Tuple[str, str]] = None

    def set_runtime_state(self, runtime_state, *, residual_mfac_hold: float = 0.0) -> None:
        super().set_runtime_state(
            runtime_state,
            residual_mfac_hold=residual_mfac_hold,
        )
        self.pending_dose_guard.reset("RUNTIME_STATE_SET")
        self.trajectory_planner.reset()
        self._trajectory_context_key = (
            runtime_state.condition_snapshot_version,
            runtime_state.mfac_context_id,
        )

    def process_cycle(self, **kwargs: Any) -> Scheme2RuntimeCycleResult:
        key = (
            str(kwargs.get("condition_snapshot_version") or ""),
            str(kwargs.get("mfac_context_id") or ""),
        )
        if key != self._trajectory_context_key:
            self.pending_dose_guard.reset("MFAC_CONTEXT_CHANGE")
            self.trajectory_planner.reset()
            self._trajectory_context_key = key

        result = super().process_cycle(**kwargs)
        pending = self.pending_dose_guard.update(
            timestamp=kwargs.get("timestamp"),
            actual_supply_flow_feedback=kwargs.get("actual_supply_flow_feedback"),
            ph_value=kwargs.get("ph"),
            state=result.runtime_state,
            data_quality_ok=bool(kwargs.get("data_quality_ok", True)),
        )
        plan = self.trajectory_planner.plan(
            timestamp=kwargs.get("timestamp"),
            raw_demand=result.algorithm_target.algorithm_target_supply_flow,
            raw_demand_valid=result.algorithm_target.algorithm_target_valid,
            current_algorithm_target=result.algorithm_target.algorithm_target_supply_flow,
            pending_response=pending,
        )
        metadata = dict(result.metadata or {})
        metadata.update(
            {
                "trajectory_shadow_enabled": True,
                "trajectory_shadow_semantics_version": TRAJECTORY_SHADOW_COORDINATOR_VERSION,
                "pending_dose_guard": pending.to_dict(),
                "trajectory_plan": plan.to_dict(),
                "algorithm_target_replaced_by_trajectory_planner": False,
                "trajectory_planner_dcs_write_enabled": False,
                "dose_debt_semantics": False,
            }
        )
        result.metadata = metadata
        return result


__all__ = [
    "TRAJECTORY_SHADOW_COORDINATOR_VERSION",
    "Scheme2TrajectoryShadowCoordinator",
]

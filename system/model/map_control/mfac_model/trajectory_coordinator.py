# -*- coding: utf-8 -*-
"""Add delayed-response memory, reviewed historical priors and staircase Shadow advice."""

from __future__ import annotations

from typing import Any, Optional, Tuple

from .flow_trajectory_planner import (
    FlowTrajectoryPlanner,
    FlowTrajectoryPlannerConfig,
)
from .historical_runtime_prior import resolve_reviewed_scalar_runtime_prior
from .historical_sensitivity_map import (
    HistoricalSensitivityMap,
    HistoricalSensitivityQuery,
)
from .mfac_schema import MFACRuntimeState
from .pending_dose_guard import PendingDoseGuard, PendingDoseGuardConfig
from .runtime_coordinator import (
    Scheme2RuntimeCoordinator,
    Scheme2RuntimeCoordinatorConfig,
    Scheme2RuntimeCycleResult,
)
from .runtime_store import Scheme2RuntimeStore


TRAJECTORY_SHADOW_COORDINATOR_VERSION = (
    "SCHEME2_TRAJECTORY_SHADOW_COORDINATOR_V3_REVIEWED_SCALAR_PRIOR"
)


class Scheme2TrajectoryShadowCoordinator(Scheme2RuntimeCoordinator):
    """Run the existing coordinator, then append non-authoritative trajectory advice.

    The attached historical map is a research/review artifact and may contain
    complex surfaces. Runtime seeding is deliberately narrower: only profiles
    that pass ``resolve_reviewed_scalar_runtime_prior`` are visible here.

    Persisted online state has first priority.  Before a channel has any online
    accepted event, an approved scalar historical prior may be re-evaluated at
    the current context/grid. Once that channel has learned online, historical
    mapping never overwrites it.
    """

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
        historical_sensitivity_map: Optional[HistoricalSensitivityMap] = None,
    ) -> None:
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
        self._historical_sensitivity_map = historical_sensitivity_map
        self._historical_query: Optional[HistoricalSensitivityQuery] = None
        self._last_historical_mapping = None
        super().__init__(
            config,
            runtime_store,
            runtime_state=runtime_state,
            initial_residual_mfac_hold=initial_residual_mfac_hold,
            startup_setpoint_target=startup_setpoint_target,
        )

    @property
    def historical_sensitivity_map(self) -> Optional[HistoricalSensitivityMap]:
        return self._historical_sensitivity_map

    def set_historical_sensitivity_map(
        self,
        value: Optional[HistoricalSensitivityMap],
    ) -> None:
        """Attach/detach a historical map; runtime still applies scalar review gate."""
        self._historical_sensitivity_map = value
        self._last_historical_mapping = None

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

    def _mapped_state(
        self,
        snapshot: str,
        context_id: str,
        *,
        existing: Optional[MFACRuntimeState],
    ) -> Optional[MFACRuntimeState]:
        mapping = self._historical_sensitivity_map
        query = self._historical_query
        if mapping is None or query is None:
            return existing
        if query.condition_snapshot_version != snapshot or query.mfac_context_id != context_id:
            return existing

        decision = resolve_reviewed_scalar_runtime_prior(mapping, query)
        self._last_historical_mapping = decision
        if not decision.available:
            return existing

        if existing is None:
            return MFACRuntimeState(
                condition_snapshot_version=snapshot,
                mfac_context_id=context_id,
                phi_live=float(decision.phi_so2),
                confidence_live=float(decision.confidence_so2),
                phi_ph_live=float(decision.phi_ph),
                confidence_ph_live=float(decision.confidence_ph),
                metadata={
                    "historical_prior_seeded": True,
                    "historical_prior_mapping": decision.to_dict(),
                    "historical_prior_model_complexity": "SCALAR",
                    "historical_prior_runtime_reviewed": True,
                    "historical_prior_online_override_policy": (
                        "PER_CHANNEL_AFTER_FIRST_ACCEPTED_ONLINE_EVENT"
                    ),
                },
            )

        update_so2 = int(existing.valid_event_count) <= 0
        update_ph = int(existing.ph_valid_event_count) <= 0
        if not update_so2 and not update_ph:
            return existing

        metadata = dict(existing.metadata or {})
        metadata["historical_prior_seeded"] = True
        metadata["historical_prior_mapping"] = decision.to_dict()
        metadata["historical_prior_model_complexity"] = "SCALAR"
        metadata["historical_prior_runtime_reviewed"] = True
        metadata["historical_prior_online_override_policy"] = (
            "PER_CHANNEL_AFTER_FIRST_ACCEPTED_ONLINE_EVENT"
        )
        return MFACRuntimeState(
            condition_snapshot_version=existing.condition_snapshot_version,
            mfac_context_id=existing.mfac_context_id,
            phi_live=(
                float(decision.phi_so2) if update_so2 else float(existing.phi_live)
            ),
            confidence_live=(
                float(decision.confidence_so2)
                if update_so2
                else float(existing.confidence_live)
            ),
            bias_live=float(existing.bias_live),
            valid_event_count=int(existing.valid_event_count),
            last_event_id=str(existing.last_event_id),
            last_update_time=str(existing.last_update_time),
            phi_ph_live=(
                float(decision.phi_ph)
                if update_ph
                else existing.phi_ph_live
            ),
            confidence_ph_live=(
                float(decision.confidence_ph)
                if update_ph
                else float(existing.confidence_ph_live)
            ),
            ph_valid_event_count=int(existing.ph_valid_event_count),
            ph_last_event_id=str(existing.ph_last_event_id),
            ph_last_update_time=str(existing.ph_last_update_time),
            metadata=metadata,
            semantics_version=existing.semantics_version,
        )

    def _select_context(self, snapshot: str, context_id: str) -> str:
        status = super()._select_context(snapshot, context_id)
        if not snapshot or not context_id:
            return status

        previous = self.runtime_state
        mapped = self._mapped_state(snapshot, context_id, existing=previous)
        if mapped is previous:
            return status
        if mapped is not None:
            # The base selector already reset held residual to zero when no
            # persisted context was found. Historical mapping changes only phi
            # state and never grants residual authority.
            self.runtime_state = mapped
            self._active_context_key = (snapshot, context_id)
            if previous is None:
                source = (
                    self._last_historical_mapping.mapping_source
                    if self._last_historical_mapping is not None
                    else "UNKNOWN"
                )
                return "CONTEXT_HISTORICAL_PRIOR:%s" % source
            return "CONTEXT_HISTORICAL_PRIOR_REFRESHED"
        return status

    def process_cycle(self, **kwargs: Any) -> Scheme2RuntimeCycleResult:
        key = (
            str(kwargs.get("condition_snapshot_version") or ""),
            str(kwargs.get("mfac_context_id") or ""),
        )
        if key != self._trajectory_context_key:
            self.pending_dose_guard.reset("MFAC_CONTEXT_CHANGE")
            self.trajectory_planner.reset()
            self._trajectory_context_key = key

        self._historical_query = HistoricalSensitivityQuery(
            condition_snapshot_version=key[0],
            mfac_context_id=key[1],
            grid_id=str(kwargs.get("grid_id") or ""),
            qbase=(
                kwargs.get("qbase_effective")
                if bool(kwargs.get("qbase_inputs_valid", False))
                else None
            ),
            inlet_so2=kwargs.get("inlet_so2"),
            ph=kwargs.get("ph"),
            outlet_so2=kwargs.get("outlet_so2"),
        )

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
                "historical_sensitivity_mapping": (
                    self._last_historical_mapping.to_dict()
                    if self._last_historical_mapping is not None
                    else None
                ),
                "historical_runtime_prior_policy": "REVIEWED_SCALAR_ONLY",
                "historical_prior_replaces_qbase": False,
                "historical_prior_enables_learning": False,
                "historical_prior_enables_residual": False,
                "historical_prior_enables_dcs_write": False,
            }
        )
        result.metadata = metadata
        return result


__all__ = [
    "TRAJECTORY_SHADOW_COORDINATOR_VERSION",
    "Scheme2TrajectoryShadowCoordinator",
]

# -*- coding: utf-8 -*-
"""Add delayed-response memory, reviewed historical priors and staircase Shadow advice."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from .decision_permission_gate import ResidualDecisionPermissionGate
from .flow_trajectory_planner import (
    FlowTrajectoryPlanner,
    FlowTrajectoryPlannerConfig,
)
from .historical_prior_artifact import load_reviewed_prior_map_for_snapshot
from .historical_runtime_prior import resolve_reviewed_scalar_runtime_prior
from .historical_sensitivity_map import (
    HistoricalSensitivityMap,
    HistoricalSensitivityQuery,
)
from .mfac_schema import MFACRuntimeState
from .pending_dose_guard import (
    PendingDoseGuard,
    PendingDoseGuardConfig,
    PendingDoseGuardDecision,
)
from .residual_control import MFACResidualDecision
from .runtime_coordinator import (
    Scheme2RuntimeCoordinator,
    Scheme2RuntimeCoordinatorConfig,
    Scheme2RuntimeCycleResult,
)
from .runtime_store import Scheme2RuntimeStore


TRAJECTORY_SHADOW_COORDINATOR_VERSION = (
    "SCHEME2_TRAJECTORY_SHADOW_COORDINATOR_V8_VERSION_BOUND_PRIOR"
)


class Scheme2TrajectoryShadowCoordinator(Scheme2RuntimeCoordinator):
    """Run one SO2-led coordinator and append non-authoritative trajectory advice.

    The historical map is filtered to reviewed scalar runtime priors.  If a map
    is not explicitly injected for a static test, the coordinator lazily loads
    the reviewed map bound by the MFAC manifest of the active condition snapshot.
    Thus Process4MapControl does not own a second historical-prior cache.

    Seven-day offline retraining and online adaptation are deliberately separate
    lifecycles.  Exact persisted online state wins first.  Across a new
    ConditionSnapshot, learned phi/confidence may be handed forward only when
    both ``mfac_context_id`` and ``grid_id`` remain unchanged; residual,
    PendingDose and HOLD cadence are reset at the version boundary.  Only after
    that handoff may a reviewed scalar historical prior fill a channel that has
    no online evidence.

    There remains exactly one residual controller, one pH arbiter and one held
    residual.  Planner ``min_hold_seconds`` is also the single source for the
    residual decision HOLD duration.
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
        self.residual_decision_gate = ResidualDecisionPermissionGate(
            min_hold_seconds=float(trajectory_planner_config.min_hold_seconds)
        )
        self._trajectory_context_key: Optional[Tuple[str, str]] = None
        self._historical_sensitivity_map = historical_sensitivity_map
        self._historical_map_explicit = historical_sensitivity_map is not None
        self._historical_map_snapshot_loaded = (
            historical_sensitivity_map.condition_snapshot_version
            if historical_sensitivity_map is not None
            else ""
        )
        self._historical_map_source = (
            "EXPLICIT_INJECTION" if historical_sensitivity_map is not None else "NONE"
        )
        self._historical_query: Optional[HistoricalSensitivityQuery] = None
        self._last_historical_mapping = None
        self._last_cross_snapshot_handoff: Optional[Dict[str, Any]] = None
        self._pending_for_current_cycle: Optional[PendingDoseGuardDecision] = None
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
        """Explicit injection for static/test integrations; disables lazy loading."""
        self._historical_sensitivity_map = value
        self._historical_map_explicit = value is not None
        self._historical_map_snapshot_loaded = (
            value.condition_snapshot_version if value is not None else ""
        )
        self._historical_map_source = (
            "EXPLICIT_INJECTION" if value is not None else "NONE"
        )
        self._last_historical_mapping = None

    def _ensure_version_bound_historical_map(self, snapshot: str) -> None:
        if self._historical_map_explicit:
            return
        snapshot_text = str(snapshot or "").strip()
        if not snapshot_text:
            self._historical_sensitivity_map = None
            self._historical_map_snapshot_loaded = ""
            self._historical_map_source = "NONE"
            return
        if self._historical_map_snapshot_loaded == snapshot_text:
            return
        mapping = load_reviewed_prior_map_for_snapshot(snapshot_text)
        self._historical_sensitivity_map = mapping
        self._historical_map_snapshot_loaded = snapshot_text
        self._historical_map_source = (
            "VERSION_MANIFEST_REVIEWED_MAP" if mapping is not None else "NONE"
        )
        self._last_historical_mapping = None

    def set_runtime_state(self, runtime_state, *, residual_mfac_hold: float = 0.0) -> None:
        super().set_runtime_state(
            runtime_state,
            residual_mfac_hold=residual_mfac_hold,
        )
        self.pending_dose_guard.reset("RUNTIME_STATE_SET")
        self.trajectory_planner.reset()
        self.residual_decision_gate.reset("RUNTIME_STATE_SET")
        self._pending_for_current_cycle = None
        self._trajectory_context_key = (
            runtime_state.condition_snapshot_version,
            runtime_state.mfac_context_id,
        )

    @staticmethod
    def _channel_has_online_evidence(
        state: MFACRuntimeState,
        channel: str,
    ) -> bool:
        if channel == "SO2":
            if int(state.valid_event_count) > 0:
                return True
            metadata_key = "online_confidence_so2"
        elif channel == "PH":
            if int(state.ph_valid_event_count) > 0:
                return True
            metadata_key = "online_confidence_ph"
        else:
            raise ValueError("unknown MFAC channel")
        evidence = dict((state.metadata or {}).get(metadata_key) or {})
        try:
            return float(evidence.get("effective_event_count") or 0.0) > 0.0
        except (TypeError, ValueError, OverflowError):
            return False

    def _stamp_runtime_grid(self, state: Optional[MFACRuntimeState]) -> None:
        query = self._historical_query
        if state is None or query is None:
            return
        grid_id = str(query.grid_id or "").strip()
        if not grid_id:
            return
        metadata = dict(state.metadata or {})
        metadata["runtime_grid_id"] = grid_id
        metadata["runtime_context_identity_policy"] = (
            "CONDITION_SNAPSHOT_VERSION+MFAC_CONTEXT_ID;GRID_FOR_WEEKLY_HANDOFF"
        )
        state.metadata = metadata

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

        runtime_grid_id = str(query.grid_id or "").strip()
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
                        "PER_CHANNEL_AFTER_FIRST_CLEAN_ONLINE_EVIDENCE"
                    ),
                    "runtime_grid_id": runtime_grid_id,
                },
            )

        update_so2 = not self._channel_has_online_evidence(existing, "SO2")
        update_ph = not self._channel_has_online_evidence(existing, "PH")
        if not update_so2 and not update_ph:
            return existing

        metadata = dict(existing.metadata or {})
        metadata["historical_prior_seeded"] = True
        metadata["historical_prior_mapping"] = decision.to_dict()
        metadata["historical_prior_model_complexity"] = "SCALAR"
        metadata["historical_prior_runtime_reviewed"] = True
        metadata["historical_prior_online_override_policy"] = (
            "PER_CHANNEL_AFTER_FIRST_CLEAN_ONLINE_EVIDENCE"
        )
        if runtime_grid_id:
            metadata["runtime_grid_id"] = runtime_grid_id
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
                float(decision.phi_ph) if update_ph else existing.phi_ph_live
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
        self._last_cross_snapshot_handoff = None
        self._ensure_version_bound_historical_map(snapshot)
        status = super()._select_context(snapshot, context_id)
        if not snapshot or not context_id:
            return status

        query = self._historical_query
        if (
            self.runtime_state is None
            and query is not None
            and status == "CONTEXT_SAFE_EMPTY:SNAPSHOT_VERSION_MISMATCH"
        ):
            handoff = self.runtime_store.restore_same_context_across_snapshot(
                condition_snapshot_version=snapshot,
                mfac_context_id=context_id,
                grid_id=str(query.grid_id or ""),
            )
            self._last_cross_snapshot_handoff = {
                "restored": bool(handoff.restored),
                "reason": handoff.reason,
                "residual_reused": False,
            }
            if handoff.restored and handoff.runtime_state is not None:
                # Only phi/confidence/evidence state crosses the weekly version
                # boundary.  Residual and all delayed-control memory restart at 0.
                super().set_runtime_state(
                    handoff.runtime_state,
                    residual_mfac_hold=0.0,
                )
                status = "CONTEXT_CROSS_SNAPSHOT_HANDOFF"

        previous = self.runtime_state
        mapped = self._mapped_state(snapshot, context_id, existing=previous)
        if mapped is not previous and mapped is not None:
            self.runtime_state = mapped
            self._active_context_key = (snapshot, context_id)
            if previous is None:
                source = (
                    self._last_historical_mapping.mapping_source
                    if self._last_historical_mapping is not None
                    else "UNKNOWN"
                )
                status = "CONTEXT_HISTORICAL_PRIOR:%s" % source
            else:
                status = (
                    "CONTEXT_CROSS_SNAPSHOT_HANDOFF_PRIOR_REFRESHED"
                    if status == "CONTEXT_CROSS_SNAPSHOT_HANDOFF"
                    else "CONTEXT_HISTORICAL_PRIOR_REFRESHED"
                )

        self._stamp_runtime_grid(self.runtime_state)
        return status

    def _ph_arbitration_context(
        self,
        *,
        timestamp: Any,
        actual_supply_flow_feedback: Any,
        ph_value: Any,
        state: Optional[MFACRuntimeState],
        data_quality_ok: bool,
    ) -> Dict[str, Any]:
        base = super()._ph_arbitration_context(
            timestamp=timestamp,
            actual_supply_flow_feedback=actual_supply_flow_feedback,
            ph_value=ph_value,
            state=state,
            data_quality_ok=data_quality_ok,
        )
        pending = self.pending_dose_guard.update(
            timestamp=timestamp,
            actual_supply_flow_feedback=actual_supply_flow_feedback,
            ph_value=ph_value,
            state=state,
            data_quality_ok=bool(data_quality_ok),
        )
        self._pending_for_current_cycle = pending
        base.update(
            {
                "pending_predicted_ph_upper": pending.predicted_ph_upper,
                "pending_predicted_ph_lower": pending.predicted_ph_lower,
                "pending_source": "PENDING_DOSE_GUARD",
                "pending_status": pending.status,
                "pending_reason": pending.reason,
            }
        )
        return base

    def _residual_update_permission(
        self,
        *,
        timestamp: Any,
        hold_source: MFACResidualDecision,
        response_ready: bool,
        response_ready_tracking_id: str,
        qbase_inputs_valid: bool,
        data_quality_ok: bool,
        fast_active: bool,
        equipment_changed: bool,
        ph_arbitration_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        proposed = (
            hold_source.candidate_residual
            if hold_source.status == "CALCULATED"
            else self.residual_hold_manager.held_residual
        )
        pending_status = str(
            ph_arbitration_context.get("pending_status") or ""
        )
        decision = self.residual_decision_gate.evaluate(
            timestamp=timestamp,
            residual_control_enabled=bool(self.config.residual_control_enabled),
            qbase_inputs_valid=bool(qbase_inputs_valid),
            data_quality_ok=bool(data_quality_ok),
            fast_active=bool(fast_active),
            equipment_changed=bool(equipment_changed),
            held_residual=self.residual_hold_manager.held_residual,
            proposed_residual=proposed,
            response_ready=bool(response_ready),
            pending_status=pending_status,
        )
        payload = decision.to_dict()
        payload.update(
            {
                "gate_owner": "TRAJECTORY_STEP_AND_OBSERVE",
                "response_ready_tracking_event_id": str(
                    response_ready_tracking_id or ""
                ),
                "source_candidate_status": str(hold_source.status),
                "min_hold_seconds_source": "TRAJECTORY_PLANNER_CONFIG",
                "min_hold_seconds": float(
                    self.trajectory_planner.config.min_hold_seconds
                ),
            }
        )
        return payload

    def _record_residual_update_permission_result(
        self,
        *,
        timestamp: Any,
        permission: Dict[str, Any],
        previous_residual: float,
        new_residual: float,
    ) -> None:
        if not bool(permission.get("allowed", False)):
            return
        self.residual_decision_gate.record_residual_change(
            timestamp=timestamp,
            previous_residual=previous_residual,
            new_residual=new_residual,
        )

    def process_cycle(self, **kwargs: Any) -> Scheme2RuntimeCycleResult:
        key = (
            str(kwargs.get("condition_snapshot_version") or ""),
            str(kwargs.get("mfac_context_id") or ""),
        )
        if key != self._trajectory_context_key:
            self.pending_dose_guard.reset("MFAC_CONTEXT_CHANGE")
            self.trajectory_planner.reset()
            self.residual_decision_gate.reset("MFAC_CONTEXT_CHANGE")
            self._pending_for_current_cycle = None
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
        pending = self._pending_for_current_cycle
        if pending is None:
            raise RuntimeError("pending pH arbitration context was not produced")

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
                "pending_used_by_ph_arbitration": True,
                "trajectory_plan": plan.to_dict(),
                "residual_decision_gate": dict(
                    result.metadata.get("residual_decision_permission") or {}
                ),
                "algorithm_target_replaced_by_trajectory_planner": False,
                "trajectory_planner_dcs_write_enabled": False,
                "dose_debt_semantics": False,
                "historical_sensitivity_mapping": (
                    self._last_historical_mapping.to_dict()
                    if self._last_historical_mapping is not None
                    else None
                ),
                "historical_prior_map_source": self._historical_map_source,
                "historical_prior_map_snapshot_loaded": (
                    self._historical_map_snapshot_loaded
                ),
                "historical_runtime_prior_policy": "REVIEWED_SCALAR_ONLY",
                "historical_prior_replaces_qbase": False,
                "historical_prior_enables_learning": False,
                "historical_prior_enables_residual": False,
                "historical_prior_enables_dcs_write": False,
                "offline_online_lifecycle": (
                    "7DAY_OFFLINE_VERSION_REFRESH_PLUS_EVENT_DRIVEN_ONLINE_ADAPTATION"
                ),
                "runtime_state_handoff_policy": (
                    "EXACT_STATE_THEN_SAME_CONTEXT_GRID_THEN_REVIEWED_PRIOR"
                ),
                "cross_snapshot_runtime_state_handoff": (
                    dict(self._last_cross_snapshot_handoff)
                    if self._last_cross_snapshot_handoff is not None
                    else None
                ),
                "cross_snapshot_residual_reused": False,
            }
        )
        result.metadata = metadata
        return result


__all__ = [
    "TRAJECTORY_SHADOW_COORDINATOR_VERSION",
    "Scheme2TrajectoryShadowCoordinator",
]
# -*- coding: utf-8 -*-
"""Shadow-only runtime orchestration for Scheme 2 MFAC.

The coordinator is deliberately a sidecar.  It accepts DCS application and
feedback evidence supplied by the caller, but it has no DCS write API.  The
algorithm target is always produced by ``ContinuousTargetPublisher`` from
``qbase_effective + residual_mfac_hold``; actual flow is only forwarded to the
execution and response monitors.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import threading
from typing import Any, Dict, List, Optional, Tuple

from .continuous_target import (
    ONLINE_SHADOW,
    ContinuousTargetConfig,
    ContinuousTargetDecision,
    ContinuousTargetPublisher,
)
from .mfac_eligibility import MFACEligibilityConfig, StrictMFACEligibilityGate
from .mfac_schema import ActionResponseEvent, MFACRuntimeState
from .online_adaptation import (
    MFACOnlineAdaptationConfig,
    MFACOnlineAdaptationResult,
    MFACOnlineAdapter,
)
from .online_event_adapter import OnlineResponseToMFACAdapter
from .process_response import (
    ProcessResponseConfig,
    ProcessResponseEvent,
    ProcessResponseMonitor,
)
from .residual_control import (
    MFACResidualConfig,
    MFACResidualController,
    MFACResidualDecision,
    MFACResidualHoldDecision,
    MFACResidualHoldManager,
)
from .runtime_store import Scheme2RuntimeStore
from .supply_flow_tracking import (
    SupplyFlowTrackingConfig,
    SupplyFlowTrackingEvent,
    SupplyFlowTrackingMonitor,
)


SCHEME2_RUNTIME_COORDINATOR_VERSION = "SCHEME2_RUNTIME_COORDINATOR_V1"


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


@dataclass(frozen=True)
class Scheme2RuntimeCoordinatorConfig:
    """Explicit runtime settings; plant timing values have no defaults."""

    tracking: SupplyFlowTrackingConfig
    response: ProcessResponseConfig
    online_adaptation: MFACOnlineAdaptationConfig
    residual: MFACResidualConfig
    continuous_target: ContinuousTargetConfig = field(
        default_factory=ContinuousTargetConfig
    )
    eligibility: MFACEligibilityConfig = field(
        default_factory=MFACEligibilityConfig
    )
    learning_enabled: bool = False
    residual_control_enabled: bool = False
    persist_runtime: bool = True


@dataclass
class Scheme2RuntimeCycleResult:
    timestamp: str
    algorithm_target: ContinuousTargetDecision
    tracking_events: List[SupplyFlowTrackingEvent]
    active_tracking_event: Optional[SupplyFlowTrackingEvent]
    response_events: List[ProcessResponseEvent]
    active_response_tracking_event_id: str
    action_response_events: List[ActionResponseEvent]
    adaptation_results: List[MFACOnlineAdaptationResult]
    residual_decision: MFACResidualDecision
    residual_hold: MFACResidualHoldDecision
    runtime_state: Optional[MFACRuntimeState]
    learning_enabled: bool
    residual_control_enabled: bool
    dcs_write_enabled: bool = False
    persistence_status: str = "NOT_SAVED"
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = SCHEME2_RUNTIME_COORDINATOR_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "algorithm_target": self.algorithm_target.to_dict(),
            "tracking_events": [event.to_dict() for event in self.tracking_events],
            "active_tracking_event": (
                self.active_tracking_event.to_dict()
                if self.active_tracking_event is not None
                else None
            ),
            "response_events": [event.to_dict() for event in self.response_events],
            "active_response_tracking_event_id": (
                self.active_response_tracking_event_id
            ),
            "action_response_events": [
                event.to_dict() for event in self.action_response_events
            ],
            "adaptation_results": [
                result.to_dict() for result in self.adaptation_results
            ],
            "residual_decision": self.residual_decision.to_dict(),
            "residual_hold": self.residual_hold.to_dict(),
            "runtime_state": (
                self.runtime_state.to_dict()
                if self.runtime_state is not None
                else None
            ),
            "learning_enabled": self.learning_enabled,
            "residual_control_enabled": self.residual_control_enabled,
            "dcs_write_enabled": self.dcs_write_enabled,
            "persistence_status": self.persistence_status,
            "metadata": dict(self.metadata),
            "semantics_version": self.semantics_version,
        }


class Scheme2RuntimeCoordinator:
    """Run the complete Scheme-2 observation/adaptation chain once per sample.

    Production integration is expected to construct this class with both
    ``learning_enabled`` and ``residual_control_enabled`` false until formal
    calibration and activation approval.  DCS writing cannot be enabled on
    this class: ``dcs_write_enabled`` is a constant false capability marker.
    """

    dcs_write_enabled = False

    def __init__(
        self,
        config: Scheme2RuntimeCoordinatorConfig,
        runtime_store: Scheme2RuntimeStore,
        *,
        runtime_state: Optional[MFACRuntimeState] = None,
        initial_residual_mfac_hold: float = 0.0,
        startup_setpoint_target: Optional[float] = None,
    ) -> None:
        self.config = config
        self.runtime_store = runtime_store
        self.target_publisher = ContinuousTargetPublisher(
            config.continuous_target,
            startup_setpoint_target=startup_setpoint_target,
        )
        self.tracking_monitor = SupplyFlowTrackingMonitor(config.tracking)
        self.response_monitor = ProcessResponseMonitor(config.response)
        self.eligibility_gate = StrictMFACEligibilityGate(config.eligibility)
        self.event_adapter = OnlineResponseToMFACAdapter(self.eligibility_gate)
        self.online_adapter = MFACOnlineAdapter(config.online_adaptation)
        self.residual_controller = MFACResidualController(config.residual)

        initial_residual = _finite(initial_residual_mfac_hold)
        if initial_residual is None:
            raise ValueError("initial_residual_mfac_hold must be finite")
        if not config.residual_control_enabled:
            initial_residual = 0.0
        self.residual_hold_manager = MFACResidualHoldManager(initial_residual)
        self.runtime_state = runtime_state
        self._active_context_key: Optional[Tuple[str, str]] = None
        if runtime_state is not None:
            self._active_context_key = (
                runtime_state.condition_snapshot_version,
                runtime_state.mfac_context_id,
            )
        self._lock = threading.RLock()
        self._restore_warning = ""
        persisted_target = runtime_store.last_valid_algorithm_target
        if persisted_target is not None:
            try:
                self.target_publisher.restore_last_valid_algorithm_target(
                    persisted_target
                )
            except ValueError as exc:
                self._restore_warning = "TARGET_RESTORE_REJECTED:%s" % exc

    @property
    def residual_mfac_hold(self) -> float:
        if not self.config.residual_control_enabled:
            return 0.0
        return self.residual_hold_manager.held_residual

    def set_runtime_state(
        self,
        runtime_state: MFACRuntimeState,
        *,
        residual_mfac_hold: float = 0.0,
    ) -> None:
        """Install an explicitly bootstrapped/restored context state."""
        residual = _finite(residual_mfac_hold)
        if residual is None:
            raise ValueError("residual_mfac_hold must be finite")
        with self._lock:
            self.runtime_state = runtime_state
            self._active_context_key = (
                runtime_state.condition_snapshot_version,
                runtime_state.mfac_context_id,
            )
            self.residual_hold_manager = MFACResidualHoldManager(
                residual if self.config.residual_control_enabled else 0.0
            )

    def process_cycle(
        self,
        *,
        timestamp: Any,
        qbase_effective: Any,
        qbase_inputs_valid: bool,
        outlet_so2: Any,
        so2_target: Any,
        condition_snapshot_version: str,
        mfac_context_id: str,
        condition_label: str,
        base_condition_id: str,
        grid_id: str = "",
        policy_region_id: str = "",
        inlet_so2: Any = None,
        ph: Any = None,
        actual_supply_flow_feedback: Any = None,
        target_was_applied: bool = False,
        dcs_applied_target_supply_flow: Any = None,
        replay_semantics: str = ONLINE_SHADOW,
        fast_active: bool = False,
        data_quality_ok: bool = True,
        equipment_changed: bool = False,
    ) -> Scheme2RuntimeCycleResult:
        """Advance all stages without issuing or attempting a DCS command."""
        timestamp_text = str(timestamp or "")
        snapshot = str(condition_snapshot_version or "")
        context_id = str(mfac_context_id or "")

        with self._lock:
            context_status = self._select_context(snapshot, context_id)
            residual_for_target = self.residual_mfac_hold
            target = self.target_publisher.publish(
                qbase_effective,
                residual_for_target,
                inputs_valid=bool(qbase_inputs_valid),
                timestamp=timestamp_text,
                replay_semantics=replay_semantics,
            )

            tracking = self.tracking_monitor.update(
                timestamp=timestamp,
                algorithm_target_supply_flow=(
                    target.algorithm_target_supply_flow
                ),
                algorithm_target_valid=target.algorithm_target_valid,
                target_was_applied=bool(target_was_applied),
                dcs_applied_target_supply_flow=dcs_applied_target_supply_flow,
                actual_supply_flow_feedback=actual_supply_flow_feedback,
                replay_semantics=replay_semantics,
            )
            reached = next(
                (
                    event
                    for event in tracking.emitted_events
                    if event.status == "REACHED"
                ),
                None,
            )

            response = self.response_monitor.update(
                timestamp=timestamp,
                outlet_so2=outlet_so2,
                inlet_so2=inlet_so2,
                qbase_effective=(
                    qbase_effective if bool(qbase_inputs_valid) else None
                ),
                ph=ph,
                so2_target=so2_target,
                actual_supply_flow_feedback=actual_supply_flow_feedback,
                condition_snapshot_version=snapshot,
                mfac_context_id=context_id,
                fast_active=bool(fast_active),
                data_quality_ok=bool(data_quality_ok),
                reached_event=reached,
            )

            actions: List[ActionResponseEvent] = []
            adaptations: List[MFACOnlineAdaptationResult] = []
            for response_event in response.emitted_events:
                action = self.event_adapter.adapt(
                    response_event,
                    condition_label=condition_label,
                    base_condition_id=base_condition_id,
                    grid_id=grid_id,
                    policy_region_id=policy_region_id,
                    equipment_changed=bool(equipment_changed),
                    extra_metadata={
                        "coordinator_semantics_version": (
                            SCHEME2_RUNTIME_COORDINATOR_VERSION
                        ),
                        "learning_enabled": bool(self.config.learning_enabled),
                    },
                )
                actions.append(action)
                adaptation = self._adapt(action)
                adaptations.append(adaptation)
                if adaptation.updated and adaptation.runtime_state is not None:
                    self.runtime_state = adaptation.runtime_state

            residual_decision = self.residual_controller.compute(
                so2_target=so2_target,
                outlet_so2=outlet_so2,
                state=self.runtime_state,
                control_enabled=bool(self.config.residual_control_enabled),
            )
            response_ready = any(
                event.status == "COMPLETED" for event in response.emitted_events
            )
            residual_hold = self.residual_hold_manager.update(
                residual_decision,
                allow_update=(
                    bool(self.config.residual_control_enabled)
                    and response_ready
                ),
                reset=not bool(self.config.residual_control_enabled),
            )

            persistence_status = self._persist()
            metadata = {
                "context_status": context_status,
                "replay_semantics": str(replay_semantics or ONLINE_SHADOW),
                "response_ready_for_residual": response_ready,
                "qbase_inputs_valid": bool(qbase_inputs_valid),
                "actual_flow_used_as_algorithm_target": False,
                "target_formula": "clip(qbase_effective + residual_mfac_hold)",
                "restore_warning": self._restore_warning,
            }
            return Scheme2RuntimeCycleResult(
                timestamp=timestamp_text,
                algorithm_target=target,
                tracking_events=list(tracking.emitted_events),
                active_tracking_event=tracking.active_event,
                response_events=list(response.emitted_events),
                active_response_tracking_event_id=(
                    response.active_tracking_event_id
                ),
                action_response_events=actions,
                adaptation_results=adaptations,
                residual_decision=residual_decision,
                residual_hold=residual_hold,
                runtime_state=self.runtime_state,
                learning_enabled=bool(self.config.learning_enabled),
                residual_control_enabled=bool(
                    self.config.residual_control_enabled
                ),
                dcs_write_enabled=False,
                persistence_status=persistence_status,
                metadata=metadata,
            )

    def _select_context(self, snapshot: str, context_id: str) -> str:
        key = (snapshot, context_id)
        if not snapshot or not context_id:
            if self._active_context_key is not None:
                self.runtime_state = None
                self.residual_hold_manager = MFACResidualHoldManager(0.0)
                self._active_context_key = None
            return "CONTEXT_UNAVAILABLE"
        if self._active_context_key == key:
            return "CONTEXT_ACTIVE"

        restored = self.runtime_store.restore_context(
            condition_snapshot_version=snapshot,
            mfac_context_id=context_id,
        )
        self._active_context_key = key
        if restored.restored and restored.runtime_state is not None:
            self.runtime_state = restored.runtime_state
            residual = (
                restored.residual_mfac_hold
                if self.config.residual_control_enabled
                else 0.0
            )
            self.residual_hold_manager = MFACResidualHoldManager(residual)
            return "CONTEXT_RESTORED"

        self.runtime_state = None
        self.residual_hold_manager = MFACResidualHoldManager(0.0)
        return "CONTEXT_SAFE_EMPTY:%s" % restored.reason

    def _adapt(self, event: ActionResponseEvent) -> MFACOnlineAdaptationResult:
        old_phi = (
            _finite(self.runtime_state.phi_live)
            if self.runtime_state is not None
            else None
        )
        if not self.config.learning_enabled:
            return MFACOnlineAdaptationResult(
                updated=False,
                reason="LEARNING_DISABLED",
                old_phi=old_phi,
                new_phi=old_phi,
                event_id=event.event_id,
                runtime_state=self.runtime_state,
            )
        if self.runtime_state is None:
            return MFACOnlineAdaptationResult(
                updated=False,
                reason="NO_RUNTIME_STATE",
                old_phi=None,
                new_phi=None,
                event_id=event.event_id,
                runtime_state=None,
            )
        return self.online_adapter.update(self.runtime_state, event)

    def _persist(self) -> str:
        if not self.config.persist_runtime:
            return "PERSISTENCE_DISABLED"
        last_target = self.target_publisher.last_valid_algorithm_target
        if last_target is not None:
            self.runtime_store.set_last_valid_algorithm_target(last_target)
        if self.runtime_state is not None:
            self.runtime_store.upsert_context(
                self.runtime_state,
                residual_mfac_hold=self.residual_mfac_hold,
            )
        self.runtime_store.save()
        return "SAVED"

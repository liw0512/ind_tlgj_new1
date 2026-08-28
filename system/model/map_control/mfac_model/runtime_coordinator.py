# -*- coding: utf-8 -*-
"""Shadow-only runtime orchestration for Scheme 2 dual-response MFAC.

SO2 remains the only control-producing MFAC channel.  pH is observed and
learned independently after the same real flow-reach event, then only
arbitrates the SO2 residual by PASS/SCALE/BLOCK.  Arbitration acts on the
increment from the already-held residual to the newly desired residual.  A
protected hook allows the trajectory sidecar to supply pending pH extrema
without creating a second residual-control path.

The coordinator has no DCS write API and therefore remains a fail-closed Shadow
sidecar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import threading
from typing import Any, Dict, List, Optional, Set, Tuple

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
from .ph_adaptation import (
    PHOnlineAdaptationConfig,
    PHOnlineAdaptationResult,
    PHOnlineAdapter,
)
from .ph_arbitration import (
    PHResidualArbitrationConfig,
    PHResidualArbitrationDecision,
    PHResidualArbiter,
)
from .ph_response import (
    PHResponseConfig,
    PHResponseEvent,
    PHResponseMonitor,
)
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


SCHEME2_RUNTIME_COORDINATOR_VERSION = (
    "SCHEME2_RUNTIME_COORDINATOR_V3_INCREMENTAL_PH_ARBITRATION"
)


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


@dataclass(frozen=True)
class Scheme2RuntimeCoordinatorConfig:
    """Explicit runtime settings; plant timing values have no implicit defaults."""

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
    ph_response: Optional[PHResponseConfig] = None
    ph_online_adaptation: Optional[PHOnlineAdaptationConfig] = None
    ph_arbitration: Optional[PHResidualArbitrationConfig] = None
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
    ph_response_events: List[PHResponseEvent] = field(default_factory=list)
    active_ph_response_tracking_event_id: str = ""
    ph_adaptation_results: List[PHOnlineAdaptationResult] = field(default_factory=list)
    ph_arbitration: Optional[PHResidualArbitrationDecision] = None
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
            "active_response_tracking_event_id": self.active_response_tracking_event_id,
            "ph_response_events": [
                event.to_dict() for event in self.ph_response_events
            ],
            "active_ph_response_tracking_event_id": (
                self.active_ph_response_tracking_event_id
            ),
            "action_response_events": [
                event.to_dict() for event in self.action_response_events
            ],
            "adaptation_results": [
                result.to_dict() for result in self.adaptation_results
            ],
            "ph_adaptation_results": [
                result.to_dict() for result in self.ph_adaptation_results
            ],
            "residual_decision": self.residual_decision.to_dict(),
            "ph_arbitration": (
                self.ph_arbitration.to_dict()
                if self.ph_arbitration is not None
                else None
            ),
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
    """Run SO2-led dual-response MFAC once per process sample."""

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

        if config.ph_online_adaptation is not None and config.ph_response is None:
            raise ValueError("ph_online_adaptation requires ph_response")
        self.ph_response_monitor = (
            PHResponseMonitor(config.ph_response)
            if config.ph_response is not None
            else None
        )
        self.ph_online_adapter = (
            PHOnlineAdapter(config.ph_online_adaptation)
            if config.ph_online_adaptation is not None
            else None
        )
        self.ph_arbiter = (
            PHResidualArbiter(config.ph_arbitration)
            if config.ph_arbitration is not None
            else None
        )

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
        self._dual_response_status: Dict[str, Dict[str, str]] = {}
        self._dual_response_consumed: Set[str] = set()

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
            self._dual_response_status.clear()
            self._dual_response_consumed.clear()

    def _ph_arbitration_context(
        self,
        *,
        timestamp: Any,
        actual_supply_flow_feedback: Any,
        ph_value: Any,
        state: Optional[MFACRuntimeState],
        data_quality_ok: bool,
    ) -> Dict[str, Any]:
        """Return extra pH-arbitration context without creating another controller.

        Base coordinator owns no pending-response model, so it supplies only the
        currently held residual.  The trajectory subclass overrides this hook
        to add PendingDoseGuard future pH extrema for the same cycle.
        """
        del timestamp, actual_supply_flow_feedback, ph_value, state, data_quality_ok
        return {
            "held_residual": float(self.residual_hold_manager.held_residual),
            "pending_predicted_ph_upper": None,
            "pending_predicted_ph_lower": None,
            "pending_source": "NONE",
        }

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
                algorithm_target_supply_flow=target.algorithm_target_supply_flow,
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

            ph_response_events: List[PHResponseEvent] = []
            active_ph_response_tracking_event_id = ""
            if self.ph_response_monitor is not None:
                ph_response = self.ph_response_monitor.update(
                    timestamp=timestamp,
                    ph=ph,
                    qbase_effective=(
                        qbase_effective if bool(qbase_inputs_valid) else None
                    ),
                    so2_target=so2_target,
                    actual_supply_flow_feedback=actual_supply_flow_feedback,
                    condition_snapshot_version=snapshot,
                    mfac_context_id=context_id,
                    fast_active=bool(fast_active),
                    data_quality_ok=bool(data_quality_ok),
                    reached_event=reached,
                )
                ph_response_events = list(ph_response.emitted_events)
                active_ph_response_tracking_event_id = (
                    ph_response.active_tracking_event_id
                )

            actions: List[ActionResponseEvent] = []
            adaptations: List[MFACOnlineAdaptationResult] = []
            for response_event in response.emitted_events:
                self._record_response_status(
                    response_event.tracking_event_id,
                    "so2",
                    response_event.status,
                )
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
                        "response_channel": "SO2",
                    },
                )
                actions.append(action)
                adaptation = self._adapt(action)
                adaptations.append(adaptation)
                if adaptation.updated and adaptation.runtime_state is not None:
                    self.runtime_state = adaptation.runtime_state

            ph_adaptations: List[PHOnlineAdaptationResult] = []
            for ph_event in ph_response_events:
                self._record_response_status(
                    ph_event.tracking_event_id,
                    "ph",
                    ph_event.status,
                )
                adaptation = self._adapt_ph(ph_event)
                ph_adaptations.append(adaptation)
                if adaptation.updated and adaptation.runtime_state is not None:
                    self.runtime_state = adaptation.runtime_state

            residual_decision = self.residual_controller.compute(
                so2_target=so2_target,
                outlet_so2=outlet_so2,
                state=self.runtime_state,
                control_enabled=bool(self.config.residual_control_enabled),
            )

            ph_arbitration_context = self._ph_arbitration_context(
                timestamp=timestamp,
                actual_supply_flow_feedback=actual_supply_flow_feedback,
                ph_value=ph,
                state=self.runtime_state,
                data_quality_ok=bool(data_quality_ok),
            )
            ph_arbitration: Optional[PHResidualArbitrationDecision] = None
            hold_source = residual_decision
            if self.ph_arbiter is not None:
                ph_arbitration = self.ph_arbiter.arbitrate(
                    ph_value=ph,
                    state=self.runtime_state,
                    so2_residual=residual_decision,
                    arbitration_enabled=bool(
                        self.config.residual_control_enabled
                    ),
                    held_residual=ph_arbitration_context.get(
                        "held_residual",
                        self.residual_hold_manager.held_residual,
                    ),
                    pending_predicted_ph_upper=ph_arbitration_context.get(
                        "pending_predicted_ph_upper"
                    ),
                    pending_predicted_ph_lower=ph_arbitration_context.get(
                        "pending_predicted_ph_lower"
                    ),
                )
                if residual_decision.status == "CALCULATED":
                    metadata = dict(residual_decision.metadata or {})
                    metadata["ph_arbitration"] = ph_arbitration.to_dict()
                    hold_source = MFACResidualDecision(
                        status="CALCULATED",
                        candidate_residual=ph_arbitration.final_residual,
                        so2_error=residual_decision.so2_error,
                        phi_live=residual_decision.phi_live,
                        confidence_live=residual_decision.confidence_live,
                        hard_clipped=residual_decision.hard_clipped,
                        metadata=metadata,
                    )

            response_ready, response_ready_tracking_id = self._response_ready(
                list(response.emitted_events),
                ph_response_events,
            )
            residual_hold = self.residual_hold_manager.update(
                hold_source,
                allow_update=(
                    bool(self.config.residual_control_enabled)
                    and response_ready
                ),
                reset=not bool(self.config.residual_control_enabled),
            )
            if response_ready_tracking_id:
                self._dual_response_consumed.add(response_ready_tracking_id)

            persistence_status = self._persist()
            metadata = {
                "context_status": context_status,
                "replay_semantics": str(replay_semantics or ONLINE_SHADOW),
                "response_ready_for_residual": response_ready,
                "response_ready_tracking_event_id": response_ready_tracking_id,
                "dual_response_enabled": self.ph_response_monitor is not None,
                "qbase_inputs_valid": bool(qbase_inputs_valid),
                "actual_flow_used_as_algorithm_target": False,
                "target_formula": "clip(qbase_effective + residual_mfac_hold)",
                "residual_semantics": "SO2_LED_INCREMENTAL_PH_ARBITRATED",
                "ph_arbitration_context": dict(ph_arbitration_context),
                "additive_ph_residual": False,
                "restore_warning": self._restore_warning,
            }
            return Scheme2RuntimeCycleResult(
                timestamp=timestamp_text,
                algorithm_target=target,
                tracking_events=list(tracking.emitted_events),
                active_tracking_event=tracking.active_event,
                response_events=list(response.emitted_events),
                active_response_tracking_event_id=response.active_tracking_event_id,
                action_response_events=actions,
                adaptation_results=adaptations,
                residual_decision=residual_decision,
                residual_hold=residual_hold,
                runtime_state=self.runtime_state,
                learning_enabled=bool(self.config.learning_enabled),
                residual_control_enabled=bool(self.config.residual_control_enabled),
                dcs_write_enabled=False,
                persistence_status=persistence_status,
                metadata=metadata,
                ph_response_events=ph_response_events,
                active_ph_response_tracking_event_id=(
                    active_ph_response_tracking_event_id
                ),
                ph_adaptation_results=ph_adaptations,
                ph_arbitration=ph_arbitration,
            )

    def _record_response_status(
        self,
        tracking_event_id: str,
        channel: str,
        status: str,
    ) -> None:
        if not tracking_event_id:
            return
        value = self._dual_response_status.setdefault(tracking_event_id, {})
        value[str(channel)] = str(status)

    @staticmethod
    def _terminal_response_status(status: str) -> bool:
        return str(status) in {
            "COMPLETED",
            "CENSORED",
            "INSUFFICIENT_BASELINE",
            "INSUFFICIENT_RESPONSE_DATA",
        }

    def _response_ready(
        self,
        so2_events: List[ProcessResponseEvent],
        ph_events: List[PHResponseEvent],
    ) -> Tuple[bool, str]:
        if self.ph_response_monitor is None:
            for event in so2_events:
                if event.status == "COMPLETED":
                    return True, event.tracking_event_id
            return False, ""

        touched = {
            event.tracking_event_id for event in so2_events + ph_events
            if event.tracking_event_id
        }
        for tracking_id in touched:
            if tracking_id in self._dual_response_consumed:
                continue
            status = self._dual_response_status.get(tracking_id, {})
            so2_status = status.get("so2", "")
            ph_status = status.get("ph", "")
            if (
                so2_status == "COMPLETED"
                and self._terminal_response_status(ph_status)
            ):
                return True, tracking_id
        return False, ""

    def _select_context(self, snapshot: str, context_id: str) -> str:
        key = (snapshot, context_id)
        if not snapshot or not context_id:
            if self._active_context_key is not None:
                self.runtime_state = None
                self.residual_hold_manager = MFACResidualHoldManager(0.0)
                self._active_context_key = None
                self._dual_response_status.clear()
                self._dual_response_consumed.clear()
            return "CONTEXT_UNAVAILABLE"
        if self._active_context_key == key:
            return "CONTEXT_ACTIVE"

        restored = self.runtime_store.restore_context(
            condition_snapshot_version=snapshot,
            mfac_context_id=context_id,
        )
        self._active_context_key = key
        self._dual_response_status.clear()
        self._dual_response_consumed.clear()
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

    def _adapt_ph(self, event: PHResponseEvent) -> PHOnlineAdaptationResult:
        old_phi = (
            _finite(self.runtime_state.phi_ph_live)
            if self.runtime_state is not None
            else None
        )
        if not self.config.learning_enabled:
            return PHOnlineAdaptationResult(
                updated=False,
                reason="LEARNING_DISABLED",
                old_phi=old_phi,
                new_phi=old_phi,
                event_id=event.response_event_id,
                runtime_state=self.runtime_state,
            )
        if self.runtime_state is None:
            return PHOnlineAdaptationResult(
                updated=False,
                reason="NO_RUNTIME_STATE",
                old_phi=None,
                new_phi=None,
                event_id=event.response_event_id,
                runtime_state=None,
            )
        if self.ph_online_adapter is None:
            return PHOnlineAdaptationResult(
                updated=False,
                reason="PH_ADAPTATION_NOT_CONFIGURED",
                old_phi=old_phi,
                new_phi=old_phi,
                event_id=event.response_event_id,
                runtime_state=self.runtime_state,
            )
        return self.ph_online_adapter.update(self.runtime_state, event)

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

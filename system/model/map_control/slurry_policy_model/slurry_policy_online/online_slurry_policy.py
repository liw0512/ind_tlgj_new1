from __future__ import annotations

from system.model.config.standard_fields import TIME_COLUMN

import copy
import threading
import uuid
from typing import Any, Dict, List, Optional

import pandas as pd

try:
    from _engine.config_loader import enabled_towers
    from _engine.schema import OUTLET_SO2_COLUMN, condition_axis_columns
    from _engine.utils import normalize_condition_label
except ImportError:  # pragma: no cover
    from .._engine.config_loader import enabled_towers
    from .._engine.schema import OUTLET_SO2_COLUMN, condition_axis_columns
    from .._engine.utils import normalize_condition_label

from .config_loader import load_online_config
from .control_output_selector import ControlOutputSelector
from .decision_state_machine import DecisionStateMachine
from .demand_analyzer import analyze_demand
from .fast_action_envelope import apply_fast_action_envelope, build_fast_action_envelope
from .fast_context_adapter import FastContextError, extract_fast_context
from .policy_snapshot_loader import PolicySnapshotError, PolicySnapshotLoader
from .realtime_state_builder import RealtimeDataError, RealtimeStateBuilder
from .runtime_store import RuntimeStore
from .supply_flow_advisor import (
    SupplyFlowAdvisor,
    unavailable_flow_recommendation,
)
from .supply_flow_state_machine import SupplyFlowStateMachine
from .target_control import TargetError, TargetManager
from .target_flow_execution_adapter import DryRunTargetFlowExecutionAdapter
from .types import ConditionContext, ControlDemand, Decision, RealtimeState


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "t"}


def _timestamp(value: Any) -> pd.Timestamp:
    ts = pd.Timestamp(value) if value is not None else pd.Timestamp.now()
    if ts.tzinfo is not None:
        ts = ts.tz_localize(None)
    return ts


class OnlineSlurryPolicy:
    """第二模块在线推理统一入口。

    本类不直接写 DCS。evaluate() 只生成目标供浆流量推荐；现场执行适配器
    完成最终联锁、限幅和实际执行后，必须调用 record_execution() 回传结果。
    """

    def __init__(
        self,
        config_spec: Optional[str] = None,
        *,
        external_version_management: bool = False,
        active_pointer: Optional[Dict[str, Any]] = None,
        initial_runtime_state: Optional[Dict[str, Any]] = None,
    ) -> None:
        configured_plant, configured_training, online = load_online_config(config_spec)
        self.config_spec = config_spec
        self.configured_plant = configured_plant
        self.configured_training = configured_training
        self.online = online
        self.store = RuntimeStore(configured_plant, online)
        if initial_runtime_state is not None:
            self.store.state.clear()
            self.store.state.update(copy.deepcopy(dict(initial_runtime_state)))
        self.loader = PolicySnapshotLoader(configured_plant, online)
        self.lock = threading.RLock()
        self.external_version_management = bool(external_version_management)
        self._external_reload_pending = False
        self._components_ready = False
        self._last_reload_error: Optional[str] = None
        self._load_or_raise(active_pointer)

    def _load_or_raise(
        self,
        active_pointer: Optional[Dict[str, Any]] = None,
    ) -> None:
        if active_pointer is None:
            self.loader.load_active(force=True)
        else:
            self.loader.load_pointer(dict(active_pointer), force=True)
        self._rebuild_components()

    def _rebuild_components(self) -> None:
        plant = self.loader.plant_config
        training = self.loader.training_config
        self.plant = plant
        self.training = training
        self.state_builder = RealtimeStateBuilder(plant, training)
        self.target_manager = TargetManager(self.online, self.store.state)
        self.state_machine = DecisionStateMachine(
            self.online, training, self.store.state
        )
        self.output_selector = ControlOutputSelector(self.online)
        self.target_flow_executor = DryRunTargetFlowExecutionAdapter(plant)
        self.flow_advisor = SupplyFlowAdvisor(
            self.loader, plant, self.online
        )
        self.flow_state = SupplyFlowStateMachine(
            plant,
            training,
            self.online,
            self.store.state,
        )
        self._components_ready = True

    def _refresh_model(self) -> List[str]:
        reasons: List[str] = []
        if self.external_version_management:
            if self._external_reload_pending:
                self._external_reload_pending = False
                reasons.append("MODEL_VERSION_RELOADED")
            return reasons
        try:
            changed = self.loader.refresh_if_needed()
            if changed:
                self._rebuild_components()
                reasons.append("MODEL_VERSION_RELOADED")
            self._last_reload_error = None
        except Exception as exc:
            self._last_reload_error = str(exc)
            if self.loader.policy_version is None:
                raise
            reasons.append("MODEL_RELOAD_FAILED_USING_CURRENT_MEMORY_VERSION")
        return reasons

    def export_runtime_state(self) -> Dict[str, Any]:
        with self.lock:
            self.store.save()
            return copy.deepcopy(dict(self.store.state))

    def mark_external_reload(self) -> None:
        with self.lock:
            self._external_reload_pending = True

    def _normalize_input(
        self,
        realtime_data: Dict[str, Any],
        condition_result: Optional[Dict[str, Any]],
        target: Optional[Any],
        execution_context: Optional[Dict[str, Any]],
    ) -> Tuple[
        pd.Timestamp,
        Dict[str, Any],
        Dict[str, Any],
        Optional[float],
        Dict[str, Any],
    ]:
        if "process" in realtime_data:
            process = dict(realtime_data.get("process") or {})
            condition = dict(
                condition_result or realtime_data.get("condition") or {}
            )
            execution = dict(
                execution_context or realtime_data.get("execution") or {}
            )
            target_value = target
            if target_value is None:
                target_block = realtime_data.get("target")
                if isinstance(target_block, dict):
                    target_value = target_block.get("outlet_so2_target")
                elif target_block is not None:
                    target_value = target_block
            ts_value = realtime_data.get("timestamp") or process.get(
                TIME_COLUMN
            )
        else:
            process = dict(realtime_data)
            condition = dict(condition_result or realtime_data)
            execution = dict(execution_context or {})
            target_value = target
            ts_value = process.get("timestamp") or process.get(
                TIME_COLUMN
            )
        if isinstance(target_value, dict):
            target_value = target_value.get("outlet_so2_target")
        return (
            _timestamp(ts_value),
            process,
            condition,
            None if target_value is None else float(target_value),
            execution,
        )

    def _condition_context(self, value: Dict[str, Any]) -> ConditionContext:
        stable_label = value.get(
            "stable_condition_label", value.get("condition_label", "UNKNOWN")
        )
        return ConditionContext(
            condition_snapshot_version=str(
                value.get("condition_snapshot_version", "")
            ),
            condition_label=normalize_condition_label(stable_label),
            raw_grid_id=str(
                value.get("raw_grid_id", value.get("grid_id", "UNKNOWN"))
            ),
            raw_condition_label=normalize_condition_label(
                value.get("raw_condition_label", stable_label)
            ),
            condition_stable=_as_bool(value.get("condition_stable"), False),
            condition_switch_state=str(
                value.get("condition_switch_state", "UNKNOWN")
            ),
            condition_valid=_as_bool(value.get("condition_valid"), False),
            out_of_range_clipped=_as_bool(
                value.get("out_of_range_clipped"), False
            ),
            state_key=str(
                value.get("state_key", value.get("condition_state_key", ""))
                or ""
            ),
        )

    def _make_hold(
        self,
        timestamp: pd.Timestamp,
        condition: Optional[ConditionContext],
        process: Dict[str, Any],
        status: str,
        control_mode: str,
        disturbance_mode: str,
        reasons: List[str],
        demand: Optional[ControlDemand] = None,
        debug: Optional[Dict[str, Any]] = None,
        target_supply_flow: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        decision = Decision(
            decision_id="D-%s" % uuid.uuid4().hex[:16],
            timestamp=timestamp.isoformat(),
            model_version=self.loader.policy_version,
            condition_snapshot_version=(
                condition.condition_snapshot_version if condition else None
            ),
            condition_label=(condition.condition_label if condition else None),
            raw_grid_id=(condition.raw_grid_id if condition else None),
            control_mode=control_mode,
            disturbance_mode=disturbance_mode,
            current_so2=(
                float(process[OUTLET_SO2_COLUMN])
                if OUTLET_SO2_COLUMN in process
                else None
            ),
            commanded_target=(demand.commanded_target if demand else None),
            effective_target=(demand.effective_target if demand else None),
            desired_so2_response=(
                demand.desired_so2_response if demand else "UNKNOWN"
            ),
            experience_source="NONE",
            action_id="HOLD",
            action_family="HOLD",
            action_direction="HOLD",
            action_magnitude="HOLD",
            historical_reliability=None,
            historical_safety_score=None,
            historical_direction_consistency=None,
            decision_status=status,
            reason_codes=list(dict.fromkeys(reasons)),
            debug=debug or {},
        ).to_dict()
        decision["target_supply_flow"] = (
            target_supply_flow
            if target_supply_flow is not None
            else unavailable_flow_recommendation("FLOW_CONTEXT_UNAVAILABLE")
        )
        decision["control_recommendation"] = self.output_selector.select(
            decision,
            decision["target_supply_flow"],
            {},
        )
        decision["target_flow_execution_preview"] = (
            self.target_flow_executor.prepare(
                decision["decision_id"],
                decision["target_supply_flow"],
                process,
            )
        )
        self.store.append_decision(decision)
        self.store.save()
        return decision

    def _ph_reasons(self, state: RealtimeState) -> List[str]:
        reasons: List[str] = []
        for tower in enabled_towers(self.plant):
            ph = float(state.process[str(tower["ph_column"])])
            lo, hi = [float(x) for x in tower["ph_safe_range"]]
            if ph < lo:
                reasons.append("PH_BELOW_SAFE_RANGE:%s" % tower["tower_id"])
            elif ph > hi:
                reasons.append("PH_ABOVE_SAFE_RANGE:%s" % tower["tower_id"])
        return reasons

    def evaluate(
        self,
        realtime_data: Dict[str, Any],
        condition_result: Optional[Dict[str, Any]] = None,
        target: Optional[Any] = None,
        execution_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        with self.lock:
            reload_reasons = self._refresh_model()
            (
                timestamp,
                process,
                condition_raw,
                runtime_target,
                execution,
            ) = self._normalize_input(
                realtime_data, condition_result, target, execution_context
            )
            condition = self._condition_context(condition_raw)

            if not condition.condition_valid:
                return self._make_hold(
                    timestamp,
                    condition,
                    process,
                    "BLOCKED",
                    "BLOCKED",
                    "UNKNOWN",
                    reload_reasons + ["CONDITION_INVALID"],
                )
            if condition.condition_snapshot_version != self.loader.condition_version:
                return self._make_hold(
                    timestamp,
                    condition,
                    process,
                    "BLOCKED",
                    "BLOCKED",
                    "UNKNOWN",
                    reload_reasons + ["CONDITION_POLICY_VERSION_MISMATCH"],
                    debug={
                        "input_condition_version": condition.condition_snapshot_version,
                        "active_policy_version": self.loader.policy_version,
                    },
                )

            try:
                axes = condition_axis_columns(self.training)
                outlet = float(process[OUTLET_SO2_COLUMN])
                fast_context = extract_fast_context(process)
                state = self.state_builder.validate_and_build(
                    timestamp, process, condition, fast_context
                )
                (
                    commanded,
                    effective,
                    target_changed,
                    target_hold,
                ) = self.target_manager.resolve(runtime_target, timestamp)
                demand = analyze_demand(
                    outlet,
                    commanded,
                    effective,
                    target_changed,
                    self.plant,
                    self.online,
                )
                fast_envelope = build_fast_action_envelope(
                    fast_context, demand, self.online
                )
                demand = apply_fast_action_envelope(demand, fast_envelope)
            except (
                KeyError,
                TypeError,
                ValueError,
                RealtimeDataError,
                FastContextError,
                TargetError,
            ) as exc:
                return self._make_hold(
                    timestamp,
                    condition,
                    process,
                    "BLOCKED",
                    "BLOCKED",
                    "UNKNOWN",
                    reload_reasons + ["REALTIME_INPUT_INVALID", str(exc)],
                )

            self.state_machine.advance(timestamp)
            self.state_machine.notify_condition(
                condition.condition_label, condition.condition_switch_state
            )
            progressive_reasons = (
                self.state_machine.apply_progressive_magnitude_limit(demand)
            )
            common_reasons = (
                reload_reasons
                + list(fast_context.get("fast_change_reason_codes", []))
                + demand.reason_codes
                + progressive_reasons
                + self._ph_reasons(state)
            )
            flow_tracking = self.flow_state.advance(timestamp, process)
            target_supply_flow = self.flow_advisor.recommend(
                condition.condition_label,
                demand.acceptable_effect_directions,
                process,
            )
            target_supply_flow["tracking"] = flow_tracking

            if (
                not condition.condition_stable
                and state.control_mode != "FAST_CHANGE"
                and demand.safety_level != "EMERGENCY"
            ):
                return self._make_hold(
                    timestamp,
                    condition,
                    process,
                    "INITIALIZING",
                    state.control_mode,
                    state.disturbance_mode,
                    common_reasons + ["CONDITION_NOT_STABLE"],
                    demand,
                    target_supply_flow=target_supply_flow,
                )
            if not condition.condition_stable and state.control_mode == "FAST_CHANGE":
                common_reasons.append("FAST_PROTECTION_DURING_CONDITION_WARMUP")

            if (
                "MODEL_VERSION_RELOADED" in reload_reasons
                and int(
                    self.online["action_stability"].get(
                        "model_reload_hold_cycles", 1
                    )
                )
                > 0
                and demand.safety_level != "EMERGENCY"
            ):
                return self._make_hold(
                    timestamp,
                    condition,
                    process,
                    "HOLD",
                    "MODEL_TRANSITION",
                    state.disturbance_mode,
                    common_reasons + ["MODEL_RELOAD_HOLD"],
                    demand,
                    target_supply_flow=target_supply_flow,
                )

            if target_hold and demand.safety_level != "EMERGENCY":
                self.target_manager.consume_hold_cycle()
                if state.control_mode != "FAST_CHANGE":
                    return self._make_hold(
                        timestamp,
                        condition,
                        process,
                        "HOLD",
                        "TARGET_TRANSITION",
                        state.disturbance_mode,
                        common_reasons + ["TARGET_TRANSITION_HOLD"],
                        demand,
                        target_supply_flow=target_supply_flow,
                    )
                common_reasons.append("TARGET_TRANSITION_HOLD_BYPASSED_BY_FAST")
            if (
                self.state_machine.condition_hold_required()
                and demand.safety_level != "EMERGENCY"
            ):
                self.state_machine.consume_condition_hold()
                if state.control_mode != "FAST_CHANGE":
                    return self._make_hold(
                        timestamp,
                        condition,
                        process,
                        "HOLD",
                        "CONDITION_TRANSITION",
                        state.disturbance_mode,
                        common_reasons + ["CONDITION_JUST_SWITCHED"],
                        demand,
                        target_supply_flow=target_supply_flow,
                    )
                common_reasons.append("CONDITION_TRANSITION_HOLD_BYPASSED_BY_FAST")
            blocking_fast_context = dict(fast_context)
            blocking_fast_context["_allow_waiting_effect_risk_escalation"] = bool(
                self.online.get("fast_policy", {}).get(
                    "allow_waiting_effect_risk_escalation", True
                )
            )
            blocking_fast_context["_risk_escalation"] = bool(fast_envelope.risk_escalation)
            blocking_fast_context["_risk_escalation_minimum_action_interval_minutes"] = float(
                self.online.get("fast_policy", {}).get(
                    "risk_escalation_minimum_action_interval_minutes", 1.0
                )
            )
            blocking = self.state_machine.blocking_reasons(
                timestamp, demand.safety_level, blocking_fast_context
            )
            blocking.extend(self.flow_state.blocking_reasons())
            if blocking:
                return self._make_hold(
                    timestamp,
                    condition,
                    process,
                    "HOLD",
                    self.state_machine.state.get("state", "HOLD"),
                    state.disturbance_mode,
                    common_reasons + blocking,
                    demand,
                    target_supply_flow=target_supply_flow,
                )

            if not bool(target_supply_flow.get("available")):
                return self._make_hold(
                    timestamp,
                    condition,
                    process,
                    "HOLD",
                    state.control_mode,
                    state.disturbance_mode,
                    common_reasons
                    + list(target_supply_flow.get("reason_codes") or [])
                    + ["NO_ACCEPTED_FLOW_PROTOTYPE"],
                    demand,
                    debug={
                        "policy_state_key": state.policy_state_key_no_grid,
                    },
                    target_supply_flow=target_supply_flow,
                )

            evidence = target_supply_flow.get("evidence", {}) or {}
            reliability = evidence.get("reliability", {}) or {}
            expected_effect = target_supply_flow.get("expected_effect", {}) or {}
            tower_id = str(target_supply_flow.get("tower_id") or "UNKNOWN")
            flow_shape = str(target_supply_flow.get("flow_shape") or "UNKNOWN")
            prototype_id = str(target_supply_flow.get("prototype_id") or "")
            decision = Decision(
                decision_id="D-%s" % uuid.uuid4().hex[:16],
                timestamp=timestamp.isoformat(),
                model_version=self.loader.policy_version,
                condition_snapshot_version=condition.condition_snapshot_version,
                condition_label=condition.condition_label,
                raw_grid_id=condition.raw_grid_id,
                control_mode=state.control_mode,
                disturbance_mode=state.disturbance_mode,
                current_so2=outlet,
                commanded_target=demand.commanded_target,
                effective_target=demand.effective_target,
                desired_so2_response=demand.desired_so2_response,
                experience_source="SUPPLY_FLOW_PROTOTYPE",
                action_id=prototype_id,
                action_family="TOWER:%s|SUPPLY_FLOW" % tower_id,
                action_direction=str(target_supply_flow.get("action_direction")),
                action_magnitude=flow_shape,
                historical_reliability=float(reliability.get("total_score", 0.0)),
                historical_safety_score=float(
                    reliability.get("safety_history_score", 0.0)
                ),
                historical_direction_consistency=float(
                    expected_effect.get("outlet_so2_direction_consistency", 0.0)
                ),
                decision_status="RECOMMENDED",
                reason_codes=list(
                    dict.fromkeys(
                        common_reasons
                        + [
                            "EXPERIENCE_SOURCE:SUPPLY_FLOW_PROTOTYPE",
                            "FLOW_PROTOTYPE_SELECTED",
                        ]
                    )
                ),
                debug={
                    "policy_state_key": state.policy_state_key,
                    "policy_state_key_no_grid": state.policy_state_key_no_grid,
                    "condition_axis_columns": list(axes),
                    "condition_axis_1_rate": state.load_rate,
                    "condition_axis_2_rate": state.inlet_so2_rate,
                    "outlet_so2_rate": state.outlet_so2_rate,
                    "demand_level": demand.demand_level,
                    "fast_action_envelope": fast_envelope.to_dict(),
                    "flow_prototype_id": prototype_id,
                    "flow_shape": flow_shape,
                    "automatic_control_allowed": _as_bool(
                        execution.get("automatic_control_allowed"), False
                    ),
                    "model_reload_error": self._last_reload_error,
                },
            ).to_dict()
            decision["target_supply_flow"] = target_supply_flow
            decision["control_recommendation"] = self.output_selector.select(
                decision,
                target_supply_flow,
                execution,
            )
            decision["target_flow_execution_preview"] = (
                self.target_flow_executor.prepare(
                    decision["decision_id"],
                    target_supply_flow,
                    process,
                )
            )
            primary_type = str(
                ((decision["control_recommendation"].get("primary") or {}).get(
                    "recommendation_type", "HOLD"
                ))
            )
            if primary_type == "TARGET_SUPPLY_FLOW":
                self.flow_state.record_recommendation(decision)
            self.store.append_decision(decision)
            self.store.save()
            return decision

    def record_execution(self, feedback: Dict[str, Any]) -> Dict[str, Any]:
        with self.lock:
            explicit_type = str(
                feedback.get("recommendation_type") or "TARGET_SUPPLY_FLOW"
            ).upper()
            if explicit_type != "TARGET_SUPPLY_FLOW":
                raise ValueError(
                    "第二模块只接受 TARGET_SUPPLY_FLOW 执行反馈，收到: %s"
                    % explicit_type
                )
            record = self.flow_state.record_execution(feedback)
            record["target_supply_flow_tracking"] = record.get(
                "target_flow_tracking",
                self.flow_state.public_state(),
            )
            record["model_version"] = self.loader.policy_version
            self.store.append_execution(record)
            self.store.save()
            return record

    def status(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "model_version": self.loader.policy_version,
                "condition_snapshot_version": self.loader.condition_version,
                "snapshot_dir": (
                    str(self.loader.snapshot_dir)
                    if self.loader.snapshot_dir
                    else None
                ),
                "decision_state": dict(self.state_machine.state),
                "supply_flow_state": dict(
                    self.flow_state.public_state()
                ),
                "requested_output_mode": self.output_selector.requested_mode,
                "target_flow_execution_adapter": (
                    self.target_flow_executor.capabilities()
                ),
                "last_reload_error": self._last_reload_error,
                "external_version_management": self.external_version_management,
                "condition_axis_columns": list(
                    condition_axis_columns(self.training)
                ),
            }

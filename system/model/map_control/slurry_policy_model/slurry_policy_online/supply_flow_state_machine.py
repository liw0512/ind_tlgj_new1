from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

try:
    from _engine.schema import OUTLET_SO2_COLUMN
    from _engine.supply_flow_effect_profiler import (
        summarize_supply_flow_response,
    )
    from _engine.utils import (
        bool_value,
        window_coverage_ratio,
    )
except ImportError:  # pragma: no cover
    from .._engine.schema import OUTLET_SO2_COLUMN
    from .._engine.supply_flow_effect_profiler import (
        summarize_supply_flow_response,
    )
    from .._engine.utils import (
        bool_value,
        window_coverage_ratio,
    )

from .supply_flow_advisor import (
    finite_number,
    tower_ph_is_safe,
    tower_total_flow,
)
from .supply_flow_validation import SupplyFlowValidator


def _ts(value: Any) -> pd.Timestamp:
    result = pd.Timestamp(value)
    if result.tzinfo is not None:
        result = result.tz_localize(None)
    return result


class SupplyFlowStateMachine:
    """Track target-flow recommendation, execution and observed process effect."""

    def __init__(
        self,
        plant: dict,
        training: dict,
        online_config: dict,
        runtime_state: Dict[str, Any],
    ) -> None:
        self.towers = {
            str(tower["tower_id"]): tower
            for tower in plant.get("towers", [])
            if tower.get("enabled", True)
        }
        self.episode = training["episode"]
        self.response = training["response"]
        self.action_stability = online_config["action_stability"]
        self.outlet_so2_safe_range = [
            float(value) for value in plant["outlet_so2_safe_range"]
        ]
        self.state = runtime_state.setdefault("supply_flow_control_state", {})
        self.state.setdefault("state", "IDLE")
        self.validation = SupplyFlowValidator(
            runtime_state, training, online_config
        )

    def _snapshot(self, current_flow: Optional[float] = None) -> Dict[str, Any]:
        active_plan = dict(self.state.get("active_plan") or {})
        active_plan.pop("effect_samples", None)
        result = {
            "mode": "TARGET_SUPPLY_FLOW",
            "state": str(self.state.get("state", "IDLE")),
            "active_plan": active_plan,
            "last_result": dict(self.state.get("last_result") or {}),
            "validation": self.validation.summary(),
        }
        if current_flow is not None:
            result["observed_current_flow"] = current_flow
        return result

    def public_state(self) -> Dict[str, Any]:
        """Expose diagnostics without leaking the internal rolling buffers."""
        return self._snapshot()

    def _append_history(self, now: pd.Timestamp, process: Dict[str, Any]) -> None:
        ph_values = {
            tower_id: finite_number(process.get(str(tower["ph_column"])))
            for tower_id, tower in self.towers.items()
        }
        self.state.setdefault("sample_history", []).append(
            {
                "timestamp": now.isoformat(),
                "outlet_so2": finite_number(process.get(OUTLET_SO2_COLUMN)),
                "tower_ph": ph_values,
            }
        )
        cutoff = now - pd.Timedelta(minutes=float(self.episode["baseline_minutes"]))
        self.state["sample_history"] = [
            item
            for item in self.state["sample_history"]
            if _ts(item["timestamp"]) >= cutoff
        ]

    def _capture_effect_baseline(
        self,
        now: pd.Timestamp,
        tower_id: str,
    ) -> Dict[str, Any]:
        history = list(self.state.get("sample_history") or [])
        frame = pd.DataFrame(
            {
                "timestamp": [_ts(item["timestamp"]) for item in history],
                "outlet_so2": [item.get("outlet_so2") for item in history],
                "tower_ph": [
                    (item.get("tower_ph") or {}).get(tower_id)
                    for item in history
                ],
            }
        )
        expected_minutes = float(self.episode["baseline_minutes"])
        coverage = (
            window_coverage_ratio(frame, "timestamp", expected_minutes)
            if not frame.empty
            else 0.0
        )
        so2 = pd.to_numeric(frame.get("outlet_so2"), errors="coerce").dropna()
        ph = pd.to_numeric(frame.get("tower_ph"), errors="coerce").dropna()
        return {
            "captured_at": now.isoformat(),
            "coverage_ratio": coverage,
            "outlet_so2": float(so2.median()) if not so2.empty else None,
            "tower_ph": float(ph.median()) if not ph.empty else None,
            "sample_count": int(len(frame)),
        }

    def _begin_effect_observation(
        self,
        now: pd.Timestamp,
        current_flow: float,
    ) -> Dict[str, Any]:
        plan = self.state["active_plan"]
        effect_start = now + pd.Timedelta(
            minutes=float(self.episode["response_delay_minutes"])
        )
        effect_end = effect_start + pd.Timedelta(
            minutes=float(self.episode["response_window_minutes"])
        )
        plan["flow_completed_at"] = now.isoformat()
        plan["observed_final_flow"] = current_flow
        plan["effect_observation_start_time"] = effect_start.isoformat()
        plan["effect_observation_end_time"] = effect_end.isoformat()
        plan["effect_samples"] = []
        self.state["state"] = "WAITING_EFFECT"
        self.state["flow_result"] = {
            "status": "COMPLETED",
            "reason_code": "FLOW_TRAJECTORY_COMPLETED",
            "timestamp": now.isoformat(),
            "decision_id": plan.get("decision_id"),
            "prototype_id": plan.get("prototype_id"),
            "tower_id": plan.get("tower_id"),
            "observed_final_flow": current_flow,
        }
        return self._snapshot(current_flow)

    def _finish_effect_observation(
        self,
        now: pd.Timestamp,
        plan: Dict[str, Any],
    ) -> Dict[str, Any]:
        samples = list(plan.get("effect_samples") or [])
        frame = pd.DataFrame(
            samples,
            columns=["timestamp", "outlet_so2", "tower_ph"],
        )
        expected_minutes = float(self.episode["response_window_minutes"])
        coverage = (
            window_coverage_ratio(frame, "timestamp", expected_minutes)
            if not frame.empty
            else 0.0
        )
        so2 = pd.to_numeric(frame.get("outlet_so2"), errors="coerce").dropna()
        ph = pd.to_numeric(frame.get("tower_ph"), errors="coerce").dropna()
        baseline = plan.get("effect_baseline") or {}
        baseline_so2 = finite_number(baseline.get("outlet_so2"))
        baseline_ph = finite_number(baseline.get("tower_ph"))
        so2_low, so2_high = self.outlet_so2_safe_range
        tower = self.towers[str(plan["tower_id"])]
        ph_low, ph_high = [float(value) for value in tower["ph_safe_range"]]
        metrics = summarize_supply_flow_response(
            float("nan") if baseline_so2 is None else baseline_so2,
            float("nan") if baseline_ph is None else baseline_ph,
            so2,
            ph,
            (so2_low, so2_high),
            (ph_low, ph_high),
            self.response,
        )
        response_so2 = finite_number(metrics["response_outlet_so2"])
        response_ph = finite_number(metrics["response_tower_ph"])
        so2_direction = str(metrics["outlet_so2_direction"])
        minimum_coverage = float(self.episode["minimum_window_coverage_ratio"])
        complete = (
            float(baseline.get("coverage_ratio", 0.0)) >= minimum_coverage
            and coverage >= minimum_coverage
            and baseline_so2 is not None
            and baseline_ph is not None
            and response_so2 is not None
            and response_ph is not None
        )

        first_effect_time = None
        extreme_effect_time = None
        if baseline_so2 is not None and not so2.empty:
            deadband = float(self.response["so2_direction_deadband"])
            ordered = frame.assign(
                outlet_so2_numeric=pd.to_numeric(
                    frame["outlet_so2"], errors="coerce"
                )
            ).dropna(subset=["outlet_so2_numeric"])
            expected = str(plan.get("expected_outlet_so2_direction", "UNKNOWN"))
            delta = ordered["outlet_so2_numeric"] - baseline_so2
            if expected == "DECREASE":
                matches = ordered.loc[delta <= -deadband]
                extreme_index = ordered["outlet_so2_numeric"].idxmin()
            elif expected == "INCREASE":
                matches = ordered.loc[delta >= deadband]
                extreme_index = ordered["outlet_so2_numeric"].idxmax()
            else:
                matches = ordered.loc[delta.abs() >= deadband]
                extreme_index = delta.abs().idxmax()
            if not matches.empty:
                first_effect_time = str(matches.iloc[0]["timestamp"])
            if not ordered.empty:
                extreme_effect_time = str(frame.loc[extreme_index, "timestamp"])

        effect = {
            "complete": complete,
            "baseline": dict(baseline),
            "response_start_time": plan["effect_observation_start_time"],
            "response_end_time": plan["effect_observation_end_time"],
            "response_coverage_ratio": coverage,
            "response_sample_count": int(len(frame)),
            "response_outlet_so2": response_so2,
            "delta_outlet_so2": finite_number(metrics["delta_outlet_so2"]),
            "outlet_so2_direction": so2_direction,
            "expected_outlet_so2_direction": plan.get(
                "expected_outlet_so2_direction"
            ),
            "outlet_so2_direction_matches_expected": so2_direction
            == str(plan.get("expected_outlet_so2_direction", "UNKNOWN")),
            "response_outlet_so2_min": finite_number(
                metrics["response_outlet_so2_min"]
            ),
            "response_outlet_so2_max": finite_number(
                metrics["response_outlet_so2_max"]
            ),
            "outlet_so2_safe_ratio": metrics["outlet_so2_safe_ratio"],
            "outlet_so2_over_hard_max": metrics["outlet_so2_over_hard_max"],
            "response_tower_ph": response_ph,
            "delta_tower_ph": finite_number(metrics["delta_tower_ph"]),
            "tower_ph_direction": metrics["tower_ph_direction"],
            "response_tower_ph_min": finite_number(
                metrics["response_tower_ph_min"]
            ),
            "response_tower_ph_max": finite_number(
                metrics["response_tower_ph_max"]
            ),
            "tower_ph_out_of_range": metrics["tower_ph_out_of_range"],
            "oscillation_sign_changes": metrics["oscillation_sign_changes"],
            "first_effect_time": first_effect_time,
            "extreme_effect_time": extreme_effect_time,
        }
        result = {
            "status": "EFFECT_COMPLETED",
            "reason_code": (
                "FLOW_EFFECT_PROFILE_COMPLETE"
                if complete
                else "FLOW_EFFECT_PROFILE_INCOMPLETE"
            ),
            "timestamp": now.isoformat(),
            "decision_id": plan.get("decision_id"),
            "prototype_id": plan.get("prototype_id"),
            "tower_id": plan.get("tower_id"),
            "flow": dict(self.state.get("flow_result") or {}),
            "effect": effect,
        }
        self.state["state"] = "EFFECT_COMPLETED"
        self.state["last_result"] = result
        self.validation.record_effect(result)
        self.state.pop("active_plan", None)
        self.state.pop("flow_result", None)
        return self._snapshot()

    def _advance_effect(
        self,
        now: pd.Timestamp,
        process: Dict[str, Any],
        plan: Dict[str, Any],
    ) -> Dict[str, Any]:
        effect_start = _ts(plan["effect_observation_start_time"])
        effect_end = _ts(plan["effect_observation_end_time"])
        if now < effect_start:
            self.state["state"] = "WAITING_EFFECT"
            return self._snapshot(tower_total_flow(self.towers[str(plan["tower_id"])], process))

        self.state["state"] = "EVALUATING_EFFECT"
        tower = self.towers[str(plan["tower_id"])]
        # A late online frame must not extend the configured response window or
        # use a post-window value as if it had been observed at the boundary.
        if now <= effect_end:
            plan.setdefault("effect_samples", []).append(
                {
                    "timestamp": now.isoformat(),
                    "outlet_so2": finite_number(process.get(OUTLET_SO2_COLUMN)),
                    "tower_ph": finite_number(
                        process.get(str(tower["ph_column"]))
                    ),
                }
            )
        if now >= effect_end:
            return self._finish_effect_observation(now, plan)
        return self._snapshot(tower_total_flow(tower, process))

    def _finish(
        self,
        status: str,
        now: pd.Timestamp,
        current_flow: Optional[float],
        reason: str,
    ) -> Dict[str, Any]:
        plan = dict(self.state.get("active_plan") or {})
        result = {
            "status": status,
            "reason_code": reason,
            "timestamp": now.isoformat(),
            "decision_id": plan.get("decision_id"),
            "prototype_id": plan.get("prototype_id"),
            "tower_id": plan.get("tower_id"),
            "observed_final_flow": current_flow,
        }
        self.state["state"] = status
        self.state["last_result"] = result
        self.validation.record_flow_terminal(result)
        self.state.pop("active_plan", None)
        return self._snapshot(current_flow)

    @staticmethod
    def _reached(value: float, target: float, tolerance: float) -> bool:
        return abs(value - target) <= tolerance

    def advance(self, now: pd.Timestamp, process: Dict[str, Any]) -> Dict[str, Any]:
        now = _ts(now)
        self._append_history(now, process)
        pending = self.state.get("pending_plan") or {}
        if pending.get("timestamp"):
            age = (now - _ts(pending["timestamp"])).total_seconds()
            timeout = float(
                self.action_stability["recommendation_feedback_timeout_seconds"]
            )
            if age >= timeout:
                owner = str(pending.get("owner", ""))
                self.state.pop("pending_plan", None)
                if owner == "TARGET_SUPPLY_FLOW":
                    self.state["state"] = "RECOMMENDATION_TIMED_OUT"

        plan = self.state.get("active_plan") or {}
        if not plan:
            return self._snapshot()
        if self.state.get("state") in {"WAITING_EFFECT", "EVALUATING_EFFECT"}:
            return self._advance_effect(now, process, plan)
        tower = self.towers.get(str(plan.get("tower_id", "")))
        if tower is None:
            return self._finish(
                "ABORTED", now, None, "FLOW_TOWER_CONFIG_MISSING"
            )
        current_flow = tower_total_flow(tower, process)
        if not tower_ph_is_safe(tower, process):
            return self._finish(
                "ABORTED", now, current_flow, "FLOW_PH_SAFETY_ABORT"
            )

        deadline = _ts(plan["execution_deadline"])
        if now >= deadline:
            return self._finish(
                "TIMED_OUT", now, current_flow, "FLOW_EXECUTION_TIMEOUT"
            )
        if current_flow is None:
            if self.state.get("state") != "FLOW_FEEDBACK_MISSING":
                self.state["state_before_feedback_missing"] = self.state.get(
                    "state", "WAITING_FLOW_START"
                )
            self.state["state"] = "FLOW_FEEDBACK_MISSING"
            return self._snapshot()

        phase = str(self.state.get("state", "WAITING_FLOW_START"))
        if phase == "FLOW_FEEDBACK_MISSING":
            phase = str(
                self.state.pop(
                    "state_before_feedback_missing",
                    "WAITING_FLOW_START",
                )
            )
            self.state["state"] = phase
        baseline = float(plan["baseline_flow"])
        direction = 1.0 if plan["action_direction"] == "INCREASE" else -1.0
        peak_excursion = abs(float(plan["target_peak_flow"]) - baseline)
        final_excursion = abs(float(plan["target_final_flow"]) - baseline)
        start_threshold = max(max(peak_excursion, final_excursion) * 0.1, 1e-6)
        directed_progress = direction * (current_flow - baseline)

        if phase in {"WAITING_FLOW_START", "FLOW_FEEDBACK_MISSING"}:
            if directed_progress < start_threshold:
                self.state["state"] = "WAITING_FLOW_START"
                return self._snapshot(current_flow)
            self.state["flow_started_at"] = now.isoformat()
            plan["effect_baseline"] = self._capture_effect_baseline(
                now, str(plan["tower_id"])
            )
            if str(plan.get("flow_shape", "")) in {"PULSE", "BOOST_STEP"}:
                self.state["state"] = "WAITING_PEAK_TARGET"
            else:
                self.state["state"] = "WAITING_FINAL_TARGET"
            phase = str(self.state["state"])

        if phase == "WAITING_PEAK_TARGET":
            peak = float(plan["target_peak_flow"])
            tolerance = float(plan["peak_flow_tolerance"])
            crossed = (
                current_flow >= peak - tolerance
                if direction > 0
                else current_flow <= peak + tolerance
            )
            if not crossed:
                return self._snapshot(current_flow)
            self.state["peak_reached_at"] = now.isoformat()
            self.state["state"] = "WAITING_FINAL_TARGET"

        if self.state.get("state") == "WAITING_FINAL_TARGET" and self._reached(
            current_flow,
            float(plan["target_final_flow"]),
            float(plan["final_flow_tolerance"]),
        ):
            return self._begin_effect_observation(now, current_flow)
        return self._snapshot(current_flow)

    def record_recommendation(self, decision: Dict[str, Any]) -> None:
        control = ((decision.get("control_recommendation") or {}).get("primary") or {})
        flow_recommendation = decision.get("target_supply_flow") or {}
        if (
            control.get("recommendation_type") != "TARGET_SUPPLY_FLOW"
            or not bool(control.get("actionable"))
            or not flow_recommendation.get("available")
        ):
            return
        self.state["pending_plan"] = {
            "owner": "TARGET_SUPPLY_FLOW",
            "decision_id": decision.get("decision_id"),
            "timestamp": decision.get("timestamp"),
            "prototype_id": control.get("prototype_id"),
            "tower_id": control.get("tower_id"),
            "action_direction": control.get("action_direction"),
            "flow_shape": control.get("flow_shape"),
            "flow_execution_profile": control.get("flow_execution_profile"),
            "baseline_flow": control.get("current_flow"),
            "target_peak_flow": control.get("target_peak_flow"),
            "target_final_flow": control.get("target_final_flow"),
            "peak_flow_tolerance": control.get("peak_flow_tolerance"),
            "final_flow_tolerance": control.get("final_flow_tolerance"),
            "expected_outlet_so2_direction": (
                (flow_recommendation.get("expected_effect") or {}).get(
                    "outlet_so2_direction"
                )
            ),
        }
        self.state["state"] = "TARGET_FLOW_RECOMMENDED"

    def blocking_reasons(self) -> list[str]:
        pending = self.state.get("pending_plan") or {}
        active = self.state.get("active_plan") or {}
        reasons: list[str] = []
        if pending.get("owner") == "TARGET_SUPPLY_FLOW":
            reasons.append("WAITING_TARGET_FLOW_EXECUTION_FEEDBACK")
        if active.get("owner") == "TARGET_SUPPLY_FLOW":
            reasons.append("TARGET_FLOW_ACTION_IN_PROGRESS")
        return reasons

    @staticmethod
    def _feedback_flow(
        feedback: Dict[str, Any], tower_id: str
    ) -> Optional[float]:
        value = feedback.get(
            "actual_tower_flow_after",
            feedback.get("actual_supply_flow_after"),
        )
        if isinstance(value, dict):
            value = value.get(tower_id)
        return finite_number(value)

    def _activate_plan(
        self,
        pending: Dict[str, Any],
        executed_at: pd.Timestamp,
        feedback_flow: Optional[float],
    ) -> Dict[str, Any]:
        if feedback_flow is not None:
            offset = feedback_flow - float(pending["baseline_flow"])
            pending["baseline_flow"] = feedback_flow
            pending["target_peak_flow"] = float(pending["target_peak_flow"]) + offset
            pending["target_final_flow"] = float(pending["target_final_flow"]) + offset
        pending["executed_at"] = executed_at.isoformat()
        pending["execution_deadline"] = (
            executed_at
            + pd.Timedelta(minutes=float(self.episode["max_action_duration_minutes"]))
        ).isoformat()
        self.state["active_plan"] = pending
        self.state["state"] = "WAITING_FLOW_START"
        self.state.pop("flow_started_at", None)
        self.state.pop("peak_reached_at", None)
        return self._snapshot(feedback_flow)

    def record_execution(self, feedback: Dict[str, Any]) -> Dict[str, Any]:
        pending = dict(self.state.get("pending_plan") or {})
        if pending.get("owner") != "TARGET_SUPPLY_FLOW":
            raise ValueError("当前没有待确认的 TARGET_SUPPLY_FLOW 推荐")
        decision_id = str(feedback.get("decision_id") or "")
        if not decision_id or decision_id != str(pending.get("decision_id") or ""):
            raise ValueError("目标流量执行反馈 decision_id 与待确认推荐不一致")
        executed = bool_value(
            feedback.get("actual_action_executed", feedback.get("executed")),
            False,
        )
        accepted = bool_value(
            feedback.get("recommendation_accepted", feedback.get("accepted")),
            executed,
        )
        executed_at = _ts(
            feedback.get("actual_execution_time")
            or feedback.get("timestamp")
            or pending["timestamp"]
        )
        record = {
            "recommendation_type": "TARGET_SUPPLY_FLOW",
            "decision_id": decision_id,
            "timestamp": executed_at.isoformat(),
            "recommendation_accepted": accepted,
            "actual_action_executed": executed,
            "target_flow_recommendation": pending,
        }
        if not executed:
            self.state.pop("pending_plan", None)
            self.state["state"] = "IDLE"
            record["state_after_feedback"] = "IDLE"
            record["target_flow_tracking"] = self._snapshot()
            return record
        actual_direction = str(
            feedback.get("actual_action_direction")
            or pending.get("action_direction")
            or "UNKNOWN"
        ).upper()
        if actual_direction != str(pending.get("action_direction", "")).upper():
            raise ValueError("目标流量实际执行方向与待确认推荐不一致")
        self.state.pop("pending_plan", None)
        tracking = self._activate_plan(
            pending,
            executed_at,
            self._feedback_flow(feedback, str(pending.get("tower_id", ""))),
        )
        record["state_after_feedback"] = "WAITING_FLOW_START"
        record["target_flow_tracking"] = tracking
        return record

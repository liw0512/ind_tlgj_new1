from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from .demand_analyzer import MAGNITUDE_ORDER


def _ts(value: Any) -> pd.Timestamp:
    result = pd.Timestamp(value)
    if result.tzinfo is not None:
        result = result.tz_localize(None)
    return result


class DecisionStateMachine:
    def __init__(self, online_config: dict, effective_training: dict, runtime_state: Dict[str, Any]) -> None:
        self.config = online_config["action_stability"]
        self.regular_config = online_config.get("regular_control", {})
        self.training = effective_training
        self.state = runtime_state.setdefault("decision_state", {})
        self.state.setdefault("state", "READY")
        self.state.setdefault("action_history", [])

    def _response_delay(self) -> float:
        value = self.config.get("response_delay_minutes")
        if value is None:
            value = self.training.get("episode", {}).get("response_delay_minutes", 3.0)
        return float(value)

    def _response_window(self) -> float:
        value = self.config.get("response_window_minutes")
        if value is None:
            value = self.training.get("episode", {}).get("response_window_minutes", 10.0)
        return float(value)

    def advance(self, now: pd.Timestamp) -> None:
        pending = self.state.get("pending_recommendation")
        if pending:
            created = _ts(pending["timestamp"])
            timeout = float(self.config.get("recommendation_feedback_timeout_seconds", 90.0))
            if (now - created).total_seconds() >= timeout:
                self.state.pop("pending_recommendation", None)
                self.state["state"] = "READY"
                self.state["last_timeout_reason"] = "EXECUTION_FEEDBACK_TIMEOUT"

        response_end = self.state.get("response_end_time")
        effect_start = self.state.get("effect_observation_start_time")
        if response_end:
            end = _ts(response_end)
            if now >= end:
                last = self.state.get("last_executed_action") or {}
                direction = str(last.get("action_direction", "UNKNOWN"))
                chain_direction = str(self.state.get("completed_chain_direction", ""))
                if direction in {"INCREASE", "DECREASE"}:
                    if direction == chain_direction:
                        self.state["completed_matching_action_count"] = int(
                            self.state.get("completed_matching_action_count", 0)
                        ) + 1
                    else:
                        self.state["completed_chain_direction"] = direction
                        self.state["completed_matching_action_count"] = 1
                self.state["last_response_completed_at"] = now.isoformat()
                self.state["state"] = "READY"
                self.state.pop("effect_observation_start_time", None)
                self.state.pop("response_end_time", None)
            elif effect_start and now >= _ts(effect_start):
                self.state["state"] = "EVALUATING_EFFECT"
            else:
                self.state["state"] = "WAITING_EFFECT"

        history = []
        cutoff = now - pd.Timedelta(hours=2)
        for item in self.state.get("action_history", []):
            try:
                if _ts(item["timestamp"]) >= cutoff:
                    history.append(item)
            except Exception:
                continue
        self.state["action_history"] = history

    def notify_condition(self, label: str, switch_state: str) -> None:
        previous = self.state.get("last_condition_label")
        switched = str(switch_state).upper() == "SWITCHED" or (
            previous is not None and str(previous) != str(label)
        )
        if switched:
            self.state["condition_hold_cycles_remaining"] = int(
                self.config.get("condition_switch_hold_cycles", 1)
            )
        self.state["last_condition_label"] = str(label)

    def condition_hold_required(self) -> bool:
        return int(self.state.get("condition_hold_cycles_remaining", 0)) > 0

    def consume_condition_hold(self) -> None:
        remaining = int(self.state.get("condition_hold_cycles_remaining", 0))
        self.state["condition_hold_cycles_remaining"] = max(0, remaining - 1)

    def stability_context(self, now: pd.Timestamp) -> Dict[str, Any]:
        last = self.state.get("last_executed_action") or {}
        reverse_active = False
        if last.get("timestamp"):
            elapsed = (now - _ts(last["timestamp"])).total_seconds() / 60.0
            reverse_active = elapsed < float(self.config["reverse_action_lock_minutes"])
        return {
            "state": self.state.get("state", "READY"),
            "last_action_direction": last.get("action_direction"),
            "last_action_family": last.get("action_family"),
            "last_action_time": last.get("timestamp"),
            "reverse_lock_active": reverse_active,
        }

    def blocking_reasons(self, now: pd.Timestamp, safety_level: str) -> List[str]:
        if safety_level == "EMERGENCY":
            return []
        reasons: List[str] = []
        if self.state.get("pending_recommendation"):
            reasons.append("WAITING_EXECUTION_FEEDBACK")
        if (
            self.state.get("state") in {"WAITING_EFFECT", "EVALUATING_EFFECT"}
            and bool(self.config.get("block_normal_actions_while_waiting_effect", True))
        ):
            reasons.append("WAITING_PREVIOUS_ACTION_EFFECT")

        last = self.state.get("last_executed_action") or {}
        if last.get("timestamp"):
            elapsed = (now - _ts(last["timestamp"])).total_seconds() / 60.0
            if elapsed < float(self.config["minimum_action_interval_minutes"]):
                reasons.append("MINIMUM_ACTION_INTERVAL_ACTIVE")

        cutoff = now - pd.Timedelta(hours=1)
        count = 0
        for item in self.state.get("action_history", []):
            try:
                if _ts(item["timestamp"]) >= cutoff:
                    count += 1
            except Exception:
                continue
        if count >= int(self.config["maximum_actions_per_hour"]):
            reasons.append("MAXIMUM_ACTIONS_PER_HOUR_REACHED")
        return list(dict.fromkeys(reasons))

    def apply_progressive_magnitude_limit(self, demand: Any) -> List[str]:
        # V2 塔级策略已经使用“当前 SO2 与动态目标的偏差 + 历史 ΔSO2 能力”
        # 在线匹配动作幅度。此时如果再强制首个动作只能 SMALL，会把一个本来
        # 明确更匹配的 MEDIUM 候选挡掉，形成重复且互相冲突的幅度决策。
        #
        # 因此新 COARSE_TOWER（也是缺省模式）不再叠加旧 progressive cap；
        # 仍保留 WAITING_EFFECT、最小动作间隔、反向锁和每小时动作次数等时序保护。
        # 只有显式启用 LEGACY_DETAILED 时才保留旧版逐级升级行为。
        policy_state_mode = str(
            self.training.get("state", {}).get("policy_state_mode", "COARSE_TOWER")
        ).upper()
        if policy_state_mode != "LEGACY_DETAILED":
            return []

        progressive = self.regular_config.get("progressive_action", {}) or {}
        if not bool(progressive.get("enabled", True)):
            return []
        if demand.safety_level in {"WARNING", "EMERGENCY"}:
            return []
        desired_action_direction = {
            "SO2_DOWN": "INCREASE",
            "SO2_UP": "DECREASE",
            "SO2_HOLD": "HOLD",
        }.get(demand.desired_so2_response, "HOLD")
        if desired_action_direction == "HOLD":
            demand.maximum_action_magnitude = "HOLD"
            return ["PROGRESSIVE_LIMIT_HOLD"]

        count = 0
        if str(self.state.get("completed_chain_direction", "")) == desired_action_direction:
            count = int(self.state.get("completed_matching_action_count", 0))
        cap = str(progressive.get("initial_max_magnitude", "SMALL")).upper()
        if count >= int(progressive.get("medium_after_completed_matching_actions", 1)):
            cap = "MEDIUM"
        if (
            count >= int(progressive.get("strong_after_completed_matching_actions", 2))
            and not bool(progressive.get("strong_only_warning_or_emergency", True))
        ):
            cap = "STRONG"
        if MAGNITUDE_ORDER.get(cap, 0) < MAGNITUDE_ORDER.get(demand.maximum_action_magnitude, 0):
            demand.maximum_action_magnitude = cap
            return ["PROGRESSIVE_ACTION_CAP:%s" % cap]
        return []

    def record_recommendation(self, decision: Dict[str, Any]) -> None:
        if decision.get("action_family") == "HOLD":
            return
        self.state["pending_recommendation"] = {
            "decision_id": decision["decision_id"],
            "timestamp": decision["timestamp"],
            "action_id": decision["action_id"],
            "action_family": decision["action_family"],
            "action_direction": decision["action_direction"],
            "action_magnitude": decision["action_magnitude"],
            "recommended_valve_deltas": decision.get("recommended_valve_deltas", {}),
        }
        self.state["state"] = "ACTION_RECOMMENDED"

    def record_execution(self, feedback: Dict[str, Any]) -> Dict[str, Any]:
        pending = self.state.get("pending_recommendation") or {}
        decision_id = str(feedback.get("decision_id") or pending.get("decision_id") or "")
        if pending and decision_id and decision_id != str(pending.get("decision_id")):
            raise ValueError("执行反馈 decision_id 与待确认推荐不一致")

        executed = bool(feedback.get("actual_action_executed", feedback.get("executed", False)))
        accepted = bool(feedback.get("recommendation_accepted", feedback.get("accepted", executed)))
        timestamp = _ts(feedback.get("actual_execution_time") or feedback.get("timestamp") or pending.get("timestamp") or pd.Timestamp.now())
        record = {
            "decision_id": decision_id,
            "timestamp": timestamp.isoformat(),
            "recommendation_accepted": accepted,
            "actual_action_executed": executed,
            "actual_valve_before": feedback.get("actual_valve_before", {}),
            "actual_valve_after": feedback.get("actual_valve_after", {}),
            "pending_recommendation": pending,
        }
        self.state.pop("pending_recommendation", None)
        if not executed:
            self.state["state"] = "READY"
            record["state_after_feedback"] = "READY"
            return record

        action_direction = str(feedback.get("actual_action_direction") or pending.get("action_direction") or "UNKNOWN")
        action_family = str(feedback.get("actual_action_family") or pending.get("action_family") or "UNKNOWN")
        action_id = str(feedback.get("actual_action_id") or pending.get("action_id") or "UNKNOWN")
        action_magnitude = str(feedback.get("actual_action_magnitude") or pending.get("action_magnitude") or "UNKNOWN")
        last = {
            "timestamp": timestamp.isoformat(),
            "decision_id": decision_id,
            "action_id": action_id,
            "action_family": action_family,
            "action_direction": action_direction,
            "action_magnitude": action_magnitude,
        }
        self.state["last_executed_action"] = last
        self.state.setdefault("action_history", []).append(last)
        observation_start = timestamp + pd.Timedelta(minutes=self._response_delay())
        response_end = observation_start + pd.Timedelta(minutes=self._response_window())
        self.state["effect_observation_start_time"] = observation_start.isoformat()
        self.state["response_end_time"] = response_end.isoformat()
        self.state["state"] = "WAITING_EFFECT"
        record["effect_observation_start_time"] = observation_start.isoformat()
        record["response_end_time"] = response_end.isoformat()
        record["state_after_feedback"] = "WAITING_EFFECT"
        return record

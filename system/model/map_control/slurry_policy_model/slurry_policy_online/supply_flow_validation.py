from __future__ import annotations

from typing import Any, Dict

import pandas as pd


class SupplyFlowValidator:
    """Aggregate target-flow outcomes for runtime quality review."""

    def __init__(
        self,
        runtime_state: Dict[str, Any],
        training: dict,
        online_config: dict,
    ) -> None:
        reliability = training["reliability"]
        acceptance = online_config["profile_acceptance"]
        stable_ratio = float(acceptance["minimum_stable_response_ratio"])
        self.criteria = {
            "minimum_evaluated_plans": int(
                reliability["minimum_supported_events"]
            ),
            "minimum_observed_days": int(reliability["minimum_supported_days"]),
            "minimum_flow_completion_rate": stable_ratio,
            "minimum_effect_profile_complete_rate": stable_ratio,
            "minimum_direction_match_rate": float(
                acceptance["minimum_direction_consistency"]
            ),
            "minimum_safety_pass_rate": float(
                acceptance["minimum_safety_history_score"]
            )
            / 100.0,
            "maximum_timeout_rate": 1.0 - stable_ratio,
        }
        self.state = runtime_state.setdefault("supply_flow_validation", {})
        self.state.setdefault("global", self._empty_counts())
        self.state.setdefault("by_prototype", {})

    @staticmethod
    def _empty_counts() -> Dict[str, Any]:
        return {
            "evaluated_plan_count": 0,
            "flow_completed_count": 0,
            "effect_profile_complete_count": 0,
            "direction_match_count": 0,
            "safety_pass_count": 0,
            "timeout_count": 0,
            "aborted_count": 0,
            "skipped_count": 0,
            "observed_dates": [],
        }

    def _buckets(self, prototype_id: str) -> list[Dict[str, Any]]:
        by_prototype = self.state["by_prototype"]
        bucket = by_prototype.setdefault(prototype_id or "UNKNOWN", self._empty_counts())
        return [self.state["global"], bucket]

    @staticmethod
    def _add_date(bucket: Dict[str, Any], timestamp: Any) -> None:
        try:
            date = pd.Timestamp(timestamp).date().isoformat()
        except Exception:
            return
        bucket["observed_dates"] = sorted(
            set(bucket.get("observed_dates", [])) | {date}
        )

    def record_flow_terminal(self, result: Dict[str, Any]) -> None:
        status = str(result.get("status", ""))
        for bucket in self._buckets(str(result.get("prototype_id") or "UNKNOWN")):
            bucket["evaluated_plan_count"] += 1
            if status == "TIMED_OUT":
                bucket["timeout_count"] += 1
            elif status == "ABORTED":
                bucket["aborted_count"] += 1
            self._add_date(bucket, result.get("timestamp"))

    def record_skipped(self, result: Dict[str, Any]) -> None:
        for bucket in self._buckets(str(result.get("prototype_id") or "UNKNOWN")):
            bucket["skipped_count"] += 1

    def record_effect(self, result: Dict[str, Any]) -> None:
        effect = result.get("effect") or {}
        safe = not bool(effect.get("outlet_so2_over_hard_max")) and not bool(
            effect.get("tower_ph_out_of_range")
        )
        for bucket in self._buckets(str(result.get("prototype_id") or "UNKNOWN")):
            bucket["evaluated_plan_count"] += 1
            bucket["flow_completed_count"] += 1
            if bool(effect.get("complete")):
                bucket["effect_profile_complete_count"] += 1
                if bool(effect.get("outlet_so2_direction_matches_expected")):
                    bucket["direction_match_count"] += 1
                if safe:
                    bucket["safety_pass_count"] += 1
            self._add_date(bucket, result.get("timestamp"))

    @staticmethod
    def _ratio(numerator: int, denominator: int) -> float:
        return float(numerator / denominator) if denominator > 0 else 0.0

    def _summary(self, counts: Dict[str, Any]) -> Dict[str, Any]:
        evaluated = int(counts.get("evaluated_plan_count", 0))
        flow_completed = int(counts.get("flow_completed_count", 0))
        effect_complete = int(counts.get("effect_profile_complete_count", 0))
        observed_days = len(set(counts.get("observed_dates", [])))
        metrics = {
            "evaluated_plan_count": evaluated,
            "observed_day_count": observed_days,
            "flow_completion_rate": self._ratio(flow_completed, evaluated),
            "effect_profile_complete_rate": self._ratio(
                effect_complete, flow_completed
            ),
            "direction_match_rate": self._ratio(
                int(counts.get("direction_match_count", 0)), effect_complete
            ),
            "safety_pass_rate": self._ratio(
                int(counts.get("safety_pass_count", 0)), effect_complete
            ),
            "timeout_rate": self._ratio(
                int(counts.get("timeout_count", 0)), evaluated
            ),
            "timeout_count": int(counts.get("timeout_count", 0)),
            "aborted_count": int(counts.get("aborted_count", 0)),
            "skipped_count": int(counts.get("skipped_count", 0)),
        }
        warmup_reasons = []
        if evaluated < self.criteria["minimum_evaluated_plans"]:
            warmup_reasons.append("INSUFFICIENT_EVALUATED_PLANS")
        if observed_days < self.criteria["minimum_observed_days"]:
            warmup_reasons.append("INSUFFICIENT_OBSERVED_DAYS")
        failed_reasons = []
        if metrics["flow_completion_rate"] < self.criteria["minimum_flow_completion_rate"]:
            failed_reasons.append("FLOW_COMPLETION_RATE_BELOW_THRESHOLD")
        if metrics["effect_profile_complete_rate"] < self.criteria["minimum_effect_profile_complete_rate"]:
            failed_reasons.append("EFFECT_PROFILE_COMPLETE_RATE_BELOW_THRESHOLD")
        if metrics["direction_match_rate"] < self.criteria["minimum_direction_match_rate"]:
            failed_reasons.append("DIRECTION_MATCH_RATE_BELOW_THRESHOLD")
        if metrics["safety_pass_rate"] < self.criteria["minimum_safety_pass_rate"]:
            failed_reasons.append("SAFETY_PASS_RATE_BELOW_THRESHOLD")
        if metrics["timeout_rate"] > self.criteria["maximum_timeout_rate"]:
            failed_reasons.append("TIMEOUT_RATE_ABOVE_THRESHOLD")

        if warmup_reasons:
            status = "WARMUP"
            reasons = warmup_reasons
        elif failed_reasons:
            status = "NOT_READY"
            reasons = failed_reasons
        else:
            status = "READY_FOR_REVIEW"
            reasons = ["FLOW_VALIDATION_THRESHOLDS_MET"]
        return {
            "status": status,
            "reason_codes": reasons,
            "automatic_switch_allowed": False,
            "metrics": metrics,
        }

    def summary(self) -> Dict[str, Any]:
        return {
            "mode": "REVIEW_GATE_ONLY",
            "criteria": dict(self.criteria),
            "criteria_source": {
                "support": "training.reliability",
                "completion_and_timeout": (
                    "online.profile_acceptance.minimum_stable_response_ratio"
                ),
                "direction": (
                    "online.profile_acceptance.minimum_direction_consistency"
                ),
                "safety": (
                    "online.profile_acceptance.minimum_safety_history_score"
                ),
            },
            "global": self._summary(self.state["global"]),
            "by_prototype": {
                key: self._summary(value)
                for key, value in sorted(self.state["by_prototype"].items())
            },
        }

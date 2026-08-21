from __future__ import annotations

from typing import Any, Dict


class ControlOutputSelector:
    """Build the single canonical target-supply-flow recommendation.

    Valve deltas are not a fallback.  If no accepted flow prototype exists the
    safe result is HOLD.
    """

    def __init__(self, online_config: dict) -> None:
        self.requested_mode = "TARGET_SUPPLY_FLOW"

    @staticmethod
    def _hold(decision: Dict[str, Any], reasons: list[str]) -> Dict[str, Any]:
        return {
            "recommendation_type": "HOLD",
            "actionable": False,
            "decision_id": decision.get("decision_id"),
            "action_direction": "HOLD",
            "reason_codes": list(dict.fromkeys(reasons)),
        }

    def select(
        self,
        decision: Dict[str, Any],
        flow_recommendation: Dict[str, Any],
        execution_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        del execution_context  # Execution readiness affects DCS execution, not recommendation semantics.
        reasons = ["CANONICAL_OUTPUT:TARGET_SUPPLY_FLOW"]
        actionable = (
            bool(flow_recommendation.get("available"))
            and decision.get("decision_status") == "RECOMMENDED"
            and decision.get("action_direction") in {"INCREASE", "DECREASE"}
        )
        if not actionable:
            reasons.extend(flow_recommendation.get("reason_codes") or ["NO_ACCEPTED_FLOW_PROTOTYPE"])
            primary = self._hold(decision, reasons)
        else:
            primary = {
                "recommendation_type": "TARGET_SUPPLY_FLOW",
                "actionable": True,
                "decision_id": decision.get("decision_id"),
                "prototype_id": str(flow_recommendation.get("prototype_id", "")),
                "tower_id": flow_recommendation.get("tower_id"),
                "action_direction": flow_recommendation.get("action_direction"),
                "flow_shape": flow_recommendation.get("flow_shape"),
                "flow_execution_profile": flow_recommendation.get("flow_execution_profile"),
                "current_flow": flow_recommendation.get("current_flow"),
                "target_peak_flow": flow_recommendation.get("target_peak_flow"),
                "target_final_flow": flow_recommendation.get("target_final_flow"),
                "target_peak_flow_range": flow_recommendation.get("target_peak_flow_range"),
                "target_final_flow_range": flow_recommendation.get("target_final_flow_range"),
                "peak_flow_tolerance": flow_recommendation.get("peak_flow_tolerance"),
                "final_flow_tolerance": flow_recommendation.get("final_flow_tolerance"),
                "reason_codes": reasons + ["FLOW_PROTOTYPE_SELECTED"],
            }
        return {
            "requested_mode": self.requested_mode,
            "effective_mode": "TARGET_SUPPLY_FLOW",
            "primary": primary,
        }

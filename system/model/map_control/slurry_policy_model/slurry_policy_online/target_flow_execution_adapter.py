from __future__ import annotations

from typing import Any, Dict, Protocol

from .supply_flow_advisor import (
    finite_number,
    tower_ph_is_safe,
    tower_total_flow,
)


class TargetFlowExecutionAdapter(Protocol):
    def capabilities(self) -> Dict[str, Any]: ...

    def prepare(
        self,
        decision_id: str,
        flow_recommendation: Dict[str, Any],
        process: Dict[str, Any],
    ) -> Dict[str, Any]: ...


class DryRunTargetFlowExecutionAdapter:
    """Build a bounded execution preview and deliberately perform no I/O."""

    SUPPORTED_SHAPES = {"STEP", "PULSE", "BOOST_STEP"}

    def __init__(self, plant: dict) -> None:
        self.towers = {
            str(tower["tower_id"]): tower
            for tower in plant.get("towers", [])
            if tower.get("enabled", True)
        }

    def capabilities(self) -> Dict[str, Any]:
        return {
            "adapter_mode": "DRY_RUN",
            "target_flow_execution_adapter_ready": False,
            "dcs_write_supported": False,
            "supported_shapes": sorted(self.SUPPORTED_SHAPES),
        }

    @staticmethod
    def _bounded_target(
        value: Any,
        allowed_range: Any,
    ) -> tuple[float | None, list[float] | None, bool]:
        target = finite_number(value)
        if target is None:
            return None, None, False
        if not isinstance(allowed_range, (list, tuple)) or len(allowed_range) != 2:
            return max(0.0, target), None, target < 0.0
        low = finite_number(allowed_range[0])
        high = finite_number(allowed_range[1])
        if low is None or high is None:
            return max(0.0, target), None, target < 0.0
        low, high = max(0.0, min(low, high)), max(0.0, max(low, high))
        bounded = min(max(target, low), high)
        return bounded, [low, high], bounded != target

    @staticmethod
    def _direction_valid(
        direction: str,
        shape: str,
        current: float,
        peak: float,
        final: float,
        final_tolerance: float,
    ) -> bool:
        if direction == "INCREASE":
            primary_valid = (
                final > current if shape == "STEP" else peak > current
            )
            final_valid = final >= current - final_tolerance
        elif direction == "DECREASE":
            primary_valid = (
                final < current if shape == "STEP" else peak < current
            )
            final_valid = final <= current + final_tolerance
        else:
            return False
        # A pulse may legitimately return to its baseline; STEP/BOOST_STEP also
        # permit a tolerance-sized return but cannot reverse past it.
        return primary_valid and final_valid

    def _blocked(
        self,
        decision_id: str,
        reasons: list[str],
    ) -> Dict[str, Any]:
        return {
            "adapter_mode": "DRY_RUN",
            "status": "BLOCKED",
            "decision_id": decision_id,
            "command_issued": False,
            "dcs_write_attempted": False,
            "reason_codes": list(dict.fromkeys(reasons)),
            "phases": [],
        }

    def prepare(
        self,
        decision_id: str,
        flow_recommendation: Dict[str, Any],
        process: Dict[str, Any],
    ) -> Dict[str, Any]:
        reasons: list[str] = []
        if not flow_recommendation.get("available"):
            return self._blocked(decision_id, ["FLOW_CANDIDATE_UNAVAILABLE"])
        tower_id = str(flow_recommendation.get("tower_id", ""))
        tower = self.towers.get(tower_id)
        if tower is None:
            return self._blocked(decision_id, ["FLOW_TOWER_CONFIG_MISSING"])
        current_flow = tower_total_flow(tower, process)
        if current_flow is None:
            reasons.append("FLOW_METER_SET_INCOMPLETE")
        if not tower_ph_is_safe(tower, process):
            reasons.append("TOWER_PH_OUTSIDE_SAFE_RANGE")
        shape = str(flow_recommendation.get("flow_shape", "")).upper()
        if shape not in self.SUPPORTED_SHAPES:
            reasons.append("UNSUPPORTED_FLOW_SHAPE:%s" % shape)

        peak, peak_range, peak_clamped = self._bounded_target(
            flow_recommendation.get("target_peak_flow"),
            flow_recommendation.get("target_peak_flow_range"),
        )
        final, final_range, final_clamped = self._bounded_target(
            flow_recommendation.get("target_final_flow"),
            flow_recommendation.get("target_final_flow_range"),
        )
        if peak is None or final is None:
            reasons.append("TARGET_FLOW_NOT_FINITE")
        if final_range is None:
            reasons.append("FINAL_TARGET_EVIDENCE_RANGE_MISSING")
        if shape in {"PULSE", "BOOST_STEP"} and peak_range is None:
            reasons.append("PEAK_TARGET_EVIDENCE_RANGE_MISSING")
        peak_tolerance = finite_number(flow_recommendation.get("peak_flow_tolerance"))
        final_tolerance = finite_number(flow_recommendation.get("final_flow_tolerance"))
        if peak_tolerance is None or final_tolerance is None:
            reasons.append("TARGET_FLOW_TOLERANCE_INVALID")
        if (
            current_flow is not None
            and peak is not None
            and final is not None
            and final_tolerance is not None
            and not self._direction_valid(
                str(flow_recommendation.get("action_direction", "")).upper(),
                shape,
                current_flow,
                peak,
                final,
                final_tolerance,
            )
        ):
            reasons.append("TARGET_FLOW_DIRECTION_INCONSISTENT")
        if reasons:
            return self._blocked(decision_id, reasons)

        phases = []
        if shape in {"PULSE", "BOOST_STEP"}:
            phases.append(
                {
                    "phase": "PEAK_TARGET",
                    "target_flow": peak,
                    "completion_tolerance": peak_tolerance,
                }
            )
        phases.append(
            {
                "phase": "FINAL_TARGET",
                "target_flow": final,
                "completion_tolerance": final_tolerance,
            }
        )
        if peak_clamped:
            reasons.append("PEAK_TARGET_CLAMPED_TO_PROTOTYPE_IQR")
        if final_clamped:
            reasons.append("FINAL_TARGET_CLAMPED_TO_PROTOTYPE_IQR")
        return {
            "adapter_mode": "DRY_RUN",
            "status": "PREVIEW_READY",
            "decision_id": decision_id,
            "prototype_id": flow_recommendation.get("prototype_id"),
            "tower_id": tower_id,
            "action_direction": flow_recommendation.get("action_direction"),
            "flow_shape": shape,
            "flow_execution_profile": flow_recommendation.get("flow_execution_profile"),
            "observed_current_flow": current_flow,
            "target_peak_flow_range": peak_range,
            "target_final_flow_range": final_range,
            "phases": phases,
            "expected_total_active_duration_minutes": (
                ((flow_recommendation.get("execution_evidence") or {}).get(
                    "active_duration_minutes"
                ) or {}).get("median")
            ),
            "command_issued": False,
            "dcs_write_attempted": False,
            "engineering_hard_limits_verified": False,
            "reason_codes": list(
                dict.fromkeys(
                    reasons
                    + [
                        "ENGINEERING_FLOW_LIMITS_NOT_CONFIGURED",
                        "DRY_RUN_NO_DCS_WRITE",
                    ]
                )
            ),
            "feedback_contract": {
                "recommendation_type": "TARGET_SUPPLY_FLOW",
                "required": [
                    "decision_id",
                    "actual_action_executed",
                    "actual_execution_time",
                ],
                "optional": [
                    "actual_action_direction",
                    "actual_tower_flow_after",
                ],
            },
        }

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional


def unavailable_flow_recommendation(*reasons: str) -> Dict[str, Any]:
    return {
        "mode": "TARGET_SUPPLY_FLOW",
        "available": False,
        "reason_codes": list(dict.fromkeys(reasons or ("FLOW_CONTEXT_UNAVAILABLE",))),
    }


def finite_number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def tower_total_flow(tower: dict, process: Dict[str, Any]) -> Optional[float]:
    """Return a complete tower total; never silently sum a partial meter set."""
    values: List[float] = []
    for meter in tower.get("supply_flows", []) or []:
        value = finite_number(process.get(str(meter.get("column", ""))))
        if value is None:
            return None
        values.append(value)
    return sum(values) if values else None


def tower_ph_is_safe(tower: dict, process: Dict[str, Any]) -> bool:
    ph = finite_number(process.get(str(tower["ph_column"])))
    if ph is None:
        return False
    lo, hi = [float(value) for value in tower["ph_safe_range"]]
    return lo <= ph <= hi


class SupplyFlowAdvisor:
    """Select one evidence-backed target-flow recommendation."""

    def __init__(self, loader: Any, plant: dict, online_config: dict) -> None:
        self.loader = loader
        self.plant = plant
        self.acceptance = online_config["profile_acceptance"]
        self.towers = {
            str(tower["tower_id"]): tower
            for tower in plant.get("towers", [])
            if tower.get("enabled", True)
        }

    @staticmethod
    def _finite(value: Any) -> Optional[float]:
        return finite_number(value)

    def _current_flow(self, tower: dict, process: Dict[str, Any]) -> Optional[float]:
        return tower_total_flow(tower, process)

    def _tower_is_safe(self, tower: dict, process: Dict[str, Any]) -> bool:
        return tower_ph_is_safe(tower, process)

    def _accepted(self, profile: Dict[str, Any]) -> bool:
        evidence = profile.get("evidence", {}) or {}
        reliability = evidence.get("reliability", {}) or {}
        effect = profile.get("effect", {}) or {}
        timing = profile.get("timing", {}) or {}
        return (
            str(evidence.get("status", ""))
            in {str(value) for value in self.acceptance["local_allowed_status"]}
            and float(effect.get("outlet_so2_direction_consistency", 0.0))
            >= float(self.acceptance["minimum_direction_consistency"])
            and float(reliability.get("safety_history_score", 0.0))
            >= float(self.acceptance["minimum_safety_history_score"])
            and float(reliability.get("total_score", 0.0))
            >= float(self.acceptance["minimum_reliability_total_score"])
            and float(timing.get("settled_ratio", 0.0))
            >= float(self.acceptance["minimum_stable_response_ratio"])
        )

    @staticmethod
    def _rank(profile: Dict[str, Any]) -> tuple:
        evidence = profile.get("evidence", {}) or {}
        reliability = evidence.get("reliability", {}) or {}
        effect = profile.get("effect", {}) or {}
        return (
            float(reliability.get("total_score", 0.0)),
            float(reliability.get("safety_history_score", 0.0)),
            float(effect.get("outlet_so2_direction_consistency", 0.0)),
            int(profile.get("event_count", 0)),
            str(profile.get("prototype_id", "")),
        )

    def recommend(
        self,
        condition_label: str,
        acceptable_effect_directions: Iterable[str],
        process: Dict[str, Any],
    ) -> Dict[str, Any]:
        acceptable = {str(value).upper() for value in acceptable_effect_directions}
        candidates: List[tuple[Dict[str, Any], dict, float]] = []
        for profile in self.loader.load_supply_flow_prototypes().values():
            if str(profile.get("condition_label", "")) != str(condition_label):
                continue
            effect = profile.get("effect", {}) or {}
            if str(effect.get("outlet_so2_direction", "")).upper() not in acceptable:
                continue
            if str(profile.get("action_direction", "")).upper() not in {
                "INCREASE",
                "DECREASE",
            }:
                continue
            tower = self.towers.get(str(profile.get("tower_id", "")))
            if tower is None or not self._tower_is_safe(tower, process):
                continue
            current_flow = self._current_flow(tower, process)
            if current_flow is None or not self._accepted(profile):
                continue
            candidates.append((profile, tower, current_flow))

        if not candidates:
            return unavailable_flow_recommendation("NO_ACCEPTED_FLOW_PROTOTYPE")

        profile, _, current_flow = max(
            candidates,
            key=lambda item: self._rank(item[0]),
        )
        target = profile.get("target_flow", {}) or {}
        final_delta = self._finite(
            (target.get("final_delta_flow", {}) or {}).get("median")
        )
        peak_delta = self._finite(
            (target.get("peak_delta_flow", {}) or {}).get("median")
        )
        if final_delta is None or peak_delta is None:
            return unavailable_flow_recommendation("FLOW_PROTOTYPE_TARGET_INVALID")

        evidence = profile.get("evidence", {}) or {}
        final_distribution = target.get("final_delta_flow", {}) or {}
        peak_distribution = target.get("peak_delta_flow", {}) or {}
        final_p25 = self._finite(final_distribution.get("p25"))
        final_p75 = self._finite(final_distribution.get("p75"))
        peak_p25 = self._finite(peak_distribution.get("p25"))
        peak_p75 = self._finite(peak_distribution.get("p75"))
        final_iqr = abs(
            (final_delta if final_p75 is None else final_p75)
            - (final_delta if final_p25 is None else final_p25)
        )
        peak_iqr = abs(
            (peak_delta if peak_p75 is None else peak_p75)
            - (peak_delta if peak_p25 is None else peak_p25)
        )
        final_delta_range = [
            final_delta if final_p25 is None else final_p25,
            final_delta if final_p75 is None else final_p75,
        ]
        peak_delta_range = [
            peak_delta if peak_p25 is None else peak_p25,
            peak_delta if peak_p75 is None else peak_p75,
        ]
        return {
            "mode": "TARGET_SUPPLY_FLOW",
            "available": True,
            "reason_codes": ["FLOW_PROTOTYPE_MATCHED"],
            "prototype_id": str(profile.get("prototype_id", "")),
            "tower_id": str(profile.get("tower_id", "")),
            "action_direction": str(profile.get("action_direction", "")),
            "flow_shape": str(profile.get("flow_shape", "")),
            "flow_execution_profile": str(
                profile.get("flow_execution_profile", "")
            ),
            "current_flow": current_flow,
            "target_final_flow": max(0.0, current_flow + final_delta),
            "target_peak_flow": max(0.0, current_flow + peak_delta),
            "target_final_flow_range": [
                max(0.0, current_flow + min(final_delta_range)),
                max(0.0, current_flow + max(final_delta_range)),
            ],
            "target_peak_flow_range": [
                max(0.0, current_flow + min(peak_delta_range)),
                max(0.0, current_flow + max(peak_delta_range)),
            ],
            # Target bands come from historical IQR. The small proportional
            # floor only prevents zero-width bands when all examples agree.
            "final_flow_tolerance": max(final_iqr / 2.0, abs(final_delta) * 0.1, 1e-6),
            "peak_flow_tolerance": max(peak_iqr / 2.0, abs(peak_delta) * 0.1, 1e-6),
            "expected_effect": dict(profile.get("effect", {}) or {}),
            "expected_timing": dict(profile.get("timing", {}) or {}),
            "execution_evidence": dict(profile.get("execution", {}) or {}),
            "evidence": {
                "status": evidence.get("status"),
                "event_count": int(profile.get("event_count", 0)),
                "reliability": dict(evidence.get("reliability", {}) or {}),
            },
        }

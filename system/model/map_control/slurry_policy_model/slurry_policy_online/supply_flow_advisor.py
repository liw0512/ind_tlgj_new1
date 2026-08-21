from __future__ import annotations

import json
import math
from typing import Any, Dict, Iterable, List, Optional


FAST_PROFILE_KINDS = {
    "FAST_EXACT",
    "FAST_DIRECTION_SEVERITY_POOL",
    "FAST_PLANT_SAFE_BASELINE",
}


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


def _mapping(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            decoded = json.loads(value)
        except Exception:
            return {}
        return dict(decoded) if isinstance(decoded, dict) else {}
    return {}


def _sequence(value: Any) -> List[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    if isinstance(value, str) and value.strip():
        try:
            decoded = json.loads(value)
        except Exception:
            decoded = None
        if isinstance(decoded, list):
            return [str(item) for item in decoded]
    return []


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
    ph = finite_number(process.get(str(tower["ph_column"])) )
    if ph is None:
        return False
    lo, hi = [float(value) for value in tower["ph_safe_range"]]
    return lo <= ph <= hi


class SupplyFlowAdvisor:
    """Select one evidence-backed target-flow recommendation.

    FAST_CHANGE is deliberately routed before the regular economic policy:
    exact causal FAST history -> same direction/severity pool -> plant-wide
    historically safe increase baseline -> HOLD.  A missing exact history is
    therefore not equivalent to "no protective action".
    """

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

    @staticmethod
    def _fast_rank(profile: Dict[str, Any]) -> tuple:
        safety = profile.get("safety", {}) or {}
        median_safe = finite_number(safety.get("observed_safe_ratio_median"))
        return (
            0.0 if median_safe is None else median_safe,
            int(profile.get("event_count", 0)),
            -float(profile.get("recommended_delta_flow", 0.0)),
            str(profile.get("prototype_id", "")),
        )

    @staticmethod
    def _primary_fast_rate(process: Dict[str, Any]) -> Optional[float]:
        rates = _mapping(process.get("fast_change_axis_rates"))
        columns = _sequence(process.get("fast_change_axis_columns"))
        candidates = columns + [key for key in rates if key not in columns]
        for column in candidates:
            value = finite_number(rates.get(column))
            if value is not None:
                return value
        return None

    @staticmethod
    def _fast_thresholds(profiles: Iterable[Dict[str, Any]]) -> Dict[str, float]:
        for profile in profiles:
            thresholds = profile.get("fast_rate_thresholds") or {}
            values = {
                key: finite_number(thresholds.get(key))
                for key in ("L1", "L2", "L3")
            }
            if all(values[key] is not None for key in values):
                return {key: float(values[key]) for key in values}
        # Backstop is only for a malformed/legacy snapshot; normal V1 profiles
        # persist their thresholds in the versioned artifact.
        return {"L1": 120.0, "L2": 160.0, "L3": 220.0}

    @staticmethod
    def _fast_level(rate: float, thresholds: Dict[str, float]) -> Optional[str]:
        magnitude = abs(float(rate))
        if magnitude >= float(thresholds["L3"]):
            return "L3"
        if magnitude >= float(thresholds["L2"]):
            return "L2"
        if magnitude >= float(thresholds["L1"]):
            return "L1"
        return None

    def _eligible_fast_profile(
        self,
        profile: Dict[str, Any],
        process: Dict[str, Any],
    ) -> Optional[tuple[dict, float]]:
        tower = self.towers.get(str(profile.get("tower_id", "")))
        if tower is None or not self._tower_is_safe(tower, process):
            return None
        current_flow = self._current_flow(tower, process)
        if current_flow is None:
            return None
        delta = finite_number(profile.get("recommended_delta_flow"))
        if delta is None or delta <= 0:
            return None
        return tower, current_flow

    def _fast_recommendation(
        self,
        condition_label: str,
        acceptable_effect_directions: Iterable[str],
        process: Dict[str, Any],
    ) -> Dict[str, Any]:
        direction = str(process.get("fast_change_direction", "NONE")).upper()
        if direction == "DROP":
            return unavailable_flow_recommendation("FAST_DROP_CONSERVATIVE_HOLD")
        if direction != "RISE":
            return unavailable_flow_recommendation("FAST_MIXED_CONSERVATIVE_HOLD")

        # Adding slurry is expected to push outlet SO2 downward.  The realtime
        # FastActionEnvelope/demand layer is authoritative about whether that
        # effect is currently acceptable. Historical feedforward may choose
        # HOW MUCH, but it cannot override a realtime HOLD/neutral constraint.
        acceptable = {
            str(value).upper() for value in acceptable_effect_directions
        }
        if "DECREASE" not in acceptable:
            return unavailable_flow_recommendation(
                "FAST_ENVELOPE_BLOCKS_PROTECTIVE_INCREASE"
            )

        all_profiles = list(self.loader.load_supply_flow_prototypes().values())
        fast_profiles = [
            profile
            for profile in all_profiles
            if str(profile.get("profile_kind", "")).upper() in FAST_PROFILE_KINDS
            and str(profile.get("action_direction", "")).upper() == "INCREASE"
        ]
        if not fast_profiles:
            return unavailable_flow_recommendation("NO_SAFE_FAST_BASELINE")

        rate = self._primary_fast_rate(process)
        if rate is None:
            return unavailable_flow_recommendation("FAST_AXIS_RATE_UNAVAILABLE")
        thresholds = self._fast_thresholds(fast_profiles)
        level = self._fast_level(rate, thresholds)
        if level is None:
            return unavailable_flow_recommendation("FAST_RATE_BELOW_FEEDFORWARD_L1")
        exact_mode = str(
            process.get("fast_change_exact_trend_mode", "STEADY")
        )

        layers = (
            (
                "FAST_EXACT",
                "FAST_FEEDFORWARD_EXACT_MATCHED",
                lambda profile: (
                    str(profile.get("condition_label", "")) == str(condition_label)
                    and str(profile.get("fast_level", "")).upper() == level
                    and str(profile.get("fast_exact_trend_mode", "")) == exact_mode
                ),
            ),
            (
                "FAST_DIRECTION_SEVERITY_POOL",
                "FAST_FEEDFORWARD_POOL_MATCHED",
                lambda profile: str(profile.get("fast_level", "")).upper() == level,
            ),
            (
                "FAST_PLANT_SAFE_BASELINE",
                "FAST_FEEDFORWARD_PLANT_BASELINE",
                lambda profile: True,
            ),
        )

        for kind, reason, predicate in layers:
            candidates: List[tuple[Dict[str, Any], dict, float]] = []
            for profile in fast_profiles:
                if str(profile.get("profile_kind", "")).upper() != kind:
                    continue
                if not predicate(profile):
                    continue
                eligible = self._eligible_fast_profile(profile, process)
                if eligible is None:
                    continue
                tower, current_flow = eligible
                candidates.append((profile, tower, current_flow))
            if not candidates:
                continue

            profile, _, current_flow = max(
                candidates,
                key=lambda item: self._fast_rank(item[0]),
            )
            delta = float(profile["recommended_delta_flow"])
            target = profile.get("target_flow", {}) or {}
            final_distribution = target.get("final_delta_flow", {}) or {}
            final_p25 = finite_number(final_distribution.get("p25"))
            final_p75 = finite_number(final_distribution.get("p75"))
            low_delta = delta if final_p25 is None else max(0.0, final_p25)
            high_delta = delta if final_p75 is None else max(low_delta, final_p75)
            if delta < low_delta:
                low_delta = delta
            if delta > high_delta:
                high_delta = delta
            tolerance = max(
                abs(high_delta - low_delta) / 2.0,
                abs(delta) * 0.10,
                1e-6,
            )
            target_flow = max(0.0, current_flow + delta)
            target_range = [
                max(0.0, current_flow + low_delta),
                max(0.0, current_flow + high_delta),
            ]
            evidence = profile.get("evidence", {}) or {}
            return {
                "mode": "TARGET_SUPPLY_FLOW",
                "available": True,
                "reason_codes": [
                    reason,
                    "FAST_LEVEL:%s" % level,
                    "FAST_FEEDFORWARD_CAUSAL_HISTORY",
                ],
                "prototype_id": str(profile.get("prototype_id", "")),
                "profile_kind": kind,
                "experience_source": kind,
                "tower_id": str(profile.get("tower_id", "")),
                "action_direction": "INCREASE",
                # One conservative STEP is issued; the next 10-second cycle
                # re-evaluates disturbance/effect state instead of replaying a
                # historical multi-phase peak trajectory blindly.
                "flow_shape": "STEP",
                "flow_execution_profile": "CAUSAL_FAST_ROLLING_STEP",
                "current_flow": current_flow,
                "recommended_delta_flow": delta,
                "target_final_flow": target_flow,
                "target_peak_flow": target_flow,
                "target_final_flow_range": target_range,
                "target_peak_flow_range": target_range,
                "final_flow_tolerance": tolerance,
                "peak_flow_tolerance": tolerance,
                "fast_level": level,
                "fast_primary_axis_rate": float(rate),
                "fast_rate_thresholds": thresholds,
                "expected_effect": dict(profile.get("effect", {}) or {}),
                "expected_timing": dict(profile.get("timing", {}) or {}),
                "execution_evidence": {},
                "evidence": {
                    "status": evidence.get("status", "FAST_SUPPORTED"),
                    "event_count": int(profile.get("event_count", 0)),
                    "source": evidence.get("source", kind),
                    "reliability": dict(evidence.get("reliability", {}) or {}),
                    "selection_quantile": profile.get("selection_quantile"),
                },
            }

        # FAST was detected but no safe/usable historical target survives
        # current tower/pH/meter constraints. This is a genuine safety HOLD.
        return unavailable_flow_recommendation("NO_SAFE_FAST_BASELINE")

    def recommend(
        self,
        condition_label: str,
        acceptable_effect_directions: Iterable[str],
        process: Dict[str, Any],
    ) -> Dict[str, Any]:
        mode = str(process.get("fast_change_mode", "REGULAR")).upper()
        if mode == "FAST_CHANGE":
            return self._fast_recommendation(
                condition_label,
                acceptable_effect_directions,
                process,
            )

        acceptable = {str(value).upper() for value in acceptable_effect_directions}
        candidates: List[tuple[Dict[str, Any], dict, float]] = []
        for profile in self.loader.load_supply_flow_prototypes().values():
            # FAST fallback profiles are a protection-only policy source. They
            # must never leak back into ordinary economic recommendation.
            if str(profile.get("profile_kind", "REGULAR_SUPPLY_FLOW")).upper() != "REGULAR_SUPPLY_FLOW":
                continue
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

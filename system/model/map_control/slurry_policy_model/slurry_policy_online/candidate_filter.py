from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .action_utils import normalize_blocked_valves, parse_action_family, profile_action
from .demand_analyzer import MAGNITUDE_ORDER
from .types import Candidate, ControlDemand, RealtimeState


class CandidateFilter:
    STATUS_KEY = {
        "LOCAL_CONDITION": "local_allowed_status",
        "NEIGHBOR_STATE": "neighbor_allowed_status",
        "TRANSIENT": "transient_allowed_status",
        "PLANT_ACTION_PRIOR": "plant_prior_allowed_status",
    }

    def __init__(self, plant: dict, online_config: dict) -> None:
        self.plant = plant
        self.online = online_config

    def filter(
        self,
        candidates: List[Candidate],
        state: RealtimeState,
        demand: ControlDemand,
        execution_context: Dict[str, Any],
        stability_context: Dict[str, Any],
    ) -> Tuple[List[Candidate], Dict[str, List[str]]]:
        accepted: List[Candidate] = []
        rejected: Dict[str, List[str]] = {}
        for candidate in candidates:
            reasons = self._reasons(candidate, state, demand, execution_context, stability_context)
            if reasons:
                candidate.reject_reasons = reasons
                rejected[candidate.action_id] = reasons
            else:
                accepted.append(candidate)
        return accepted, rejected

    @staticmethod
    def _number(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _predicted_ph_reasons(
        self,
        candidate: Candidate,
        tower_id: str,
        tower: Dict[str, Any],
        current_ph: float,
        direction: str,
    ) -> List[str]:
        """用当前 pH + 该塔历史 ΔpH 分布做连续安全检查。

        不再把 pH 分档加入经验主键。增加供浆时使用历史 ΔpH 的 P75 作为偏保守
        上行估计；减少供浆时使用 P25 作为偏保守下行估计；缺失时退回中位数。
        """
        reasons: List[str] = []
        if candidate.synthetic:
            return reasons

        ph_profile = candidate.profile.get("ph_effect", {}).get(tower_id, {}) or {}
        distribution = ph_profile.get("delta_distribution", {}) or {}
        if direction == "INCREASE":
            delta_ph = self._number(distribution.get("p75"))
        elif direction == "DECREASE":
            delta_ph = self._number(distribution.get("p25"))
        else:
            delta_ph = self._number(distribution.get("median"))
        if delta_ph is None:
            delta_ph = self._number(distribution.get("median"))

        lo, hi = [float(x) for x in tower["ph_safe_range"]]
        if delta_ph is not None:
            predicted_ph = current_ph + delta_ph
            candidate.evaluation.setdefault("tower_ph", {})[tower_id] = {
                "current_ph": current_ph,
                "historical_delta_ph_for_guard": delta_ph,
                "predicted_ph_after": predicted_ph,
                "safe_range": [lo, hi],
            }
            if predicted_ph > hi:
                reasons.append("PREDICTED_PH_ABOVE_SAFE_RANGE:%s" % tower_id)
            if predicted_ph < lo:
                reasons.append("PREDICTED_PH_BELOW_SAFE_RANGE:%s" % tower_id)

        # pH 越界率继续保留为可解释诊断；不额外引入一个新的厂级硬阈值。
        # 历史安全性仍由已有 safety_history_score 门槛控制，在线则由上面的
        # 当前 pH + 历史 ΔpH 预测直接做安全过滤。
        ph_safety = candidate.profile.get("safety", {}).get("tower_ph", {}).get(tower_id, {}) or {}
        out_ratio = self._number(ph_safety.get("out_of_range_ratio"))
        if out_ratio is not None:
            candidate.evaluation.setdefault("tower_ph", {}).setdefault(tower_id, {})[
                "historical_out_of_range_ratio"
            ] = out_ratio
        return reasons

    def _reasons(
        self,
        candidate: Candidate,
        state: RealtimeState,
        demand: ControlDemand,
        execution: Dict[str, Any],
        stability: Dict[str, Any],
    ) -> List[str]:
        profile = candidate.profile
        action = profile_action(profile)
        family = str(action.get("action_family", ""))
        direction = str(action.get("direction", "UNKNOWN")).upper()
        magnitude = str(action.get("magnitude", "UNKNOWN")).upper()
        reasons: List[str] = []

        if candidate.source != "RULE_BASELINE":
            acceptance = self.online["profile_acceptance"]
            status_key = self.STATUS_KEY[candidate.source]
            if str(profile.get("profile_status", "NO_DATA")) not in set(acceptance[status_key]):
                reasons.append("PROFILE_STATUS_NOT_ALLOWED")
            so2_effect = profile.get("so2_effect", {})
            if str(so2_effect.get("dominant_direction", "UNKNOWN")) not in demand.acceptable_effect_directions:
                reasons.append("SO2_EFFECT_DIRECTION_MISMATCH")
            if float(so2_effect.get("direction_consistency", 0.0)) < float(acceptance["minimum_direction_consistency"]):
                reasons.append("DIRECTION_CONSISTENCY_TOO_LOW")
            reliability = profile.get("reliability", {})
            if float(reliability.get("safety_history_score", 0.0)) < float(acceptance["minimum_safety_history_score"]):
                reasons.append("SAFETY_HISTORY_SCORE_TOO_LOW")
            if float(reliability.get("total_score", 0.0)) < float(acceptance["minimum_reliability_total_score"]):
                reasons.append("RELIABILITY_TOO_LOW")
            stable_ratio = float(profile.get("stability", {}).get("stable_response_ratio", 0.0))
            if stable_ratio < float(acceptance["minimum_stable_response_ratio"]):
                reasons.append("STABLE_RESPONSE_RATIO_TOO_LOW")
            if candidate.source == "PLANT_ACTION_PRIOR":
                if not bool(profile.get("spatial_support", {}).get("direction_generalizable", False)):
                    reasons.append("PLANT_PRIOR_NOT_GENERALIZABLE")

        if direction == "MIXED" and not bool(self.online["profile_acceptance"].get("allow_mixed_action", False)):
            reasons.append("MIXED_ACTION_DISABLED")
        if direction == "REBALANCE" and not bool(self.online["profile_acceptance"].get("allow_rebalance_action", False)):
            reasons.append("REBALANCE_ACTION_DISABLED")

        max_magnitude = demand.maximum_action_magnitude
        if MAGNITUDE_ORDER.get(magnitude, 99) > MAGNITUDE_ORDER.get(max_magnitude, 0):
            reasons.append("ACTION_MAGNITUDE_EXCEEDS_CURRENT_LIMIT")

        if direction == "DECREASE" and demand.safety_level in {"WARNING", "EMERGENCY"}:
            reasons.append("SLURRY_DECREASE_BLOCKED_BY_EMISSION_GUARD")
        if (
            direction == "DECREASE"
            and state.control_mode == "FAST_CHANGE"
            and bool(self.online["fast_mode"].get("block_economic_slurry_decrease", True))
        ):
            reasons.append("SLURRY_DECREASE_BLOCKED_IN_FAST_MODE")

        tower_ids, valve_ids = parse_action_family(family, self.plant)
        if direction != "HOLD" and len(tower_ids) > 1 and state.control_mode != "FAST_CHANGE":
            reasons.append("MULTI_TOWER_ACTION_BLOCKED_IN_NORMAL_MODE")

        representative = action.get("representative_delta", {}) or {}
        for valve_id, delta in representative.items():
            try:
                if abs(float(delta)) > 1e-12 and str(valve_id) not in valve_ids:
                    valve_ids.append(str(valve_id))
            except (TypeError, ValueError):
                continue

        # 只有现场实际传入这些状态时才生效；算法不要求人工维护一份 DCS 状态表。
        blocked = normalize_blocked_valves(execution.get("manual_valves"), self.plant)
        blocked |= normalize_blocked_valves(execution.get("faulted_valves"), self.plant)
        if blocked.intersection(valve_ids):
            reasons.append("ACTION_USES_MANUAL_OR_FAULTED_VALVE")
        if bool(execution.get("supply_pump_state_changing", False)) and direction != "HOLD":
            reasons.append("SUPPLY_PUMP_STATE_CHANGING")

        tower_map = {str(t["tower_id"]): t for t in self.plant.get("towers", []) if t.get("enabled", True)}
        for tower_id in tower_ids:
            tower = tower_map.get(tower_id)
            if not tower:
                reasons.append("UNKNOWN_ACTION_TOWER")
                continue
            try:
                ph = float(state.process[str(tower["ph_column"])])
            except (TypeError, ValueError):
                reasons.append("PH_VALUE_INVALID")
                continue
            lo, hi = [float(x) for x in tower["ph_safe_range"]]
            guard = float(tower.get("ph_guard_band", 0.0))
            if direction == "INCREASE" and ph >= hi - guard:
                reasons.append("PH_HIGH_GUARD_BLOCKS_SLURRY_INCREASE:%s" % tower_id)
            if direction == "DECREASE" and ph <= lo + guard:
                reasons.append("PH_LOW_GUARD_BLOCKS_SLURRY_DECREASE:%s" % tower_id)
            reasons.extend(self._predicted_ph_reasons(candidate, tower_id, tower, ph, direction))

        last_direction = str(stability.get("last_action_direction", ""))
        if bool(stability.get("reverse_lock_active", False)) and direction in {"INCREASE", "DECREASE"}:
            opposite = (last_direction == "INCREASE" and direction == "DECREASE") or (
                last_direction == "DECREASE" and direction == "INCREASE"
            )
            if opposite and demand.safety_level != "EMERGENCY":
                reasons.append("REVERSE_ACTION_LOCK_ACTIVE")
        return list(dict.fromkeys(reasons))

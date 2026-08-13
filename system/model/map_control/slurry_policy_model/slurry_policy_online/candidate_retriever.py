from __future__ import annotations

from typing import Any, Dict, List

from .action_utils import profile_action
from .types import Candidate, ControlDemand, RealtimeState


SOURCE_PRIORITY = {
    "TRANSIENT_EXACT": 5,
    "TRANSIENT_DIRECTION_POOL": 4,
    "LOCAL_CONDITION": 4,
    "NEIGHBOR_STATE": 3,
    "PLANT_ACTION_PRIOR": 2,
    "FAST_RULE_BASELINE": 2,
    "RULE_BASELINE": 1,
}


class CandidateRetriever:
    def __init__(self, loader: Any, plant: dict, online_config: dict) -> None:
        self.loader = loader
        self.plant = plant
        self.online = online_config

    @staticmethod
    def _wrap(source: str, owner: str, state_key: str, actions: Dict[str, Any]) -> List[Candidate]:
        return [
            Candidate(
                source=source,
                owner_id=owner,
                state_key=state_key,
                action_id=str(action_id),
                profile=profile,
                source_priority=SOURCE_PRIORITY[source],
            )
            for action_id, profile in actions.items()
        ]

    @staticmethod
    def _profile_strength(profile: Dict[str, Any]) -> tuple[float, float, float]:
        reliability = profile.get("reliability", {}) or {}
        support = profile.get("support", {}) or {}
        return (
            float(reliability.get("total_score", 0.0)),
            float(profile.get("stability", {}).get("stable_response_ratio", 0.0)),
            float(support.get("effective_weighted_event_count", 0.0)),
        )

    def _actions_for_state(self, states: Dict[str, Any], preferred_state: str) -> Dict[str, Any]:
        """读取新粗状态；旧快照没有 REGULAR/TRANSIENT 时做兼容回退。

        新训练版本会把同工况普通历史聚合到 REGULAR，不再按 pH/SO2/阀位继续切桶。
        对仍处于激活状态的旧快照，如果找不到新状态键，就从该工况所有旧细状态中
        按 action_id 选历史可靠性/稳定性/支持量最高的一份，避免代码升级后直接完全
        失去历史候选。重新训练新版本后该兼容路径自然不再使用。
        """
        direct = states.get(preferred_state, {})
        if direct:
            return dict(direct)

        collapsed: Dict[str, Any] = {}
        for actions in states.values():
            if not isinstance(actions, dict):
                continue
            for action_id, profile in actions.items():
                key = str(action_id)
                current = collapsed.get(key)
                if current is None or self._profile_strength(profile) > self._profile_strength(current):
                    collapsed[key] = profile
        return collapsed

    def transient(self, state: RealtimeState) -> List[Candidate]:
        states = self.loader.load_transient(state.disturbance_mode)
        actions = self._actions_for_state(states, state.policy_state_key_no_grid)
        return self._wrap("TRANSIENT_EXACT", state.disturbance_mode, state.policy_state_key_no_grid, actions)

    def transient_direction(self, state: RealtimeState) -> List[Candidate]:
        direction = str(state.fast_context.get("fast_change_direction", "NONE"))
        states = self.loader.load_transient_direction(direction)
        actions = self._actions_for_state(states, state.policy_state_key_no_grid)
        return self._wrap("TRANSIENT_DIRECTION_POOL", direction, state.policy_state_key_no_grid, actions)

    def local(self, state: RealtimeState) -> List[Candidate]:
        bundle = self.loader.load_condition_bundle(state.condition.condition_label)
        states = bundle.get("local", {})
        actions = self._actions_for_state(states, state.policy_state_key_no_grid)
        return self._wrap("LOCAL_CONDITION", state.condition.condition_label, state.policy_state_key_no_grid, actions)

    def neighbor(self, state: RealtimeState) -> List[Candidate]:
        bundle = self.loader.load_condition_bundle(state.condition.condition_label)
        states = bundle.get("neighbor", {})
        actions = self._actions_for_state(states, state.policy_state_key_no_grid)
        return self._wrap("NEIGHBOR_STATE", state.condition.condition_label, state.policy_state_key_no_grid, actions)

    def plant_prior(self) -> List[Candidate]:
        states = self.loader.load_plant_prior()
        actions = states.get("ALL_ACTIONS", {})
        return self._wrap("PLANT_ACTION_PRIOR", "PLANT", "ALL_ACTIONS", actions)

    def rule(
        self,
        demand: ControlDemand,
        state: RealtimeState,
        preferred_effect_direction: str = "",
        source: str = "RULE_BASELINE",
    ) -> Candidate:
        effect_direction = str(preferred_effect_direction or "").upper()
        if not effect_direction:
            effect_direction = {
                "SO2_DOWN": "DECREASE",
                "SO2_UP": "INCREASE",
                "SO2_HOLD": "NEUTRAL",
            }.get(demand.desired_so2_response, "NEUTRAL")
        direction = "HOLD"
        magnitude = "HOLD"
        family = "HOLD"
        if effect_direction == "DECREASE":
            direction = "INCREASE"
            magnitude = "SMALL" if demand.demand_level in {"TARGET_SMALL", "TARGET_HOLD"} else demand.maximum_action_magnitude
            family = self._select_rule_family("INCREASE", state)
        elif effect_direction == "INCREASE":
            direction = "DECREASE"
            magnitude = "MICRO" if demand.maximum_action_magnitude in {"MICRO", "SMALL"} else "SMALL"
            family = self._select_rule_family("DECREASE", state)
        profile = {
            "action_profile": {
                "action_family": family,
                "direction": direction,
                "magnitude": magnitude,
                "representative_delta": {},
            },
            "so2_effect": {
                "dominant_direction": effect_direction,
                "direction_consistency": 0.0,
            },
            "stability": {"stable_response_ratio": 0.0},
            "safety": {"any_safety_violation_ratio": 0.0},
            "reliability": {"total_score": 0.0, "safety_history_score": 0.0},
            "profile_status": "RULE",
        }
        action_id = "%s|%s|%s" % (family, direction, magnitude)
        return Candidate(
            source=source,
            owner_id="FAST_RULE" if source == "FAST_RULE_BASELINE" else "RULE",
            state_key=state.policy_state_key_no_grid,
            action_id=action_id,
            profile=profile,
            source_priority=SOURCE_PRIORITY[source],
            synthetic=True,
        )

    def _select_rule_family(self, direction: str, state: RealtimeState) -> str:
        limits = self.online["execution_limits"]
        preferred_key = "preferred_increase_action_families" if direction == "INCREASE" else "preferred_decrease_action_families"
        preferred = [str(x) for x in limits.get(preferred_key, [])]
        if preferred:
            return preferred[0]

        candidates = []
        for tower in self.plant.get("towers", []):
            if not tower.get("enabled", True):
                continue
            ph = float(state.process[str(tower["ph_column"])])
            lo, hi = [float(x) for x in tower["ph_safe_range"]]
            margin = (hi - ph) if direction == "INCREASE" else (ph - lo)
            candidates.append((margin, tower))
        if not candidates:
            return "HOLD"
        tower = max(candidates, key=lambda item: item[0])[1]
        tower_id = str(tower["tower_id"])
        # 规则回退也使用塔级动作族；具体分阀由 ValveActionResolver 负责。
        return "TOWER:%s|SUPPLY" % tower_id

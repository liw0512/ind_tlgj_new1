from __future__ import annotations

from typing import Any, Dict, List

from .action_utils import parse_action_family, profile_action, valve_lookup
from .types import Candidate, ResolvedAction, RealtimeState


class ActionResolutionError(ValueError):
    pass


class ValveActionResolver:
    """把塔级供浆动作转换为当前厂的具体分阀命令。

    第二模块学习的主对象已经是塔，而不是“阀1/阀2怎么分配”的操作习惯。
    新 ``TOWER:<id>|SUPPLY`` 动作先从历史各分阀代表 delta 求该塔等效归一化
    变化，再把同一塔级等效变化映射到该厂全部分阀；阀位边界和单阀动作上限
    仍在本层统一处理。
    """

    def __init__(self, plant: dict, online_config: dict) -> None:
        self.plant = plant
        self.online = online_config
        self.valves = valve_lookup(plant)

    def _tower(self, tower_id: str) -> Dict[str, Any] | None:
        for tower in self.plant.get("towers", []):
            if tower.get("enabled", True) and str(tower.get("tower_id")) == str(tower_id):
                return tower
        return None

    def _historical_tower_equivalent(
        self, tower_id: str, representative: Dict[str, Any]
    ) -> float | None:
        tower = self._tower(tower_id)
        if not tower:
            return None
        normalized: List[float] = []
        for valve in tower.get("valves", []):
            valve_id = str(valve["valve_id"])
            try:
                delta = float(representative.get(valve_id))
            except (TypeError, ValueError):
                continue
            span = float(valve["max_opening"]) - float(valve["min_opening"])
            if span > 0:
                normalized.append(delta / span)
        if not normalized:
            return None
        return float(sum(normalized) / len(normalized))

    def resolve(self, candidate: Candidate, state: RealtimeState) -> ResolvedAction:
        action = profile_action(candidate.profile)
        family = str(action.get("action_family", "HOLD"))
        direction = str(action.get("direction", "HOLD")).upper()
        magnitude = str(action.get("magnitude", "HOLD")).upper()
        current = {
            valve_id: float(state.process[str(valve["column"])])
            for valve_id, valve in self.valves.items()
        }
        if family == "HOLD" or direction == "HOLD" or magnitude == "HOLD":
            return ResolvedAction(
                action_id=candidate.action_id,
                action_family="HOLD",
                action_direction="HOLD",
                action_magnitude="HOLD",
                recommended_valve_deltas={key: 0.0 for key in self.valves},
                projected_valve_openings=dict(current),
                active_valve_ids=[],
                active_tower_ids=[],
                reason_codes=["HOLD_ACTION"],
            )

        tower_ids, family_valves = parse_action_family(family, self.plant)
        if not family_valves:
            raise ActionResolutionError("动作族无法解析阀门: %s" % family)

        representative = action.get("representative_delta", {}) or {}
        deltas: Dict[str, float] = {key: 0.0 for key in self.valves}
        historical_available = False
        resolution_reason = "RULE_DELTA"

        # V2 新动作：历史分阀 delta 只用来恢复“塔级等效动作”，不照搬历史
        # 某个操作员具体动了哪个分阀。这样一阀/两阀/三阀电厂使用同一塔级逻辑。
        if family.startswith("TOWER:") and "|SUPPLY" in family and len(tower_ids) == 1:
            tower_id = tower_ids[0]
            equivalent = self._historical_tower_equivalent(tower_id, representative)
            if equivalent is not None and abs(equivalent) > 1e-12:
                historical_available = True
                resolution_reason = "TOWER_EQUIVALENT_HISTORICAL_DELTA"
                for valve_id in family_valves:
                    valve = self.valves[valve_id]
                    span = float(valve["max_opening"]) - float(valve["min_opening"])
                    deltas[valve_id] = equivalent * span
                candidate.evaluation["tower_equivalent_normalized_delta"] = equivalent
        else:
            # 兼容旧快照：旧动作族仍按原来的分阀代表增量执行。
            for valve_id in self.valves:
                value = representative.get(valve_id)
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    continue
                if abs(number) > 1e-12:
                    historical_available = True
                    deltas[valve_id] = number
            if historical_available:
                resolution_reason = "LEGACY_HISTORICAL_DELTA"

        if not historical_available:
            step = float(self.online["execution_limits"]["rule_step_by_magnitude"][magnitude])
            signed = step if direction == "INCREASE" else -step
            for valve_id in family_valves:
                deltas[valve_id] = signed

        # 历史中非动作族阀门的微小中位数不作为本次命令。
        allowed = set(family_valves)
        for valve_id in deltas:
            if valve_id not in allowed:
                deltas[valve_id] = 0.0

        if direction == "INCREASE" and any(value < -1e-12 for value in deltas.values()):
            raise ActionResolutionError("历史代表增量方向与INCREASE不一致")
        if direction == "DECREASE" and any(value > 1e-12 for value in deltas.values()):
            raise ActionResolutionError("历史代表增量方向与DECREASE不一致")

        limits = self.online["execution_limits"]
        cap = float(limits["maximum_single_valve_delta_by_magnitude"][magnitude])
        margin = float(limits.get("valve_limit_margin", 0.0))
        scale = 1.0
        for valve_id, delta in deltas.items():
            if abs(delta) <= 1e-12:
                continue
            valve = self.valves[valve_id]
            lo = float(valve["min_opening"]) + margin
            hi = float(valve["max_opening"]) - margin
            available = (hi - current[valve_id]) if delta > 0 else (current[valve_id] - lo)
            allowed_abs = max(0.0, min(cap, available))
            scale = min(scale, allowed_abs / abs(delta))
        if scale <= 0:
            raise ActionResolutionError("阀门边界没有可用动作空间")
        if scale < 1.0:
            deltas = {key: value * scale for key, value in deltas.items()}

        minimum_global = float(limits.get("minimum_command_delta", 0.0))
        active_valves: List[str] = []
        for valve_id, delta in list(deltas.items()):
            threshold = max(minimum_global, float(self.valves[valve_id]["action_threshold"]))
            if abs(delta) < threshold:
                deltas[valve_id] = 0.0
            else:
                active_valves.append(valve_id)
        if not active_valves:
            raise ActionResolutionError("限幅后所有阀门增量均低于有效动作阈值")

        projected = {key: current[key] + deltas[key] for key in self.valves}
        active_towers = sorted({str(self.valves[valve_id]["tower_id"]) for valve_id in active_valves})
        return ResolvedAction(
            action_id=candidate.action_id,
            action_family=family,
            action_direction=direction,
            action_magnitude=magnitude,
            recommended_valve_deltas=deltas,
            projected_valve_openings=projected,
            active_valve_ids=active_valves,
            active_tower_ids=active_towers,
            reason_codes=[resolution_reason, "VALVE_LIMITS_APPLIED"],
        )

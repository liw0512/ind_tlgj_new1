from __future__ import annotations

from typing import Any, Dict, List

try:
    from _engine.supply_pump import evaluate_supply_pump_availability
except ImportError:  # pragma: no cover
    from .._engine.supply_pump import evaluate_supply_pump_availability

from .action_utils import parse_action_family, profile_action, valve_lookup
from .types import Candidate, ResolvedAction, RealtimeState


class ActionResolutionError(ValueError):
    pass


class ValveActionResolver:
    """把塔级供浆动作转换为当前厂的具体分阀命令。

    第二模块学习的主对象是塔级等效供浆动作。在线解析时先根据实时供浆泵
    电流判断每个阀门是否存在可用供浆路径，再只向可用阀门映射动作。
    停泵支路不会把缺失动作补偿到其他阀门。
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

        requested_family_valves = list(dict.fromkeys(family_valves))
        pump_availability = evaluate_supply_pump_availability(
            self.plant,
            state.process,
        )
        candidate.evaluation["supply_pump_availability"] = pump_availability
        available_valves = set(pump_availability["available_valve_ids"])
        family_valves = [
            valve_id
            for valve_id in requested_family_valves
            if valve_id in available_valves
        ]
        if not family_valves:
            raise ActionResolutionError("动作族没有运行供浆泵对应的可用阀门")

        # 对多塔动作再做一次安全校验：不能因为某一座塔全部停泵而把原多塔动作
        # 悄悄退化成另一座塔的单塔动作。
        for tower_id in tower_ids:
            tower = self._tower(tower_id)
            if not tower:
                continue
            tower_valves = {
                str(v["valve_id"])
                for v in tower.get("valves", [])
            }
            requested_for_tower = tower_valves & set(requested_family_valves)
            if requested_for_tower and not (
                requested_for_tower & available_valves
            ):
                raise ActionResolutionError(
                    "塔 %s 没有运行供浆泵对应的可用阀门" % tower_id
                )

        representative = action.get("representative_delta", {}) or {}
        deltas: Dict[str, float] = {key: 0.0 for key in self.valves}
        historical_available = False
        resolution_reason = "RULE_DELTA"

        # 历史分阀 delta 只用于恢复塔级等效动作。等效量仍基于完整历史塔动作，
        # 但在线只映射到当前有运行供浆泵支撑的阀门；不会把停泵支路的份额
        # 额外补到其他阀门。
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
            # 兼容旧动作族：仍读取原代表分阀增量，但只允许当前泵可用阀门执行。
            for valve_id in family_valves:
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
        reason_codes = [resolution_reason]
        if set(requested_family_valves) - set(family_valves):
            reason_codes.append("SUPPLY_PUMP_VALVE_AVAILABILITY_APPLIED")
        if pump_availability.get("invalid_pump_ids"):
            reason_codes.append("SUPPLY_PUMP_CURRENT_INVALID_FAILSAFE")
        reason_codes.append("VALVE_LIMITS_APPLIED")
        return ResolvedAction(
            action_id=candidate.action_id,
            action_family=family,
            action_direction=direction,
            action_magnitude=magnitude,
            recommended_valve_deltas=deltas,
            projected_valve_openings=projected,
            active_valve_ids=active_valves,
            active_tower_ids=active_towers,
            reason_codes=reason_codes,
        )

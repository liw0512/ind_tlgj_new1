from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

from system.model.config.operator_settings import effective_ph_safe_range

try:
    from _engine.config_loader import enabled_towers
    from _engine.schema import OUTLET_SO2_COLUMN, condition_axis_columns
    from _engine.state_builder import build_policy_state
except ImportError:  # pragma: no cover
    from .._engine.config_loader import enabled_towers
    from .._engine.schema import OUTLET_SO2_COLUMN, condition_axis_columns
    from .._engine.state_builder import build_policy_state

from .types import ConditionContext, RealtimeState


class RealtimeDataError(ValueError):
    pass


def _number(data: Dict[str, Any], key: str, required: bool = True) -> float:
    value = data.get(key)
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float("nan")
    if required and not np.isfinite(number):
        raise RealtimeDataError("实时字段缺失或不是有限数值: %s" % key)
    return number


class RealtimeStateBuilder:
    def __init__(self, plant: dict, training: dict) -> None:
        self.plant = plant
        self.training = training

    def _sync_effective_ph_ranges(self) -> None:
        """把当前操作员有效 pH 范围同步到在线 plant 对象。

        OnlineSlurryPolicy、CandidateFilter、RealtimeStateBuilder 共用同一个 plant 对象，
        因此这里同步后，本周期的状态构建、pH reason 和候选动作安全过滤会使用同一范围。
        当操作员“恢复默认”后，effective_ph_safe_range() 会重新返回中央 plant_config 默认值。
        """
        for tower in self.plant.get("towers", []) or []:
            tower_id = str(tower.get("tower_id", ""))
            if not tower_id:
                continue
            low, high = effective_ph_safe_range(tower_id)
            tower["ph_safe_range"] = [low, high]

    def validate_and_build(
        self,
        timestamp: pd.Timestamp,
        process: Dict[str, Any],
        condition: ConditionContext,
        fast_context: Dict[str, Any],
    ) -> RealtimeState:
        self._sync_effective_ph_ranges()
        plant = self.plant

        outlet = _number(process, OUTLET_SO2_COLUMN)
        axes = condition_axis_columns(self.training)
        rates = dict(fast_context.get("fast_change_axis_rates") or {})
        first_rate = float(rates.get(axes[0], 0.0) or 0.0)
        second_rate = float(rates.get(axes[1], 0.0) or 0.0) if len(axes) > 1 else 0.0
        outlet_rate = float(fast_context.get("fast_change_outlet_so2_rate", 0.0) or 0.0)
        disturbance_mode = str(fast_context.get("fast_change_exact_trend_mode", "STEADY"))
        control_mode = str(fast_context.get("fast_change_mode", "REGULAR"))

        row: Dict[str, Any] = {
            "anchor_grid_id": condition.raw_grid_id,
            "condition_state_key": condition.state_key,
            "before_outlet_so2": outlet,
            "before_outlet_so2_rate": outlet_rate,
            "disturbance_mode": disturbance_mode,
        }
        for tower in enabled_towers(plant):
            tower_id = str(tower["tower_id"])
            row["before_ph__%s" % tower_id] = _number(process, str(tower["ph_column"]))
            for valve in tower.get("valves", []):
                valve_id = str(valve["valve_id"])
                row["before_valve__%s" % valve_id] = _number(process, str(valve["column"]))
        policy_state, no_grid = build_policy_state(row, plant, self.training)
        return RealtimeState(
            timestamp=timestamp.isoformat(),
            condition=condition,
            process=dict(process),
            load_rate=first_rate,
            inlet_so2_rate=second_rate,
            outlet_so2_rate=outlet_rate,
            disturbance_mode=disturbance_mode,
            control_mode=control_mode,
            fast_context=dict(fast_context),
            policy_state_key=policy_state,
            policy_state_key_no_grid=no_grid,
        )

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

from system.model.config.operator_settings import effective_plant_config

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

    def validate_and_build(
        self,
        timestamp: pd.Timestamp,
        process: Dict[str, Any],
        condition: ConditionContext,
        fast_context: Dict[str, Any],
    ) -> RealtimeState:
        # pH 安全范围允许操作员在运行时覆盖；未覆盖时保持模型/plant_config 内部默认值。
        # 这里只生成当前周期的有效 plant 副本，不修改模型快照本体。
        plant = effective_plant_config(self.plant)

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

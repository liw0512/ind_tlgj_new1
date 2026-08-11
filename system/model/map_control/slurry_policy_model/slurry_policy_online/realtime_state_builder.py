from __future__ import annotations

from typing import Any, Dict, Tuple

import numpy as np
import pandas as pd

try:
    from _engine.config_loader import enabled_towers
    from _engine.schema import INLET_SO2_COLUMN, LOAD_COLUMN, OUTLET_SO2_COLUMN
    from _engine.state_builder import build_policy_state
except ImportError:  # pragma: no cover
    from .._engine.config_loader import enabled_towers
    from .._engine.schema import INLET_SO2_COLUMN, LOAD_COLUMN, OUTLET_SO2_COLUMN
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
        disturbance: Dict[str, Any],
    ) -> RealtimeState:
        load = _number(process, LOAD_COLUMN)
        inlet = _number(process, INLET_SO2_COLUMN)
        outlet = _number(process, OUTLET_SO2_COLUMN)
        row: Dict[str, Any] = {
            "anchor_grid_id": condition.raw_grid_id,
            "condition_state_key": condition.state_key,
            "before_outlet_so2": outlet,
            "before_outlet_so2_rate": float(disturbance["outlet_so2_rate"]),
            "disturbance_mode": str(disturbance["disturbance_mode"]),
        }
        for tower in enabled_towers(self.plant):
            tower_id = str(tower["tower_id"])
            row["before_ph__%s" % tower_id] = _number(process, str(tower["ph_column"]))
            for valve in tower.get("valves", []):
                valve_id = str(valve["valve_id"])
                row["before_valve__%s" % valve_id] = _number(process, str(valve["column"]))
        policy_state, no_grid = build_policy_state(row, self.plant, self.training)
        return RealtimeState(
            timestamp=timestamp.isoformat(),
            condition=condition,
            process=dict(process),
            load_rate=float(disturbance["load_rate"]),
            inlet_so2_rate=float(disturbance["inlet_so2_rate"]),
            outlet_so2_rate=float(disturbance["outlet_so2_rate"]),
            disturbance_mode=str(disturbance["disturbance_mode"]),
            control_mode=str(disturbance["control_mode"]),
            policy_state_key=policy_state,
            policy_state_key_no_grid=no_grid,
        )

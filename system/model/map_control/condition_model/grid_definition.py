# -*- coding: utf-8 -*-
"""Complete fixed grid creation, mapping and adjacency."""

import math
from typing import Dict, List, Tuple

from system.model.map_control.condition_model.condition_config import AxisConfig, ConditionModelConfig
from system.model.map_control.condition_model.condition_schema import GridCell


def create_complete_grid(config: ConditionModelConfig) -> Dict[str, GridCell]:
    config.validate()
    catalog = {}
    for p_index in range(config.load.cell_count):
        load_low = config.load.minimum + p_index * config.load.step
        load_high = config.load.maximum if p_index == config.load.cell_count - 1 else load_low + config.load.step
        for s_index in range(config.inlet_so2.cell_count):
            so2_low = config.inlet_so2.minimum + s_index * config.inlet_so2.step
            so2_high = config.inlet_so2.maximum if s_index == config.inlet_so2.cell_count - 1 else so2_low + config.inlet_so2.step
            grid_id = f"P{p_index + 1}-S{s_index + 1}"
            catalog[grid_id] = GridCell(
                grid_id=grid_id,
                load_level=p_index + 1,
                inlet_so2_level=s_index + 1,
                load_range=(load_low, load_high),
                inlet_so2_range=(so2_low, so2_high),
                policy_region_id=f"R_{grid_id.replace('-', '_')}",
            )
    return catalog


def _locate(value: float, axis: AxisConfig) -> Tuple[int, bool]:
    clipped = value < axis.minimum or value > axis.maximum
    bounded = min(max(value, axis.minimum), axis.maximum)
    if math.isclose(bounded, axis.maximum):
        return axis.cell_count, clipped
    return min(axis.cell_count, int((bounded - axis.minimum) // axis.step) + 1), clipped


def locate_grid(load_value: float, inlet_so2: float, config: ConditionModelConfig) -> Tuple[str, bool, str]:
    p_level, load_clipped = _locate(float(load_value), config.load)
    s_level, so2_clipped = _locate(float(inlet_so2), config.inlet_so2)
    if load_clipped and so2_clipped:
        clip_axis = "both"
    elif load_clipped:
        clip_axis = "load"
    elif so2_clipped:
        clip_axis = "inlet_so2"
    else:
        clip_axis = "none"
    return f"P{p_level}-S{s_level}", load_clipped or so2_clipped, clip_axis


def build_fixed_adjacency(catalog: Dict[str, GridCell]) -> Dict[str, List[str]]:
    by_coordinate = {(cell.load_level, cell.inlet_so2_level): cell.grid_id for cell in catalog.values()}
    result = {}
    for cell in catalog.values():
        neighbors = []
        for delta_p, delta_s in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            neighbor = by_coordinate.get((cell.load_level + delta_p, cell.inlet_so2_level + delta_s))
            if neighbor:
                neighbors.append(neighbor)
        result[cell.grid_id] = sorted(neighbors)
    return result

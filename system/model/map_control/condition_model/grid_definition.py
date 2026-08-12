# -*- coding: utf-8 -*-
"""Generic one/two-axis fixed-grid creation, mapping and adjacency.

P/S remain stable grid-id slot codes only. All runtime/config names are generic
axis_1/axis_2 and never imply load or inlet SO2 semantics.
"""

import math
from typing import Dict, List, Tuple

from system.model.map_control.condition_model.condition_config import (
    ConditionAxisConfig,
    ConditionModelConfig,
)
from system.model.map_control.condition_model.condition_schema import GridCell


def create_complete_grid(config: ConditionModelConfig) -> Dict[str, GridCell]:
    config.validate()
    axis_1 = config.axis_1
    axis_2 = config.axis_2
    catalog = {}
    for p_index in range(axis_1.cell_count):
        first_low = axis_1.minimum + p_index * axis_1.step
        first_high = axis_1.maximum if p_index == axis_1.cell_count - 1 else first_low + axis_1.step
        for s_index in range(axis_2.cell_count):
            second_low = axis_2.minimum + s_index * axis_2.step
            second_high = axis_2.maximum if s_index == axis_2.cell_count - 1 else second_low + axis_2.step
            grid_id = f"P{p_index + 1}-S{s_index + 1}"
            catalog[grid_id] = GridCell(
                grid_id=grid_id,
                axis_1_level=p_index + 1,
                axis_2_level=s_index + 1,
                axis_1_range=(first_low, first_high),
                axis_2_range=(second_low, second_high),
                policy_region_id=f"R_{grid_id.replace('-', '_')}",
            )
    return catalog


def _locate(value: float, axis: ConditionAxisConfig) -> Tuple[int, bool]:
    clipped = value < axis.minimum or value > axis.maximum
    bounded = min(max(value, axis.minimum), axis.maximum)
    if math.isclose(bounded, axis.maximum):
        return axis.cell_count, clipped
    return min(axis.cell_count, int((bounded - axis.minimum) // axis.step) + 1), clipped


def locate_grid(first_axis_value: float, second_axis_value: float, config: ConditionModelConfig) -> Tuple[str, bool, str]:
    """Locate one row in the configured one/two-axis fixed grid."""
    p_level, first_clipped = _locate(float(first_axis_value), config.axis_1)
    s_level, second_clipped = _locate(float(second_axis_value), config.axis_2)

    if first_clipped and second_clipped and not config.single_axis_mode:
        clip_axis = ",".join(config.condition_axis_columns)
    elif first_clipped:
        clip_axis = config.axis_1.column
    elif second_clipped and not config.single_axis_mode:
        clip_axis = config.axis_2.column
    else:
        clip_axis = "none"

    return (
        f"P{p_level}-S{s_level}",
        first_clipped or (second_clipped and not config.single_axis_mode),
        clip_axis,
    )


def build_fixed_adjacency(catalog: Dict[str, GridCell]) -> Dict[str, List[str]]:
    by_coordinate = {(cell.axis_1_level, cell.axis_2_level): cell.grid_id for cell in catalog.values()}
    result = {}
    for cell in catalog.values():
        neighbors = []
        for delta_p, delta_s in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            neighbor = by_coordinate.get((cell.axis_1_level + delta_p, cell.axis_2_level + delta_s))
            if neighbor:
                neighbors.append(neighbor)
        result[cell.grid_id] = sorted(neighbors)
    return result

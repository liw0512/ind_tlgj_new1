# -*- coding: utf-8 -*-
"""Complete fixed-grid creation, mapping and adjacency.

``P`` and ``S`` are stable internal slot codes for the first and second
configured condition axes.  They no longer imply physical meanings such as
Power or SO2.  In one-axis mode the S slot is an internal singleton, so the
published grid is effectively one-dimensional while keeping historical grid-id
compatibility.
"""

import math
from typing import Dict, List, Tuple

from system.model.map_control.condition_model.condition_config import (
    AxisConfig,
    ConditionModelConfig,
)
from system.model.map_control.condition_model.condition_schema import GridCell


def create_complete_grid(config: ConditionModelConfig) -> Dict[str, GridCell]:
    config.validate()
    catalog = {}
    for p_index in range(config.load.cell_count):
        first_low = config.load.minimum + p_index * config.load.step
        first_high = (
            config.load.maximum
            if p_index == config.load.cell_count - 1
            else first_low + config.load.step
        )
        for s_index in range(config.inlet_so2.cell_count):
            second_low = config.inlet_so2.minimum + s_index * config.inlet_so2.step
            second_high = (
                config.inlet_so2.maximum
                if s_index == config.inlet_so2.cell_count - 1
                else second_low + config.inlet_so2.step
            )
            grid_id = f"P{p_index + 1}-S{s_index + 1}"
            catalog[grid_id] = GridCell(
                grid_id=grid_id,
                # Historical field names are kept inside the snapshot schema
                # for backward compatibility.  They mean axis-slot 1/2.
                load_level=p_index + 1,
                inlet_so2_level=s_index + 1,
                load_range=(first_low, first_high),
                inlet_so2_range=(second_low, second_high),
                policy_region_id=f"R_{grid_id.replace('-', '_')}",
            )
    return catalog


def _locate(value: float, axis: AxisConfig) -> Tuple[int, bool]:
    clipped = value < axis.minimum or value > axis.maximum
    bounded = min(max(value, axis.minimum), axis.maximum)
    if math.isclose(bounded, axis.maximum):
        return axis.cell_count, clipped
    return (
        min(axis.cell_count, int((bounded - axis.minimum) // axis.step) + 1),
        clipped,
    )


def locate_grid(
    first_axis_value: float,
    second_axis_value: float,
    config: ConditionModelConfig,
) -> Tuple[str, bool, str]:
    """Locate one row in the configured one/two-axis fixed grid.

    In one-axis mode ``second_axis_value`` is intentionally ignored by the
    singleton S slot.  Callers may keep passing the same source value for both
    internal slots, which avoids any synthetic source column.
    """

    p_level, first_clipped = _locate(float(first_axis_value), config.load)
    s_level, second_clipped = _locate(
        float(second_axis_value),
        config.inlet_so2,
    )

    if first_clipped and second_clipped:
        clip_axis = ",".join(config.condition_axis_columns)
    elif first_clipped:
        clip_axis = config.load_column
    elif second_clipped and not config.single_axis_mode:
        clip_axis = config.inlet_so2_column
    else:
        clip_axis = "none"

    return (
        f"P{p_level}-S{s_level}",
        first_clipped or (second_clipped and not config.single_axis_mode),
        clip_axis,
    )


def build_fixed_adjacency(catalog: Dict[str, GridCell]) -> Dict[str, List[str]]:
    by_coordinate = {
        (cell.load_level, cell.inlet_so2_level): cell.grid_id
        for cell in catalog.values()
    }
    result = {}
    for cell in catalog.values():
        neighbors = []
        for delta_p, delta_s in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            neighbor = by_coordinate.get(
                (
                    cell.load_level + delta_p,
                    cell.inlet_so2_level + delta_s,
                )
            )
            if neighbor:
                neighbors.append(neighbor)
        result[cell.grid_id] = sorted(neighbors)
    return result

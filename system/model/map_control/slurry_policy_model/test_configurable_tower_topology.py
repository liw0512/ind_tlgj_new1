# -*- coding: utf-8 -*-
"""Regression tests for single-/dual-tower and arbitrary valve counts."""

import math

from system.model.map_control.slurry_policy_model._engine.action_detector import (
    _classify_action,
)
from system.model.map_control.slurry_policy_model._engine.config_loader import (
    all_valves,
    enabled_towers,
    validate_plant_config,
)
from system.model.map_control.slurry_policy_model._engine.data_loader import (
    required_columns,
)
from system.model.map_control.slurry_policy_model.slurry_policy_online.valve_action_resolver import (
    ValveActionResolver,
)


def _valve(valve_id, column):
    return {
        "valve_id": valve_id,
        "column": column,
        "min_opening": 0.0,
        "max_opening": 100.0,
        "action_threshold": 0.5,
    }


def _tower(tower_id, ph_column, valves, enabled=True):
    return {
        "tower_id": tower_id,
        "enabled": enabled,
        "ph_column": ph_column,
        "ph_safe_range": [4.5, 6.5],
        "ph_guard_band": 0.15,
        "valves": valves,
    }


def _plant(towers):
    return {
        "paths": {
            "output_root": "unused",
            "condition_snapshots_dir": "unused",
        },
        "time_column": "date",
        "outlet_so2_safe_range": [0.0, 35.0],
        "supply_pump_state_columns": [],
        "towers": towers,
    }


def _training():
    return {
        "_condition_axes": [
            {"column": "axis_a", "min": 0.0, "max": 10.0, "step": 1.0}
        ]
    }


def test_single_tower_one_valve():
    plant = _plant(
        [
            _tower("xst", "xstjy_PH", [_valve("xst_v1", "xst_FMKD1")]),
            _tower("apt", "aptjy_PH", [_valve("apt_v1", "apt_FMKD")], enabled=False),
        ]
    )
    validate_plant_config(plant)
    assert [t["tower_id"] for t in enabled_towers(plant)] == ["xst"]
    assert [v["valve_id"] for v in all_valves(plant)] == ["xst_v1"]

    required = required_columns(plant, _training())
    assert "xstjy_PH" in required
    assert "xst_FMKD1" in required
    assert "aptjy_PH" not in required
    assert "apt_FMKD" not in required

    family, direction, magnitude, active_valves, active_towers, _ = _classify_action(
        {"xst_v1": 2.0}, plant, {}
    )
    assert family == "TOWER:xst|SUPPLY"
    assert direction == "INCREASE"
    assert active_valves == ["xst_v1"]
    assert active_towers == ["xst"]
    assert math.isclose(magnitude, 0.02, rel_tol=1e-9)

    resolver = ValveActionResolver(plant, {})
    equivalent = resolver._historical_tower_equivalent("xst", {"xst_v1": 2.0})
    assert math.isclose(equivalent, 0.02, rel_tol=1e-9)


def test_single_tower_two_valves_uses_tower_equivalent_not_sum():
    plant = _plant(
        [
            _tower(
                "xst",
                "xstjy_PH",
                [
                    _valve("xst_v1", "xst_FMKD1"),
                    _valve("xst_v2", "xst_FMKD2"),
                ],
            )
        ]
    )
    validate_plant_config(plant)
    required = required_columns(plant, _training())
    assert "xst_FMKD1" in required and "xst_FMKD2" in required

    family, direction, magnitude, active_valves, active_towers, _ = _classify_action(
        {"xst_v1": 2.0, "xst_v2": 4.0}, plant, {}
    )
    assert family == "TOWER:xst|SUPPLY"
    assert direction == "INCREASE"
    assert set(active_valves) == {"xst_v1", "xst_v2"}
    assert active_towers == ["xst"]
    # Mean normalized change: (2/100 + 4/100) / 2 = 0.03, not 0.06.
    assert math.isclose(magnitude, 0.03, rel_tol=1e-9)

    resolver = ValveActionResolver(plant, {})
    equivalent = resolver._historical_tower_equivalent(
        "xst", {"xst_v1": 2.0, "xst_v2": 4.0}
    )
    assert math.isclose(equivalent, 0.03, rel_tol=1e-9)


def test_dual_tower_keeps_tower_actions_separate():
    plant = _plant(
        [
            _tower(
                "xst",
                "xstjy_PH",
                [
                    _valve("xst_v1", "xst_FMKD1"),
                    _valve("xst_v2", "xst_FMKD2"),
                ],
            ),
            _tower("apt", "aptjy_PH", [_valve("apt_v1", "apt_FMKD")]),
        ]
    )
    validate_plant_config(plant)
    required = required_columns(plant, _training())
    assert "xstjy_PH" in required and "aptjy_PH" in required
    assert "xst_FMKD1" in required and "xst_FMKD2" in required
    assert "apt_FMKD" in required

    xst_family, _, _, _, xst_towers, _ = _classify_action(
        {"xst_v1": 2.0, "xst_v2": 2.0, "apt_v1": 0.0}, plant, {}
    )
    assert xst_family == "TOWER:xst|SUPPLY"
    assert xst_towers == ["xst"]

    apt_family, _, _, _, apt_towers, _ = _classify_action(
        {"xst_v1": 0.0, "xst_v2": 0.0, "apt_v1": 3.0}, plant, {}
    )
    assert apt_family == "TOWER:apt|SUPPLY"
    assert apt_towers == ["apt"]

    joint_family, _, _, _, joint_towers, _ = _classify_action(
        {"xst_v1": 2.0, "xst_v2": 2.0, "apt_v1": 3.0}, plant, {}
    )
    assert joint_family == "MULTI_TOWER:apt+xst|COMBINED"
    assert joint_towers == ["apt", "xst"]


def main():
    test_single_tower_one_valve()
    test_single_tower_two_valves_uses_tower_equivalent_not_sum()
    test_dual_tower_keeps_tower_actions_separate()
    print("CONFIGURABLE_TOWER_TOPOLOGY_TEST_PASSED")


if __name__ == "__main__":
    main()

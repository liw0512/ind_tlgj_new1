# -*- coding: utf-8 -*-
"""Core structural regression checks for slurry_policy_model.

Replaces many development-time micro/performance/self-check scripts with a
small scenario suite that protects cross-plant topology and execution safety.
"""

from __future__ import annotations

import math

import pandas as pd

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
from system.model.map_control.slurry_policy_model._engine.supply_pump import (
    detect_supply_pump_state_change,
    evaluate_supply_pump_availability,
    pump_state_from_current,
)
from system.model.map_control.slurry_policy_model.slurry_policy_online.candidate_filter import (
    CandidateFilter,
)
from system.model.map_control.slurry_policy_model.slurry_policy_online.types import (
    Candidate,
    ConditionContext,
    ControlDemand,
    RealtimeState,
)
from system.model.map_control.slurry_policy_model.slurry_policy_online.valve_action_resolver import (
    ActionResolutionError,
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


def _pump(pump_id, current_column, served_valve_ids, threshold=10.0):
    return {
        "pump_id": pump_id,
        "current_column": current_column,
        "run_current_threshold": threshold,
        "served_valve_ids": list(served_valve_ids),
    }


def _tower(tower_id, ph_column, valves, *, pumps=None, enabled=True):
    tower = {
        "tower_id": tower_id,
        "enabled": enabled,
        "ph_column": ph_column,
        "ph_safe_range": [4.5, 6.5],
        "ph_guard_band": 0.15,
        "valves": valves,
    }
    if pumps is not None:
        tower["supply_pumps"] = pumps
    return tower


def _plant(towers):
    return {
        "paths": {
            "output_root": "unused",
            "condition_snapshots_dir": "unused",
        },
        "time_column": "date",
        "outlet_so2_safe_range": [0.0, 35.0],
        "towers": towers,
    }


def _training(axes=None):
    return {
        "_condition_axes": axes
        or [{"column": "axis_a", "min": 0.0, "max": 10.0, "step": 1.0}]
    }


def _online_config():
    return {
        "profile_acceptance": {
            "allow_mixed_action": False,
            "allow_rebalance_action": False,
        },
        "fast_mode": {"block_economic_slurry_decrease": True},
        "execution_limits": {
            "valve_limit_margin": 0.0,
            "maximum_single_valve_delta_by_magnitude": {
                "MICRO": 10.0,
                "SMALL": 10.0,
                "MEDIUM": 10.0,
                "STRONG": 10.0,
            },
            "rule_step_by_magnitude": {
                "MICRO": 0.6,
                "SMALL": 1.2,
                "MEDIUM": 2.5,
                "STRONG": 4.0,
            },
            "minimum_command_delta": 0.1,
        },
    }


def _state(process):
    return RealtimeState(
        timestamp="2026-08-12T12:00:00",
        condition=ConditionContext(
            condition_snapshot_version="v001",
            condition_label="C1",
            condition_stable=True,
            condition_valid=True,
        ),
        process=process,
        load_rate=0.0,
        inlet_so2_rate=0.0,
        outlet_so2_rate=0.0,
        disturbance_mode="REGULAR",
        control_mode="NORMAL",
        policy_state_key="C1|REGULAR",
        policy_state_key_no_grid="REGULAR",
    )


def _candidate():
    return Candidate(
        source="RULE_BASELINE",
        owner_id="RULE",
        state_key="REGULAR",
        action_id="XST-INCREASE-SMALL",
        source_priority=1,
        synthetic=True,
        profile={
            "action_profile": {
                "action_family": "TOWER:xst|SUPPLY",
                "direction": "INCREASE",
                "magnitude": "SMALL",
                "representative_delta": {"xst_v1": 2.0, "xst_v2": 4.0},
            }
        },
    )


def _demand():
    return ControlDemand(
        commanded_target=20.0,
        effective_target=20.0,
        current_so2=25.0,
        error=5.0,
        demand_level="TARGET_MEDIUM",
        desired_so2_response="DECREASE",
        acceptable_effect_directions=["DECREASE"],
        maximum_action_magnitude="STRONG",
        safety_level="NORMAL",
    )


def test_arbitrary_condition_axis_and_tower_topology():
    single = _plant(
        [
            _tower("xst", "xstjy_PH", [_valve("xst_v1", "xst_FMKD1")]),
            _tower(
                "apt",
                "aptjy_PH",
                [_valve("apt_v1", "apt_FMKD")],
                enabled=False,
            ),
        ]
    )
    validate_plant_config(single)
    assert [t["tower_id"] for t in enabled_towers(single)] == ["xst"]
    assert [v["valve_id"] for v in all_valves(single)] == ["xst_v1"]

    required = required_columns(
        single,
        _training(
            [
                {
                    "column": "blast_pressure",
                    "min": 100.0,
                    "max": 400.0,
                    "step": 100.0,
                }
            ]
        ),
    )
    assert "blast_pressure" in required
    assert "jzfh" not in required and "yyq_SO2" not in required
    assert "xstjy_PH" in required and "aptjy_PH" not in required

    two_valve = _plant(
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
    family, direction, magnitude, active_valves, active_towers, _ = _classify_action(
        {"xst_v1": 2.0, "xst_v2": 4.0}, two_valve, {}
    )
    assert family == "TOWER:xst|SUPPLY"
    assert direction == "INCREASE"
    assert set(active_valves) == {"xst_v1", "xst_v2"}
    assert active_towers == ["xst"]
    assert math.isclose(magnitude, 0.03, rel_tol=1e-9)

    dual = _plant(
        [
            _tower(
                "xst",
                "xstjy_PH",
                [_valve("xst_v1", "xst_FMKD1"), _valve("xst_v2", "xst_FMKD2")],
            ),
            _tower("apt", "aptjy_PH", [_valve("apt_v1", "apt_FMKD")]),
        ]
    )
    validate_plant_config(dual)
    xst_family, _, _, _, xst_towers, _ = _classify_action(
        {"xst_v1": 2.0, "xst_v2": 2.0, "apt_v1": 0.0}, dual, {}
    )
    apt_family, _, _, _, apt_towers, _ = _classify_action(
        {"xst_v1": 0.0, "xst_v2": 0.0, "apt_v1": 3.0}, dual, {}
    )
    assert xst_family == "TOWER:xst|SUPPLY" and xst_towers == ["xst"]
    assert apt_family == "TOWER:apt|SUPPLY" and apt_towers == ["apt"]


def test_fixed_speed_pump_topologies_and_offline_state_change():
    assert pump_state_from_current(10.1, 10.0) == 1
    assert pump_state_from_current(10.0, 10.0) == 0
    assert pump_state_from_current(None, 10.0) == 0
    assert pump_state_from_current(float("nan"), 10.0) == 0

    one_pump_two_valves = _plant(
        [
            _tower(
                "xst",
                "xstjy_PH",
                [_valve("xst_v1", "xst_FMKD1"), _valve("xst_v2", "xst_FMKD2")],
                pumps=[_pump("pump_A", "xstgjb_ADL", ["xst_v1", "xst_v2"])],
            )
        ]
    )
    validate_plant_config(one_pump_two_valves)
    availability = evaluate_supply_pump_availability(
        one_pump_two_valves, {"xstgjb_ADL": 25.0}
    )
    assert availability["pump_states"] == {"pump_A": 1}
    assert set(availability["available_valve_ids"]) == {"xst_v1", "xst_v2"}

    shared = _plant(
        [
            _tower(
                "xst",
                "xstjy_PH",
                [_valve("xst_v1", "xst_FMKD1"), _valve("xst_v2", "xst_FMKD2")],
                pumps=[
                    _pump("pump_A", "xstgjb_ADL", ["xst_v1", "xst_v2"]),
                    _pump("pump_B", "xstgjb_BDL", ["xst_v1", "xst_v2"]),
                ],
            )
        ]
    )
    availability = evaluate_supply_pump_availability(
        shared, {"xstgjb_ADL": 0.0, "xstgjb_BDL": 22.0}
    )
    assert set(availability["available_valve_ids"]) == {"xst_v1", "xst_v2"}

    independent = _plant(
        [
            _tower(
                "xst",
                "xstjy_PH",
                [_valve("xst_v1", "xst_FMKD1"), _valve("xst_v2", "xst_FMKD2")],
                pumps=[
                    _pump("pump_A", "xstgjb_ADL", ["xst_v1"]),
                    _pump("pump_B", "xstgjb_BDL", ["xst_v2"]),
                ],
            )
        ]
    )
    validate_plant_config(independent)
    availability = evaluate_supply_pump_availability(
        independent, {"xstgjb_ADL": 20.0, "xstgjb_BDL": None}
    )
    assert availability["available_valve_ids"] == ["xst_v1"]
    assert availability["unavailable_valve_ids"] == ["xst_v2"]
    assert availability["invalid_pump_ids"] == ["pump_B"]

    stable = pd.DataFrame({"xstgjb_ADL": [31.8, 32.2, 31.9, 32.4]})
    changed, columns = detect_supply_pump_state_change(
        _plant(
            [
                _tower(
                    "xst",
                    "xstjy_PH",
                    [_valve("xst_v1", "xst_FMKD1")],
                    pumps=[_pump("pump_A", "xstgjb_ADL", ["xst_v1"])],
                )
            ]
        ),
        stable,
    ) if False else (None, None)

    # Call with the actual public argument order used by the engine.
    pump_plant = _plant(
        [
            _tower(
                "xst",
                "xstjy_PH",
                [_valve("xst_v1", "xst_FMKD1")],
                pumps=[_pump("pump_A", "xstgjb_ADL", ["xst_v1"])],
            )
        ]
    )
    changed, columns = detect_supply_pump_state_change(stable, pump_plant)
    assert changed is False and columns == []
    changed, columns = detect_supply_pump_state_change(
        pd.DataFrame({"xstgjb_ADL": [31.8, 30.0, 0.5, 0.2]}), pump_plant
    )
    assert changed is True and columns == ["xstgjb_ADL"]


def test_online_pump_gating_does_not_compensate_stopped_branch():
    plant = _plant(
        [
            _tower(
                "xst",
                "xstjy_PH",
                [_valve("xst_v1", "xst_FMKD1"), _valve("xst_v2", "xst_FMKD2")],
                pumps=[
                    _pump("pump_A", "xstgjb_ADL", ["xst_v1"]),
                    _pump("pump_B", "xstgjb_BDL", ["xst_v2"]),
                ],
            )
        ]
    )
    state = _state(
        {
            "xstjy_PH": 5.2,
            "xst_FMKD1": 30.0,
            "xst_FMKD2": 40.0,
            "xstgjb_ADL": 20.0,
            "xstgjb_BDL": 0.0,
        }
    )
    resolved = ValveActionResolver(plant, _online_config()).resolve(
        _candidate(), state
    )
    assert math.isclose(
        resolved.recommended_valve_deltas["xst_v1"], 3.0, rel_tol=1e-9
    )
    assert math.isclose(
        resolved.recommended_valve_deltas["xst_v2"], 0.0, abs_tol=1e-12
    )
    assert resolved.active_valve_ids == ["xst_v1"]

    stopped_state = _state(
        {
            "xstjy_PH": 5.2,
            "xst_FMKD1": 30.0,
            "xst_FMKD2": 40.0,
            "xstgjb_ADL": 0.0,
            "xstgjb_BDL": 0.0,
        }
    )
    candidate = _candidate()
    accepted, rejected = CandidateFilter(plant, _online_config()).filter(
        [candidate], stopped_state, _demand(), {}, {}
    )
    assert accepted == []
    assert "NO_AVAILABLE_SUPPLY_PATH:xst" in rejected[candidate.action_id]
    try:
        ValveActionResolver(plant, _online_config()).resolve(candidate, stopped_state)
    except ActionResolutionError:
        pass
    else:
        raise AssertionError("all stopped supply paths must reject tower action")


def main():
    test_arbitrary_condition_axis_and_tower_topology()
    test_fixed_speed_pump_topologies_and_offline_state_change()
    test_online_pump_gating_does_not_compensate_stopped_branch()
    print("SLURRY_POLICY_CORE_REGRESSION_PASSED")


if __name__ == "__main__":
    main()

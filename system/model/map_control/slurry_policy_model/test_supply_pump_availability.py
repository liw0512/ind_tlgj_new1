# -*- coding: utf-8 -*-
"""Regression tests for fixed-speed supply-pump current gating."""

import math

import pandas as pd

from system.model.map_control.slurry_policy_model._engine.config_loader import (
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


def _tower(valves, pumps=None):
    tower = {
        "tower_id": "xst",
        "enabled": True,
        "ph_column": "xstjy_PH",
        "ph_safe_range": [4.5, 6.5],
        "ph_guard_band": 0.15,
        "valves": valves,
    }
    if pumps is not None:
        tower["supply_pumps"] = pumps
    return tower


def _plant(tower):
    return {
        "paths": {
            "output_root": "unused",
            "condition_snapshots_dir": "unused",
        },
        "time_column": "date",
        "outlet_so2_safe_range": [0.0, 35.0],
        "towers": [tower],
    }


def _training():
    return {
        "_condition_axes": [
            {"column": "axis_a", "min": 0.0, "max": 10.0, "step": 1.0}
        ]
    }


def _online_config():
    return {
        "profile_acceptance": {
            "allow_mixed_action": False,
            "allow_rebalance_action": False,
        },
        "fast_mode": {
            "block_economic_slurry_decrease": True,
        },
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
                "representative_delta": {
                    "xst_v1": 2.0,
                    "xst_v2": 4.0,
                },
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


def test_fixed_speed_current_is_strict_binary():
    assert pump_state_from_current(10.1, 10.0) == 1
    assert pump_state_from_current(10.0, 10.0) == 0
    assert pump_state_from_current(0.0, 10.0) == 0
    assert pump_state_from_current(None, 10.0) == 0
    assert pump_state_from_current(float("nan"), 10.0) == 0


def test_one_pump_can_serve_two_valves():
    plant = _plant(
        _tower(
            [_valve("xst_v1", "xst_FMKD1"), _valve("xst_v2", "xst_FMKD2")],
            [_pump("xst_pump_A", "xstgjb_ADL", ["xst_v1", "xst_v2"])],
        )
    )
    validate_plant_config(plant)
    availability = evaluate_supply_pump_availability(
        plant,
        {"xstgjb_ADL": 25.0},
    )
    assert availability["pump_states"] == {"xst_pump_A": 1}
    assert set(availability["available_valve_ids"]) == {"xst_v1", "xst_v2"}
    assert "xstgjb_ADL" in required_columns(plant, _training())


def test_multiple_pumps_can_share_one_or_many_valves():
    plant = _plant(
        _tower(
            [_valve("xst_v1", "xst_FMKD1"), _valve("xst_v2", "xst_FMKD2")],
            [
                _pump("xst_pump_A", "xstgjb_ADL", ["xst_v1", "xst_v2"]),
                _pump("xst_pump_B", "xstgjb_BDL", ["xst_v1", "xst_v2"]),
            ],
        )
    )
    validate_plant_config(plant)
    availability = evaluate_supply_pump_availability(
        plant,
        {"xstgjb_ADL": 0.0, "xstgjb_BDL": 22.0},
    )
    assert availability["pump_states"] == {
        "xst_pump_A": 0,
        "xst_pump_B": 1,
    }
    assert set(availability["available_valve_ids"]) == {"xst_v1", "xst_v2"}


def test_independent_branches_and_missing_current_fail_safe():
    plant = _plant(
        _tower(
            [_valve("xst_v1", "xst_FMKD1"), _valve("xst_v2", "xst_FMKD2")],
            [
                _pump("xst_pump_A", "xstgjb_ADL", ["xst_v1"]),
                _pump("xst_pump_B", "xstgjb_BDL", ["xst_v2"]),
            ],
        )
    )
    validate_plant_config(plant)
    availability = evaluate_supply_pump_availability(
        plant,
        {"xstgjb_ADL": 20.0, "xstgjb_BDL": None},
    )
    assert availability["pump_states"] == {
        "xst_pump_A": 1,
        "xst_pump_B": 0,
    }
    assert availability["available_valve_ids"] == ["xst_v1"]
    assert availability["unavailable_valve_ids"] == ["xst_v2"]
    assert availability["invalid_pump_ids"] == ["xst_pump_B"]


def test_offline_episode_only_changes_when_binary_pump_state_changes():
    plant = _plant(
        _tower(
            [_valve("xst_v1", "xst_FMKD1")],
            [_pump("xst_pump_A", "xstgjb_ADL", ["xst_v1"])],
        )
    )
    stable_running = pd.DataFrame({"xstgjb_ADL": [31.8, 32.2, 31.9, 32.4]})
    changed, columns = detect_supply_pump_state_change(stable_running, plant)
    assert not changed
    assert columns == []

    start_stop = pd.DataFrame({"xstgjb_ADL": [31.8, 30.0, 0.5, 0.2]})
    changed, columns = detect_supply_pump_state_change(start_stop, plant)
    assert changed
    assert columns == ["xstgjb_ADL"]


def test_resolver_uses_only_running_branch_without_compensation():
    plant = _plant(
        _tower(
            [_valve("xst_v1", "xst_FMKD1"), _valve("xst_v2", "xst_FMKD2")],
            [
                _pump("xst_pump_A", "xstgjb_ADL", ["xst_v1"]),
                _pump("xst_pump_B", "xstgjb_BDL", ["xst_v2"]),
            ],
        )
    )
    validate_plant_config(plant)
    state = _state(
        {
            "xstjy_PH": 5.2,
            "xst_FMKD1": 30.0,
            "xst_FMKD2": 40.0,
            "xstgjb_ADL": 20.0,
            "xstgjb_BDL": 0.0,
        }
    )
    candidate = _candidate()
    resolved = ValveActionResolver(plant, _online_config()).resolve(candidate, state)

    # Historical +2/+4 over equal spans => tower equivalent +3%.
    # Pump B is stopped, so valve2 is zeroed; valve1 remains +3, NOT +6.
    assert math.isclose(resolved.recommended_valve_deltas["xst_v1"], 3.0, rel_tol=1e-9)
    assert math.isclose(resolved.recommended_valve_deltas["xst_v2"], 0.0, abs_tol=1e-12)
    assert resolved.active_valve_ids == ["xst_v1"]
    assert "SUPPLY_PUMP_VALVE_AVAILABILITY_APPLIED" in resolved.reason_codes


def test_candidate_is_rejected_when_all_supply_paths_are_stopped():
    plant = _plant(
        _tower(
            [_valve("xst_v1", "xst_FMKD1"), _valve("xst_v2", "xst_FMKD2")],
            [
                _pump("xst_pump_A", "xstgjb_ADL", ["xst_v1"]),
                _pump("xst_pump_B", "xstgjb_BDL", ["xst_v2"]),
            ],
        )
    )
    state = _state(
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
        [candidate], state, _demand(), {}, {}
    )
    assert accepted == []
    assert "NO_AVAILABLE_SUPPLY_PATH:xst" in rejected[candidate.action_id]

    try:
        ValveActionResolver(plant, _online_config()).resolve(_candidate(), state)
    except ActionResolutionError:
        pass
    else:
        raise AssertionError("all stopped pumps must make the tower action unresolvable")


def test_no_pump_topology_keeps_backward_compatible_valve_availability():
    plant = _plant(
        _tower(
            [_valve("xst_v1", "xst_FMKD1"), _valve("xst_v2", "xst_FMKD2")],
            pumps=None,
        )
    )
    validate_plant_config(plant)
    availability = evaluate_supply_pump_availability(plant, {})
    assert set(availability["available_valve_ids"]) == {"xst_v1", "xst_v2"}
    assert availability["pump_states"] == {}


def main():
    test_fixed_speed_current_is_strict_binary()
    test_one_pump_can_serve_two_valves()
    test_multiple_pumps_can_share_one_or_many_valves()
    test_independent_branches_and_missing_current_fail_safe()
    test_offline_episode_only_changes_when_binary_pump_state_changes()
    test_resolver_uses_only_running_branch_without_compensation()
    test_candidate_is_rejected_when_all_supply_paths_are_stopped()
    test_no_pump_topology_keeps_backward_compatible_valve_availability()
    print("SUPPLY_PUMP_AVAILABILITY_TEST_PASSED")


if __name__ == "__main__":
    main()

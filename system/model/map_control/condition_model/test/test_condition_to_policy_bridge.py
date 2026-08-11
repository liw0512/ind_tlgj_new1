# -*- coding: utf-8 -*-
"""Integration regression test: first-module complete row -> second module."""

from system.model.slurry_control.condition_model.condition_config import from_dict
from system.model.slurry_control.condition_model.condition_schema import (
    ConditionSnapshot,
    GridCell,
    PolicyRegion,
)
from system.model.slurry_control.condition_model.online_condition_classifier import (
    OnlineConditionPolicyPipeline,
)


def build_config():
    return from_dict(
        {
            "grid_definition": {
                "jzfh": {"min": 100, "max": 120, "step": 10},
                "yyq_SO2": {"min": 500, "max": 900, "step": 200},
            },
            "data_columns": {
                "outlet_so2": "jyq_SO2",
                "xst_ph": "xstjy_PH",
                "apt_ph": "aptjy_PH",
                "liquid_gas": "liquid_gas_ratio",
            },
            "emission_limit": 35,
            "merge": {
                "enabled": False,
                "mode": "disabled",
                "min_observed_samples": 1,
                "min_mature_samples": 1,
                "min_auto_merge_samples": 1,
                "min_auto_confirm_samples": 1,
                "min_common_state_samples": 1,
                "min_risk_samples": 1,
                "min_metric_coverage_ratio": 0.8,
                "min_consecutive_pass_snapshots": 1,
                "min_new_samples_per_member_for_confirmation": 1,
                "max_auto_region_cells": 4,
                "max_liquid_gas_relative_difference": 0.15,
                "max_pump_distribution_distance": 0.25,
                "max_risk_rate_difference": 0.10,
            },
            "online": {
                "stability_mode": "MAJORITY",
                "stability_window_size": 6,
                "majority_tie_policy": "KEEP_LAST_STABLE",
                "allow_provisional_region_fallback": True,
            },
        }
    )


def build_snapshot(config):
    cells = {}
    regions = {}
    label = 1
    for load_level in (1, 2):
        for so2_level in (1, 2):
            grid_id = "P%d-S%d" % (load_level, so2_level)
            region_id = "R_P%d_S%d" % (load_level, so2_level)
            cells[grid_id] = GridCell(
                grid_id=grid_id,
                load_level=load_level,
                inlet_so2_level=so2_level,
                load_range=(100.0 + 10 * (load_level - 1), 100.0 + 10 * load_level),
                inlet_so2_range=(500.0 + 200 * (so2_level - 1), 500.0 + 200 * so2_level),
                policy_region_id=region_id,
                coverage_status="MATURE",
                sample_count=20,
            )
            regions[region_id] = PolicyRegion(
                region_id=region_id,
                member_grid_ids=[grid_id],
                condition_label=str(label),
            )
            label += 1
    return ConditionSnapshot(
        snapshot_version="v001",
        build_time="2026-08-03T00:00:00Z",
        grid_config=config.to_dict(),
        grid_catalog=cells,
        grid_adjacency={},
        policy_regions=regions,
    )


def sample():
    return {
        "date": "2026-08-03 15:00:00",
        "jzfh": 105.0,
        "yyq_SO2": 600.0,
        "jyq_SO2": 23.4,
        "xstjy_PH": 5.2,
        "aptjy_PH": 6.0,
        "liquid_gas_ratio": 10.0,
        "xst_FMKD1": 30.0,
        "xst_FMKD2": 31.0,
        "apt_FMKD": 25.0,
        "outlet_so2_target": 20.0,
        "raw_custom_field": "MUST_BE_PRESERVED",
    }


class FakePolicy:
    def __init__(self):
        self.received = []

    def evaluate(self, realtime_data, target=None, execution_context=None):
        self.received.append(
            (dict(realtime_data), target, dict(execution_context or {}))
        )
        return {
            "decision_id": "D-TEST",
            "timestamp": realtime_data["date"],
            "model_version": "v001",
            "condition_snapshot_version": realtime_data[
                "condition_snapshot_version"
            ],
            "condition_label": realtime_data["condition_label"],
            "raw_grid_id": realtime_data["raw_grid_id"],
            "control_mode": "REGULAR",
            "disturbance_mode": "STEADY",
            "current_so2": realtime_data["jyq_SO2"],
            "commanded_target": target,
            "effective_target": target,
            "desired_so2_response": "SO2_DOWN",
            "experience_source": "LOCAL_CONDITION",
            "action_id": "TEST_ACTION",
            "action_family": "TOWER:xst|BALANCED",
            "action_direction": "INCREASE",
            "action_magnitude": "SMALL",
            "recommended_valve_deltas": {
                "xst_v1": 1.0,
                "xst_v2": 1.0,
            },
            "projected_valve_openings": {
                "xst_v1": 31.0,
                "xst_v2": 32.0,
            },
            "historical_reliability": 0.80,
            "historical_safety_score": 0.95,
            "historical_direction_consistency": 0.90,
            "decision_status": "RECOMMENDED",
            "reason_codes": ["TEST"],
            "debug": {"received_complete_row": True},
        }

    def record_execution(self, feedback):
        return dict(feedback)

    def status(self):
        return {"model_version": "v001"}


def main():
    config = build_config()
    snapshot = build_snapshot(config)
    fake = FakePolicy()
    pipeline = OnlineConditionPolicyPipeline(
        config,
        snapshot,
        integration_config={
            "enabled": True,
            "initialize_on_start": True,
            "failure_mode": "RAISE",
            "output_prefix": "slurry_policy_",
            "target_column": "outlet_so2_target",
            "default_execution_context": {
                "automatic_control_allowed": False,
                "manual_valves": [],
                "faulted_valves": [],
                "supply_pump_state_changing": False,
            },
        },
        slurry_policy=fake,
    )

    final = None
    for _ in range(6):
        final = pipeline.process(sample())

    assert final is not None
    assert final["raw_custom_field"] == "MUST_BE_PRESERVED"
    assert final["condition_stable"] is True
    assert final["condition_snapshot_version"] == "v001"
    assert final["slurry_policy_decision_id"] == "D-TEST"
    assert final["slurry_policy_action_direction"] == "INCREASE"
    assert final["slurry_policy_recommended_valve_deltas"] == {
        "xst_v1": 1.0,
        "xst_v2": 1.0,
    }

    received, target, execution = fake.received[-1]
    assert received["raw_custom_field"] == "MUST_BE_PRESERVED"
    assert received["condition_label"] == final["condition_label"]
    assert received["majority_count"] == final["majority_count"]
    assert target == 20.0
    assert execution["automatic_control_allowed"] is False

    print("CONDITION_TO_POLICY_BRIDGE_TEST_PASSED")


if __name__ == "__main__":
    main()

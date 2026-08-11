# -*- coding: utf-8 -*-
"""Regression test for the six-sample online condition majority stabilizer."""

from system.model.slurry_control.condition_model.condition_config import from_dict
from system.model.slurry_control.condition_model.initial_condition_builder import (
    InitialConditionBuilder,
)
from system.model.slurry_control.condition_model.online_condition_classifier import (
    OnlineConditionClassifier,
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


def row(inlet_so2):
    return {
        "jzfh": 105,
        "yyq_SO2": inlet_so2,
        "jyq_SO2": 20,
        "xstjy_PH": 5.2,
        "aptjy_PH": 6.0,
        "liquid_gas_ratio": 10.0,
        "xst_circulation_pump_count": 2,
        "apt_circulation_pump_count": 1,
    }


def main():
    config = build_config()
    snapshot = InitialConditionBuilder(config).build(
        [row(600), row(800)],
        "v001",
    )
    classifier = OnlineConditionClassifier(config, snapshot)

    # Raw labels alternate 1/2. Before six valid samples the result is visible
    # but is not formally stable.
    results = [classifier.classify(row(value)) for value in (600, 800, 600, 800, 600)]
    assert all(not item.condition_stable for item in results)
    assert all(item.condition_switch_state == "INITIALIZING" for item in results)

    # The sixth sample creates a 3:3 tie. With no previous stable label, the
    # most recent tied label (2) initializes the stable result.
    sixth = classifier.classify(row(800))
    assert sixth.condition_stable
    assert sixth.condition_switch_state == "INITIALIZED"
    assert sixth.condition_label == "2"
    assert sixth.majority_tied
    assert sixth.majority_count == 3

    # Another 1 still leaves a 3:3 tie, so KEEP_LAST_STABLE retains label 2.
    seventh = classifier.classify(row(600))
    assert seventh.condition_switch_state == "STABLE"
    assert seventh.condition_label == "2"
    assert seventh.majority_tied

    # One more 1 gives label 1 a 4:2 majority and formally switches.
    eighth = classifier.classify(row(600))
    assert eighth.condition_switch_state == "SWITCHED"
    assert eighth.condition_label == "1"
    assert eighth.stable_condition_label == "1"
    assert eighth.grid_id == eighth.stable_grid_id
    assert eighth.raw_condition_label == "1"

    # Old snapshot/config keys remain readable but are retired and ignored.
    old_style = config.to_dict()
    old_style["online"] = {
        "load_hysteresis": 0,
        "inlet_so2_hysteresis": 0,
        "minimum_dwell_cycles": 2,
        "allow_provisional_region_fallback": True,
    }
    migrated = from_dict(old_style)
    assert migrated.online.stability_window_size == 6
    assert migrated.online.stability_mode == "MAJORITY"

    print("ONLINE_MAJORITY_TEST_PASSED")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""Regression tests for one/two arbitrary condition-axis configuration.

Run from project root:
    python -m system.model.map_control.condition_model.test.test_configurable_condition_axes

The test intentionally covers the complete first-module path used by the
second module: config -> initial snapshot -> condition_label -> snapshot
round-trip -> incremental update -> online classification.
"""

import tempfile
from pathlib import Path

from system.model.map_control.condition_model.condition_config import from_dict
from system.model.map_control.condition_model.initial_condition_builder import (
    InitialConditionBuilder,
    condition_label_for_row,
)
from system.model.map_control.condition_model.incremental_condition_updater import (
    IncrementalConditionUpdater,
)
from system.model.map_control.condition_model.online_condition_classifier import (
    OnlineConditionClassifier,
)
from system.model.map_control.condition_model.snapshot_io import (
    read_snapshot,
    write_snapshot,
)


def _common_config(condition_axes, stability_window_size=1):
    return from_dict(
        {
            "condition_axes": condition_axes,
            "data_columns": {
                "outlet_so2": "jyq_SO2",
                "xst_ph": "xstjy_PH",
                "apt_ph": "aptjy_PH",
                "liquid_gas": "liquid_gas_ratio",
            },
            "emission_limit": 35,
            "out_of_range_policy": "clip",
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
                "max_auto_region_cells": 8,
                "max_liquid_gas_relative_difference": 0.15,
                "max_pump_distribution_distance": 0.25,
                "max_risk_rate_difference": 0.10,
            },
            "online": {
                "stability_mode": "MAJORITY",
                "stability_window_size": stability_window_size,
                "majority_tie_policy": "KEEP_LAST_STABLE",
                "allow_provisional_region_fallback": True,
            },
        }
    )


def _process_fields(**extra):
    row = {
        "jyq_SO2": 20.0,
        "xstjy_PH": 5.4,
        "aptjy_PH": 5.8,
        "liquid_gas_ratio": 10.0,
        "xst_circulation_pump_count": 2,
        "apt_circulation_pump_count": 1,
    }
    row.update(extra)
    return row


def test_single_arbitrary_axis():
    config = _common_config(
        [
            {
                "column": "blast_pressure",
                "min": 100.0,
                "max": 400.0,
                "step": 100.0,
            }
        ]
    )
    assert config.single_axis_mode is True
    assert config.condition_axis_columns == ("blast_pressure",)
    assert config.inlet_so2.cell_count == 1

    rows = [
        _process_fields(blast_pressure=120.0),
        _process_fields(blast_pressure=220.0),
        _process_fields(blast_pressure=320.0),
    ]
    snapshot = InitialConditionBuilder(config).build(rows, "v001")

    # Three real bins, with the internal second slot fixed to S1.
    assert set(snapshot.grid_catalog) == {"P1-S1", "P2-S1", "P3-S1"}
    context = condition_label_for_row(rows[1], config, snapshot=snapshot)
    assert context["condition_valid"] is True
    assert context["grid_id"] == "P2-S1"
    assert context["base_condition_id"] == "2"
    assert context["condition_label"] == "2"

    # No jzfh or yyq_SO2 source field is required in this plant profile.
    assert "jzfh" not in rows[1]
    assert "yyq_SO2" not in rows[1]

    # Real-axis clipping reports the actual configured source column.
    clipped = condition_label_for_row(
        _process_fields(blast_pressure=999.0),
        config,
        snapshot=snapshot,
    )
    assert clipped["condition_valid"] is True
    assert clipped["out_of_range_clipped"] is True
    assert clipped["clip_axis"] == "blast_pressure"

    # Snapshot config must preserve one-axis semantics after reload.
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "condition_snapshot.json"
        write_snapshot(snapshot, str(path))
        loaded = read_snapshot(str(path))
        loaded_config = from_dict(loaded.grid_config)
        assert loaded_config.single_axis_mode is True
        assert loaded_config.condition_axis_columns == ("blast_pressure",)

        updated = IncrementalConditionUpdater(loaded_config).update(
            loaded,
            [_process_fields(blast_pressure=250.0)],
            "v002",
        )
        assert updated.snapshot_version == "v002"
        assert updated.grid_catalog["P2-S1"].sample_count >= 2

    classifier = OnlineConditionClassifier(config, snapshot)
    online = classifier.classify(_process_fields(blast_pressure=220.0))
    assert online.condition_valid is True
    assert online.condition_stable is True
    assert online.grid_id == "P2-S1"
    assert online.condition_label == "2"


def test_two_arbitrary_axes():
    config = _common_config(
        [
            {
                "column": "gas_flow",
                "min": 1000.0,
                "max": 3000.0,
                "step": 1000.0,
            },
            {
                "column": "inlet_sulfur",
                "min": 0.0,
                "max": 200.0,
                "step": 100.0,
            },
        ]
    )
    assert config.single_axis_mode is False
    assert config.condition_axis_columns == ("gas_flow", "inlet_sulfur")

    rows = [
        _process_fields(gas_flow=1200.0, inlet_sulfur=20.0),
        _process_fields(gas_flow=2200.0, inlet_sulfur=120.0),
    ]
    snapshot = InitialConditionBuilder(config).build(rows, "v001")
    assert set(snapshot.grid_catalog) == {
        "P1-S1",
        "P1-S2",
        "P2-S1",
        "P2-S2",
    }
    context = condition_label_for_row(rows[1], config, snapshot=snapshot)
    assert context["condition_valid"] is True
    assert context["grid_id"] == "P2-S2"
    assert context["base_condition_id"] == "4"


def test_legacy_two_axis_config_still_loads():
    config = _common_config(
        [
            {"column": "jzfh", "min": 100.0, "max": 120.0, "step": 10.0},
            {"column": "yyq_SO2", "min": 500.0, "max": 900.0, "step": 200.0},
        ]
    )
    legacy = config.to_dict()
    legacy.pop("condition_axes")
    migrated = from_dict(legacy)
    assert migrated.single_axis_mode is False
    assert migrated.condition_axis_columns == ("jzfh", "yyq_SO2")
    assert migrated.load == config.load
    assert migrated.inlet_so2 == config.inlet_so2


def test_invalid_axis_count_is_rejected():
    try:
        _common_config([])
    except ValueError as exc:
        assert "1 or 2" in str(exc)
    else:
        raise AssertionError("zero condition axes should fail")

    try:
        _common_config(
            [
                {"column": "a", "min": 0, "max": 10, "step": 1},
                {"column": "b", "min": 0, "max": 10, "step": 1},
                {"column": "c", "min": 0, "max": 10, "step": 1},
            ]
        )
    except ValueError as exc:
        assert "1 or 2" in str(exc)
    else:
        raise AssertionError("three condition axes should fail")


def main():
    test_single_arbitrary_axis()
    test_two_arbitrary_axes()
    test_legacy_two_axis_config_still_loads()
    test_invalid_axis_count_is_rejected()
    print("CONFIGURABLE_CONDITION_AXES_TEST_PASSED")


if __name__ == "__main__":
    main()

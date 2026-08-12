# -*- coding: utf-8 -*-
"""Regression tests for policy-model configurable condition axes.

Run from project root:
    python -m system.model.map_control.slurry_policy_model.test_configurable_condition_axes

No database, GUI or DCS connection is required.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd

from system.model.map_control.slurry_policy_model._engine.calibration import (
    calibrate_disturbance_thresholds,
)
from system.model.map_control.slurry_policy_model._engine.data_loader import (
    load_input_data,
    required_columns,
)
from system.model.map_control.slurry_policy_model._engine.disturbance_classifier import (
    classify_disturbance,
    is_fast_disturbance,
)
from system.model.map_control.slurry_policy_model._engine.schema import (
    condition_axis_columns,
    freeze_condition_axes,
)
from system.model.map_control.slurry_policy_model.slurry_policy_online.disturbance_monitor import (
    DisturbanceMonitor,
)


def _plant():
    return {
        "time_column": "date",
        "outlet_so2_safe_range": [0.0, 35.0],
        "supply_pump_state_columns": [],
        "towers": [
            {
                "tower_id": "xst",
                "enabled": True,
                "ph_column": "xstjy_PH",
                "ph_safe_range": [4.6, 5.6],
                "ph_guard_band": 0.15,
                "valves": [
                    {
                        "valve_id": "xst_v1",
                        "column": "xst_FMKD1",
                        "min_opening": 0.0,
                        "max_opening": 100.0,
                        "action_threshold": 0.5,
                    }
                ],
            }
        ],
    }


def _training(axes):
    return {
        "_condition_axes": axes,
        "io": {
            "csv_encoding": "utf-8-sig",
            "timestamp_format": None,
            "drop_duplicate_timestamp_keep": "last",
            "strict_required_columns": True,
        },
        "performance": {
            "read_only_required_columns": True,
            "skip_sort_when_already_ordered": True,
        },
        "preprocessing": {
            "coerce_numeric": True,
            "max_continuous_gap_seconds": 180,
        },
        "disturbance": {
            "mode": "auto",
            "trend_window_minutes": 2.0,
            "auto_slow_quantile": 0.50,
            "auto_fast_quantile": 0.90,
            "minimum_axis_slow_step_ratio": 0.01,
            "minimum_axis_fast_step_ratio": 0.03,
        },
    }


def _condition_columns(version="v001", grid="P1-S1", label="1"):
    return {
        "condition_snapshot_version": version,
        "grid_id": grid,
        "condition_label": label,
        "policy_region_id": "R_P1_S1",
        "state_key": "XP0-AP0-NORMAL-SUPPLY_NORMAL",
        "condition_valid": True,
        "out_of_range_clipped": False,
    }


def test_single_axis_loader_does_not_require_power_or_inlet_so2():
    training = _training(
        [
            {
                "column": "blast_pressure",
                "min": 100.0,
                "max": 400.0,
                "step": 100.0,
            }
        ]
    )
    assert condition_axis_columns(training) == ("blast_pressure",)
    required = required_columns(_plant(), training)
    assert "blast_pressure" in required
    assert "jzfh" not in required
    assert "yyq_SO2" not in required

    rows = []
    for index, value in enumerate((120.0, 125.0, 150.0, 210.0)):
        row = {
            "date": f"2026-01-01 00:0{index}:00",
            "blast_pressure": value,
            "jyq_SO2": 20.0 + index,
            "xstjy_PH": 5.2,
            "xst_FMKD1": 30.0,
            **_condition_columns(),
        }
        rows.append(row)

    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "single_axis.csv"
        pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")
        frame, warnings = load_input_data(
            str(path), _plant(), training
        )
    assert len(frame) == 4
    assert not warnings
    assert "blast_pressure" in frame.columns
    assert "jzfh" not in frame.columns
    assert "yyq_SO2" not in frame.columns


def test_single_axis_disturbance_auto_calibration_and_monitor():
    training = _training(
        [
            {
                "column": "blast_pressure",
                "min": 100.0,
                "max": 400.0,
                "step": 100.0,
            }
        ]
    )
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=8, freq="min"),
            "blast_pressure": [100, 101, 102, 104, 110, 130, 160, 200],
        }
    )
    effective = calibrate_disturbance_thresholds(
        frame, _plant(), training
    )
    assert len(effective["axis_thresholds"]) == 1
    threshold = effective["axis_thresholds"][0]
    assert threshold["column"] == "blast_pressure"
    assert threshold["fast_rate"] >= threshold["slow_rate"] >= 0

    mode = classify_disturbance(
        threshold["fast_rate"] * 1.2,
        None,
        effective,
    )
    assert mode == "AXIS1_RISE_FAST"
    assert is_fast_disturbance(mode)

    runtime_state = {}
    monitor = DisturbanceMonitor(
        effective,
        {
            "fast_mode": {
                "minimum_hold_minutes": 0.0,
                "exit_stable_cycles": 1,
                "recovery_hold_minutes": 0.0,
            }
        },
        runtime_state,
    )
    first = pd.Timestamp("2026-01-01 00:00:00")
    monitor.update(first, 100.0, None, 20.0)
    result = monitor.update(
        first + pd.Timedelta(minutes=2),
        100.0 + threshold["fast_rate"] * 2.5,
        None,
        22.0,
    )
    assert "condition_axis_1_rate" in result
    assert "condition_axis_2_rate" in result
    assert result["condition_axis_2_rate"] == 0.0


def test_two_arbitrary_axes_are_preserved_when_frozen():
    training = _training(
        [
            {"column": "gas_flow", "min": 1000, "max": 3000, "step": 1000},
            {"column": "inlet_sulfur", "min": 0, "max": 200, "step": 100},
        ]
    )
    frozen = freeze_condition_axes(training)
    assert condition_axis_columns(frozen) == ("gas_flow", "inlet_sulfur")
    assert frozen["_condition_axes"][0]["step"] == 1000
    assert frozen["_condition_axes"][1]["step"] == 100


def test_mixed_fast_label_keeps_fast_marker():
    effective = {
        "axis_thresholds": [
            {"axis_index": 1, "column": "a", "slow_rate": 1, "fast_rate": 3},
            {"axis_index": 2, "column": "b", "slow_rate": 1, "fast_rate": 3},
        ]
    }
    mode = classify_disturbance(4.0, -4.0, effective)
    assert mode == "MIXED_DISTURBANCE_FAST"
    assert is_fast_disturbance(mode)


def main():
    test_single_axis_loader_does_not_require_power_or_inlet_so2()
    test_single_axis_disturbance_auto_calibration_and_monitor()
    test_two_arbitrary_axes_are_preserved_when_frozen()
    test_mixed_fast_label_keeps_fast_marker()
    print("POLICY_CONFIGURABLE_CONDITION_AXES_TEST_PASSED")


if __name__ == "__main__":
    main()

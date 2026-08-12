# -*- coding: utf-8 -*-
"""Regression tests for topology-independent first-module pH statistics."""

import tempfile
from pathlib import Path

import pandas as pd

from system.model.map_control.condition_model.condition_config import from_dict
from system.model.map_control.condition_model.incremental_condition_updater import (
    IncrementalConditionUpdater,
)
from system.model.map_control.condition_model.initial_condition_builder import (
    InitialConditionBuilder,
    build_initial_condition_csv,
    normalize_and_validate_training_frame,
)
from system.model.map_control.condition_model.snapshot_io import read_snapshot


def _config():
    return from_dict(
        {
            "condition_axes": [
                {
                    "column": "blast_pressure",
                    "min": 100.0,
                    "max": 400.0,
                    "step": 100.0,
                }
            ],
            "data_columns": {
                "outlet_so2": "jyq_SO2",
                "xst_ph": "xstjy_PH",
                "apt_ph": "aptjy_PH",
                "liquid_gas": "liquid_gas_ratio",
            },
            "emission_limit": 35.0,
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
                "stability_window_size": 1,
                "majority_tie_policy": "KEEP_LAST_STABLE",
                "allow_provisional_region_fallback": True,
            },
        }
    )


def _single_tower_rows(include_xst_ph=True):
    rows = []
    for index, axis in enumerate((120.0, 220.0, 320.0)):
        row = {
            "date": f"2026-08-12 00:0{index}:00",
            "blast_pressure": axis,
            "liquid_gas_ratio": 10.0 + index,
            "jyq_SO2": 20.0 + index,
            "xst_circulation_pump_count": 2,
        }
        if include_xst_ph:
            row["xstjy_PH"] = 5.2 + 0.01 * index
        # Intentionally no aptjy_PH: this represents a real single-tower plant.
        rows.append(row)
    return rows


def test_validation_does_not_require_disabled_or_absent_tower_ph():
    config = _config()
    frame = pd.DataFrame(_single_tower_rows(include_xst_ph=True))
    normalized = normalize_and_validate_training_frame(
        frame, config, context="single tower"
    )
    assert "aptjy_PH" not in normalized.columns

    no_ph_frame = pd.DataFrame(_single_tower_rows(include_xst_ph=False))
    normalized_no_ph = normalize_and_validate_training_frame(
        no_ph_frame, config, context="first module without tower pH"
    )
    assert "xstjy_PH" not in normalized_no_ph.columns
    assert "aptjy_PH" not in normalized_no_ph.columns


def test_initial_and_incremental_statistics_keep_missing_apt_ph_empty():
    config = _config()
    rows = _single_tower_rows(include_xst_ph=True)
    snapshot = InitialConditionBuilder(config).build(rows, "v001")

    observed = [cell for cell in snapshot.grid_catalog.values() if cell.sample_count]
    assert observed
    assert any(cell.statistics["mean_xst_ph"] is not None for cell in observed)
    assert all(cell.statistics["mean_apt_ph"] is None for cell in observed)

    updated = IncrementalConditionUpdater(config).update(
        snapshot,
        [
            {
                "blast_pressure": 225.0,
                "liquid_gas_ratio": 10.5,
                "jyq_SO2": 21.0,
                "xstjy_PH": 5.25,
                "xst_circulation_pump_count": 2,
            }
        ],
        "v002",
    )
    assert updated.snapshot_version == "v002"
    assert all(
        cell.statistics["mean_apt_ph"] is None
        for cell in updated.grid_catalog.values()
        if cell.sample_count
    )


def test_full_csv_builder_accepts_single_tower_without_apt_ph():
    config = _config()
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        source = root / "input.csv"
        output = root / "after_condition.csv"
        snapshot_path = root / "snapshots" / "v001" / "condition_snapshot.json"
        pd.DataFrame(_single_tower_rows(include_xst_ph=True)).to_csv(
            source, index=False, encoding="utf-8-sig"
        )
        build_initial_condition_csv(
            str(source),
            str(output),
            snapshot_output_path=str(snapshot_path),
            merge_statistics_json_path=str(root / "condition_merge_statistics.json"),
            auto_merge_report_path=str(root / "auto_merge_report.json"),
            snapshot_version="v001",
            config=config,
        )
        result = pd.read_csv(output, encoding="utf-8-sig")
        assert "condition_label" in result.columns
        assert result["condition_valid"].astype(bool).all()
        loaded = read_snapshot(str(snapshot_path))
        assert loaded.snapshot_version == "v001"


def main():
    test_validation_does_not_require_disabled_or_absent_tower_ph()
    test_initial_and_incremental_statistics_keep_missing_apt_ph_empty()
    test_full_csv_builder_accepts_single_tower_without_apt_ph()
    print("OPTIONAL_TOWER_PH_TEST_PASSED")


if __name__ == "__main__":
    main()

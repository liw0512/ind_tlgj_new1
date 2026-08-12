# -*- coding: utf-8 -*-
"""Core structural regression checks for condition_model.

This file intentionally replaces many development-time micro tests with a few
scenario tests that exercise complete invariants: configurable axes, snapshot
round-trip/incremental/online use, optional tower pH, and auto-merge lifecycle.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd

from system.model.map_control.condition_model.condition_config import from_dict
from system.model.map_control.condition_model.initial_condition_builder import (
    InitialConditionBuilder,
    build_initial_condition_csv,
    condition_label_for_row,
    normalize_and_validate_training_frame,
)
from system.model.map_control.condition_model.incremental_condition_updater import (
    IncrementalConditionUpdater,
    build_incremental_condition_csv,
)
from system.model.map_control.condition_model.online_condition_classifier import (
    OnlineConditionClassifier,
)
from system.model.map_control.condition_model.snapshot_io import (
    read_snapshot,
    write_snapshot,
)


def _config(axes, *, merge=False, stability_window_size=1):
    return from_dict(
        {
            "condition_axes": axes,
            "data_columns": {
                "outlet_so2": "jyq_SO2",
                "xst_ph": "xstjy_PH",
                "apt_ph": "aptjy_PH",
                "liquid_gas": "liquid_gas_ratio",
            },
            "emission_limit": 35.0,
            "out_of_range_policy": "clip",
            "merge": {
                "enabled": bool(merge),
                "mode": "evidence_only" if merge else "disabled",
                "min_observed_samples": 2 if merge else 1,
                "min_mature_samples": 3 if merge else 1,
                "min_auto_merge_samples": 5 if merge else 1,
                "min_auto_confirm_samples": 8 if merge else 1,
                "min_common_state_samples": 2 if merge else 1,
                "min_risk_samples": 2 if merge else 1,
                "min_metric_coverage_ratio": 0.80,
                "min_consecutive_pass_snapshots": 3 if merge else 1,
                "min_new_samples_per_member_for_confirmation": 2 if merge else 1,
                "max_auto_region_cells": 4 if merge else 8,
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


def _row(**extra):
    row = {
        "jyq_SO2": 20.0,
        "xstjy_PH": 5.3,
        "aptjy_PH": 5.9,
        "liquid_gas_ratio": 10.0,
        "xst_circulation_pump_count": 2,
        "apt_circulation_pump_count": 1,
    }
    row.update(extra)
    return row


def test_configurable_axes_snapshot_incremental_and_online():
    single = _config(
        [{"column": "blast_pressure", "min": 100.0, "max": 400.0, "step": 100.0}]
    )
    rows = [
        _row(blast_pressure=120.0),
        _row(blast_pressure=220.0),
        _row(blast_pressure=320.0),
    ]
    snapshot = InitialConditionBuilder(single).build(rows, "v001")
    assert single.condition_axis_columns == ("blast_pressure",)
    assert set(snapshot.grid_catalog) == {"P1-S1", "P2-S1", "P3-S1"}
    context = condition_label_for_row(rows[1], single, snapshot=snapshot)
    assert context["condition_valid"] is True
    assert context["grid_id"] == "P2-S1"
    assert "jzfh" not in rows[1] and "yyq_SO2" not in rows[1]

    clipped = condition_label_for_row(
        _row(blast_pressure=999.0), single, snapshot=snapshot
    )
    assert clipped["out_of_range_clipped"] is True
    assert clipped["clip_axis"] == "blast_pressure"

    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "condition_snapshot.json"
        write_snapshot(snapshot, str(path))
        loaded = read_snapshot(str(path))
        loaded_config = from_dict(loaded.grid_config)
        assert loaded_config.condition_axis_columns == ("blast_pressure",)
        updated = IncrementalConditionUpdater(loaded_config).update(
            loaded, [_row(blast_pressure=250.0)], "v002"
        )
        assert updated.snapshot_version == "v002"
        assert updated.grid_catalog["P2-S1"].sample_count >= 2

    online = OnlineConditionClassifier(single, snapshot).classify(
        _row(blast_pressure=220.0)
    )
    assert online.condition_valid is True
    assert online.condition_stable is True
    assert online.grid_id == "P2-S1"

    dual = _config(
        [
            {"column": "gas_flow", "min": 1000.0, "max": 3000.0, "step": 1000.0},
            {"column": "inlet_sulfur", "min": 0.0, "max": 200.0, "step": 100.0},
        ]
    )
    dual_rows = [
        _row(gas_flow=1200.0, inlet_sulfur=20.0),
        _row(gas_flow=2200.0, inlet_sulfur=120.0),
    ]
    dual_snapshot = InitialConditionBuilder(dual).build(dual_rows, "v001")
    assert dual.condition_axis_columns == ("gas_flow", "inlet_sulfur")
    assert set(dual_snapshot.grid_catalog) == {
        "P1-S1", "P1-S2", "P2-S1", "P2-S2"
    }
    dual_context = condition_label_for_row(
        dual_rows[1], dual, snapshot=dual_snapshot
    )
    assert dual_context["grid_id"] == "P2-S2"


def test_single_tower_optional_ph_is_not_required():
    config = _config(
        [{"column": "blast_pressure", "min": 100.0, "max": 400.0, "step": 100.0}]
    )
    rows = []
    for index, axis in enumerate((120.0, 220.0, 320.0)):
        rows.append(
            {
                "date": f"2026-08-12 00:0{index}:00",
                "blast_pressure": axis,
                "liquid_gas_ratio": 10.0 + index,
                "jyq_SO2": 20.0 + index,
                "xstjy_PH": 5.2 + index * 0.01,
                "xst_circulation_pump_count": 2,
            }
        )

    normalized = normalize_and_validate_training_frame(
        pd.DataFrame(rows), config, context="single tower"
    )
    assert "aptjy_PH" not in normalized.columns

    snapshot = InitialConditionBuilder(config).build(rows, "v001")
    observed = [cell for cell in snapshot.grid_catalog.values() if cell.sample_count]
    assert observed
    assert any(cell.statistics["mean_xst_ph"] is not None for cell in observed)
    assert all(cell.statistics["mean_apt_ph"] is None for cell in observed)

    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        source = root / "input.csv"
        output = root / "after_condition.csv"
        snapshot_path = root / "snapshots" / "v001" / "condition_snapshot.json"
        pd.DataFrame(rows).to_csv(source, index=False, encoding="utf-8-sig")
        build_initial_condition_csv(
            str(source),
            str(output),
            snapshot_output_path=str(snapshot_path),
            merge_statistics_json_path=str(root / "stats.json"),
            auto_merge_report_path=str(root / "report.json"),
            snapshot_version="v001",
            config=config,
        )
        result = pd.read_csv(output, encoding="utf-8-sig")
        assert result["condition_valid"].astype(bool).all()
        assert read_snapshot(str(snapshot_path)).snapshot_version == "v001"


def _merge_frame(first_count, second_count, *, second_lg=10.5, second_so2=20.0, second_pumps=2):
    rows = []
    for _ in range(first_count):
        rows.append(
            _row(
                jzfh=105.0,
                yyq_SO2=600.0,
                liquid_gas_ratio=10.0,
                jyq_SO2=20.0,
                xst_circulation_pump_count=2,
            )
        )
    for _ in range(second_count):
        rows.append(
            _row(
                jzfh=105.0,
                yyq_SO2=800.0,
                liquid_gas_ratio=second_lg,
                jyq_SO2=second_so2,
                xst_circulation_pump_count=second_pumps,
            )
        )
    return pd.DataFrame(rows)


def test_auto_merge_confirm_and_split_lifecycle():
    config = _config(
        [
            {"column": "jzfh", "min": 100.0, "max": 120.0, "step": 10.0},
            {"column": "yyq_SO2", "min": 500.0, "max": 900.0, "step": 200.0},
        ],
        merge=True,
    )

    unrelated = pd.DataFrame(
        [
            _row(
                jzfh=115.0,
                yyq_SO2=600.0,
                liquid_gas_ratio=9.0,
                xst_circulation_pump_count=2,
            )
            for _ in range(3)
        ]
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        batches = {
            "p1": _merge_frame(5, 5),
            "hold": unrelated,
            "p2": _merge_frame(2, 2),
            "p3": _merge_frame(2, 2),
            "split": _merge_frame(
                0, 30, second_lg=30.0, second_so2=40.0, second_pumps=4
            ),
        }
        for name, frame in batches.items():
            frame.to_csv(root / f"{name}.csv", index=False, encoding="utf-8-sig")

        build_initial_condition_csv(
            str(root / "p1.csv"),
            str(root / "o1.csv"),
            str(root / "v001.json"),
            str(root / "stats.json"),
            str(root / "r1.json"),
            "v001",
            config=config,
        )
        first = read_snapshot(str(root / "v001.json"))
        merged = [r for r in first.policy_regions.values() if len(r.member_grid_ids) > 1]
        assert merged and merged[0].status == "AUTO_PROVISIONAL_MERGE"

        for source, target, batch in (
            ("v001", "v002", "hold"),
            ("v002", "v003", "p2"),
            ("v003", "v004", "p3"),
            ("v004", "v005", "split"),
        ):
            build_incremental_condition_csv(
                str(root / f"{source}.json"),
                str(root / f"{batch}.csv"),
                str(root / f"o_{target}.csv"),
                str(root / f"{target}.json"),
                str(root / "stats.json"),
                str(root / f"r_{target}.json"),
                target,
            )

        held = read_snapshot(str(root / "v002.json"))
        held_region = [
            r
            for r in held.policy_regions.values()
            if {"P1-S1", "P1-S2"}.issubset(r.member_grid_ids)
        ][0]
        assert held_region.evidence["verification_passes"] == 1

        confirmed = read_snapshot(str(root / "v004.json"))
        confirmed_region = [
            r
            for r in confirmed.policy_regions.values()
            if {"P1-S1", "P1-S2"}.issubset(r.member_grid_ids)
        ][0]
        assert confirmed_region.status == "AUTO_CONFIRMED_MERGE"

        split = read_snapshot(str(root / "v005.json"))
        assert not [r for r in split.policy_regions.values() if len(r.member_grid_ids) > 1]
        assert split.metadata["auto_merge_state"]["split_events"]


def main():
    test_configurable_axes_snapshot_incremental_and_online()
    test_single_tower_optional_ph_is_not_required()
    test_auto_merge_confirm_and_split_lifecycle()
    print("CONDITION_MODEL_CORE_REGRESSION_PASSED")


if __name__ == "__main__":
    main()

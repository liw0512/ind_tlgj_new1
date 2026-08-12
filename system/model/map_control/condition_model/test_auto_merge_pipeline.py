# -*- coding: utf-8 -*-
"""Minimal regression test for automatic provisional/confirmed/split flow.

Run from the project root after placing this file in condition_model:
    python -m system.model.map_control.condition_model.test_auto_merge_pipeline
"""

import json
import tempfile
from pathlib import Path

import pandas as pd

from system.model.map_control.condition_model.condition_config import from_dict
from system.model.map_control.condition_model.initial_condition_builder import (
    apply_merge_pairs,
    build_initial_condition_csv,
    load_merge_statistics,
)
from system.model.map_control.condition_model.incremental_condition_updater import (
    build_incremental_condition_csv,
)
from system.model.map_control.condition_model.snapshot_io import read_snapshot


def _frame(first_count, second_count, second_lg=10.5, second_outlet=20.0, second_pumps=2):
    rows = []
    for _ in range(first_count):
        rows.append({
            "jzfh": 105,
            "yyq_SO2": 600,
            "jyq_SO2": 20.0,
            "xstjy_PH": 5.5,
            "aptjy_PH": 5.7,
            "liquid_gas_ratio": 10.0,
            "xst_circulation_pump_count": 2,
            "apt_circulation_pump_count": 1,
        })
    for _ in range(second_count):
        rows.append({
            "jzfh": 105,
            "yyq_SO2": 800,
            "jyq_SO2": second_outlet,
            "xstjy_PH": 5.5,
            "aptjy_PH": 5.7,
            "liquid_gas_ratio": second_lg,
            "xst_circulation_pump_count": second_pumps,
            "apt_circulation_pump_count": 1,
        })
    return pd.DataFrame(rows)


def _unrelated_frame(count):
    return pd.DataFrame([
        {
            "jzfh": 115,
            "yyq_SO2": 600,
            "jyq_SO2": 20.0,
            "xstjy_PH": 5.5,
            "aptjy_PH": 5.7,
            "liquid_gas_ratio": 9.0,
            "xst_circulation_pump_count": 2,
            "apt_circulation_pump_count": 1,
        }
        for _ in range(count)
    ])


def main() -> None:
    config = from_dict({
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
        "out_of_range_policy": "clip",
        "merge": {
            "enabled": True,
            "mode": "evidence_only",
            "min_observed_samples": 2,
            "min_mature_samples": 3,
            "min_auto_merge_samples": 5,
            "min_auto_confirm_samples": 8,
            "min_common_state_samples": 2,
            "min_risk_samples": 2,
            "min_metric_coverage_ratio": 0.8,
            "min_consecutive_pass_snapshots": 3,
            "min_new_samples_per_member_for_confirmation": 2,
            "max_auto_region_cells": 4,
            "max_liquid_gas_relative_difference": 0.15,
            "max_pump_distribution_distance": 0.25,
            "max_risk_rate_difference": 0.10,
        },
        "online": {
            "load_hysteresis": 0,
            "inlet_so2_hysteresis": 0,
            "minimum_dwell_cycles": 1,
            "allow_provisional_region_fallback": True,
        },
    })

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        batches = {
            "p1": _frame(5, 5),
            # This batch does not add evidence to the provisional region and
            # therefore must not advance its verification counter.
            "p_hold": _unrelated_frame(3),
            "p2": _frame(2, 2),
            "p3": _frame(2, 2),
            "p4": _frame(0, 30, second_lg=30, second_outlet=40, second_pumps=4),
        }
        for name, frame in batches.items():
            frame.to_csv(root / f"{name}.csv", index=False, encoding="utf-8-sig")

        build_initial_condition_csv(
            str(root / "p1.csv"), str(root / "o1.csv"),
            str(root / "v001.json"), str(root / "stats.json"),
            str(root / "r1.json"), "v001", config=config,
        )
        first = read_snapshot(str(root / "v001.json"))
        regions = [item for item in first.policy_regions.values() if len(item.member_grid_ids) > 1]
        assert regions[0].status == "AUTO_PROVISIONAL_MERGE"

        for source_version, target_version, batch in (
            ("v001", "v002", "p_hold"),
            ("v002", "v003", "p2"),
            ("v003", "v004", "p3"),
            ("v004", "v005", "p4"),
        ):
            build_incremental_condition_csv(
                str(root / f"{source_version}.json"),
                str(root / f"{batch}.csv"),
                str(root / f"o{target_version}.csv"),
                str(root / f"{target_version}.json"),
                str(root / "stats.json"),
                str(root / f"r{target_version}.json"),
                target_version,
            )

        held = read_snapshot(str(root / "v002.json"))
        held_regions = [
            item for item in held.policy_regions.values()
            if {"P1-S1", "P1-S2"}.issubset(item.member_grid_ids)
        ]
        assert held_regions
        assert held_regions[0].evidence["verification_passes"] == 1
        assert (
            held_regions[0].evidence["verification_progress"]
            == "HELD_INSUFFICIENT_NEW_SAMPLES"
        )

        confirmed = read_snapshot(str(root / "v004.json"))
        regions = [item for item in confirmed.policy_regions.values() if len(item.member_grid_ids) > 1]
        target_region = [
            item for item in regions
            if {"P1-S1", "P1-S2"}.issubset(item.member_grid_ids)
        ][0]
        assert target_region.status == "AUTO_CONFIRMED_MERGE"

        split = read_snapshot(str(root / "v005.json"))
        assert not [item for item in split.policy_regions.values() if len(item.member_grid_ids) > 1]
        assert split.metadata["auto_merge_state"]["split_events"]

        for path in root.glob("*.json"):
            text = path.read_text(encoding="utf-8")
            assert "NaN" not in text and "Infinity" not in text
            json.loads(text)

        try:
            apply_merge_pairs(load_merge_statistics(None), [(1, 2)])
            raise AssertionError("manual merge guard did not raise")
        except RuntimeError:
            pass

    print("AUTO_MERGE_PIPELINE_TEST_PASSED")


if __name__ == "__main__":
    main()

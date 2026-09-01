from copy import deepcopy

from system.model.map_control.condition_model.condition_config import default_config
from system.model.map_control.condition_model.initial_condition_builder import (
    InitialConditionBuilder,
)
from system.model.map_control.condition_model.seeded_region_manager import (
    SeededRegionManager,
)


REGION_LABELS = {
    "EDGE_LOW": "10001",
    "C1": "10002",
    "C2": "10003",
    "C3": "10004",
    "C4": "10005",
    "EDGE_HIGH": "10006",
}


def _region(snapshot, label):
    return next(
        region
        for region in snapshot.policy_regions.values()
        if region.condition_label == label
    )


def _rows(*, yyq_so2, liquid_gas_ratio, count, dates):
    date_values = list(dates)
    return [
        {
            "date": date_values[index % len(date_values)],
            "yyq_SO2": yyq_so2,
            "liquid_gas_ratio": liquid_gas_ratio,
            "xst_circulation_pump_count": 3,
            "apt_circulation_pump_count": 0,
        }
        for index in range(count)
    ]


def _next_snapshot(previous, version):
    updated = deepcopy(previous)
    updated.previous_snapshot_version = previous.snapshot_version
    updated.snapshot_version = version
    return updated


def test_steel_seed_regions_cover_all_100mg_base_cells():
    config = default_config()
    snapshot = InitialConditionBuilder(config).build([], "v001")
    snapshot, report = SeededRegionManager.from_path().initialize(
        snapshot,
        [],
        config,
    )

    assert len(snapshot.grid_catalog) == 45
    assert len(_region(snapshot, REGION_LABELS["EDGE_LOW"]).member_grid_ids) == 4
    assert len(_region(snapshot, REGION_LABELS["C1"]).member_grid_ids) == 7
    assert len(_region(snapshot, REGION_LABELS["C2"]).member_grid_ids) == 3
    assert len(_region(snapshot, REGION_LABELS["C3"]).member_grid_ids) == 3
    assert len(_region(snapshot, REGION_LABELS["C4"]).member_grid_ids) == 8
    assert len(_region(snapshot, REGION_LABELS["EDGE_HIGH"]).member_grid_ids) == 20
    assert report["automatic_boundary_change_enabled"] is False
    assert report["robust_quantile_scope"] == "IN_RANGE_ONLY"
    assert report["evidence_type"] == "OPERATING_CONTEXT_DISTRIBUTION_SHIFT"
    assert report["structural_decision_authority"] is False


def test_seeded_region_labels_do_not_overlap_base_condition_ids():
    config = default_config()
    snapshot = InitialConditionBuilder(config).build([], "v001")
    snapshot, report = SeededRegionManager.from_path().initialize(snapshot, [], config)

    base_labels = {str(index) for index in range(1, len(snapshot.grid_catalog) + 1)}
    region_labels = {
        str(region.condition_label)
        for region in snapshot.policy_regions.values()
    }

    assert region_labels == set(REGION_LABELS.values())
    assert region_labels.isdisjoint(base_labels)
    assert all(label.isdigit() for label in region_labels)

    names_by_label = {
        region.condition_label: region.evidence.get("region_name")
        for region in snapshot.policy_regions.values()
    }
    assert names_by_label == {
        label: name for name, label in REGION_LABELS.items()
    }
    report_names = {
        item["condition_label"]: item["region_name"]
        for item in report["regions"]
    }
    assert report_names == names_by_label


def test_incremental_update_keeps_previous_published_regions():
    config = default_config()
    base = InitialConditionBuilder(config).build([], "v001")
    manager = SeededRegionManager.from_path()
    base, _ = manager.initialize(base, [], config)

    updated = _next_snapshot(base, "v002")
    updated, report = manager.update(updated, base, [], config)

    assert {
        grid_id: cell.policy_region_id
        for grid_id, cell in updated.grid_catalog.items()
    } == {
        grid_id: cell.policy_region_id
        for grid_id, cell in base.grid_catalog.items()
    }
    assert all(item["decision"] == "KEEP" for item in report["regions"])
    assert report["robust_quantile_scope"] == "IN_RANGE_ONLY"
    assert report["mode"] == "KEEP_WITH_CONTEXT_SHIFT_WATCH"


def test_pending_context_shift_pauses_on_insufficient_and_only_stable_clears():
    config = default_config()
    manager = SeededRegionManager.from_path()
    base = InitialConditionBuilder(config).build([], "v001")
    baseline_rows = _rows(
        yyq_so2=1950.0,
        liquid_gas_ratio=24.0,
        count=300,
        dates=("2026/07/01", "2026/07/02"),
    )
    base, _ = manager.initialize(base, baseline_rows, config)
    key = "P15-S1::XP3-AP0"
    state = base.metadata["condition_region_v2"]
    assert key in state["robust_baseline_by_grid_pump"]

    v002 = _next_snapshot(base, "v002")
    v002, _ = manager.update(
        v002,
        base,
        _rows(
            yyq_so2=1950.0,
            liquid_gas_ratio=26.0,
            count=300,
            dates=("2026/07/03", "2026/07/04"),
        ),
        config,
    )
    pending = v002.metadata["condition_region_v2"]["pending_context_shift_by_grid_pump"][key]
    assert pending["status"] == "SUSPECTED_CONTEXT_SHIFT"
    assert pending["consecutive_supported_versions"] == 1
    assert pending["continuity_state"] == "ACTIVE_SUPPORTED_SHIFT"

    v003 = _next_snapshot(v002, "v003")
    v003, _ = manager.update(
        v003,
        v002,
        _rows(
            yyq_so2=1950.0,
            liquid_gas_ratio=26.0,
            count=20,
            dates=("2026/07/05",),
        ),
        config,
    )
    state = v003.metadata["condition_region_v2"]
    assert state["last_batch_context_shift_by_grid_pump"][key]["status"] == "INSUFFICIENT_EVIDENCE"
    pending = state["pending_context_shift_by_grid_pump"][key]
    assert pending["consecutive_supported_versions"] == 1
    assert pending["continuity_state"] == "PAUSED_INSUFFICIENT_EVIDENCE"

    v004 = _next_snapshot(v003, "v004")
    v004, _ = manager.update(
        v004,
        v003,
        _rows(
            yyq_so2=1950.0,
            liquid_gas_ratio=26.0,
            count=300,
            dates=("2026/07/06", "2026/07/07"),
        ),
        config,
    )
    pending = v004.metadata["condition_region_v2"]["pending_context_shift_by_grid_pump"][key]
    assert pending["consecutive_supported_versions"] == 2
    assert pending["continuity_state"] == "ACTIVE_SUPPORTED_SHIFT"

    v005 = _next_snapshot(v004, "v005")
    v005, _ = manager.update(
        v005,
        v004,
        _rows(
            yyq_so2=1950.0,
            liquid_gas_ratio=24.0,
            count=300,
            dates=("2026/07/08", "2026/07/09"),
        ),
        config,
    )
    state = v005.metadata["condition_region_v2"]
    assert state["last_batch_context_shift_by_grid_pump"][key]["status"] == "STABLE"
    assert key not in state["pending_context_shift_by_grid_pump"]


def test_new_grid_pump_stratum_warms_up_before_becoming_baseline():
    config = default_config()
    manager = SeededRegionManager.from_path()
    base = InitialConditionBuilder(config).build([], "v001")
    base, _ = manager.initialize(
        base,
        _rows(
            yyq_so2=1950.0,
            liquid_gas_ratio=24.0,
            count=300,
            dates=("2026/07/01", "2026/07/02"),
        ),
        config,
    )
    new_key = "P16-S1::XP3-AP0"

    v002 = _next_snapshot(base, "v002")
    v002, _ = manager.update(
        v002,
        base,
        _rows(
            yyq_so2=2050.0,
            liquid_gas_ratio=24.0,
            count=20,
            dates=("2026/07/03",),
        ),
        config,
    )
    state = v002.metadata["condition_region_v2"]
    assert state["last_batch_context_shift_by_grid_pump"][new_key]["status"] == "BASELINE_WARMUP"
    assert new_key not in state["robust_baseline_by_grid_pump"]
    assert new_key in state["baseline_warmup_by_grid_pump"]

    v003 = _next_snapshot(v002, "v003")
    v003, _ = manager.update(
        v003,
        v002,
        _rows(
            yyq_so2=2050.0,
            liquid_gas_ratio=24.0,
            count=300,
            dates=("2026/07/04", "2026/07/05"),
        ),
        config,
    )
    state = v003.metadata["condition_region_v2"]
    assert state["last_batch_context_shift_by_grid_pump"][new_key]["status"] == "BASELINE_INITIALIZED"
    assert new_key in state["robust_baseline_by_grid_pump"]
    assert new_key not in state["baseline_warmup_by_grid_pump"]

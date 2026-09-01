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


def test_seeded_region_labels_do_not_overlap_base_condition_ids():
    config = default_config()
    snapshot = InitialConditionBuilder(config).build([], "v001")
    snapshot, _ = SeededRegionManager.from_path().initialize(snapshot, [], config)

    base_labels = {str(index) for index in range(1, len(snapshot.grid_catalog) + 1)}
    region_labels = {
        str(region.condition_label)
        for region in snapshot.policy_regions.values()
    }

    assert region_labels == set(REGION_LABELS.values())
    assert region_labels.isdisjoint(base_labels)
    assert all(label.isdigit() for label in region_labels)


def test_incremental_update_keeps_previous_published_regions():
    config = default_config()
    base = InitialConditionBuilder(config).build([], "v001")
    manager = SeededRegionManager.from_path()
    base, _ = manager.initialize(base, [], config)

    updated = deepcopy(base)
    updated.snapshot_version = "v002"
    updated.previous_snapshot_version = "v001"
    updated, report = manager.update(updated, base, [], config)

    assert {
        grid_id: cell.policy_region_id
        for grid_id, cell in updated.grid_catalog.items()
    } == {
        grid_id: cell.policy_region_id
        for grid_id, cell in base.grid_catalog.items()
    }
    assert all(item["decision"] == "KEEP" for item in report["regions"])

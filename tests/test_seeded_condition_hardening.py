from copy import deepcopy

from system.model.map_control.condition_model.condition_config import default_config
from system.model.map_control.condition_model.initial_condition_builder import (
    InitialConditionBuilder,
)
from system.model.map_control.condition_model.seeded_region_hardening import (
    ACCEPT_NEW_CONTEXT_BASELINE,
    KEEP_REFERENCE,
    SENSOR_OR_DATA_ISSUE,
    HardenedSeededRegionManager,
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


def _region_report(report, condition_label):
    return next(
        item
        for item in report["regions"]
        if item["condition_label"] == condition_label
    )


def _confirmed_shift_chain():
    config = default_config()
    manager = HardenedSeededRegionManager.from_path()
    key = "P15-S1::XP3-AP0"

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

    previous = base
    for version, dates in (
        ("v002", ("2026/07/03", "2026/07/04")),
        ("v003", ("2026/07/05", "2026/07/06")),
        ("v004", ("2026/07/07", "2026/07/08")),
    ):
        current = _next_snapshot(previous, version)
        current, report = manager.update(
            current,
            previous,
            _rows(
                yyq_so2=1950.0,
                liquid_gas_ratio=26.0,
                count=300,
                dates=dates,
            ),
            config,
        )
        previous = current
    return config, manager, key, base, previous, report


def test_hardened_initial_disables_legacy_auto_merge_semantics():
    config = default_config()
    snapshot = InitialConditionBuilder(config).build([], "v001")
    snapshot, report = HardenedSeededRegionManager.from_path().initialize(
        snapshot,
        _rows(
            yyq_so2=1950.0,
            liquid_gas_ratio=24.0,
            count=300,
            dates=("2026/07/01", "2026/07/02"),
        ),
        config,
    )

    state = snapshot.metadata["condition_region_v2"]
    assert state["schema_version"] == "1.2"
    assert state["legacy_auto_merge_bypassed"] is True
    assert snapshot.grid_config["merge"]["enabled"] is False
    assert snapshot.grid_config["merge"]["mode"] == "disabled"
    assert report["legacy_auto_merge_bypassed"] is True
    assert report["context_resolution_policy"]["automatic_reference_replacement"] is False
    assert set(report["context_resolution_policy"]["allowed_decisions"]) == {
        KEEP_REFERENCE,
        ACCEPT_NEW_CONTEXT_BASELINE,
        SENSOR_OR_DATA_ISSUE,
    }


def test_confirmed_context_shift_is_visible_in_compact_region_report():
    _, _, key, _, v004, report = _confirmed_shift_chain()
    state = v004.metadata["condition_region_v2"]
    pending = state["pending_context_shift_by_grid_pump"][key]

    assert pending["consecutive_supported_versions"] == 3
    assert pending["confirmed_context_shift"] is True
    assert pending["requires_context_review"] is True
    assert pending["candidate_supported_versions"] == 3
    assert pending["candidate_summary"]["in_range_count"] == 900
    assert pending["candidate_reference_eligible"] is True

    region = _region_report(report, "10004")
    assert region["pending_context_shift_count"] == 1
    assert region["active_pending_context_shift_count"] == 1
    assert region["paused_pending_context_shift_count"] == 0
    assert region["confirmed_context_shift_count"] == 1
    assert region["requires_context_review"] is True
    assert "SUSPECTED_CONTEXT_SHIFT" in region["pending_context_shift_statuses"]
    assert report["confirmed_context_shift_count"] == 1
    assert report["manual_context_review_required"] is True


def test_accept_new_context_baseline_requires_explicit_resolution_and_versions_reference():
    config, manager, key, base, v004, _ = _confirmed_shift_chain()
    original_baseline = deepcopy(
        base.metadata["condition_region_v2"]["robust_baseline_by_grid_pump"][key]
    )
    v004_state = v004.metadata["condition_region_v2"]
    assert v004_state["context_reference_generation_by_grid_pump"][key] == 1
    assert v004_state["robust_baseline_by_grid_pump"][key] == original_baseline

    v005 = _next_snapshot(v004, "v005")
    v005, report = manager.update(
        v005,
        v004,
        [],
        config,
        context_resolutions={
            key: {
                "decision": ACCEPT_NEW_CONTEXT_BASELINE,
                "reviewer": "offline-review",
                "reason": "persistent gas-flow context accepted",
                "reviewed_at": "2026-07-09T12:00:00+08:00",
            }
        },
    )
    state = v005.metadata["condition_region_v2"]

    assert key not in state["pending_context_shift_by_grid_pump"]
    assert state["context_reference_generation_by_grid_pump"][key] == 2
    assert state["robust_baseline_by_grid_pump"][key] != original_baseline
    assert state["robust_baseline_by_grid_pump"][key]["in_range_count"] == 900
    assert set(state["robust_baseline_dates_by_grid_pump"][key]) == {
        "2026/07/03",
        "2026/07/04",
        "2026/07/05",
        "2026/07/06",
        "2026/07/07",
        "2026/07/08",
    }
    event = state["context_resolution_history"][-1]
    assert event["decision"] == ACCEPT_NEW_CONTEXT_BASELINE
    assert event["reference_generation_before"] == 1
    assert event["reference_generation_after"] == 2
    assert event["structural_decision_authority"] is False
    assert report["pending_context_shift_count"] == 0
    assert report["manual_context_review_required"] is False


def test_keep_reference_resolution_clears_pending_without_changing_reference():
    config, manager, key, _, v004, _ = _confirmed_shift_chain()
    before = deepcopy(
        v004.metadata["condition_region_v2"]["robust_baseline_by_grid_pump"][key]
    )

    v005 = _next_snapshot(v004, "v005")
    v005, _ = manager.update(
        v005,
        v004,
        [],
        config,
        context_resolutions={
            key: {
                "decision": KEEP_REFERENCE,
                "reviewer": "offline-review",
                "reason": "retain original reference for temporary context",
            }
        },
    )
    state = v005.metadata["condition_region_v2"]

    assert key not in state["pending_context_shift_by_grid_pump"]
    assert state["robust_baseline_by_grid_pump"][key] == before
    assert state["context_reference_generation_by_grid_pump"][key] == 1
    assert state["latest_context_resolution_by_grid_pump"][key]["decision"] == KEEP_REFERENCE

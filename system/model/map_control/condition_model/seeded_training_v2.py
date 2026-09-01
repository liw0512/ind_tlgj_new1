# -*- coding: utf-8 -*-
"""Validated seeded-region V2 training entrypoints for the first condition module.

The base-grid statistics remain owned by InitialConditionBuilder and
IncrementalConditionUpdater. Region publication and operating-context lifecycle
are handled by HardenedSeededRegionManager. The hardened path explicitly
bypasses legacy AutoMerge publication, preserves fixed seeded region boundaries,
and requires explicit review before a confirmed context shift can replace its
reference baseline.

The module supports both package execution (``python -m ...``) and direct script
execution from the repository root. Direct execution needs the project root on
``sys.path`` before importing the top-level ``system`` package.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from system.model.map_control.condition_model.condition_config import (
    MAX_SNAPSHOT_VERSIONS_TO_KEEP,
    default_config,
    from_dict,
)
from system.model.map_control.condition_model.initial_condition_builder import (
    InitialConditionBuilder,
    _empty_merge_statistics,
    append_condition_columns,
    normalize_and_validate_training_frame,
    sync_statistics_compatibility_from_snapshot,
    update_merge_statistics,
    write_merge_statistics,
)
from system.model.map_control.condition_model.incremental_condition_updater import (
    IncrementalConditionUpdater,
    resolve_incremental_snapshot_target,
)
from system.model.map_control.condition_model.seeded_region_hardening import (
    HardenedSeededRegionManager,
)
from system.model.map_control.condition_model.snapshot_io import (
    cleanup_old_snapshot_versions,
    read_latest_available_snapshot,
    write_snapshot,
)


def _write_json(value: Any, path: Optional[str]) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def _read_optional_mapping(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return {}
    target = Path(path)
    with open(target, "r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, Mapping):
        raise TypeError(f"expected JSON object in {target}")
    return dict(value)


def _mark_v2_compatibility_statistics(statistics: Dict[str, Any]) -> Dict[str, Any]:
    """Make legacy merge-statistics semantics explicit for seeded-region V2."""
    statistics["description"] = (
        "Legacy compatibility statistics for condition-label readers. "
        "Raw liquid_gas_mean is retained for backward compatibility only and "
        "is NOT seeded-region V2 merge/split evidence."
    )
    statistics["v2_semantics"] = {
        "region_membership_authority": "condition_snapshot.policy_regions",
        "robust_operating_context_evidence": "condition_snapshot.metadata.condition_region_v2",
        "robust_structure_evidence": None,
        "raw_liquid_gas_mean_is_structural_evidence": False,
        "liquid_gas_context_shift_is_process_drift": False,
        "legacy_auto_merge_is_active": False,
        "context_reference_replacement_is_automatic": False,
        "condition_regions_members": (
            "observed base-condition statistics only; use snapshot policy_regions "
            "for full region membership including zero-sample grids"
        ),
    }
    return statistics


def build_initial_seeded_condition_csv(
    *,
    input_csv_path: str,
    output_csv_path: str,
    snapshot_output_path: str,
    structure_report_path: Optional[str] = None,
    merge_statistics_json_path: Optional[str] = None,
    seed_path: Optional[str] = None,
    snapshot_version: str = "v001",
    encoding: str = "utf-8-sig",
) -> str:
    config = default_config()
    frame = pd.read_csv(input_csv_path, encoding=encoding)
    frame = normalize_and_validate_training_frame(
        frame,
        config,
        context="seeded initial training",
    )
    rows = frame.to_dict(orient="records")

    snapshot = InitialConditionBuilder(config).build(rows, snapshot_version)
    snapshot, report = HardenedSeededRegionManager.from_path(seed_path).initialize(
        snapshot,
        rows,
        config,
    )

    statistics = update_merge_statistics(
        _empty_merge_statistics(),
        rows,
        config,
    )
    statistics = sync_statistics_compatibility_from_snapshot(
        statistics,
        snapshot,
        config,
    )
    statistics = _mark_v2_compatibility_statistics(statistics)

    write_snapshot(snapshot, snapshot_output_path)
    cleanup_old_snapshot_versions(
        snapshot_output_path,
        MAX_SNAPSHOT_VERSIONS_TO_KEEP,
    )
    write_merge_statistics(statistics, merge_statistics_json_path)
    _write_json(report, structure_report_path)

    result = append_condition_columns(frame, snapshot, config)
    target = Path(output_csv_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(target, index=False, encoding="utf-8-sig")
    return str(target)


def build_incremental_seeded_condition_csv(
    *,
    base_snapshot_path: str,
    input_csv_path: str,
    output_csv_path: str,
    snapshot_output_path: str = "auto",
    structure_report_path: Optional[str] = None,
    merge_statistics_json_path: Optional[str] = None,
    context_resolution_path: Optional[str] = None,
    seed_path: Optional[str] = None,
    snapshot_version: str = "auto",
    encoding: str = "utf-8-sig",
) -> str:
    base_snapshot, resolved_base_snapshot_path = read_latest_available_snapshot(
        base_snapshot_path
    )
    base_state = (base_snapshot.metadata or {}).get("condition_region_v2") or {}
    if not base_state:
        raise RuntimeError(
            "seeded V2 incremental training cannot silently upgrade a legacy "
            "AutoMerge snapshot; rebuild a seeded initial snapshot first"
        )

    config = from_dict(base_snapshot.grid_config)
    resolved_output, resolved_version = resolve_incremental_snapshot_target(
        resolved_base_snapshot_path,
        base_snapshot,
        snapshot_output_path,
        snapshot_version,
    )

    frame = pd.read_csv(input_csv_path, encoding=encoding)
    frame = normalize_and_validate_training_frame(
        frame,
        config,
        context="seeded incremental training",
    )
    rows = frame.to_dict(orient="records")
    resolutions = _read_optional_mapping(context_resolution_path)

    # Important: use only the additive frozen-grid updater here. The legacy
    # build_incremental_condition_csv() AutoMerge publication path is not called.
    updated = IncrementalConditionUpdater(config).update(
        base_snapshot,
        rows,
        resolved_version,
    )
    updated, report = HardenedSeededRegionManager.from_path(seed_path).update(
        updated,
        base_snapshot,
        rows,
        config,
        context_resolutions=resolutions,
    )

    statistics = update_merge_statistics(
        _empty_merge_statistics(),
        rows,
        config,
    )
    statistics = sync_statistics_compatibility_from_snapshot(
        statistics,
        updated,
        config,
    )
    statistics = _mark_v2_compatibility_statistics(statistics)

    write_snapshot(updated, resolved_output)
    cleanup_old_snapshot_versions(
        resolved_output,
        MAX_SNAPSHOT_VERSIONS_TO_KEEP,
    )
    write_merge_statistics(statistics, merge_statistics_json_path)
    if structure_report_path is None:
        structure_report_path = str(
            Path(resolved_output).parent / "condition_structure_report.json"
        )
    _write_json(report, structure_report_path)

    result = append_condition_columns(frame, updated, config)
    target = Path(output_csv_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(target, index=False, encoding="utf-8-sig")
    return str(target)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Hardened seeded-region V2 training for the first condition module"
    )
    sub = parser.add_subparsers(dest="mode", required=True)

    initial = sub.add_parser("initial")
    initial.add_argument("--input", required=True)
    initial.add_argument("--output", required=True)
    initial.add_argument("--snapshot-output", required=True)
    initial.add_argument("--structure-report")
    initial.add_argument("--merge-statistics-output")
    initial.add_argument("--seed")
    initial.add_argument("--snapshot-version", default="v001")
    initial.add_argument("--encoding", default="utf-8-sig")

    incremental = sub.add_parser("incremental")
    incremental.add_argument("--base-snapshot", required=True)
    incremental.add_argument("--input", required=True)
    incremental.add_argument("--output", required=True)
    incremental.add_argument("--snapshot-output", default="auto")
    incremental.add_argument("--structure-report")
    incremental.add_argument("--merge-statistics-output")
    incremental.add_argument(
        "--context-resolutions",
        help=(
            "optional JSON object keyed by grid+pump stratum; each value is "
            "KEEP_REFERENCE, ACCEPT_NEW_CONTEXT_BASELINE, SENSOR_OR_DATA_ISSUE, "
            "or an object containing decision/reviewer/reason/reviewed_at"
        ),
    )
    incremental.add_argument("--seed")
    incremental.add_argument("--snapshot-version", default="auto")
    incremental.add_argument("--encoding", default="utf-8-sig")

    args = parser.parse_args()
    if args.mode == "initial":
        output = build_initial_seeded_condition_csv(
            input_csv_path=args.input,
            output_csv_path=args.output,
            snapshot_output_path=args.snapshot_output,
            structure_report_path=args.structure_report,
            merge_statistics_json_path=args.merge_statistics_output,
            seed_path=args.seed,
            snapshot_version=args.snapshot_version,
            encoding=args.encoding,
        )
    else:
        output = build_incremental_seeded_condition_csv(
            base_snapshot_path=args.base_snapshot,
            input_csv_path=args.input,
            output_csv_path=args.output,
            snapshot_output_path=args.snapshot_output,
            structure_report_path=args.structure_report,
            merge_statistics_json_path=args.merge_statistics_output,
            context_resolution_path=args.context_resolutions,
            seed_path=args.seed,
            snapshot_version=args.snapshot_version,
            encoding=args.encoding,
        )
    print(output)


if __name__ == "__main__":
    main()

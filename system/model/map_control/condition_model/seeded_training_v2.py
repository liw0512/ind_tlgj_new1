# -*- coding: utf-8 -*-
"""Shadow/offline entrypoints for the first-module seeded-region V2 flow.

This file deliberately does not replace the production initial/incremental
entrypoints yet. It reuses the existing base-grid builders and snapshot format,
then applies the new seeded-region layer. After CSV replay validates the
migration, the production entrypoints can be switched with a very small diff.

The module supports both package execution (``python -m ...``) and direct script
execution from the repository root.  Direct execution needs the project root on
``sys.path`` before importing the top-level ``system`` package.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

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
from system.model.map_control.condition_model.seeded_region_manager import (
    SeededRegionManager,
)
from system.model.map_control.condition_model.snapshot_io import (
    cleanup_old_snapshot_versions,
    read_latest_available_snapshot,
    write_snapshot,
)


def _write_json(value, path: Optional[str]) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


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
    snapshot, report = SeededRegionManager.from_path(seed_path).initialize(
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
    seed_path: Optional[str] = None,
    snapshot_version: str = "auto",
    encoding: str = "utf-8-sig",
) -> str:
    base_snapshot, resolved_base_snapshot_path = read_latest_available_snapshot(
        base_snapshot_path
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

    updated = IncrementalConditionUpdater(config).update(
        base_snapshot,
        rows,
        resolved_version,
    )
    updated, report = SeededRegionManager.from_path(seed_path).update(
        updated,
        base_snapshot,
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
        updated,
        config,
    )

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
        description="Offline V2 seeded-region training for the first condition module"
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
            seed_path=args.seed,
            snapshot_version=args.snapshot_version,
            encoding=args.encoding,
        )
    print(output)


if __name__ == "__main__":
    main()

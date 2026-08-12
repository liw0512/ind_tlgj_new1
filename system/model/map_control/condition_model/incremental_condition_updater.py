# -*- coding: utf-8 -*-
"""Incremental V3 statistics updater using the frozen grid definition.

Historical statistics are updated directly through per-cell additive
accumulators.  The module contains no action-event, confidence, or slurry-flow
logic.
"""

import argparse
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from system.model.map_control.condition_model.condition_config import (
    INCREMENTAL_CONDITION_TRAIN_CONFIG,
    MAX_SNAPSHOT_VERSIONS_TO_KEEP,
    ConditionModelConfig,
    from_dict,
)
from system.model.map_control.condition_model.condition_schema import (
    ConditionSnapshot,
)
from system.model.map_control.condition_model.auto_merge_manager import (
    AutoMergeManager,
    SNAPSHOT_SCHEMA_VERSION,
    write_auto_merge_report,
)
from system.model.map_control.condition_model.grid_definition import locate_grid
from system.model.map_control.condition_model.initial_condition_builder import (
    append_condition_columns,
    build_state_key,
    ensure_cell_accumulators,
    finalize_cell_from_accumulators,
    get_condition_axis_values,
    load_merge_statistics,
    normalize_and_validate_training_frame,
    snapshot_has_published_labels,
    sync_statistics_compatibility_from_snapshot,
    update_merge_statistics,
    update_numeric_accumulator,
    update_risk_accumulator,
    write_merge_statistics,
)
from system.model.map_control.condition_model.snapshot_io import (
    cleanup_old_snapshot_versions,
    read_latest_available_snapshot,
    write_snapshot,
)


SNAPSHOT_FILENAME = "condition_snapshot.json"


def next_snapshot_version(base_version: str) -> str:
    text = str(base_version).strip()
    if len(text) >= 2 and text[0].lower() == "v" and text[1:].isdigit():
        return f"v{int(text[1:]) + 1:03d}"
    raise ValueError(f"Cannot auto-increment snapshot version: {base_version}")


def resolve_incremental_snapshot_target(
    base_snapshot_path: str,
    base_snapshot: ConditionSnapshot,
    snapshot_output_path: str = "auto",
    snapshot_version: str = "auto",
) -> tuple:
    resolved_version = (
        next_snapshot_version(base_snapshot.snapshot_version)
        if not snapshot_version or str(snapshot_version).lower() == "auto"
        else str(snapshot_version)
    )
    if not snapshot_output_path or str(snapshot_output_path).lower() == "auto":
        base_path = Path(base_snapshot_path)
        snapshots_dir = base_path.parent.parent
        resolved_path = snapshots_dir / resolved_version / SNAPSHOT_FILENAME
    else:
        resolved_path = Path(snapshot_output_path)
    return str(resolved_path), resolved_version


def resolve_auto_merge_report_target(
    snapshot_output_path: str,
    auto_merge_report_path: str = "auto",
) -> str:
    if not auto_merge_report_path or str(auto_merge_report_path).lower() == "auto":
        return str(Path(snapshot_output_path).parent / "auto_merge_report.json")
    return str(Path(auto_merge_report_path))


class IncrementalConditionUpdater:
    def __init__(self, config: ConditionModelConfig):
        self.config = config

    def update(
        self,
        snapshot: ConditionSnapshot,
        rows: Iterable[Dict[str, Any]],
        new_version: str,
    ) -> ConditionSnapshot:
        frozen_config = from_dict(snapshot.grid_config)
        if (
            frozen_config.load != self.config.load
            or frozen_config.inlet_so2 != self.config.inlet_so2
            or frozen_config.load_column != self.config.load_column
            or frozen_config.inlet_so2_column != self.config.inlet_so2_column
            or frozen_config.data_columns != self.config.data_columns
            or frozen_config.emission_limit != self.config.emission_limit
        ):
            raise ValueError(
                "Incremental update cannot change the published grid, field "
                "mapping, or emission limit"
            )

        updated = deepcopy(snapshot)
        updated.snapshot_version = new_version
        updated.previous_snapshot_version = snapshot.snapshot_version
        updated.build_time = datetime.now(timezone.utc).isoformat()
        updated.metadata = deepcopy(snapshot.metadata)
        updated.metadata.pop("action_event_registry", None)
        updated.metadata["snapshot_schema_version"] = SNAPSHOT_SCHEMA_VERSION

        for cell in updated.grid_catalog.values():
            ensure_cell_accumulators(cell)
        for row in rows:
            self._add_incremental_row(updated, row)
        for cell in updated.grid_catalog.values():
            finalize_cell_from_accumulators(cell, self.config)
        return updated

    def _add_incremental_row(
        self,
        snapshot: ConditionSnapshot,
        row: Dict[str, Any],
    ) -> None:
        if row.get("condition_mapping_ok", True) is False:
            return
        try:
            load_value, inlet_so2 = get_condition_axis_values(row, self.config)
            grid_id, clipped, _ = locate_grid(
                load_value,
                inlet_so2,
                self.config,
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            return

        cell = snapshot.grid_catalog[grid_id]
        ensure_cell_accumulators(cell)
        cell.sample_count += 1
        cell.clipped_count += int(clipped)

        state_key = build_state_key(row)
        profile = cell.state_profiles.setdefault(state_key, {"sample_count": 0})
        profile["sample_count"] += 1
        pump_key = "-".join(state_key.split("-")[:2])
        cell.pump_distribution[pump_key] = (
            cell.pump_distribution.get(pump_key, 0) + 1
        )

        columns = self.config.data_columns
        numeric = cell.accumulators.setdefault("numeric", {})
        update_numeric_accumulator(
            numeric.setdefault("liquid_gas", {}),
            row.get(columns.liquid_gas),
        )
        update_numeric_accumulator(
            numeric.setdefault("xst_ph", {}),
            row.get(columns.xst_ph),
        )
        update_numeric_accumulator(
            numeric.setdefault("apt_ph", {}),
            row.get(columns.apt_ph),
        )
        update_numeric_accumulator(
            numeric.setdefault("net_so2", {}),
            row.get(columns.outlet_so2),
        )

        risk = cell.accumulators.setdefault(
            "risk",
            {"valid_count": 0, "risk_count": 0},
        )
        update_risk_accumulator(
            risk,
            row.get(columns.outlet_so2),
            self.config.emission_limit,
        )


def build_incremental_condition_csv(
    base_snapshot_path: str,
    input_csv_path: str,
    output_csv_path: str,
    snapshot_output_path: str = "auto",
    merge_statistics_json_path: str = "",
    auto_merge_report_path: str = "auto",
    snapshot_version: str = "auto",
    encoding: str = "utf-8-sig",
) -> str:
    base_snapshot, resolved_base_snapshot_path = read_latest_available_snapshot(
        base_snapshot_path
    )
    snapshot_output_path, snapshot_version = resolve_incremental_snapshot_target(
        resolved_base_snapshot_path,
        base_snapshot,
        snapshot_output_path,
        snapshot_version,
    )
    auto_merge_report_path = resolve_auto_merge_report_target(
        snapshot_output_path,
        auto_merge_report_path,
    )

    config = from_dict(base_snapshot.grid_config)
    frame = pd.read_csv(input_csv_path, encoding=encoding)
    frame = normalize_and_validate_training_frame(
        frame,
        config,
        context="incremental training",
    )
    rows = frame.to_dict(orient="records")
    updated_snapshot = IncrementalConditionUpdater(config).update(
        base_snapshot,
        rows,
        snapshot_version,
    )
    updated_snapshot, auto_merge_report = AutoMergeManager(config).apply(
        updated_snapshot,
        previous_snapshot=base_snapshot,
    )

    target = Path(output_csv_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    statistics = load_merge_statistics(merge_statistics_json_path)
    if snapshot_has_published_labels(base_snapshot):
        statistics = sync_statistics_compatibility_from_snapshot(
            statistics,
            base_snapshot,
            config,
        )
    statistics = update_merge_statistics(statistics, rows, config)
    statistics = sync_statistics_compatibility_from_snapshot(
        statistics,
        updated_snapshot,
        config,
    )

    write_snapshot(updated_snapshot, snapshot_output_path)
    cleanup_old_snapshot_versions(
        snapshot_output_path,
        MAX_SNAPSHOT_VERSIONS_TO_KEEP,
    )
    write_merge_statistics(statistics, merge_statistics_json_path)
    write_auto_merge_report(auto_merge_report, auto_merge_report_path)

    result = append_condition_columns(frame, updated_snapshot, config)
    result.to_csv(target, index=False, encoding="utf-8-sig")
    summary = auto_merge_report.get("summary", {})
    print(
        f"增量工况训练标注完成: input={input_csv_path}, output={target}, "
        f"base_snapshot={resolved_base_snapshot_path}, "
        f"snapshot={snapshot_output_path}, version={snapshot_version}, "
        f"rows={len(result)}, "
        f"provisional_regions={summary.get('provisional_region_count', 0)}, "
        f"confirmed_regions={summary.get('confirmed_region_count', 0)}, "
        f"split_events={summary.get('split_event_count', 0)}"
    )
    return str(target)


def run_configured_incremental_train() -> str:
    return build_incremental_condition_csv(
        base_snapshot_path=INCREMENTAL_CONDITION_TRAIN_CONFIG[
            "base_snapshot_path"
        ],
        input_csv_path=INCREMENTAL_CONDITION_TRAIN_CONFIG["input_csv_path"],
        output_csv_path=INCREMENTAL_CONDITION_TRAIN_CONFIG["output_csv_path"],
        snapshot_output_path=INCREMENTAL_CONDITION_TRAIN_CONFIG[
            "snapshot_output_path"
        ],
        merge_statistics_json_path=INCREMENTAL_CONDITION_TRAIN_CONFIG.get(
            "merge_statistics_json_path",
            "",
        ),
        auto_merge_report_path=INCREMENTAL_CONDITION_TRAIN_CONFIG.get(
            "auto_merge_report_path",
            "auto",
        ),
        snapshot_version=INCREMENTAL_CONDITION_TRAIN_CONFIG.get(
            "snapshot_version",
            "auto",
        ),
        encoding=INCREMENTAL_CONDITION_TRAIN_CONFIG.get(
            "encoding",
            "utf-8-sig",
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build incremental condition labels for a CSV dataset"
    )
    parser.add_argument("--base-snapshot", default=None)
    parser.add_argument("--input", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--snapshot-output", default=None)
    parser.add_argument("--merge-statistics-output", default=None)
    parser.add_argument("--auto-merge-report", default=None)
    parser.add_argument("--snapshot-version", default=None)
    parser.add_argument("--encoding", default=None)
    args = parser.parse_args()

    if args.input or args.output or args.base_snapshot:
        output = build_incremental_condition_csv(
            base_snapshot_path=(
                args.base_snapshot
                or INCREMENTAL_CONDITION_TRAIN_CONFIG["base_snapshot_path"]
            ),
            input_csv_path=(
                args.input
                or INCREMENTAL_CONDITION_TRAIN_CONFIG["input_csv_path"]
            ),
            output_csv_path=(
                args.output
                or INCREMENTAL_CONDITION_TRAIN_CONFIG["output_csv_path"]
            ),
            snapshot_output_path=(
                args.snapshot_output
                or INCREMENTAL_CONDITION_TRAIN_CONFIG["snapshot_output_path"]
            ),
            merge_statistics_json_path=(
                args.merge_statistics_output
                or INCREMENTAL_CONDITION_TRAIN_CONFIG.get(
                    "merge_statistics_json_path",
                    "",
                )
            ),
            auto_merge_report_path=(
                args.auto_merge_report
                or INCREMENTAL_CONDITION_TRAIN_CONFIG.get(
                    "auto_merge_report_path",
                    "auto",
                )
            ),
            snapshot_version=(
                args.snapshot_version
                or INCREMENTAL_CONDITION_TRAIN_CONFIG.get(
                    "snapshot_version",
                    "auto",
                )
            ),
            encoding=(
                args.encoding
                or INCREMENTAL_CONDITION_TRAIN_CONFIG.get(
                    "encoding",
                    "utf-8-sig",
                )
            ),
        )
    else:
        output = run_configured_incremental_train()
    print(output)


if __name__ == "__main__":
    main()

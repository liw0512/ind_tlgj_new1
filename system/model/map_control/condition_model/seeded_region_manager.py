# -*- coding: utf-8 -*-
"""Seeded condition-region publication and conservative drift lifecycle.

The 100 mg/Nm3 base grid remains the stable coordinate system. This module
publishes versioned operating regions on top of that grid and maintains robust
liquid/gas evidence by ``base_grid + circulation-pump state``.

Important separation of concerns:
- the existing InitialConditionBuilder / IncrementalConditionUpdater continue
  to own base-grid statistics;
- this module owns region publication and robust distribution evidence;
- it does not use historical supply actions to infer Q->SO2 / Q->pH gains;
  those dynamics belong to the second module.
"""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

from system.model.config.standard_fields import LIQUID_GAS_RATIO_COLUMN
from system.model.map_control.condition_model.condition_schema import (
    ConditionSnapshot,
    PolicyRegion,
)
from system.model.map_control.condition_model.grid_definition import locate_grid
from system.model.map_control.condition_model.robust_statistics import (
    RobustHistogramConfig,
    add_value,
    classify_distribution_shift,
    empty_histogram,
    merge_histograms,
    summarize_histogram,
)


REGION_STRUCTURE_SCHEMA_VERSION = "1.0"
ROBUST_QUANTILE_SCOPE = "IN_RANGE_ONLY"
DEFAULT_SEED_PATH = Path(__file__).with_name("region_seed_steel_v001.json")


def load_seed_definition(path: Optional[str] = None) -> Dict[str, Any]:
    target = Path(path) if path else DEFAULT_SEED_PATH
    with open(target, "r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict) or not value.get("regions"):
        raise ValueError(f"invalid condition region seed: {target}")
    return value


def _group_key(grid_id: str, pump_state: str) -> str:
    return f"{grid_id}::{pump_state}"


def _pump_state_from_state_key(state_key: str) -> str:
    parts = str(state_key).split("-")
    return "-".join(parts[:2]) if len(parts) >= 2 else str(state_key)


def _date_key(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text[:10] if len(text) >= 10 else None


def _row_date(row: Mapping[str, Any]) -> Optional[str]:
    for name in ("date", "timestamp", "datetime", "time"):
        if name in row:
            key = _date_key(row.get(name))
            if key:
                return key
    return None


class SeededRegionManager:
    def __init__(self, seed_definition: Mapping[str, Any]):
        self.seed = deepcopy(dict(seed_definition))
        self.robust_config = RobustHistogramConfig.from_mapping(
            self.seed.get("robust_liquid_gas")
        )
        self.robust_config.validate()

    @classmethod
    def from_path(cls, path: Optional[str] = None) -> "SeededRegionManager":
        return cls(load_seed_definition(path))

    def initialize(
        self,
        snapshot: ConditionSnapshot,
        rows: Iterable[Mapping[str, Any]],
        config: Any,
    ) -> Tuple[ConditionSnapshot, Dict[str, Any]]:
        self._validate_seed_against_config(config)
        self._publish_seed_regions(snapshot, config)
        histograms, dates = self._batch_histograms(rows, config)
        robust_baseline = {
            key: histogram
            for key, histogram in histograms.items()
        }
        report = self._structure_report(
            snapshot=snapshot,
            drift_by_group={},
            mode="INITIAL_SEED",
        )
        snapshot.metadata = dict(snapshot.metadata or {})
        snapshot.metadata.pop("auto_merge_state", None)
        snapshot.metadata["condition_region_v2"] = {
            "schema_version": REGION_STRUCTURE_SCHEMA_VERSION,
            "seed_version": self.seed.get("seed_version", "unknown"),
            "region_mode": "SEEDED_KEEP",
            "robust_quantile_scope": ROBUST_QUANTILE_SCOPE,
            "robust_liquid_gas_config": self.robust_config.to_dict(),
            "robust_baseline_by_grid_pump": robust_baseline,
            "last_batch_dates_by_grid_pump": {
                key: sorted(value) for key, value in dates.items()
            },
            "last_batch_drift_by_grid_pump": {},
            "pending_shift_by_grid_pump": {},
            "structure_report": report,
        }
        return snapshot, report

    def update(
        self,
        snapshot: ConditionSnapshot,
        previous_snapshot: ConditionSnapshot,
        rows: Iterable[Mapping[str, Any]],
        config: Any,
    ) -> Tuple[ConditionSnapshot, Dict[str, Any]]:
        self._validate_seed_against_config(config)
        self._preserve_previous_regions(snapshot, previous_snapshot)

        previous_state = dict(
            (previous_snapshot.metadata or {}).get("condition_region_v2") or {}
        )
        previous_config = RobustHistogramConfig.from_mapping(
            previous_state.get("robust_liquid_gas_config")
        )
        if previous_state and previous_config.to_dict() != self.robust_config.to_dict():
            raise ValueError(
                "robust liquid/gas histogram geometry or drift thresholds changed; "
                "start a new condition baseline generation instead of incremental update"
            )

        baseline = deepcopy(
            previous_state.get("robust_baseline_by_grid_pump") or {}
        )
        previous_pending = deepcopy(
            previous_state.get("pending_shift_by_grid_pump") or {}
        )
        batch_histograms, dates = self._batch_histograms(rows, config)

        drift_by_group: Dict[str, Any] = {}
        pending: Dict[str, Any] = {}

        for key, batch_histogram in batch_histograms.items():
            base_histogram = baseline.get(key)
            if base_histogram is None:
                baseline[key] = batch_histogram
                drift_by_group[key] = {
                    "status": "NEW_BASELINE_STRATUM",
                    "direction": "UNKNOWN",
                    "independent_days": len(dates.get(key, set())),
                    "quantile_scope": ROBUST_QUANTILE_SCOPE,
                    "batch": summarize_histogram(batch_histogram, self.robust_config),
                }
                continue

            drift = classify_distribution_shift(
                base_histogram,
                batch_histogram,
                self.robust_config,
                independent_days=len(dates.get(key, set())),
            )
            drift["quantile_scope"] = ROBUST_QUANTILE_SCOPE
            drift_by_group[key] = drift
            status = drift["status"]

            if status == "STABLE":
                baseline[key] = merge_histograms(
                    base_histogram,
                    batch_histogram,
                    self.robust_config,
                )
                continue

            if status in {"WATCH", "SUSPECTED_DRIFT", "STRONG_SHIFT"}:
                old = previous_pending.get(key) or {}
                same_direction = (
                    old.get("direction") == drift.get("direction")
                    and drift.get("direction") in {"UP", "DOWN"}
                )
                consecutive = int(old.get("consecutive_versions", 0)) + 1 if same_direction else 1
                pending[key] = {
                    "status": status,
                    "direction": drift.get("direction"),
                    "consecutive_versions": consecutive,
                    "first_seen_version": old.get(
                        "first_seen_version",
                        snapshot.snapshot_version,
                    ),
                    "last_seen_version": snapshot.snapshot_version,
                    "requires_physical_review": (
                        consecutive >= self.robust_config.confirmation_versions
                    ),
                    "latest_drift": drift,
                    "baseline_absorption": "HELD",
                }

        report = self._structure_report(
            snapshot=snapshot,
            drift_by_group=drift_by_group,
            mode="KEEP_WITH_DRIFT_WATCH",
        )
        snapshot.metadata = dict(snapshot.metadata or {})
        snapshot.metadata.pop("auto_merge_state", None)
        snapshot.metadata["condition_region_v2"] = {
            "schema_version": REGION_STRUCTURE_SCHEMA_VERSION,
            "seed_version": previous_state.get(
                "seed_version",
                self.seed.get("seed_version", "unknown"),
            ),
            "region_mode": "SEEDED_KEEP",
            "robust_quantile_scope": ROBUST_QUANTILE_SCOPE,
            "robust_liquid_gas_config": self.robust_config.to_dict(),
            "robust_baseline_by_grid_pump": baseline,
            "last_batch_dates_by_grid_pump": {
                key: sorted(value) for key, value in dates.items()
            },
            "last_batch_drift_by_grid_pump": drift_by_group,
            "pending_shift_by_grid_pump": pending,
            "structure_report": report,
        }
        return snapshot, report

    def _validate_seed_against_config(self, config: Any) -> None:
        if not getattr(config, "single_axis_mode", False):
            raise ValueError("steel v001 region seed requires the current single-axis condition model")
        axis = self.seed.get("axis") or {}
        expected_column = str(axis.get("column", "")).strip()
        if expected_column != config.axis_1.column:
            raise ValueError(
                f"seed axis mismatch: seed={expected_column!r}, config={config.axis_1.column!r}"
            )
        minimum = float(axis.get("minimum"))
        maximum = float(axis.get("maximum"))
        step = float(axis.get("base_step"))
        if (
            abs(minimum - config.axis_1.minimum) > 1.0e-9
            or abs(maximum - config.axis_1.maximum) > 1.0e-9
            or abs(step - config.axis_1.step) > 1.0e-9
        ):
            raise ValueError("seed grid geometry does not match published condition grid")

    def _publish_seed_regions(
        self,
        snapshot: ConditionSnapshot,
        config: Any,
    ) -> None:
        policy_regions: Dict[str, PolicyRegion] = {}
        assigned = set()
        for item in self.seed["regions"]:
            label = str(item["condition_label"])
            region_name = str(item.get("region_name", label))
            lower = float(item["minimum"])
            upper = float(item["maximum"])
            region_id = f"R_SEED_{label}"
            members = []
            for grid_id, cell in snapshot.grid_catalog.items():
                cell_lower, cell_upper = map(float, cell.axis_1_range)
                if cell_lower >= lower - 1.0e-9 and cell_upper <= upper + 1.0e-9:
                    members.append(grid_id)
            if not members:
                raise ValueError(f"seed region {label} has no base-grid members")
            overlap = assigned.intersection(members)
            if overlap:
                raise ValueError(f"seed region overlap for {label}: {sorted(overlap)}")
            assigned.update(members)
            members.sort(key=lambda gid: snapshot.grid_catalog[gid].axis_1_level)
            region_type = str(item.get("region_type", "CORE"))
            support_level = str(item.get("support_level", "MEDIUM"))
            policy_regions[region_id] = PolicyRegion(
                region_id=region_id,
                member_grid_ids=members,
                status=f"SEEDED_{region_type}",
                condition_label=label,
                evidence={
                    "source": "DATA_DRIVEN_SEED",
                    "seed_version": self.seed.get("seed_version", "unknown"),
                    "region_name": region_name,
                    "axis_column": config.axis_1.column,
                    "axis_range": [lower, upper],
                    "region_type": region_type,
                    "support_level": support_level,
                    "local_correction_eligible": bool(
                        item.get("local_correction_eligible", False)
                    ),
                },
            )
            for grid_id in members:
                snapshot.grid_catalog[grid_id].policy_region_id = region_id

        all_grid_ids = set(snapshot.grid_catalog)
        if assigned != all_grid_ids:
            missing = sorted(all_grid_ids - assigned)
            raise ValueError(f"seed regions do not cover the full base grid: {missing}")
        snapshot.policy_regions = policy_regions

    def _preserve_previous_regions(
        self,
        snapshot: ConditionSnapshot,
        previous_snapshot: ConditionSnapshot,
    ) -> None:
        if set(snapshot.grid_catalog) != set(previous_snapshot.grid_catalog):
            raise ValueError("incremental region update cannot change base-grid geometry")
        snapshot.policy_regions = deepcopy(previous_snapshot.policy_regions)
        for grid_id, previous_cell in previous_snapshot.grid_catalog.items():
            snapshot.grid_catalog[grid_id].policy_region_id = previous_cell.policy_region_id

    def _batch_histograms(
        self,
        rows: Iterable[Mapping[str, Any]],
        config: Any,
    ) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, set]]:
        from system.model.map_control.condition_model.initial_condition_builder import (
            build_state_key,
            get_condition_axis_values,
        )

        histograms: Dict[str, Dict[str, Any]] = {}
        dates: Dict[str, set] = {}
        for row in rows:
            if row.get("condition_mapping_ok", True) is False:
                continue
            try:
                first, second = get_condition_axis_values(dict(row), config)
                grid_id, _, _ = locate_grid(first, second, config)
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            state_key = build_state_key(dict(row))
            pump_state = _pump_state_from_state_key(state_key)
            key = _group_key(grid_id, pump_state)
            histogram = histograms.setdefault(
                key,
                empty_histogram(self.robust_config),
            )
            add_value(
                histogram,
                row.get(LIQUID_GAS_RATIO_COLUMN),
                self.robust_config,
            )
            day = _row_date(row)
            if day:
                dates.setdefault(key, set()).add(day)
        return histograms, dates

    def _structure_report(
        self,
        *,
        snapshot: ConditionSnapshot,
        drift_by_group: Mapping[str, Any],
        mode: str,
    ) -> Dict[str, Any]:
        regions = []
        for region in sorted(
            snapshot.policy_regions.values(),
            key=lambda value: min(
                snapshot.grid_catalog[grid_id].axis_1_level
                for grid_id in value.member_grid_ids
            ),
        ):
            statuses = []
            for grid_id in region.member_grid_ids:
                prefix = f"{grid_id}::"
                statuses.extend(
                    item.get("status")
                    for key, item in drift_by_group.items()
                    if key.startswith(prefix)
                )
            regions.append({
                "region_id": region.region_id,
                "condition_label": region.condition_label,
                "region_name": region.evidence.get("region_name", region.condition_label),
                "member_grid_ids": list(region.member_grid_ids),
                "region_type": region.evidence.get("region_type"),
                "support_level": region.evidence.get("support_level"),
                "decision": "KEEP",
                "drift_statuses": sorted({item for item in statuses if item}),
                "merge_split_policy": "REPORT_ONLY",
            })
        return {
            "schema_version": REGION_STRUCTURE_SCHEMA_VERSION,
            "snapshot_version": snapshot.snapshot_version,
            "mode": mode,
            "automatic_boundary_change_enabled": False,
            "robust_quantile_scope": ROBUST_QUANTILE_SCOPE,
            "regions": regions,
            "notes": [
                "Base-grid resolution remains fixed.",
                "Incremental versions keep the previous published regions by default.",
                "Robust liquid/gas drift is evidence only; it cannot directly merge or split regions.",
                "Histogram P05/P50/P95 and trimmed mean use in-range values only; underflow/overflow remain separate data-quality evidence.",
                "Quasi-free process evidence and second-module dynamic evidence will be added before enabling boundary changes.",
            ],
        }

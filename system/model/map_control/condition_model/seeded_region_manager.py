# -*- coding: utf-8 -*-
"""Seeded condition-region publication and conservative context-shift lifecycle.

The 100 mg/Nm3 base grid remains the stable coordinate system. This module
publishes versioned operating regions on top of that grid and maintains robust
liquid/gas evidence by ``base_grid + circulation-pump state``.

Important separation of concerns:
- the existing InitialConditionBuilder / IncrementalConditionUpdater continue
  to own base-grid statistics;
- this module owns region publication and robust operating-context evidence;
- liquid/gas distribution change is NOT proof of process-dynamic drift;
- historical supply actions and Q->SO2 / Q->pH dynamics belong to module 2.
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
    ACTIVE_CONTEXT_SHIFT_STATUSES,
    INSUFFICIENT_EVIDENCE_STATUS,
    OPERATING_CONTEXT_EVIDENCE_TYPE,
    STABLE_STATUS,
    RobustHistogramConfig,
    add_value,
    classify_distribution_shift,
    empty_histogram,
    merge_histograms,
    summarize_histogram,
)


REGION_STRUCTURE_SCHEMA_VERSION = "1.1"
ROBUST_QUANTILE_SCOPE = "IN_RANGE_ONLY"
DEFAULT_SEED_PATH = Path(__file__).with_name("region_seed_steel_v001.json")
LEGACY_CONTEXT_STATUS_MAP = {
    "SUSPECTED_DRIFT": "SUSPECTED_CONTEXT_SHIFT",
    "STRONG_SHIFT": "STRONG_CONTEXT_SHIFT",
}


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


def _normalize_pending_status(value: Any) -> Any:
    text = str(value) if value is not None else value
    return LEGACY_CONTEXT_STATUS_MAP.get(text, text)


def _date_sets(value: Optional[Mapping[str, Any]]) -> Dict[str, set]:
    result: Dict[str, set] = {}
    for key, dates in (value or {}).items():
        result[str(key)] = {str(item) for item in (dates or []) if item}
    return result


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

    def _baseline_ready(self, histogram: Mapping[str, Any], days: set) -> bool:
        summary = summarize_histogram(histogram, self.robust_config)
        return (
            int(summary.get("in_range_count") or 0) >= self.robust_config.min_baseline_samples
            and len(days) >= self.robust_config.min_independent_days
        )

    def initialize(
        self,
        snapshot: ConditionSnapshot,
        rows: Iterable[Mapping[str, Any]],
        config: Any,
    ) -> Tuple[ConditionSnapshot, Dict[str, Any]]:
        self._validate_seed_against_config(config)
        self._publish_seed_regions(snapshot, config)
        histograms, dates = self._batch_histograms(rows, config)

        robust_baseline: Dict[str, Dict[str, Any]] = {}
        baseline_warmup: Dict[str, Dict[str, Any]] = {}
        baseline_warmup_dates: Dict[str, set] = {}
        for key, histogram in histograms.items():
            observed_days = set(dates.get(key, set()))
            if self._baseline_ready(histogram, observed_days):
                robust_baseline[key] = histogram
            else:
                baseline_warmup[key] = histogram
                baseline_warmup_dates[key] = observed_days

        report = self._structure_report(
            snapshot=snapshot,
            context_shift_by_group={},
            mode="INITIAL_SEED",
        )
        snapshot.metadata = dict(snapshot.metadata or {})
        snapshot.metadata.pop("auto_merge_state", None)
        snapshot.metadata["condition_region_v2"] = {
            "schema_version": REGION_STRUCTURE_SCHEMA_VERSION,
            "seed_version": self.seed.get("seed_version", "unknown"),
            "region_mode": "SEEDED_KEEP",
            "robust_quantile_scope": ROBUST_QUANTILE_SCOPE,
            "evidence_type": OPERATING_CONTEXT_EVIDENCE_TYPE,
            "structural_decision_authority": False,
            "robust_liquid_gas_config": self.robust_config.to_dict(),
            "robust_baseline_by_grid_pump": robust_baseline,
            "baseline_warmup_by_grid_pump": baseline_warmup,
            "baseline_warmup_dates_by_grid_pump": {
                key: sorted(value) for key, value in baseline_warmup_dates.items()
            },
            "last_batch_dates_by_grid_pump": {
                key: sorted(value) for key, value in dates.items()
            },
            "last_batch_context_shift_by_grid_pump": {},
            "last_batch_drift_by_grid_pump": {},
            "pending_context_shift_by_grid_pump": {},
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
                "robust liquid/gas histogram geometry or context-shift thresholds changed; "
                "start a new condition baseline generation instead of incremental update"
            )

        baseline = deepcopy(
            previous_state.get("robust_baseline_by_grid_pump") or {}
        )
        warmup = deepcopy(
            previous_state.get("baseline_warmup_by_grid_pump") or {}
        )
        warmup_dates = _date_sets(
            previous_state.get("baseline_warmup_dates_by_grid_pump")
        )
        previous_batch_dates = _date_sets(
            previous_state.get("last_batch_dates_by_grid_pump")
        )

        previous_pending = deepcopy(
            previous_state.get("pending_context_shift_by_grid_pump")
            or previous_state.get("pending_shift_by_grid_pump")
            or {}
        )
        for item in previous_pending.values():
            if isinstance(item, dict):
                item["status"] = _normalize_pending_status(item.get("status"))
                latest = item.get("latest_shift") or item.get("latest_drift")
                if isinstance(latest, dict):
                    latest["status"] = _normalize_pending_status(latest.get("status"))
                    item["latest_shift"] = latest
                    item.pop("latest_drift", None)

        # Backward migration for v001/v002 snapshots created before baseline
        # warmup existed: immature reference strata are moved out of baseline so
        # later batches can accumulate toward a valid reference instead of being
        # permanently stuck at INSUFFICIENT_EVIDENCE.
        for key in list(baseline):
            history_days = set(previous_batch_dates.get(key, set()))
            if self._baseline_ready(baseline[key], history_days):
                continue
            warmup[key] = merge_histograms(
                warmup.get(key),
                baseline.pop(key),
                self.robust_config,
            )
            warmup_dates.setdefault(key, set()).update(history_days)

        batch_histograms, dates = self._batch_histograms(rows, config)
        context_shift_by_group: Dict[str, Any] = {}

        # Pending evidence survives a batch with no observations. Absence of
        # evidence is not evidence of stability.
        pending: Dict[str, Any] = deepcopy(previous_pending)
        for key, item in pending.items():
            item["continuity_state"] = "PAUSED_NO_OBSERVATION"
            item["last_checked_version"] = snapshot.snapshot_version
            item["baseline_absorption"] = "HELD"

        for key, batch_histogram in batch_histograms.items():
            base_histogram = baseline.get(key)
            observed_days = set(dates.get(key, set()))

            if base_histogram is None:
                combined = merge_histograms(
                    warmup.get(key),
                    batch_histogram,
                    self.robust_config,
                )
                combined_days = set(warmup_dates.get(key, set()))
                combined_days.update(observed_days)
                summary = summarize_histogram(combined, self.robust_config)

                if self._baseline_ready(combined, combined_days):
                    baseline[key] = combined
                    warmup.pop(key, None)
                    warmup_dates.pop(key, None)
                    pending.pop(key, None)
                    context_shift_by_group[key] = {
                        "status": "BASELINE_INITIALIZED",
                        "evidence_type": OPERATING_CONTEXT_EVIDENCE_TYPE,
                        "structural_evidence": False,
                        "direction": "UNKNOWN",
                        "independent_days": len(combined_days),
                        "quantile_scope": ROBUST_QUANTILE_SCOPE,
                        "baseline_absorption": "INITIALIZED_FROM_WARMUP",
                        "baseline": summary,
                        "batch": summarize_histogram(batch_histogram, self.robust_config),
                    }
                else:
                    warmup[key] = combined
                    warmup_dates[key] = combined_days
                    context_shift_by_group[key] = {
                        "status": "BASELINE_WARMUP",
                        "evidence_type": OPERATING_CONTEXT_EVIDENCE_TYPE,
                        "structural_evidence": False,
                        "direction": "UNKNOWN",
                        "independent_days": len(combined_days),
                        "quantile_scope": ROBUST_QUANTILE_SCOPE,
                        "baseline_absorption": "WARMUP_ONLY",
                        "warmup": summary,
                        "required_in_range_samples": self.robust_config.min_baseline_samples,
                        "required_independent_days": self.robust_config.min_independent_days,
                    }
                continue

            shift = classify_distribution_shift(
                base_histogram,
                batch_histogram,
                self.robust_config,
                independent_days=len(observed_days),
            )
            shift["quantile_scope"] = ROBUST_QUANTILE_SCOPE
            context_shift_by_group[key] = shift
            status = shift["status"]

            if status == STABLE_STATUS:
                baseline[key] = merge_histograms(
                    base_histogram,
                    batch_histogram,
                    self.robust_config,
                )
                shift["baseline_absorption"] = "ABSORBED"
                shift["pending_action"] = "CLEARED_IF_PRESENT"
                pending.pop(key, None)
                continue

            if status == INSUFFICIENT_EVIDENCE_STATUS:
                shift["baseline_absorption"] = "HELD_INSUFFICIENT_EVIDENCE"
                old = pending.get(key)
                if old:
                    old["continuity_state"] = "PAUSED_INSUFFICIENT_EVIDENCE"
                    old["last_checked_version"] = snapshot.snapshot_version
                    old["latest_observation"] = shift
                    old["baseline_absorption"] = "HELD"
                continue

            if status in ACTIVE_CONTEXT_SHIFT_STATUSES:
                old = pending.get(key) or {}
                old_direction = old.get("direction")
                same_direction = (
                    old_direction == shift.get("direction")
                    and shift.get("direction") in {"UP", "DOWN"}
                )
                previous_count = int(
                    old.get(
                        "consecutive_supported_versions",
                        old.get("consecutive_versions", 0),
                    )
                    or 0
                )
                supported_count = previous_count + 1 if same_direction else 1
                first_seen = (
                    old.get("first_seen_version", snapshot.snapshot_version)
                    if same_direction
                    else snapshot.snapshot_version
                )
                confirmed = supported_count >= self.robust_config.confirmation_versions
                pending[key] = {
                    "status": status,
                    "evidence_type": OPERATING_CONTEXT_EVIDENCE_TYPE,
                    "structural_evidence": False,
                    "direction": shift.get("direction"),
                    "consecutive_supported_versions": supported_count,
                    # Kept as a compatibility alias. It counts supported shift
                    # observations, not every wall-clock snapshot version.
                    "consecutive_versions": supported_count,
                    "first_seen_version": first_seen,
                    "last_seen_version": snapshot.snapshot_version,
                    "last_checked_version": snapshot.snapshot_version,
                    "continuity_state": "ACTIVE_SUPPORTED_SHIFT",
                    "confirmed_context_shift": confirmed,
                    "requires_context_review": confirmed,
                    "requires_physical_review": confirmed,
                    "latest_shift": shift,
                    "baseline_absorption": "HELD",
                }

        report = self._structure_report(
            snapshot=snapshot,
            context_shift_by_group=context_shift_by_group,
            mode="KEEP_WITH_CONTEXT_SHIFT_WATCH",
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
            "evidence_type": OPERATING_CONTEXT_EVIDENCE_TYPE,
            "structural_decision_authority": False,
            "robust_liquid_gas_config": self.robust_config.to_dict(),
            "robust_baseline_by_grid_pump": baseline,
            "baseline_warmup_by_grid_pump": warmup,
            "baseline_warmup_dates_by_grid_pump": {
                key: sorted(value) for key, value in warmup_dates.items()
            },
            "last_batch_dates_by_grid_pump": {
                key: sorted(value) for key, value in dates.items()
            },
            "last_batch_context_shift_by_grid_pump": context_shift_by_group,
            # Compatibility alias for tooling written against the first V2
            # replay. Its semantic type is explicitly OPERATING_CONTEXT only.
            "last_batch_drift_by_grid_pump": context_shift_by_group,
            "pending_context_shift_by_grid_pump": pending,
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
        context_shift_by_group: Mapping[str, Any],
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
                    for key, item in context_shift_by_group.items()
                    if key.startswith(prefix)
                )
            unique_statuses = sorted({item for item in statuses if item})
            regions.append({
                "region_id": region.region_id,
                "condition_label": region.condition_label,
                "region_name": region.evidence.get("region_name", region.condition_label),
                "member_grid_ids": list(region.member_grid_ids),
                "region_type": region.evidence.get("region_type"),
                "support_level": region.evidence.get("support_level"),
                "decision": "KEEP",
                "context_shift_statuses": unique_statuses,
                "merge_split_policy": "REPORT_ONLY",
            })
        return {
            "schema_version": REGION_STRUCTURE_SCHEMA_VERSION,
            "snapshot_version": snapshot.snapshot_version,
            "mode": mode,
            "automatic_boundary_change_enabled": False,
            "robust_quantile_scope": ROBUST_QUANTILE_SCOPE,
            "evidence_type": OPERATING_CONTEXT_EVIDENCE_TYPE,
            "structural_decision_authority": False,
            "regions": regions,
            "notes": [
                "Base-grid resolution remains fixed.",
                "Incremental versions keep the previous published regions by default.",
                "Robust liquid/gas shift is operating-context evidence only; it cannot directly merge or split regions.",
                "A liquid/gas context shift is not a confirmed process-dynamic drift because liquid/gas ratio is derived from pump topology and gas flow.",
                "Histogram P05/P50/P95 and trimmed mean use in-range values only; underflow/overflow remain separate data-quality evidence.",
                "INSUFFICIENT_EVIDENCE or no observation pauses existing pending context evidence without increasing or clearing its supported-version count.",
                "Only a supported STABLE observation clears pending context-shift evidence; same-direction supported shifts increase persistence.",
                "New grid+pump strata remain BASELINE_WARMUP until minimum sample and independent-day support is reached.",
                "Quasi-free process evidence and second-module dynamic evidence are required before any process-drift or boundary-change decision.",
            ],
        }

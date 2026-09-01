# -*- coding: utf-8 -*-
"""Production hardening layer for seeded condition-region V2.

The already replay-validated :class:`SeededRegionManager` remains the owner of
base-grid/region publication and conservative context-shift classification.
This module deliberately layers production lifecycle concerns on top instead
of rewriting the validated 1.1 algorithm:

- explicitly disable legacy AutoMerge semantics in published grid_config;
- persist candidate context distributions without absorbing them automatically;
- support explicit human/offline resolution of confirmed context shifts;
- version accepted context references with a per-stratum generation counter;
- enrich the compact structure report with pending/confirmed lifecycle state.

A liquid/gas context shift is operating-context evidence only. None of the
resolution decisions below may merge/split operating regions or claim process-
dynamic drift. Process-dynamic evidence remains a separate module-2 concern.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

from system.model.map_control.condition_model.condition_schema import ConditionSnapshot
from system.model.map_control.condition_model.robust_statistics import (
    ACTIVE_CONTEXT_SHIFT_STATUSES,
    merge_histograms,
    summarize_histogram,
)
from system.model.map_control.condition_model.seeded_region_manager import (
    OPERATING_CONTEXT_EVIDENCE_TYPE,
    ROBUST_QUANTILE_SCOPE,
    SeededRegionManager,
)


HARDENED_REGION_SCHEMA_VERSION = "1.2"
KEEP_REFERENCE = "KEEP_REFERENCE"
ACCEPT_NEW_CONTEXT_BASELINE = "ACCEPT_NEW_CONTEXT_BASELINE"
SENSOR_OR_DATA_ISSUE = "SENSOR_OR_DATA_ISSUE"
CONTEXT_RESOLUTION_DECISIONS = {
    KEEP_REFERENCE,
    ACCEPT_NEW_CONTEXT_BASELINE,
    SENSOR_OR_DATA_ISSUE,
}


def _as_resolution_spec(value: Any) -> Dict[str, Any]:
    if isinstance(value, str):
        return {"decision": value}
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError("context resolution must be a decision string or mapping")


def _pending_state(previous_state: Mapping[str, Any]) -> Dict[str, Any]:
    return deepcopy(
        previous_state.get("pending_context_shift_by_grid_pump")
        or previous_state.get("pending_shift_by_grid_pump")
        or {}
    )


class HardenedSeededRegionManager(SeededRegionManager):
    """SeededRegionManager plus production review/reference lifecycle."""

    def initialize(
        self,
        snapshot: ConditionSnapshot,
        rows: Iterable[Mapping[str, Any]],
        config: Any,
    ) -> Tuple[ConditionSnapshot, Dict[str, Any]]:
        rows_list = list(rows)
        snapshot, _ = super().initialize(snapshot, rows_list, config)
        self._mark_legacy_auto_merge_bypassed(snapshot)

        state = snapshot.metadata["condition_region_v2"]
        state["schema_version"] = HARDENED_REGION_SCHEMA_VERSION
        state["legacy_auto_merge_bypassed"] = True
        state["context_reference_generation_by_grid_pump"] = {
            key: 1 for key in state.get("robust_baseline_by_grid_pump", {})
        }
        state["context_resolution_history"] = []
        state["latest_context_resolution_by_grid_pump"] = {}
        state["context_resolution_policy"] = self._resolution_policy()

        report = self._hardened_structure_report(
            snapshot,
            state,
            mode="INITIAL_SEED",
        )
        state["structure_report"] = report
        return snapshot, report

    def update(
        self,
        snapshot: ConditionSnapshot,
        previous_snapshot: ConditionSnapshot,
        rows: Iterable[Mapping[str, Any]],
        config: Any,
        *,
        context_resolutions: Optional[Mapping[str, Any]] = None,
    ) -> Tuple[ConditionSnapshot, Dict[str, Any]]:
        rows_list = list(rows)
        previous_state = dict(
            (previous_snapshot.metadata or {}).get("condition_region_v2") or {}
        )
        previous_pending = _pending_state(previous_state)

        snapshot, _ = super().update(
            snapshot,
            previous_snapshot,
            rows_list,
            config,
        )
        self._mark_legacy_auto_merge_bypassed(snapshot)

        state = snapshot.metadata["condition_region_v2"]
        state["schema_version"] = HARDENED_REGION_SCHEMA_VERSION
        state["legacy_auto_merge_bypassed"] = True
        state["context_resolution_policy"] = self._resolution_policy()

        generations = {
            str(key): max(1, int(value or 1))
            for key, value in (
                previous_state.get("context_reference_generation_by_grid_pump")
                or {}
            ).items()
        }
        for key in state.get("robust_baseline_by_grid_pump", {}):
            generations.setdefault(key, 1)
        state["context_reference_generation_by_grid_pump"] = generations

        history = deepcopy(previous_state.get("context_resolution_history") or [])
        latest_resolution = deepcopy(
            previous_state.get("latest_context_resolution_by_grid_pump") or {}
        )
        state["context_resolution_history"] = history
        state["latest_context_resolution_by_grid_pump"] = latest_resolution

        self._persist_pending_candidates(
            state=state,
            previous_pending=previous_pending,
            rows=rows_list,
            config=config,
        )
        self._apply_context_resolutions(
            snapshot=snapshot,
            state=state,
            context_resolutions=context_resolutions or {},
        )

        report = self._hardened_structure_report(
            snapshot,
            state,
            mode="KEEP_WITH_CONTEXT_SHIFT_WATCH",
        )
        state["structure_report"] = report
        return snapshot, report

    @staticmethod
    def _mark_legacy_auto_merge_bypassed(snapshot: ConditionSnapshot) -> None:
        grid_config = deepcopy(snapshot.grid_config or {})
        merge_config = dict(grid_config.get("merge") or {})
        # Keep the historical merge fields for read compatibility, but make the
        # executable semantics unambiguous for seeded V2 snapshots.
        merge_config["enabled"] = False
        merge_config["mode"] = "disabled"
        grid_config["merge"] = merge_config
        snapshot.grid_config = grid_config
        snapshot.metadata = dict(snapshot.metadata or {})
        snapshot.metadata.pop("auto_merge_state", None)

    def _persist_pending_candidates(
        self,
        *,
        state: Dict[str, Any],
        previous_pending: Mapping[str, Any],
        rows: Iterable[Mapping[str, Any]],
        config: Any,
    ) -> None:
        pending = state.get("pending_context_shift_by_grid_pump") or {}
        current = state.get("last_batch_context_shift_by_grid_pump") or {}
        batch_histograms, batch_dates = self._batch_histograms(rows, config)

        for key, item in pending.items():
            observation = current.get(key) or {}
            status = observation.get("status")
            if status not in ACTIVE_CONTEXT_SHIFT_STATUSES:
                # PAUSED/NO_OBSERVATION retains any already persisted candidate.
                old = previous_pending.get(key) or {}
                for name in (
                    "candidate_histogram",
                    "candidate_dates",
                    "candidate_summary",
                    "candidate_supported_versions",
                ):
                    if name not in item and name in old:
                        item[name] = deepcopy(old[name])
                continue

            batch_histogram = batch_histograms.get(key)
            if not batch_histogram:
                continue
            old = previous_pending.get(key) or {}
            same_direction = (
                old.get("direction") == item.get("direction")
                and item.get("direction") in {"UP", "DOWN"}
            )
            if same_direction and old.get("candidate_histogram"):
                candidate = merge_histograms(
                    old.get("candidate_histogram"),
                    batch_histogram,
                    self.robust_config,
                )
                candidate_dates = {
                    str(value) for value in old.get("candidate_dates", []) if value
                }
                candidate_supported_versions = int(
                    old.get("candidate_supported_versions", 0) or 0
                ) + 1
            else:
                candidate = deepcopy(batch_histogram)
                candidate_dates = set()
                candidate_supported_versions = 1
            candidate_dates.update(batch_dates.get(key, set()))

            item["candidate_histogram"] = candidate
            item["candidate_dates"] = sorted(candidate_dates)
            item["candidate_summary"] = summarize_histogram(
                candidate,
                self.robust_config,
            )
            item["candidate_supported_versions"] = candidate_supported_versions
            item["candidate_reference_eligible"] = self._baseline_ready(
                candidate,
                candidate_dates,
            )

        state["pending_context_shift_by_grid_pump"] = pending
        state["pending_shift_by_grid_pump"] = pending

    def _apply_context_resolutions(
        self,
        *,
        snapshot: ConditionSnapshot,
        state: Dict[str, Any],
        context_resolutions: Mapping[str, Any],
    ) -> None:
        if not context_resolutions:
            return

        pending = state.get("pending_context_shift_by_grid_pump") or {}
        baseline = state.get("robust_baseline_by_grid_pump") or {}
        baseline_dates = {
            str(key): {str(value) for value in (values or []) if value}
            for key, values in (
                state.get("robust_baseline_dates_by_grid_pump") or {}
            ).items()
        }
        generations = state.get("context_reference_generation_by_grid_pump") or {}
        history = state.get("context_resolution_history") or []
        latest = state.get("latest_context_resolution_by_grid_pump") or {}

        for key, raw_spec in context_resolutions.items():
            key = str(key)
            if key not in pending:
                raise ValueError(
                    f"context resolution references non-pending stratum: {key}"
                )
            spec = _as_resolution_spec(raw_spec)
            decision = str(spec.get("decision", "")).strip().upper()
            if decision not in CONTEXT_RESOLUTION_DECISIONS:
                raise ValueError(
                    f"unsupported context resolution decision for {key}: {decision!r}"
                )

            item = pending[key]
            generation_before = int(generations.get(key, 1) or 1)
            generation_after = generation_before

            if decision == ACCEPT_NEW_CONTEXT_BASELINE:
                if not bool(item.get("confirmed_context_shift")):
                    raise ValueError(
                        f"cannot accept unconfirmed context shift as reference: {key}"
                    )
                candidate = item.get("candidate_histogram")
                candidate_days = {
                    str(value) for value in item.get("candidate_dates", []) if value
                }
                if not candidate or not self._baseline_ready(candidate, candidate_days):
                    raise ValueError(
                        "confirmed shift has no review-ready candidate histogram; "
                        f"collect a supported hardened-V2 batch before accepting: {key}"
                    )
                baseline[key] = deepcopy(candidate)
                baseline_dates[key] = candidate_days
                generation_after = generation_before + 1
                generations[key] = generation_after

            event = {
                "group_key": key,
                "decision": decision,
                "snapshot_version": snapshot.snapshot_version,
                "reviewer": spec.get("reviewer"),
                "reason": spec.get("reason"),
                "reviewed_at": spec.get("reviewed_at"),
                "previous_status": item.get("status"),
                "previous_direction": item.get("direction"),
                "previous_confirmed_context_shift": bool(
                    item.get("confirmed_context_shift")
                ),
                "reference_generation_before": generation_before,
                "reference_generation_after": generation_after,
                "structural_decision_authority": False,
            }
            history.append(event)
            latest[key] = event
            pending.pop(key, None)

        state["robust_baseline_by_grid_pump"] = baseline
        state["robust_baseline_dates_by_grid_pump"] = {
            key: sorted(value) for key, value in baseline_dates.items()
        }
        state["context_reference_generation_by_grid_pump"] = generations
        state["context_resolution_history"] = history
        state["latest_context_resolution_by_grid_pump"] = latest
        state["pending_context_shift_by_grid_pump"] = pending
        state["pending_shift_by_grid_pump"] = pending

    def _hardened_structure_report(
        self,
        snapshot: ConditionSnapshot,
        state: Mapping[str, Any],
        *,
        mode: str,
    ) -> Dict[str, Any]:
        base_report = dict(state.get("structure_report") or {})
        if not base_report or base_report.get("snapshot_version") != snapshot.snapshot_version:
            base_report = super()._structure_report(
                snapshot=snapshot,
                context_shift_by_group=state.get(
                    "last_batch_context_shift_by_grid_pump"
                ) or {},
                mode=mode,
            )
        report = deepcopy(base_report)
        report["schema_version"] = HARDENED_REGION_SCHEMA_VERSION
        report["mode"] = mode
        report["legacy_auto_merge_bypassed"] = True
        report["context_resolution_policy"] = self._resolution_policy()

        pending = state.get("pending_context_shift_by_grid_pump") or {}
        confirmed_total = 0
        requires_review = False

        for region_item in report.get("regions", []):
            members = set(region_item.get("member_grid_ids") or [])
            matching = []
            for key, item in pending.items():
                grid_id = str(key).split("::", 1)[0]
                if grid_id in members:
                    matching.append(item)
            statuses = sorted({item.get("status") for item in matching if item.get("status")})
            continuity = sorted({
                item.get("continuity_state")
                for item in matching
                if item.get("continuity_state")
            })
            confirmed = sum(
                1 for item in matching if bool(item.get("confirmed_context_shift"))
            )
            active = sum(
                1
                for item in matching
                if item.get("continuity_state") == "ACTIVE_SUPPORTED_SHIFT"
            )
            paused = len(matching) - active
            region_requires_review = any(
                bool(item.get("requires_context_review")) for item in matching
            )

            region_item["pending_context_shift_statuses"] = statuses
            region_item["pending_context_shift_count"] = len(matching)
            region_item["active_pending_context_shift_count"] = active
            region_item["paused_pending_context_shift_count"] = paused
            region_item["confirmed_context_shift_count"] = confirmed
            region_item["requires_context_review"] = region_requires_review
            region_item["pending_continuity_states"] = continuity

            confirmed_total += confirmed
            requires_review = requires_review or region_requires_review

        report["pending_context_shift_count"] = len(pending)
        report["confirmed_context_shift_count"] = confirmed_total
        report["manual_context_review_required"] = requires_review
        report["context_resolution_history_count"] = len(
            state.get("context_resolution_history") or []
        )
        notes = list(report.get("notes") or [])
        for note in (
            "Confirmed context shift requires explicit review; it never auto-replaces the reference baseline.",
            "KEEP_REFERENCE and SENSOR_OR_DATA_ISSUE retain the current reference; ACCEPT_NEW_CONTEXT_BASELINE creates a new per-stratum reference generation from held candidate evidence.",
            "Legacy AutoMerge is bypassed for hardened seeded V2 snapshots; operating-region boundaries remain report-only unless a separate offline structure process publishes a new version.",
        ):
            if note not in notes:
                notes.append(note)
        report["notes"] = notes
        return report

    @staticmethod
    def _resolution_policy() -> Dict[str, Any]:
        return {
            "automatic_reference_replacement": False,
            "allowed_decisions": [
                KEEP_REFERENCE,
                ACCEPT_NEW_CONTEXT_BASELINE,
                SENSOR_OR_DATA_ISSUE,
            ],
            "accept_new_context_requires_confirmed_shift": True,
            "accept_new_context_requires_candidate_baseline_support": True,
            "structural_decision_authority": False,
        }

# -*- coding: utf-8 -*-
"""Automatic policy-region publication and lifecycle management.

Flow:
    pair evidence -> deterministic rectangular grouping
    -> AUTO_PROVISIONAL_MERGE
    -> repeated verification with genuinely new samples
    -> AUTO_CONFIRMED_MERGE

Every snapshot is rebuilt from current cumulative base-cell evidence.  A
previously merged region is therefore removed automatically when current
evidence no longer supports it.  Manual label-pair publication is not used.
"""

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from system.model.slurry_control.condition_model.condition_config import (
    ConditionModelConfig,
)
from system.model.slurry_control.condition_model.condition_merger import (
    ConditionMerger,
)
from system.model.slurry_control.condition_model.condition_schema import (
    ConditionSnapshot,
    GridCell,
    PolicyRegion,
)


AUTO_MERGE_ALGORITHM_VERSION = "auto-merge-v2"
SNAPSHOT_SCHEMA_VERSION = "5.1"


def _base_id(cell: GridCell, config: ConditionModelConfig) -> str:
    return str(
        (cell.load_level - 1) * config.inlet_so2.cell_count
        + cell.inlet_so2_level
    )


def _grid_sort_key(
    grid_id: str,
    catalog: Dict[str, GridCell],
) -> Tuple[int, int]:
    cell = catalog[grid_id]
    return cell.load_level, cell.inlet_so2_level


def _signature(
    grid_ids: Iterable[str],
    catalog: Dict[str, GridCell],
) -> str:
    return "|".join(
        sorted(
            set(grid_ids),
            key=lambda item: _grid_sort_key(item, catalog),
        )
    )


def _candidate_id(
    snapshot_version: str,
    first: str,
    second: str,
    decision: Dict[str, Any],
) -> str:
    payload = json.dumps(
        {
            "snapshot_version": snapshot_version,
            "first": first,
            "second": second,
            "decision": decision,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    return f"MC-{snapshot_version}-{digest}"


class AutoMergeManager:
    """Build and publish automatic rectangular policy regions."""

    def __init__(self, config: ConditionModelConfig):
        self.config = config
        self.merger = ConditionMerger(config)

    def apply(
        self,
        snapshot: ConditionSnapshot,
        previous_snapshot: Optional[ConditionSnapshot] = None,
    ) -> Tuple[ConditionSnapshot, Dict[str, Any]]:
        now = datetime.now(timezone.utc).isoformat()
        catalog = snapshot.grid_catalog
        previous_state = self._previous_state(previous_snapshot)

        report: Dict[str, Any] = {
            "algorithm_version": AUTO_MERGE_ALGORITHM_VERSION,
            "snapshot_version": snapshot.snapshot_version,
            "previous_snapshot_version": snapshot.previous_snapshot_version,
            "generated_at": now,
            "merge_enabled": self.config.merge.enabled,
            "merge_mode": self.config.merge.mode,
            "config": self.config.merge.__dict__.copy(),
            "candidates": [],
            "region_attempts": [],
            "published_regions": [],
            "region_lifecycle_events": [],
            "split_events": [],
        }

        if (
            not self.config.merge.enabled
            or self.config.merge.mode == "disabled"
        ):
            groups = [{grid_id} for grid_id in catalog]
        else:
            groups = self._build_groups(snapshot, report)

        current_region_state: Dict[str, Dict[str, Any]] = {}
        policy_regions: Dict[str, PolicyRegion] = {}

        sorted_groups = sorted(
            groups,
            key=lambda group: min(
                int(_base_id(catalog[grid_id], self.config))
                for grid_id in group
            ),
        )

        for member_ids in sorted_groups:
            ordered = sorted(
                member_ids,
                key=lambda item: _grid_sort_key(item, catalog),
            )
            label = min(
                (
                    _base_id(catalog[grid_id], self.config)
                    for grid_id in ordered
                ),
                key=int,
            )
            region_id = f"R_{int(label):04d}"

            if len(ordered) == 1:
                status = "INDEPENDENT"
                verification_passes = 0
                verification_progress = "NOT_APPLICABLE"
                evidence: Dict[str, Any] = {
                    "source": "AUTO_MERGE_MANAGER",
                    "algorithm_version": AUTO_MERGE_ALGORITHM_VERSION,
                }
            else:
                signature = _signature(ordered, catalog)
                previous_record = previous_state.get(signature, {})
                current_sample_counts = {
                    grid_id: int(catalog[grid_id].sample_count)
                    for grid_id in ordered
                }
                current_evidence_counts = {
                    grid_id: self._effective_evidence_count(catalog[grid_id])
                    for grid_id in ordered
                }
                (
                    verification_passes,
                    verification_progress,
                    new_evidence_counts,
                    counted_evidence_counts,
                ) = self._advance_verification(
                    ordered,
                    current_evidence_counts,
                    previous_record,
                )

                min_sample_count = min(current_sample_counts.values())
                min_evidence_count = min(current_evidence_counts.values())
                confirmed = (
                    verification_passes
                    >= self.config.merge.min_consecutive_pass_snapshots
                    and min_evidence_count
                    >= self.config.merge.min_auto_confirm_samples
                )
                status = (
                    "AUTO_CONFIRMED_MERGE"
                    if confirmed
                    else "AUTO_PROVISIONAL_MERGE"
                )
                region_decision = self.merger.evaluate_region_members(
                    [catalog[grid_id] for grid_id in ordered]
                )
                evidence = {
                    "source": "AUTO_MERGE_MANAGER",
                    "algorithm_version": AUTO_MERGE_ALGORITHM_VERSION,
                    "signature": signature,
                    "verification_passes": verification_passes,
                    # Kept for compatibility with the first automatic version.
                    "consecutive_passes": verification_passes,
                    "required_verification_passes": (
                        self.config.merge.min_consecutive_pass_snapshots
                    ),
                    "minimum_member_sample_count": min_sample_count,
                    "minimum_member_evidence_count": min_evidence_count,
                    "required_confirm_evidence_samples": (
                        self.config.merge.min_auto_confirm_samples
                    ),
                    "minimum_new_samples_per_member_for_confirmation": (
                        self.config.merge
                        .min_new_samples_per_member_for_confirmation
                    ),
                    "member_sample_counts": current_sample_counts,
                    "member_evidence_counts": current_evidence_counts,
                    "new_evidence_samples_since_last_counted_pass": (
                        new_evidence_counts
                    ),
                    "verification_progress": verification_progress,
                    "region_decision": region_decision,
                }
                current_region_state[signature] = {
                    "member_grid_ids": ordered,
                    "condition_label": label,
                    "status": status,
                    "verification_passes": verification_passes,
                    "consecutive_passes": verification_passes,
                    "verification_progress": verification_progress,
                    "member_sample_counts": current_sample_counts,
                    "member_evidence_counts": current_evidence_counts,
                    "counted_member_evidence_counts": counted_evidence_counts,
                    "first_seen_snapshot_version": previous_record.get(
                        "first_seen_snapshot_version",
                        snapshot.snapshot_version,
                    ),
                    "last_seen_snapshot_version": snapshot.snapshot_version,
                    "last_counted_snapshot_version": (
                        snapshot.snapshot_version
                        if verification_progress
                        in {"INITIAL_PASS", "COUNTED_NEW_EVIDENCE"}
                        else previous_record.get(
                            "last_counted_snapshot_version"
                        )
                    ),
                }

            policy_regions[region_id] = PolicyRegion(
                region_id=region_id,
                member_grid_ids=ordered,
                status=status,
                evidence=evidence,
                condition_label=label,
            )
            for grid_id in ordered:
                catalog[grid_id].policy_region_id = region_id

            report["published_regions"].append(
                {
                    "region_id": region_id,
                    "condition_label": label,
                    "member_grid_ids": ordered,
                    "status": status,
                    "verification_passes": verification_passes,
                    "verification_progress": verification_progress,
                }
            )

        snapshot.policy_regions = policy_regions
        lifecycle_events, split_events = self._lifecycle_events(
            previous_state,
            policy_regions,
            snapshot.snapshot_version,
        )
        report["region_lifecycle_events"] = lifecycle_events
        report["split_events"] = split_events

        summary = self._summary(report)
        snapshot.metadata = dict(snapshot.metadata or {})
        snapshot.metadata["snapshot_schema_version"] = SNAPSHOT_SCHEMA_VERSION
        snapshot.metadata["auto_merge_state"] = {
            "algorithm_version": AUTO_MERGE_ALGORITHM_VERSION,
            "last_evaluated_snapshot_version": snapshot.snapshot_version,
            "regions": current_region_state,
            "region_lifecycle_events": lifecycle_events,
            "split_events": split_events,
            "summary": summary,
        }
        report["summary"] = summary
        return snapshot, report

    def _advance_verification(
        self,
        ordered_grid_ids: List[str],
        current_evidence_counts: Dict[str, int],
        previous_record: Dict[str, Any],
    ) -> Tuple[int, str, Dict[str, int], Dict[str, int]]:
        """Count a verification pass only when every member gained evidence.

        Small batches accumulate because the comparison is made against the
        last *counted* pass, not merely the immediately previous snapshot.
        Unrelated incremental snapshots therefore cannot confirm a merge, but
        they also do not erase already accumulated verification progress.
        """

        if not previous_record:
            return (
                1,
                "INITIAL_PASS",
                dict(current_evidence_counts),
                dict(current_evidence_counts),
            )

        previous_passes = int(
            previous_record.get(
                "verification_passes",
                previous_record.get("consecutive_passes", 0),
            )
        )
        counted = {
            str(grid_id): int(count)
            for grid_id, count in (
                previous_record.get("counted_member_evidence_counts")
                or previous_record.get("member_evidence_counts")
                or previous_record.get("counted_member_sample_counts")
                or previous_record.get("member_sample_counts")
                or {}
            ).items()
        }
        # Legacy auto-merge records may not contain per-member counts.  Treat
        # the current snapshot as a fresh initial verification pass.
        if any(grid_id not in counted for grid_id in ordered_grid_ids):
            return (
                max(previous_passes, 1),
                "MIGRATED_WITHOUT_COUNTED_SAMPLE_BASELINE",
                {grid_id: 0 for grid_id in ordered_grid_ids},
                dict(current_evidence_counts),
            )

        new_counts = {
            grid_id: max(
                0,
                current_evidence_counts[grid_id] - counted.get(grid_id, 0),
            )
            for grid_id in ordered_grid_ids
        }
        required = (
            self.config.merge
            .min_new_samples_per_member_for_confirmation
        )
        if all(count >= required for count in new_counts.values()):
            return (
                previous_passes + 1,
                "COUNTED_NEW_EVIDENCE",
                new_counts,
                dict(current_evidence_counts),
            )
        return (
            previous_passes,
            "HELD_INSUFFICIENT_NEW_SAMPLES",
            new_counts,
            counted,
        )

    @staticmethod
    def _effective_evidence_count(cell: GridCell) -> int:
        """Count rows that can support both liquid-gas and risk evidence."""

        numeric = (cell.accumulators or {}).get("numeric", {})
        risk = (cell.accumulators or {}).get("risk", {})
        try:
            liquid_gas_count = max(
                0,
                int((numeric.get("liquid_gas") or {}).get("count", 0)),
            )
        except (TypeError, ValueError):
            liquid_gas_count = 0
        try:
            risk_valid_count = max(
                0,
                int(risk.get("valid_count", 0)),
            )
        except (TypeError, ValueError):
            risk_valid_count = 0
        return min(int(cell.sample_count), liquid_gas_count, risk_valid_count)

    def _build_groups(
        self,
        snapshot: ConditionSnapshot,
        report: Dict[str, Any],
    ) -> List[Set[str]]:
        catalog = snapshot.grid_catalog
        parent = {grid_id: grid_id for grid_id in catalog}
        members: Dict[str, Set[str]] = {
            grid_id: {grid_id}
            for grid_id in catalog
        }

        def find(grid_id: str) -> str:
            while parent[grid_id] != grid_id:
                parent[grid_id] = parent[parent[grid_id]]
                grid_id = parent[grid_id]
            return grid_id

        def union(
            first_root: str,
            second_root: str,
            merged_members: Set[str],
        ) -> str:
            roots = sorted(
                (first_root, second_root),
                key=lambda item: _grid_sort_key(item, catalog),
            )
            kept, removed = roots[0], roots[1]
            parent[removed] = kept
            members[kept] = set(merged_members)
            members.pop(removed, None)
            return kept

        allowed_records: List[Dict[str, Any]] = []
        for first, second in self.merger.generate_candidates(
            catalog,
            snapshot.grid_adjacency,
        ):
            decision = self.merger.evaluate_pair(
                catalog[first],
                catalog[second],
            )
            record = {
                "candidate_id": _candidate_id(
                    snapshot.snapshot_version,
                    first,
                    second,
                    decision,
                ),
                "first_grid_id": first,
                "second_grid_id": second,
                **decision,
            }
            report["candidates"].append(record)
            if decision["allowed"]:
                allowed_records.append(record)

        allowed_records.sort(
            key=lambda item: (
                self.merger.candidate_sort_key(item),
                _grid_sort_key(item["first_grid_id"], catalog),
                _grid_sort_key(item["second_grid_id"], catalog),
            )
        )

        for record in allowed_records:
            first_root = find(record["first_grid_id"])
            second_root = find(record["second_grid_id"])
            if first_root == second_root:
                continue

            proposed = members[first_root] | members[second_root]
            region_decision = self.merger.evaluate_region_members(
                [catalog[grid_id] for grid_id in proposed]
            )
            attempt = {
                "candidate_id": record["candidate_id"],
                "proposed_member_grid_ids": sorted(
                    proposed,
                    key=lambda item: _grid_sort_key(item, catalog),
                ),
                **region_decision,
            }
            report["region_attempts"].append(attempt)
            if region_decision["allowed"]:
                union(first_root, second_root, proposed)

        roots = {find(grid_id) for grid_id in catalog}
        return [set(members[root]) for root in roots]

    @staticmethod
    def _previous_state(
        previous_snapshot: Optional[ConditionSnapshot],
    ) -> Dict[str, Dict[str, Any]]:
        if previous_snapshot is None:
            return {}
        metadata = previous_snapshot.metadata or {}
        state = metadata.get("auto_merge_state") or {}
        regions = state.get("regions") or {}
        return {
            str(signature): dict(record or {})
            for signature, record in regions.items()
        }

    @staticmethod
    def _lifecycle_events(
        previous_state: Dict[str, Dict[str, Any]],
        policy_regions: Dict[str, PolicyRegion],
        snapshot_version: str,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Classify signature changes without calling an expansion a split."""

        current_sets = [
            {
                "region_id": region.region_id,
                "status": region.status,
                "members": set(region.member_grid_ids),
            }
            for region in policy_regions.values()
        ]
        current_merged_signatures = {
            "|".join(sorted(item["members"]))
            for item in current_sets
            if len(item["members"]) > 1
        }
        events: List[Dict[str, Any]] = []
        split_events: List[Dict[str, Any]] = []

        for signature, old_record in previous_state.items():
            old_members = set(old_record.get("member_grid_ids", []))
            canonical_signature = "|".join(sorted(old_members))
            if canonical_signature in current_merged_signatures:
                continue

            overlapping = [
                item
                for item in current_sets
                if item["members"] & old_members
            ]
            containing = [
                item
                for item in overlapping
                if old_members < item["members"]
            ]
            if containing:
                event_type = "REGION_EXPANDED"
                reason = "PREVIOUS_REGION_IS_CONTAINED_IN_A_LARGER_CURRENT_REGION"
            elif len(overlapping) == 1 and overlapping[0]["members"] == old_members:
                # Defensive fallback; exact merged signatures are handled above.
                continue
            elif len(overlapping) == 1 and overlapping[0]["members"] < old_members:
                event_type = "REGION_CONTRACTED"
                reason = "PART_OF_THE_PREVIOUS_REGION_NO_LONGER_PASSES"
            elif len(overlapping) > 1:
                event_type = "REGION_SPLIT"
                reason = "PREVIOUS_MEMBERS_ARE_NOW_PUBLISHED_IN_MULTIPLE_REGIONS"
            else:
                event_type = "REGION_REMOVED"
                reason = "CURRENT_EVIDENCE_NO_LONGER_PUBLISHES_THE_REGION"

            event = {
                "event_type": event_type,
                "previous_signature": signature,
                "previous_member_grid_ids": sorted(old_members),
                "previous_status": old_record.get("status"),
                "current_overlapping_regions": [
                    {
                        "region_id": item["region_id"],
                        "status": item["status"],
                        "member_grid_ids": sorted(item["members"]),
                    }
                    for item in overlapping
                ],
                "reason": reason,
                "detected_in_snapshot_version": snapshot_version,
            }
            events.append(event)
            if event_type != "REGION_EXPANDED":
                split_events.append(event)

        return events, split_events

    @staticmethod
    def _summary(report: Dict[str, Any]) -> Dict[str, int]:
        candidates = report.get("candidates", [])
        regions = report.get("published_regions", [])
        return {
            "candidate_count": len(candidates),
            "eligible_pair_count": sum(
                1 for item in candidates if item.get("allowed")
            ),
            "region_attempt_count": len(
                report.get("region_attempts", [])
            ),
            "published_merged_region_count": sum(
                1
                for item in regions
                if len(item.get("member_grid_ids", [])) > 1
            ),
            "provisional_region_count": sum(
                1
                for item in regions
                if item.get("status") == "AUTO_PROVISIONAL_MERGE"
            ),
            "confirmed_region_count": sum(
                1
                for item in regions
                if item.get("status") == "AUTO_CONFIRMED_MERGE"
            ),
            "confirmation_held_region_count": sum(
                1
                for item in regions
                if item.get("verification_progress")
                == "HELD_INSUFFICIENT_NEW_SAMPLES"
            ),
            "lifecycle_event_count": len(
                report.get("region_lifecycle_events", [])
            ),
            "split_event_count": len(report.get("split_events", [])),
        }


def write_auto_merge_report(
    report: Dict[str, Any],
    path: Optional[str],
) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=target.name,
        suffix=".staging",
        dir=str(target.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(
                report,
                stream,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
        os.replace(temporary, target)
    except Exception:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise

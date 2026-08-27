# -*- coding: utf-8 -*-
"""Bind Scheme-2 SO2 and pH bootstrap evidence to the same physical cohort.

The independent bootstrap trainers remain responsible for channel-specific
seed/replay calculations.  This module adds the missing cross-channel contract:
manual LOCAL_GAIN bootstrap is valid only when both channels consume exactly the
same cohort-approved event IDs under the same condition snapshot/context.

No runtime activation, online learning, residual control or DCS permission can
be granted by this module.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Tuple

from .bootstrap_trainer import (
    MFACBootstrapEvidence,
    MFACReplayConfig,
    build_bootstrap_evidence,
)
from .mfac_schema import ActionResponseEvent
from .ph_bootstrap_trainer import (
    PHBootstrapEvidence,
    PHReplayConfig,
    build_ph_bootstrap_evidence,
)


DUAL_RESPONSE_BOOTSTRAP_VERSION = (
    "SCHEME2_DUAL_RESPONSE_BOOTSTRAP_V1_SAME_COHORT_BINDING"
)


@dataclass(frozen=True)
class DualResponseBootstrapRejection:
    condition_snapshot_version: str
    mfac_context_id: str
    event_ids: Tuple[str, ...]
    reasons: Tuple[str, ...]
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["event_ids"] = list(self.event_ids)
        value["reasons"] = list(self.reasons)
        return value


@dataclass(frozen=True)
class DualResponseBootstrapBundle:
    condition_snapshot_version: str
    mfac_context_id: str
    event_ids: Tuple[str, ...]
    valid_event_count: int
    independent_days: int
    so2: MFACBootstrapEvidence
    ph: PHBootstrapEvidence
    status: str = "DUAL_BOOTSTRAP_EVIDENCE_READY_FOR_PROFILE_REVIEW"
    activation_status: str = "NOT_ACTIVATABLE"
    learning_permission: bool = False
    residual_control_permission: bool = False
    dcs_write_enabled: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = DUAL_RESPONSE_BOOTSTRAP_VERSION

    def __post_init__(self) -> None:
        if self.activation_status != "NOT_ACTIVATABLE":
            raise ValueError("dual bootstrap bundle must remain NOT_ACTIVATABLE")
        if self.learning_permission or self.residual_control_permission or self.dcs_write_enabled:
            raise ValueError("dual bootstrap bundle cannot grant runtime permissions")
        if self.so2.condition_snapshot_version != self.condition_snapshot_version:
            raise ValueError("SO2 bootstrap snapshot mismatch")
        if self.ph.condition_snapshot_version != self.condition_snapshot_version:
            raise ValueError("pH bootstrap snapshot mismatch")
        if self.so2.mfac_context_id != self.mfac_context_id:
            raise ValueError("SO2 bootstrap context mismatch")
        if self.ph.mfac_context_id != self.mfac_context_id:
            raise ValueError("pH bootstrap context mismatch")
        if tuple(self.so2.event_ids) != self.event_ids:
            raise ValueError("SO2 bootstrap event IDs are not the bound cohort")
        if tuple(self.ph.event_ids) != self.event_ids:
            raise ValueError("pH bootstrap event IDs are not the bound cohort")
        if self.so2.valid_event_count != self.valid_event_count:
            raise ValueError("SO2 bootstrap event count mismatch")
        if self.ph.valid_event_count != self.valid_event_count:
            raise ValueError("pH bootstrap event count mismatch")
        if self.so2.independent_days != self.independent_days:
            raise ValueError("SO2 bootstrap independent-day mismatch")
        if self.ph.independent_days != self.independent_days:
            raise ValueError("pH bootstrap independent-day mismatch")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "condition_snapshot_version": self.condition_snapshot_version,
            "mfac_context_id": self.mfac_context_id,
            "event_ids": list(self.event_ids),
            "valid_event_count": self.valid_event_count,
            "independent_days": self.independent_days,
            "so2": self.so2.to_dict(),
            "ph": self.ph.to_dict(),
            "status": self.status,
            "activation_status": self.activation_status,
            "learning_permission": self.learning_permission,
            "residual_control_permission": self.residual_control_permission,
            "dcs_write_enabled": self.dcs_write_enabled,
            "metadata": dict(self.metadata),
            "semantics_version": self.semantics_version,
        }


@dataclass(frozen=True)
class DualResponseBootstrapBuildResult:
    bundles: Tuple[DualResponseBootstrapBundle, ...] = ()
    rejections: Tuple[DualResponseBootstrapRejection, ...] = ()
    semantics_version: str = DUAL_RESPONSE_BOOTSTRAP_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bundles": [item.to_dict() for item in self.bundles],
            "rejections": [item.to_dict() for item in self.rejections],
            "semantics_version": self.semantics_version,
        }


def _manual_bootstrap_approved(event: ActionResponseEvent) -> bool:
    metadata = dict(event.metadata or {})
    return (
        str(event.action_source or "")
        == "MANUAL_LOCAL_STEP_IDENTIFICATION_REVIEWED"
        and metadata.get("evidence_role") == "LOCAL_GAIN"
        and metadata.get("manual_evidence_review_approved") is True
        and metadata.get("cohort_bootstrap_review_approved") is True
        and metadata.get("offline_bootstrap_evidence_allowed") is True
        and metadata.get("automatic_online_adaptation_allowed") is False
        and bool(event.learning_eligible)
    )


def build_dual_response_bootstrap_evidence(
    events: Iterable[ActionResponseEvent],
    *,
    so2_replay_config: MFACReplayConfig,
    ph_replay_config: PHReplayConfig,
) -> DualResponseBootstrapBuildResult:
    """Build same-cohort SO2+pH bootstrap bundles for manual LOCAL_GAIN data."""

    groups: Dict[tuple[str, str], List[ActionResponseEvent]] = {}
    immediate_rejections: List[DualResponseBootstrapRejection] = []

    for event in events:
        key = (
            str(event.condition_snapshot_version or ""),
            str(event.mfac_context_id or ""),
        )
        if not _manual_bootstrap_approved(event):
            immediate_rejections.append(
                DualResponseBootstrapRejection(
                    condition_snapshot_version=key[0],
                    mfac_context_id=key[1],
                    event_ids=(str(event.event_id or ""),),
                    reasons=("MANUAL_COHORT_BOOTSTRAP_APPROVAL_MISSING",),
                    metadata={
                        "action_source": str(event.action_source or ""),
                        "learning_eligible": bool(event.learning_eligible),
                    },
                )
            )
            continue
        groups.setdefault(key, []).append(event)

    bundles: List[DualResponseBootstrapBundle] = []
    rejections: List[DualResponseBootstrapRejection] = list(immediate_rejections)

    for (snapshot, context), group in sorted(groups.items()):
        ordered_input_ids = tuple(
            event.event_id
            for event in sorted(group, key=lambda item: str(item.action_start_time or ""))
        )
        reasons: List[str] = []
        if not snapshot:
            reasons.append("CONDITION_SNAPSHOT_REQUIRED")
        if not context:
            reasons.append("MFAC_CONTEXT_REQUIRED")
        if len(set(ordered_input_ids)) != len(ordered_input_ids):
            reasons.append("DUPLICATE_EVENT_ID")

        so2_evidence = build_bootstrap_evidence(group, so2_replay_config)
        ph_evidence = build_ph_bootstrap_evidence(group, ph_replay_config)

        if len(so2_evidence) != 1:
            reasons.append("SO2_BOOTSTRAP_CONTEXT_NOT_COMPLETE")
        if len(ph_evidence) != 1:
            reasons.append("PH_BOOTSTRAP_CONTEXT_NOT_COMPLETE")

        so2 = so2_evidence[0] if len(so2_evidence) == 1 else None
        ph = ph_evidence[0] if len(ph_evidence) == 1 else None

        if so2 is not None:
            if tuple(so2.event_ids) != ordered_input_ids:
                reasons.append("SO2_EVENT_SET_DIFFERS_FROM_INPUT_COHORT")
            if so2.rejected_replay_event_ids:
                reasons.append("SO2_REPLAY_REJECTED_EVENT")
            if so2.phi_seed >= 0.0 or so2.phi_replayed >= 0.0:
                reasons.append("SO2_BOOTSTRAP_DIRECTION_INVALID")
        if ph is not None:
            if tuple(ph.event_ids) != ordered_input_ids:
                reasons.append("PH_EVENT_SET_DIFFERS_FROM_INPUT_COHORT")
            if ph.rejected_replay_event_ids:
                reasons.append("PH_REPLAY_REJECTED_EVENT")
            if ph.phi_seed <= 0.0 or ph.phi_replayed <= 0.0:
                reasons.append("PH_BOOTSTRAP_DIRECTION_INVALID")

        if so2 is not None and ph is not None:
            if tuple(so2.event_ids) != tuple(ph.event_ids):
                reasons.append("SO2_PH_EVENT_SET_MISMATCH")
            if so2.valid_event_count != ph.valid_event_count:
                reasons.append("SO2_PH_EVENT_COUNT_MISMATCH")
            if so2.independent_days != ph.independent_days:
                reasons.append("SO2_PH_INDEPENDENT_DAYS_MISMATCH")

        reasons = list(dict.fromkeys(reasons))
        if reasons:
            rejections.append(
                DualResponseBootstrapRejection(
                    condition_snapshot_version=snapshot,
                    mfac_context_id=context,
                    event_ids=ordered_input_ids,
                    reasons=tuple(reasons),
                    metadata={
                        "same_physical_cohort_required": True,
                        "so2_event_ids": list(so2.event_ids) if so2 is not None else [],
                        "ph_event_ids": list(ph.event_ids) if ph is not None else [],
                    },
                )
            )
            continue

        bundles.append(
            DualResponseBootstrapBundle(
                condition_snapshot_version=snapshot,
                mfac_context_id=context,
                event_ids=ordered_input_ids,
                valid_event_count=so2.valid_event_count,
                independent_days=so2.independent_days,
                so2=so2,
                ph=ph,
                status="DUAL_BOOTSTRAP_EVIDENCE_READY_FOR_PROFILE_REVIEW",
                activation_status="NOT_ACTIVATABLE",
                learning_permission=False,
                residual_control_permission=False,
                dcs_write_enabled=False,
                metadata={
                    "same_physical_cohort": True,
                    "same_event_ids_for_so2_and_ph": True,
                    "manual_cohort_review_required_upstream": True,
                    "bootstrap_profile_review_required_downstream": True,
                    "normal_runtime_activation_allowed": False,
                },
            )
        )

    return DualResponseBootstrapBuildResult(
        bundles=tuple(bundles),
        rejections=tuple(rejections),
    )


__all__ = [
    "DUAL_RESPONSE_BOOTSTRAP_VERSION",
    "DualResponseBootstrapRejection",
    "DualResponseBootstrapBundle",
    "DualResponseBootstrapBuildResult",
    "build_dual_response_bootstrap_evidence",
]

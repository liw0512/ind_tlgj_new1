# -*- coding: utf-8 -*-
"""Content-addressed provenance manifest for Scheme-2 MFAC review evidence.

The bundle does not create a new configuration authority.  It binds existing
manual LOCAL_GAIN evidence, raw traces, observed timing, confidence evidence and
the dual calibration profile by canonical SHA256 digests so a later loader can
fail closed when individually valid artifacts are mixed from different cohorts
or silently replaced.

The bundle is audit-only and cannot enable learning, residual control or DCS.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import re
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

from .channel_calibration_review import ObservedResponseTimingEvidence
from .channel_confidence_evidence import ChannelConfidenceEvidence
from .dual_response_calibration_profile import (
    CHANNEL_CALIBRATED,
    DualResponseCalibrationProfile,
)
from .local_step_raw_trace import LocalStepRawTraceBundle
from .mfac_schema import ActionResponseEvent


EVIDENCE_PROVENANCE_BUNDLE_VERSION = (
    "SCHEME2_EVIDENCE_PROVENANCE_BUNDLE_V1_CONTENT_ADDRESSED_REVIEW_ONLY"
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_ARTIFACT_TYPES = {
    "LOCAL_GAIN_COHORT_APPROVED_EVENTS",
    "LOCAL_STEP_RAW_TRACE",
    "OBSERVED_RESPONSE_TIMING",
    "CHANNEL_CONFIDENCE_EVIDENCE",
    "DUAL_RESPONSE_CALIBRATION_PROFILE",
}


def _canonical_json(value: Any) -> bytes:
    try:
        text = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("evidence payload is not canonical-JSON serializable") from exc
    return text.encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _event_payload(events: Iterable[ActionResponseEvent]) -> Tuple[Dict[str, Any], ...]:
    return tuple(
        event.to_dict()
        for event in sorted(events, key=lambda item: str(item.event_id or ""))
    )


def _require_text(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("%s is required" % name)
    return text


def _channel_name(value: Any) -> str:
    channel = str(value or "").upper()
    if channel not in {"SO2", "PH"}:
        raise ValueError("channel must be SO2 or PH")
    return channel


@dataclass(frozen=True)
class EvidenceArtifactRef:
    artifact_type: str
    artifact_id: str
    sha256: str
    condition_snapshot_version: str
    mfac_context_id: str
    event_ids: Tuple[str, ...] = ()
    channel: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.artifact_type not in _ALLOWED_ARTIFACT_TYPES:
            raise ValueError("unsupported evidence artifact type")
        _require_text(self.artifact_id, "artifact_id")
        if not _SHA256_RE.fullmatch(str(self.sha256 or "")):
            raise ValueError("artifact sha256 must be 64 lowercase hex characters")
        _require_text(self.condition_snapshot_version, "condition_snapshot_version")
        _require_text(self.mfac_context_id, "mfac_context_id")
        ids = tuple(str(value or "").strip() for value in self.event_ids)
        if any(not value for value in ids) or len(set(ids)) != len(ids):
            raise ValueError("artifact event IDs must be unique non-empty values")
        if self.channel:
            _channel_name(self.channel)
        if self.artifact_type in {
            "OBSERVED_RESPONSE_TIMING",
            "CHANNEL_CONFIDENCE_EVIDENCE",
        } and not self.channel:
            raise ValueError("channel evidence artifact requires channel")

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["event_ids"] = list(self.event_ids)
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvidenceArtifactRef":
        payload = dict(value or {})
        payload["event_ids"] = tuple(payload.get("event_ids") or ())
        return cls(**payload)


@dataclass(frozen=True)
class DualResponseEvidenceProvenanceBundle:
    bundle_id: str
    condition_snapshot_version: str
    mfac_context_id: str
    cohort_review_id: str
    cohort_event_ids: Tuple[str, ...]
    cohort_approved_events_ref: EvidenceArtifactRef
    raw_trace_refs: Tuple[EvidenceArtifactRef, ...]
    timing_refs: Tuple[EvidenceArtifactRef, ...]
    confidence_refs: Tuple[EvidenceArtifactRef, ...]
    calibration_profile_ref: EvidenceArtifactRef
    status: str
    blockers: Tuple[str, ...] = ()
    activation_status: str = "NOT_ACTIVATABLE"
    learning_enabled: bool = False
    residual_control_enabled: bool = False
    dcs_write_enabled: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = EVIDENCE_PROVENANCE_BUNDLE_VERSION

    def __post_init__(self) -> None:
        _require_text(self.bundle_id, "bundle_id")
        _require_text(self.condition_snapshot_version, "condition_snapshot_version")
        _require_text(self.mfac_context_id, "mfac_context_id")
        _require_text(self.cohort_review_id, "cohort_review_id")
        if self.semantics_version != EVIDENCE_PROVENANCE_BUNDLE_VERSION:
            raise ValueError("unsupported evidence provenance bundle semantics")
        if self.status not in {"COMPLETE_REVIEW_CHAIN", "INCOMPLETE_REVIEW_CHAIN"}:
            raise ValueError("unsupported provenance bundle status")
        if self.activation_status != "NOT_ACTIVATABLE":
            raise ValueError("evidence provenance bundle must remain NOT_ACTIVATABLE")
        if self.learning_enabled or self.residual_control_enabled or self.dcs_write_enabled:
            raise ValueError("evidence provenance bundle cannot enable runtime permissions")

        ids = tuple(str(value or "").strip() for value in self.cohort_event_ids)
        if not ids or any(not value for value in ids) or len(set(ids)) != len(ids):
            raise ValueError("bundle requires unique non-empty cohort event IDs")
        if set(self.cohort_approved_events_ref.event_ids) != set(ids):
            raise ValueError("cohort artifact event IDs do not match bundle cohort")

        all_refs = (
            (self.cohort_approved_events_ref,)
            + tuple(self.raw_trace_refs)
            + tuple(self.timing_refs)
            + tuple(self.confidence_refs)
            + (self.calibration_profile_ref,)
        )
        ref_keys = set()
        for ref in all_refs:
            if ref.condition_snapshot_version != self.condition_snapshot_version:
                raise ValueError("artifact condition snapshot does not match bundle")
            if ref.mfac_context_id != self.mfac_context_id:
                raise ValueError("artifact MFAC context does not match bundle")
            key = (ref.artifact_type, ref.artifact_id, ref.channel)
            if key in ref_keys:
                raise ValueError("duplicate artifact reference in provenance bundle")
            ref_keys.add(key)

        timing_channels = [ref.channel.upper() for ref in self.timing_refs]
        confidence_channels = [ref.channel.upper() for ref in self.confidence_refs]
        if len(set(timing_channels)) != len(timing_channels):
            raise ValueError("duplicate timing channel reference")
        if len(set(confidence_channels)) != len(confidence_channels):
            raise ValueError("duplicate confidence channel reference")
        if self.status == "COMPLETE_REVIEW_CHAIN" and self.blockers:
            raise ValueError("complete provenance bundle cannot contain blockers")

    @property
    def is_complete_review_chain(self) -> bool:
        return self.status == "COMPLETE_REVIEW_CHAIN"

    def to_runtime_config(self) -> Dict[str, Any]:
        raise ValueError("evidence provenance bundle is audit-only and cannot activate runtime")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bundle_id": self.bundle_id,
            "condition_snapshot_version": self.condition_snapshot_version,
            "mfac_context_id": self.mfac_context_id,
            "cohort_review_id": self.cohort_review_id,
            "cohort_event_ids": list(self.cohort_event_ids),
            "cohort_approved_events_ref": self.cohort_approved_events_ref.to_dict(),
            "raw_trace_refs": [ref.to_dict() for ref in self.raw_trace_refs],
            "timing_refs": [ref.to_dict() for ref in self.timing_refs],
            "confidence_refs": [ref.to_dict() for ref in self.confidence_refs],
            "calibration_profile_ref": self.calibration_profile_ref.to_dict(),
            "status": self.status,
            "blockers": list(self.blockers),
            "activation_status": self.activation_status,
            "learning_enabled": self.learning_enabled,
            "residual_control_enabled": self.residual_control_enabled,
            "dcs_write_enabled": self.dcs_write_enabled,
            "metadata": dict(self.metadata),
            "semantics_version": self.semantics_version,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DualResponseEvidenceProvenanceBundle":
        payload = dict(value or {})
        payload["cohort_event_ids"] = tuple(payload.get("cohort_event_ids") or ())
        payload["cohort_approved_events_ref"] = EvidenceArtifactRef.from_dict(
            payload.get("cohort_approved_events_ref") or {}
        )
        payload["raw_trace_refs"] = tuple(
            EvidenceArtifactRef.from_dict(item)
            for item in payload.get("raw_trace_refs") or ()
        )
        payload["timing_refs"] = tuple(
            EvidenceArtifactRef.from_dict(item)
            for item in payload.get("timing_refs") or ()
        )
        payload["confidence_refs"] = tuple(
            EvidenceArtifactRef.from_dict(item)
            for item in payload.get("confidence_refs") or ()
        )
        payload["calibration_profile_ref"] = EvidenceArtifactRef.from_dict(
            payload.get("calibration_profile_ref") or {}
        )
        payload["blockers"] = tuple(payload.get("blockers") or ())
        return cls(**payload)


@dataclass(frozen=True)
class EvidenceProvenanceVerification:
    status: str
    valid: bool
    reasons: Tuple[str, ...] = ()
    activation_status: str = "NOT_ACTIVATABLE"
    learning_enabled: bool = False
    residual_control_enabled: bool = False
    dcs_write_enabled: bool = False
    semantics_version: str = EVIDENCE_PROVENANCE_BUNDLE_VERSION

    def __post_init__(self) -> None:
        if self.status not in {"VERIFIED", "MISMATCH"}:
            raise ValueError("unsupported provenance verification status")
        if self.activation_status != "NOT_ACTIVATABLE":
            raise ValueError("provenance verification must remain NOT_ACTIVATABLE")
        if self.learning_enabled or self.residual_control_enabled or self.dcs_write_enabled:
            raise ValueError("provenance verification cannot enable runtime permissions")


def _cohort_review_identity(events: Sequence[ActionResponseEvent]) -> Tuple[str, str, str]:
    review_ids = set()
    reviewers = set()
    times = set()
    for event in events:
        metadata = dict(event.metadata or {})
        if event.learning_eligible is not True:
            raise ValueError("provenance cohort event must be bootstrap learning-eligible")
        if metadata.get("cohort_bootstrap_review_approved") is not True:
            raise ValueError("provenance cohort event lacks human cohort approval")
        if metadata.get("offline_bootstrap_evidence_allowed") is not True:
            raise ValueError("provenance cohort event is not offline-bootstrap eligible")
        if metadata.get("automatic_online_adaptation_allowed") is not False:
            raise ValueError("provenance cohort event cannot allow automatic adaptation")
        review_ids.add(_require_text(metadata.get("cohort_review_id"), "cohort_review_id"))
        reviewers.add(_require_text(metadata.get("cohort_review_reviewer_id"), "cohort reviewer"))
        times.add(_require_text(metadata.get("cohort_review_time"), "cohort review time"))
    if len(review_ids) != 1 or len(reviewers) != 1 or len(times) != 1:
        raise ValueError("cohort-approved events must share one review ID/reviewer/time")
    return next(iter(review_ids)), next(iter(reviewers)), next(iter(times))


def build_evidence_provenance_bundle(
    *,
    bundle_id: str,
    cohort_approved_events: Iterable[ActionResponseEvent],
    raw_trace_bundles: Iterable[LocalStepRawTraceBundle],
    timing_evidence: Mapping[str, ObservedResponseTimingEvidence],
    confidence_evidence: Mapping[str, ChannelConfidenceEvidence],
    calibration_profile: DualResponseCalibrationProfile,
) -> DualResponseEvidenceProvenanceBundle:
    """Build a content-addressed review manifest from existing evidence objects."""

    events = list(cohort_approved_events)
    if not events:
        raise ValueError("provenance bundle requires cohort-approved events")
    event_ids = tuple(sorted(str(event.event_id or "") for event in events))
    if any(not value for value in event_ids) or len(set(event_ids)) != len(event_ids):
        raise ValueError("cohort-approved event IDs must be unique and non-empty")

    snapshots = {str(event.condition_snapshot_version or "") for event in events}
    contexts = {str(event.mfac_context_id or "") for event in events}
    if len(snapshots) != 1 or len(contexts) != 1:
        raise ValueError("cohort-approved events must use one condition/context")
    snapshot = _require_text(next(iter(snapshots)), "condition snapshot")
    context = _require_text(next(iter(contexts)), "MFAC context")
    cohort_review_id, cohort_reviewer, cohort_review_time = _cohort_review_identity(events)

    if calibration_profile.condition_snapshot_version != snapshot:
        raise ValueError("calibration profile condition snapshot mismatch")
    if calibration_profile.mfac_context_id != context:
        raise ValueError("calibration profile MFAC context mismatch")
    if not calibration_profile.so2.has_local_gain or not calibration_profile.ph.has_local_gain:
        raise ValueError("dual provenance bundle requires local gain for both channels")
    if set(calibration_profile.so2.evidence_event_ids) != set(event_ids):
        raise ValueError("SO2 calibration cohort differs from approved event cohort")
    if set(calibration_profile.ph.evidence_event_ids) != set(event_ids):
        raise ValueError("pH calibration cohort differs from approved event cohort")

    cohort_ref = EvidenceArtifactRef(
        artifact_type="LOCAL_GAIN_COHORT_APPROVED_EVENTS",
        artifact_id="COHORT-APPROVED-%s" % cohort_review_id,
        sha256=canonical_sha256(_event_payload(events)),
        condition_snapshot_version=snapshot,
        mfac_context_id=context,
        event_ids=event_ids,
        metadata={
            "cohort_review_id": cohort_review_id,
            "cohort_review_reviewer_id": cohort_reviewer,
            "cohort_review_time": cohort_review_time,
        },
    )

    raw_by_event: Dict[str, LocalStepRawTraceBundle] = {}
    raw_refs = []
    for raw in raw_trace_bundles:
        if raw.status != "TRACE_REVIEW_CANDIDATE":
            raise ValueError("only valid raw trace bundles can enter provenance bundle")
        if raw.condition_snapshot_version != snapshot or raw.mfac_context_id != context:
            raise ValueError("raw trace condition/context mismatch")
        if raw.event_id not in set(event_ids):
            raise ValueError("raw trace event is outside approved cohort")
        if raw.event_id in raw_by_event:
            raise ValueError("duplicate raw trace for cohort event")
        if raw.so2_trace is None or raw.ph_trace is None:
            raise ValueError("valid raw trace bundle must contain both channel traces")
        if raw.so2_trace.event_id != raw.event_id or raw.ph_trace.event_id != raw.event_id:
            raise ValueError("raw trace channel event binding mismatch")
        raw_by_event[raw.event_id] = raw
        raw_refs.append(
            EvidenceArtifactRef(
                artifact_type="LOCAL_STEP_RAW_TRACE",
                artifact_id="RAW-TRACE-%s" % raw.trial_id,
                sha256=canonical_sha256(raw.to_dict()),
                condition_snapshot_version=snapshot,
                mfac_context_id=context,
                event_ids=(raw.event_id,),
                metadata={"trial_id": raw.trial_id, "tracking_event_id": raw.tracking_event_id},
            )
        )

    timing_map = {_channel_name(key): value for key, value in dict(timing_evidence or {}).items()}
    confidence_map = {_channel_name(key): value for key, value in dict(confidence_evidence or {}).items()}
    timing_refs = []
    confidence_refs = []
    blockers = []

    for channel in ("SO2", "PH"):
        timing = timing_map.get(channel)
        confidence = confidence_map.get(channel)
        channel_profile = calibration_profile.so2 if channel == "SO2" else calibration_profile.ph

        if timing is None:
            blockers.append("%s_TIMING_EVIDENCE_MISSING" % channel)
        else:
            if timing.channel.upper() != channel:
                raise ValueError("timing evidence channel mismatch")
            if timing.condition_snapshot_version != snapshot or timing.mfac_context_id != context:
                raise ValueError("timing evidence condition/context mismatch")
            timing_ids = tuple(str(value) for value in timing.event_ids)
            if not set(timing_ids).issubset(set(event_ids)):
                raise ValueError("timing evidence lies outside approved cohort")
            if any(event_id not in raw_by_event for event_id in timing_ids):
                raise ValueError("timing evidence is missing bound raw trace artifacts")
            timing_metadata = dict(timing.metadata or {})
            if timing_metadata.get("timing_extraction_profile_reviewed") is not True:
                raise ValueError("timing evidence lacks reviewed extraction provenance")
            if timing_metadata.get("calibration_review_eligible") is not True:
                raise ValueError("timing evidence is not calibration-review eligible")
            timing_refs.append(
                EvidenceArtifactRef(
                    artifact_type="OBSERVED_RESPONSE_TIMING",
                    artifact_id=timing.evidence_id,
                    sha256=canonical_sha256(timing.to_dict()),
                    condition_snapshot_version=snapshot,
                    mfac_context_id=context,
                    event_ids=timing_ids,
                    channel=channel,
                    metadata={
                        "timing_extraction_profile_id": timing_metadata.get("timing_extraction_profile_id"),
                        "timing_extraction_reviewer_id": timing_metadata.get("timing_extraction_reviewer_id"),
                        "timing_extraction_review_time": timing_metadata.get("timing_extraction_review_time"),
                    },
                )
            )

        if confidence is None:
            blockers.append("%s_CONFIDENCE_EVIDENCE_MISSING" % channel)
        else:
            if confidence.channel.upper() != channel:
                raise ValueError("confidence evidence channel mismatch")
            if confidence.condition_snapshot_version != snapshot or confidence.mfac_context_id != context:
                raise ValueError("confidence evidence condition/context mismatch")
            if set(confidence.cohort_event_ids) != set(event_ids):
                raise ValueError("confidence evidence cohort mismatch")
            if confidence.cohort_review_id != cohort_review_id:
                raise ValueError("confidence evidence cohort review ID mismatch")
            if confidence.cohort_bootstrap_review_approved is not True:
                raise ValueError("confidence evidence lacks human cohort approval")
            if timing is None or confidence.timing_evidence_id != timing.evidence_id:
                raise ValueError("confidence evidence does not bind channel timing evidence")
            confidence_refs.append(
                EvidenceArtifactRef(
                    artifact_type="CHANNEL_CONFIDENCE_EVIDENCE",
                    artifact_id=confidence.evidence_id,
                    sha256=canonical_sha256(confidence.to_dict()),
                    condition_snapshot_version=snapshot,
                    mfac_context_id=context,
                    event_ids=tuple(confidence.cohort_event_ids),
                    channel=channel,
                    metadata={
                        "cohort_review_id": confidence.cohort_review_id,
                        "timing_evidence_id": confidence.timing_evidence_id,
                    },
                )
            )

        if channel_profile.status == CHANNEL_CALIBRATED:
            if timing is None or confidence is None:
                raise ValueError("CALIBRATED channel requires timing and confidence artifacts")
            metadata = dict(channel_profile.metadata or {})
            if metadata.get("timing_evidence_id") != timing.evidence_id:
                raise ValueError("calibrated channel/timing artifact mismatch")
            if metadata.get("confidence_evidence_id") != confidence.evidence_id:
                raise ValueError("calibrated channel/confidence artifact mismatch")
            if metadata.get("cohort_review_id") != cohort_review_id:
                raise ValueError("calibrated channel/cohort review mismatch")
        else:
            blockers.append("%s_CHANNEL_NOT_CALIBRATED" % channel)

    profile_ref = EvidenceArtifactRef(
        artifact_type="DUAL_RESPONSE_CALIBRATION_PROFILE",
        artifact_id=calibration_profile.profile_id,
        sha256=canonical_sha256(calibration_profile.to_dict()),
        condition_snapshot_version=snapshot,
        mfac_context_id=context,
        event_ids=event_ids,
        metadata={
            "so2_status": calibration_profile.so2.status,
            "ph_status": calibration_profile.ph.status,
            "activation_status": calibration_profile.activation_status,
        },
    )

    blockers = tuple(dict.fromkeys(blockers))
    status = "COMPLETE_REVIEW_CHAIN" if not blockers else "INCOMPLETE_REVIEW_CHAIN"
    return DualResponseEvidenceProvenanceBundle(
        bundle_id=_require_text(bundle_id, "bundle_id"),
        condition_snapshot_version=snapshot,
        mfac_context_id=context,
        cohort_review_id=cohort_review_id,
        cohort_event_ids=event_ids,
        cohort_approved_events_ref=cohort_ref,
        raw_trace_refs=tuple(sorted(raw_refs, key=lambda ref: ref.artifact_id)),
        timing_refs=tuple(sorted(timing_refs, key=lambda ref: ref.channel)),
        confidence_refs=tuple(sorted(confidence_refs, key=lambda ref: ref.channel)),
        calibration_profile_ref=profile_ref,
        status=status,
        blockers=blockers,
        activation_status="NOT_ACTIVATABLE",
        learning_enabled=False,
        residual_control_enabled=False,
        dcs_write_enabled=False,
        metadata={
            "content_addressed": True,
            "canonical_hash": "SHA256(CANONICAL_JSON)",
            "review_chain_complete_is_not_activation_permission": True,
            "separate_activation_review_required": True,
        },
    )


def verify_evidence_provenance_bundle(
    bundle: DualResponseEvidenceProvenanceBundle,
    *,
    cohort_approved_events: Iterable[ActionResponseEvent],
    raw_trace_bundles: Iterable[LocalStepRawTraceBundle],
    timing_evidence: Mapping[str, ObservedResponseTimingEvidence],
    confidence_evidence: Mapping[str, ChannelConfidenceEvidence],
    calibration_profile: DualResponseCalibrationProfile,
) -> EvidenceProvenanceVerification:
    """Rebuild and compare every content-addressed reference fail-closed."""
    reasons = []
    try:
        rebuilt = build_evidence_provenance_bundle(
            bundle_id=bundle.bundle_id,
            cohort_approved_events=cohort_approved_events,
            raw_trace_bundles=raw_trace_bundles,
            timing_evidence=timing_evidence,
            confidence_evidence=confidence_evidence,
            calibration_profile=calibration_profile,
        )
    except (TypeError, ValueError) as exc:
        return EvidenceProvenanceVerification(
            status="MISMATCH",
            valid=False,
            reasons=("REBUILD_FAILED:%s" % exc,),
        )

    if bundle.to_dict() != rebuilt.to_dict():
        reasons.append("PROVENANCE_MANIFEST_OR_CONTENT_DIGEST_MISMATCH")
    return EvidenceProvenanceVerification(
        status="VERIFIED" if not reasons else "MISMATCH",
        valid=not reasons,
        reasons=tuple(reasons),
    )


__all__ = [
    "EVIDENCE_PROVENANCE_BUNDLE_VERSION",
    "EvidenceArtifactRef",
    "DualResponseEvidenceProvenanceBundle",
    "EvidenceProvenanceVerification",
    "canonical_sha256",
    "build_evidence_provenance_bundle",
    "verify_evidence_provenance_bundle",
]

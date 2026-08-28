# -*- coding: utf-8 -*-
"""Atomic content-addressed persistence for Scheme-2 MFAC review evidence.

This module persists *existing* review evidence. It does not define plant or
algorithm parameters and grants no runtime permission. Production paths are
owned by ``system.model.config.mfac_paths``; tests may inject a temporary root.

Layout::

    <MFAC_EVIDENCE_ROOT>/
      objects/<sha256[:2]>/<sha256>.json
      bundles/<bundle_id>.json

Loading never trusts filenames alone: every object is parsed, hashed again,
rebuilt into typed evidence, and passed through the existing provenance verifier.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import threading
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

from system.model.config.mfac_paths import (
    MFAC_EVIDENCE_BUNDLES_DIR,
    MFAC_EVIDENCE_OBJECTS_DIR,
    MFAC_EVIDENCE_ROOT,
)

from .channel_calibration_review import ObservedResponseTimingEvidence
from .channel_confidence_evidence import ChannelConfidenceEvidence
from .dual_response_calibration_profile import DualResponseCalibrationProfile
from .evidence_provenance_bundle import (
    EVIDENCE_PROVENANCE_BUNDLE_VERSION,
    DualResponseEvidenceProvenanceBundle,
    EvidenceArtifactRef,
    EvidenceProvenanceVerification,
    canonical_sha256,
    verify_evidence_provenance_bundle,
)
from .local_step_raw_trace import LocalStepRawTraceBundle
from .mfac_schema import ActionResponseEvent
from .observed_timing_extractor import ObservedProcessTrace, ObservedTraceSample


EVIDENCE_ARTIFACT_STORE_VERSION = (
    "SCHEME2_EVIDENCE_ARTIFACT_STORE_V1_ATOMIC_CONTENT_ADDRESSED_REVIEW_ONLY"
)

_SAFE_BUNDLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bundle_id(value: Any) -> str:
    text = str(value or "").strip()
    if not _SAFE_BUNDLE_ID.fullmatch(text) or ".." in text:
        raise ValueError("bundle_id must be a safe non-path identifier")
    return text


def _resolve_paths(root: Optional[str | Path]) -> Tuple[Path, Path, Path]:
    """Use the canonical production path contract unless a test root is explicit."""
    if root is None:
        return (
            Path(MFAC_EVIDENCE_ROOT),
            Path(MFAC_EVIDENCE_OBJECTS_DIR),
            Path(MFAC_EVIDENCE_BUNDLES_DIR),
        )
    resolved = Path(root)
    return resolved, resolved / "objects", resolved / "bundles"


def _json_bytes(value: Any, *, pretty: bool = False) -> bytes:
    try:
        text = json.dumps(
            value,
            sort_keys=not pretty,
            separators=None if pretty else (",", ":"),
            ensure_ascii=False,
            allow_nan=False,
            indent=2 if pretty else None,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("evidence artifact is not JSON serializable") from exc
    if pretty:
        text += "\n"
    return text.encode("utf-8")


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    data = _json_bytes(value, pretty=True)
    try:
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass


def _object_path(objects_root: Path, sha256: str) -> Path:
    digest = str(sha256 or "")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("object digest must be lowercase SHA256")
    return objects_root / digest[:2] / (digest + ".json")


def _event_payload(events: Iterable[ActionResponseEvent]) -> Tuple[Dict[str, Any], ...]:
    return tuple(
        event.to_dict()
        for event in sorted(events, key=lambda item: str(item.event_id or ""))
    )


def _confidence_from_dict(value: Mapping[str, Any]) -> ChannelConfidenceEvidence:
    payload = dict(value or {})
    payload["cohort_event_ids"] = tuple(payload.get("cohort_event_ids") or ())
    payload["timing_event_ids"] = tuple(payload.get("timing_event_ids") or ())
    return ChannelConfidenceEvidence(**payload)


def _trace_from_dict(value: Mapping[str, Any]) -> ObservedProcessTrace:
    payload = dict(value or {})
    payload["samples"] = tuple(
        ObservedTraceSample(**dict(sample or {}))
        for sample in payload.get("samples") or ()
    )
    return ObservedProcessTrace(**payload)


def _raw_trace_from_dict(value: Mapping[str, Any]) -> LocalStepRawTraceBundle:
    payload = dict(value or {})
    payload.pop("usable_for_timing_extraction", None)
    so2 = payload.get("so2_trace")
    ph = payload.get("ph_trace")
    payload["so2_trace"] = _trace_from_dict(so2) if isinstance(so2, Mapping) else None
    payload["ph_trace"] = _trace_from_dict(ph) if isinstance(ph, Mapping) else None
    payload["reasons"] = tuple(payload.get("reasons") or ())
    return LocalStepRawTraceBundle(**payload)


def _all_refs(bundle: DualResponseEvidenceProvenanceBundle) -> Tuple[EvidenceArtifactRef, ...]:
    return (
        (bundle.cohort_approved_events_ref,)
        + tuple(bundle.raw_trace_refs)
        + tuple(bundle.timing_refs)
        + tuple(bundle.confidence_refs)
        + (bundle.calibration_profile_ref,)
    )


def _put_payload(payloads: Dict[str, Any], digest: str, payload: Any) -> None:
    if canonical_sha256(payload) != digest:
        raise ValueError("evidence payload does not match its provenance digest")
    if digest in payloads and canonical_sha256(payloads[digest]) != digest:
        raise ValueError("conflicting payloads share one provenance digest")
    payloads[digest] = payload


def _ref_payloads(
    bundle: DualResponseEvidenceProvenanceBundle,
    *,
    cohort_approved_events: Iterable[ActionResponseEvent],
    raw_trace_bundles: Iterable[LocalStepRawTraceBundle],
    timing_evidence: Mapping[str, ObservedResponseTimingEvidence],
    confidence_evidence: Mapping[str, ChannelConfidenceEvidence],
    calibration_profile: DualResponseCalibrationProfile,
) -> Dict[str, Any]:
    """Map provenance digests to the exact semantic payload that they address."""
    payloads: Dict[str, Any] = {}
    _put_payload(
        payloads,
        bundle.cohort_approved_events_ref.sha256,
        _event_payload(cohort_approved_events),
    )

    raw_by_sha = {
        canonical_sha256(item.to_dict()): item.to_dict()
        for item in raw_trace_bundles
    }
    timing_by_sha = {
        canonical_sha256(item.to_dict()): item.to_dict()
        for item in dict(timing_evidence or {}).values()
    }
    confidence_by_sha = {
        canonical_sha256(item.to_dict()): item.to_dict()
        for item in dict(confidence_evidence or {}).values()
    }

    for ref in bundle.raw_trace_refs:
        if ref.sha256 not in raw_by_sha:
            raise ValueError("raw trace payload is missing for provenance ref %s" % ref.artifact_id)
        _put_payload(payloads, ref.sha256, raw_by_sha[ref.sha256])
    for ref in bundle.timing_refs:
        if ref.sha256 not in timing_by_sha:
            raise ValueError("timing payload is missing for provenance ref %s" % ref.artifact_id)
        _put_payload(payloads, ref.sha256, timing_by_sha[ref.sha256])
    for ref in bundle.confidence_refs:
        if ref.sha256 not in confidence_by_sha:
            raise ValueError("confidence payload is missing for provenance ref %s" % ref.artifact_id)
        _put_payload(payloads, ref.sha256, confidence_by_sha[ref.sha256])

    profile_payload = calibration_profile.to_dict()
    _put_payload(payloads, bundle.calibration_profile_ref.sha256, profile_payload)

    for ref in _all_refs(bundle):
        if ref.sha256 not in payloads:
            raise ValueError("provenance object payload is missing for %s" % ref.artifact_id)
    return payloads


@dataclass(frozen=True)
class EvidenceArtifactStoreWriteResult:
    status: str
    bundle_id: str
    manifest_path: str
    object_count: int
    review_chain_complete: bool
    activation_status: str = "NOT_ACTIVATABLE"
    learning_enabled: bool = False
    residual_control_enabled: bool = False
    dcs_write_enabled: bool = False
    semantics_version: str = EVIDENCE_ARTIFACT_STORE_VERSION

    def __post_init__(self) -> None:
        if self.status != "STORED_REVIEW_EVIDENCE":
            raise ValueError("unsupported evidence store write status")
        if self.activation_status != "NOT_ACTIVATABLE":
            raise ValueError("evidence store write result must remain NOT_ACTIVATABLE")
        if self.learning_enabled or self.residual_control_enabled or self.dcs_write_enabled:
            raise ValueError("evidence store cannot enable runtime permissions")


@dataclass(frozen=True)
class EvidenceArtifactLoadResult:
    status: str
    loaded: bool
    reasons: Tuple[str, ...] = ()
    bundle: Optional[DualResponseEvidenceProvenanceBundle] = None
    cohort_approved_events: Tuple[ActionResponseEvent, ...] = ()
    raw_trace_bundles: Tuple[LocalStepRawTraceBundle, ...] = ()
    timing_evidence: Dict[str, ObservedResponseTimingEvidence] = field(default_factory=dict)
    confidence_evidence: Dict[str, ChannelConfidenceEvidence] = field(default_factory=dict)
    calibration_profile: Optional[DualResponseCalibrationProfile] = None
    verification: Optional[EvidenceProvenanceVerification] = None
    activation_status: str = "NOT_ACTIVATABLE"
    learning_enabled: bool = False
    residual_control_enabled: bool = False
    dcs_write_enabled: bool = False
    semantics_version: str = EVIDENCE_ARTIFACT_STORE_VERSION

    def __post_init__(self) -> None:
        if self.status not in {
            "VERIFIED_REVIEW_EVIDENCE",
            "EVIDENCE_BUNDLE_NOT_FOUND",
            "EVIDENCE_INTEGRITY_FAILURE",
        }:
            raise ValueError("unsupported evidence artifact load status")
        if self.activation_status != "NOT_ACTIVATABLE":
            raise ValueError("evidence loader must remain NOT_ACTIVATABLE")
        if self.learning_enabled or self.residual_control_enabled or self.dcs_write_enabled:
            raise ValueError("evidence loader cannot enable runtime permissions")
        if self.loaded and self.status != "VERIFIED_REVIEW_EVIDENCE":
            raise ValueError("loaded evidence must have VERIFIED_REVIEW_EVIDENCE status")

    @property
    def review_chain_complete(self) -> bool:
        return bool(self.bundle is not None and self.bundle.is_complete_review_chain)

    def to_runtime_config(self) -> Dict[str, Any]:
        raise ValueError("loaded evidence is review-only and cannot activate runtime")


class EvidenceArtifactStore:
    """Atomic writer for content-addressed Scheme-2 review evidence."""

    def __init__(self, root: Optional[str | Path] = None) -> None:
        self.root, self.objects_root, self.bundles_root = _resolve_paths(root)
        self.lock = threading.RLock()

    def save(
        self,
        bundle: DualResponseEvidenceProvenanceBundle,
        *,
        cohort_approved_events: Iterable[ActionResponseEvent],
        raw_trace_bundles: Iterable[LocalStepRawTraceBundle],
        timing_evidence: Mapping[str, ObservedResponseTimingEvidence],
        confidence_evidence: Mapping[str, ChannelConfidenceEvidence],
        calibration_profile: DualResponseCalibrationProfile,
    ) -> EvidenceArtifactStoreWriteResult:
        if bundle.semantics_version != EVIDENCE_PROVENANCE_BUNDLE_VERSION:
            raise ValueError("unsupported provenance bundle semantics")
        safe_id = _bundle_id(bundle.bundle_id)
        events = tuple(cohort_approved_events)
        raw = tuple(raw_trace_bundles)
        timing = dict(timing_evidence or {})
        confidence = dict(confidence_evidence or {})

        verification = verify_evidence_provenance_bundle(
            bundle,
            cohort_approved_events=events,
            raw_trace_bundles=raw,
            timing_evidence=timing,
            confidence_evidence=confidence,
            calibration_profile=calibration_profile,
        )
        if not verification.valid:
            raise ValueError(
                "cannot persist mismatched provenance evidence: %s"
                % ";".join(verification.reasons)
            )

        payloads = _ref_payloads(
            bundle,
            cohort_approved_events=events,
            raw_trace_bundles=raw,
            timing_evidence=timing,
            confidence_evidence=confidence,
            calibration_profile=calibration_profile,
        )
        expected_digests = sorted(set(ref.sha256 for ref in _all_refs(bundle)))
        if sorted(payloads) != expected_digests:
            raise ValueError("resolved evidence payload set does not match provenance refs")

        with self.lock:
            for digest, payload in payloads.items():
                path = _object_path(self.objects_root, digest)
                if path.exists():
                    existing = _read_json(path)
                    if canonical_sha256(existing) != digest:
                        raise ValueError("existing content-addressed evidence object is corrupt")
                else:
                    _atomic_write_json(path, payload)

            manifest = {
                "schema_version": EVIDENCE_ARTIFACT_STORE_VERSION,
                "bundle_id": safe_id,
                "provenance_bundle_sha256": canonical_sha256(bundle.to_dict()),
                "provenance_bundle": bundle.to_dict(),
                "object_sha256": expected_digests,
                "review_chain_complete": bundle.is_complete_review_chain,
                "activation_status": "NOT_ACTIVATABLE",
                "learning_enabled": False,
                "residual_control_enabled": False,
                "dcs_write_enabled": False,
                "written_at_utc": _utc_now(),
            }
            manifest_path = self.bundles_root / (safe_id + ".json")
            _atomic_write_json(manifest_path, manifest)

        return EvidenceArtifactStoreWriteResult(
            status="STORED_REVIEW_EVIDENCE",
            bundle_id=safe_id,
            manifest_path=str(manifest_path),
            object_count=len(expected_digests),
            review_chain_complete=bundle.is_complete_review_chain,
        )


class EvidenceArtifactLoader:
    """Fail-closed loader that reconstructs and re-verifies every evidence object."""

    def __init__(self, root: Optional[str | Path] = None) -> None:
        self.root, self.objects_root, self.bundles_root = _resolve_paths(root)

    def load(self, bundle_id: str) -> EvidenceArtifactLoadResult:
        try:
            safe_id = _bundle_id(bundle_id)
        except ValueError as exc:
            return self._failure("EVIDENCE_INTEGRITY_FAILURE", str(exc))
        manifest_path = self.bundles_root / (safe_id + ".json")
        if not manifest_path.exists():
            return EvidenceArtifactLoadResult(
                status="EVIDENCE_BUNDLE_NOT_FOUND",
                loaded=False,
                reasons=("MANIFEST_NOT_FOUND",),
            )

        try:
            manifest = _read_json(manifest_path)
            if not isinstance(manifest, dict):
                raise ValueError("evidence manifest must be a JSON object")
            if manifest.get("schema_version") != EVIDENCE_ARTIFACT_STORE_VERSION:
                raise ValueError("evidence manifest schema mismatch")
            if manifest.get("bundle_id") != safe_id:
                raise ValueError("evidence manifest bundle ID mismatch")
            if manifest.get("activation_status") != "NOT_ACTIVATABLE":
                raise ValueError("evidence manifest activation status is invalid")
            if (
                manifest.get("learning_enabled")
                or manifest.get("residual_control_enabled")
                or manifest.get("dcs_write_enabled")
            ):
                raise ValueError("evidence manifest cannot enable runtime permissions")

            bundle_payload = manifest.get("provenance_bundle")
            if not isinstance(bundle_payload, dict):
                raise ValueError("evidence manifest is missing provenance bundle")
            stored_bundle_sha = str(manifest.get("provenance_bundle_sha256") or "")
            if canonical_sha256(bundle_payload) != stored_bundle_sha:
                raise ValueError("provenance bundle manifest digest mismatch")
            bundle = DualResponseEvidenceProvenanceBundle.from_dict(bundle_payload)
            if bundle.bundle_id != safe_id:
                raise ValueError("provenance bundle ID mismatch")
            if bool(manifest.get("review_chain_complete")) != bundle.is_complete_review_chain:
                raise ValueError("manifest review-chain status does not match provenance bundle")

            expected_digests = sorted(set(ref.sha256 for ref in _all_refs(bundle)))
            manifest_digests = list(manifest.get("object_sha256") or ())
            if len(manifest_digests) != len(set(manifest_digests)):
                raise ValueError("manifest contains duplicate object digests")
            if sorted(manifest_digests) != expected_digests:
                raise ValueError("manifest object digest list does not match provenance refs")

            payload_by_digest: Dict[str, Any] = {}
            for digest in expected_digests:
                path = _object_path(self.objects_root, digest)
                if not path.exists():
                    raise ValueError("referenced evidence object is missing: %s" % digest)
                payload = _read_json(path)
                if canonical_sha256(payload) != digest:
                    raise ValueError("evidence object digest mismatch: %s" % digest)
                payload_by_digest[digest] = payload

            cohort_payload = payload_by_digest[bundle.cohort_approved_events_ref.sha256]
            if not isinstance(cohort_payload, list):
                raise ValueError("cohort evidence object must be a JSON array")
            events = tuple(
                ActionResponseEvent.from_dict(dict(item or {}))
                for item in cohort_payload
            )
            raw = tuple(
                _raw_trace_from_dict(payload_by_digest[ref.sha256])
                for ref in bundle.raw_trace_refs
            )
            timing = {
                ref.channel.upper(): ObservedResponseTimingEvidence.from_dict(
                    payload_by_digest[ref.sha256]
                )
                for ref in bundle.timing_refs
            }
            confidence = {
                ref.channel.upper(): _confidence_from_dict(payload_by_digest[ref.sha256])
                for ref in bundle.confidence_refs
            }
            profile = DualResponseCalibrationProfile.from_dict(
                payload_by_digest[bundle.calibration_profile_ref.sha256]
            )

            verification = verify_evidence_provenance_bundle(
                bundle,
                cohort_approved_events=events,
                raw_trace_bundles=raw,
                timing_evidence=timing,
                confidence_evidence=confidence,
                calibration_profile=profile,
            )
            if not verification.valid:
                raise ValueError(
                    "provenance verification failed: %s"
                    % ";".join(verification.reasons)
                )

            return EvidenceArtifactLoadResult(
                status="VERIFIED_REVIEW_EVIDENCE",
                loaded=True,
                reasons=(),
                bundle=bundle,
                cohort_approved_events=events,
                raw_trace_bundles=raw,
                timing_evidence=timing,
                confidence_evidence=confidence,
                calibration_profile=profile,
                verification=verification,
            )
        except Exception as exc:
            return self._failure("EVIDENCE_INTEGRITY_FAILURE", str(exc))

    @staticmethod
    def _failure(status: str, reason: str) -> EvidenceArtifactLoadResult:
        return EvidenceArtifactLoadResult(
            status=status,
            loaded=False,
            reasons=(str(reason),),
        )


__all__ = [
    "EVIDENCE_ARTIFACT_STORE_VERSION",
    "EvidenceArtifactStoreWriteResult",
    "EvidenceArtifactLoadResult",
    "EvidenceArtifactStore",
    "EvidenceArtifactLoader",
]

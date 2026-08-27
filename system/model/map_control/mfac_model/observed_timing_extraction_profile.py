# -*- coding: utf-8 -*-
"""Typed review boundary for observed local-step timing extraction.

The extractor implementation intentionally has no plant defaults. This profile
is the only supported calibration-evidence path from an audit JSON into
``ObservedTimingExtractionConfig``. Candidate values are informational only;
only complete ``reviewed_parameters`` under an explicitly reviewed manual-only
status can build calibration-eligible timing evidence.

The profile cannot execute a trial, write DCS, update learning or activate the
normal MFAC runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Tuple, Union

from .observed_timing_extractor import (
    OBSERVED_TIMING_EXTRACTOR_VERSION,
    ObservedProcessTrace,
    ObservedTimingExtractionConfig,
    ObservedTimingExtractionResult,
    build_observed_response_timing_evidence,
)


OBSERVED_TIMING_EXTRACTION_PROFILE_VERSION = (
    "SCHEME2_OBSERVED_TIMING_EXTRACTION_DESIGN_V2_REVIEW_SEALED"
)
LEGACY_OBSERVED_TIMING_EXTRACTION_PROFILE_VERSION = (
    "SCHEME2_OBSERVED_TIMING_EXTRACTION_DESIGN_V1_REVIEW_REQUIRED"
)

_EXTRACTION_KEYS: Tuple[str, ...] = (
    "baseline_window_seconds",
    "max_observation_seconds",
    "max_sample_gap_seconds",
    "smoothing_window_samples",
    "onset_abs_threshold",
    "onset_sustain_samples",
    "response_fraction_of_extremum",
    "response_sustain_samples",
    "min_response_abs_amplitude",
    "min_baseline_samples",
    "min_post_reach_samples",
)


def _review_time(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat()
    except ValueError as exc:
        raise ValueError("review_time must be a valid ISO timestamp") from exc


@dataclass(frozen=True)
class ObservedTimingExtractionProfile:
    design_id: str
    status: str
    activation_status: str
    extractor_semantics: str
    reviewer_id: str = ""
    review_time: str = ""
    review_candidate_so2: Dict[str, Any] = field(default_factory=dict)
    review_candidate_ph: Dict[str, Any] = field(default_factory=dict)
    reviewed_so2: Dict[str, Any] = field(default_factory=dict)
    reviewed_ph: Dict[str, Any] = field(default_factory=dict)
    automatic_execution_allowed: bool = False
    learning_permission: bool = False
    dcs_write_enabled: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = OBSERVED_TIMING_EXTRACTION_PROFILE_VERSION

    def __post_init__(self) -> None:
        if not str(self.design_id or "").strip():
            raise ValueError("observed timing extraction design_id is required")
        if self.semantics_version != OBSERVED_TIMING_EXTRACTION_PROFILE_VERSION:
            raise ValueError("unsupported observed timing extraction profile semantics")
        if self.extractor_semantics != OBSERVED_TIMING_EXTRACTOR_VERSION:
            raise ValueError("observed timing extraction profile/extractor semantics mismatch")
        if self.activation_status != "NOT_ACTIVATABLE":
            raise ValueError("observed timing extraction profile must remain NOT_ACTIVATABLE")
        if self.automatic_execution_allowed:
            raise ValueError("observed timing extraction cannot enable automatic execution")
        if self.learning_permission:
            raise ValueError("observed timing extraction cannot enable learning")
        if self.dcs_write_enabled:
            raise ValueError("observed timing extraction cannot enable DCS write")
        if self.status == "REVIEWED_MANUAL_ONLY":
            if not str(self.reviewer_id or "").strip():
                raise ValueError("reviewed extraction profile requires reviewer_id")
            if not _review_time(self.review_time):
                raise ValueError("reviewed extraction profile requires review_time")

    @staticmethod
    def _channel_name(channel: str) -> str:
        value = str(channel or "").upper()
        if value not in {"SO2", "PH"}:
            raise ValueError("channel must be SO2 or PH")
        return value

    def _reviewed(self, channel: str) -> Dict[str, Any]:
        return dict(self.reviewed_so2 if self._channel_name(channel) == "SO2" else self.reviewed_ph)

    def _candidates(self, channel: str) -> Dict[str, Any]:
        return dict(
            self.review_candidate_so2
            if self._channel_name(channel) == "SO2"
            else self.review_candidate_ph
        )

    def missing_reviewed_keys(self, channel: str) -> Tuple[str, ...]:
        values = self._reviewed(channel)
        return tuple(key for key in _EXTRACTION_KEYS if values.get(key) is None)

    def reviewed_complete(self, channel: str) -> bool:
        return not self.missing_reviewed_keys(channel)

    def can_build_config(self, channel: str) -> bool:
        self._channel_name(channel)
        return (
            self.status == "REVIEWED_MANUAL_ONLY"
            and bool(str(self.reviewer_id or "").strip())
            and bool(str(self.review_time or "").strip())
            and self.activation_status == "NOT_ACTIVATABLE"
            and self.reviewed_complete(channel)
            and not self.automatic_execution_allowed
            and not self.learning_permission
            and not self.dcs_write_enabled
        )

    def build_config(self, channel: str) -> ObservedTimingExtractionConfig:
        channel_name = self._channel_name(channel)
        if not self.can_build_config(channel_name):
            raise ValueError(
                "observed timing extraction profile is not fully reviewed for %s; "
                "status=%s missing=%s"
                % (
                    channel_name,
                    self.status,
                    ",".join(self.missing_reviewed_keys(channel_name)),
                )
            )
        return ObservedTimingExtractionConfig(**self._reviewed(channel_name))

    def build_timing_evidence(
        self,
        traces: Iterable[ObservedProcessTrace],
        *,
        channel: str,
        evidence_id: str,
    ) -> ObservedTimingExtractionResult:
        """Build calibration-review-eligible timing evidence under this review seal."""
        channel_name = self._channel_name(channel)
        config = self.build_config(channel_name)
        supplied = list(traces)
        if any(str(trace.channel or "").upper() != channel_name for trace in supplied):
            raise ValueError("trace channel does not match reviewed extraction channel")
        result = build_observed_response_timing_evidence(
            supplied,
            config=config,
            evidence_id=evidence_id,
        )
        evidence = result.timing_evidence
        if evidence is None:
            return result
        metadata = dict(evidence.metadata or {})
        metadata.update(
            {
                "timing_extraction_profile_id": self.design_id,
                "timing_extraction_profile_semantics": self.semantics_version,
                "timing_extraction_profile_reviewed": True,
                "timing_extraction_reviewer_id": str(self.reviewer_id),
                "timing_extraction_review_time": _review_time(self.review_time),
                "calibration_review_eligible": True,
                "reviewed_extraction_parameters": dict(self._reviewed(channel_name)),
                "candidate_parameters_used_for_extraction": False,
            }
        )
        sealed_evidence = replace(evidence, metadata=metadata)
        return replace(result, timing_evidence=sealed_evidence)

    def review_candidate_parameters(self, channel: str) -> Dict[str, Any]:
        """Return audit candidates only; they never participate in build_config."""
        return self._candidates(channel)

    def to_runtime_config(self) -> Dict[str, Any]:
        raise ValueError(
            "observed timing extraction profile cannot activate the normal MFAC runtime"
        )

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "ObservedTimingExtractionProfile":
        data = dict(payload or {})
        candidates = dict(data.get("review_candidate_parameters") or {})
        reviewed = dict(data.get("reviewed_parameters") or {})
        review = dict(data.get("review") or {})
        semantics = str(
            data.get("semantics_version")
            or OBSERVED_TIMING_EXTRACTION_PROFILE_VERSION
        )
        if semantics == LEGACY_OBSERVED_TIMING_EXTRACTION_PROFILE_VERSION:
            if str(data.get("status") or "") == "REVIEWED_MANUAL_ONLY":
                raise ValueError(
                    "legacy reviewed timing extraction profile lacks V2 reviewer seal and must be re-reviewed"
                )
            semantics = OBSERVED_TIMING_EXTRACTION_PROFILE_VERSION
        return cls(
            design_id=str(data.get("design_id") or ""),
            status=str(data.get("status") or ""),
            activation_status=str(data.get("activation_status") or ""),
            extractor_semantics=str(data.get("extractor_semantics") or ""),
            reviewer_id=str(review.get("reviewer_id") or data.get("reviewer_id") or ""),
            review_time=str(review.get("review_time") or data.get("review_time") or ""),
            review_candidate_so2=dict(candidates.get("SO2") or {}),
            review_candidate_ph=dict(candidates.get("PH") or {}),
            reviewed_so2=dict(reviewed.get("SO2") or {}),
            reviewed_ph=dict(reviewed.get("PH") or {}),
            automatic_execution_allowed=bool(
                data.get("automatic_execution_allowed", False)
            ),
            learning_permission=bool(data.get("learning_permission", False)),
            dcs_write_enabled=bool(data.get("dcs_write_enabled", False)),
            metadata={
                "source_local_step_observation_profile_id": data.get(
                    "source_local_step_observation_profile_id"
                ),
                "source_noise_audit_id": data.get("source_noise_audit_id"),
                "trace_contract": dict(data.get("trace_contract") or {}),
                "response_timing_definition": data.get("response_timing_definition"),
                "smoothing_definition": data.get("smoothing_definition"),
                "candidate_basis": dict(data.get("candidate_basis") or {}),
                "forbidden_shortcuts": list(data.get("forbidden_shortcuts") or []),
                "notes": list(data.get("notes") or []),
            },
            semantics_version=semantics,
        )


def load_observed_timing_extraction_profile(
    path: Union[str, Path],
) -> ObservedTimingExtractionProfile:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("observed timing extraction JSON must contain an object")
    return ObservedTimingExtractionProfile.from_mapping(payload)


__all__ = [
    "OBSERVED_TIMING_EXTRACTION_PROFILE_VERSION",
    "LEGACY_OBSERVED_TIMING_EXTRACTION_PROFILE_VERSION",
    "ObservedTimingExtractionProfile",
    "load_observed_timing_extraction_profile",
]

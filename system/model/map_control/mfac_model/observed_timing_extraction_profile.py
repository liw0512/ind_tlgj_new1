# -*- coding: utf-8 -*-
"""Typed review boundary for observed local-step timing extraction.

The extractor implementation intentionally has no plant defaults.  This profile
is the only supported path from an audit JSON into
``ObservedTimingExtractionConfig``.  Candidate values are informational only;
only complete ``reviewed_parameters`` under a reviewed manual-only status can
build an extraction config.

The profile cannot execute a trial, write DCS, update learning or activate the
normal MFAC runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Dict, Mapping, Tuple, Union

from .observed_timing_extractor import (
    OBSERVED_TIMING_EXTRACTOR_VERSION,
    ObservedTimingExtractionConfig,
)


OBSERVED_TIMING_EXTRACTION_PROFILE_VERSION = (
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


@dataclass(frozen=True)
class ObservedTimingExtractionProfile:
    design_id: str
    status: str
    activation_status: str
    extractor_semantics: str
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
        return cls(
            design_id=str(data.get("design_id") or ""),
            status=str(data.get("status") or ""),
            activation_status=str(data.get("activation_status") or ""),
            extractor_semantics=str(data.get("extractor_semantics") or ""),
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
            semantics_version=str(
                data.get("semantics_version")
                or OBSERVED_TIMING_EXTRACTION_PROFILE_VERSION
            ),
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
    "ObservedTimingExtractionProfile",
    "load_observed_timing_extraction_profile",
]

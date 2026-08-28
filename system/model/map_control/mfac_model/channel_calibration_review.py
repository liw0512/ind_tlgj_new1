# -*- coding: utf-8 -*-
"""Explicit human review for LOCAL_GAIN_READY -> CALIBRATED channel promotion.

Observed timing must come from raw process traces through a reviewed timing-
extraction profile. Confidence must come from an adequate quantitative cohort,
human cohort-bootstrap approval, and the same reviewed timing evidence.

The review can calibrate SO2 and pH independently. It grants no runtime
learning, residual-control or DCS authority.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
import math
from typing import Any, Dict, Mapping, Optional, Tuple

from .channel_confidence_evidence import (
    CHANNEL_CONFIDENCE_EVIDENCE_VERSION,
    ChannelConfidenceEvidence,
)
from .dual_response_calibration_profile import (
    CHANNEL_CALIBRATED,
    CHANNEL_CALIBRATION_REVIEW_AUTHORITY_VERSION,
    CHANNEL_LOCAL_GAIN_READY,
    OBSERVED_RESPONSE_TIMING_SEMANTICS_VERSION,
    DualResponseCalibrationProfile,
    DualResponseChannelCalibration,
    _build_reviewed_calibrated_channel,
    _validate_delay_profile,
    _validate_response_config,
)
from .mfac_schema import DelayProfile


CHANNEL_CALIBRATION_REVIEW_VERSION = CHANNEL_CALIBRATION_REVIEW_AUTHORITY_VERSION
TIMING_SOURCE_OBSERVED_PROCESS_TRACE = "OBSERVED_PROCESS_TRACE"


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _timestamp_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "to_pydatetime"):
        converted = value.to_pydatetime()
        if isinstance(converted, datetime):
            return converted.isoformat()
    text = str(value or "").strip()
    if not text:
        raise ValueError("review_time is required")
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat()
    except ValueError as exc:
        raise ValueError("review_time must be a valid ISO timestamp") from exc


def _timing_extraction_provenance(evidence: "ObservedResponseTimingEvidence") -> Dict[str, str]:
    metadata = dict(evidence.metadata or {})
    if metadata.get("timing_extraction_profile_reviewed") is not True:
        raise ValueError("timing evidence requires a reviewed extraction profile")
    if metadata.get("calibration_review_eligible") is not True:
        raise ValueError("timing evidence is not eligible for channel calibration review")
    if metadata.get("candidate_parameters_used_for_extraction") is not False:
        raise ValueError("timing evidence cannot use unreviewed candidate extraction parameters")
    required = (
        "timing_extraction_profile_id",
        "timing_extraction_profile_semantics",
        "timing_extraction_reviewer_id",
        "timing_extraction_review_time",
    )
    values: Dict[str, str] = {}
    for key in required:
        value = str(metadata.get(key) or "").strip()
        if not value:
            raise ValueError("timing evidence is missing %s" % key)
        values[key] = value
    values["timing_extraction_review_time"] = _timestamp_text(
        values["timing_extraction_review_time"]
    )
    reviewed_parameters = metadata.get("reviewed_extraction_parameters")
    if not isinstance(reviewed_parameters, Mapping) or not dict(reviewed_parameters):
        raise ValueError("timing evidence is missing reviewed extraction parameters")
    return values


@dataclass(frozen=True)
class ObservedResponseTimingEvidence:
    evidence_id: str
    channel: str
    condition_snapshot_version: str
    mfac_context_id: str
    delay_profile: DelayProfile
    event_ids: Tuple[str, ...]
    observed_event_count: int
    independent_days: int
    onset_source: str = TIMING_SOURCE_OBSERVED_PROCESS_TRACE
    response_source: str = TIMING_SOURCE_OBSERVED_PROCESS_TRACE
    configured_window_boundary_used_as_observed_timing: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = OBSERVED_RESPONSE_TIMING_SEMANTICS_VERSION

    def __post_init__(self) -> None:
        channel = str(self.channel or "").upper()
        if channel not in {"SO2", "PH"}:
            raise ValueError("timing evidence channel must be SO2 or PH")
        if not str(self.evidence_id or "").strip():
            raise ValueError("timing evidence_id is required")
        if not str(self.condition_snapshot_version or "").strip():
            raise ValueError("timing evidence condition snapshot is required")
        if not str(self.mfac_context_id or "").strip():
            raise ValueError("timing evidence MFAC context is required")
        if self.semantics_version != OBSERVED_RESPONSE_TIMING_SEMANTICS_VERSION:
            raise ValueError("unsupported observed timing evidence semantics")
        if self.onset_source != TIMING_SOURCE_OBSERVED_PROCESS_TRACE:
            raise ValueError("onset timing must come from observed process trace")
        if self.response_source != TIMING_SOURCE_OBSERVED_PROCESS_TRACE:
            raise ValueError("response timing must come from observed process trace")
        if self.configured_window_boundary_used_as_observed_timing:
            raise ValueError("configured response-window boundaries cannot be timing evidence")
        event_ids = tuple(str(value or "").strip() for value in self.event_ids)
        if not event_ids or any(not value for value in event_ids):
            raise ValueError("observed timing evidence requires event IDs")
        if len(set(event_ids)) != len(event_ids):
            raise ValueError("observed timing evidence contains duplicate event IDs")
        if int(self.observed_event_count) != len(event_ids):
            raise ValueError("observed_event_count must equal timing event ID count")
        if int(self.observed_event_count) < 2:
            raise ValueError("observed timing evidence requires at least two events")
        if int(self.independent_days) <= 0:
            raise ValueError("observed timing evidence requires independent days > 0")
        if int(self.independent_days) > int(self.observed_event_count):
            raise ValueError("independent days cannot exceed observed event count")
        _validate_delay_profile(self.delay_profile)

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["delay_profile"] = self.delay_profile.to_dict()
        value["event_ids"] = list(self.event_ids)
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ObservedResponseTimingEvidence":
        payload = dict(value or {})
        payload["delay_profile"] = DelayProfile.from_dict(payload.get("delay_profile"))
        payload["event_ids"] = tuple(payload.get("event_ids") or ())
        return cls(**payload)


@dataclass(frozen=True)
class ChannelCalibrationReviewRecord:
    review_id: str
    profile_id: str
    channel: str
    status: str
    reviewer_id: str
    review_time: str
    timing_evidence_id: str
    timing_event_ids: Tuple[str, ...]
    confidence_evidence_id: str
    confidence_candidate: float
    confidence: float
    response_config: Dict[str, Any]
    activation_status: str = "NOT_ACTIVATABLE"
    learning_enabled: bool = False
    residual_control_enabled: bool = False
    dcs_write_enabled: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = CHANNEL_CALIBRATION_REVIEW_VERSION

    def __post_init__(self) -> None:
        if self.status != "CHANNEL_CALIBRATION_REVIEW_APPROVED":
            raise ValueError("unsupported channel calibration review status")
        if not str(self.confidence_evidence_id or "").strip():
            raise ValueError("confidence_evidence_id is required")
        candidate = _finite(self.confidence_candidate)
        if candidate is None or not (0.0 <= candidate <= 1.0):
            raise ValueError("confidence_candidate must be within [0,1]")
        if self.activation_status != "NOT_ACTIVATABLE":
            raise ValueError("channel calibration review must remain NOT_ACTIVATABLE")
        if self.learning_enabled or self.residual_control_enabled or self.dcs_write_enabled:
            raise ValueError("channel calibration review cannot enable runtime permissions")
        if self.semantics_version != CHANNEL_CALIBRATION_REVIEW_VERSION:
            raise ValueError("unsupported channel calibration review semantics")

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["timing_event_ids"] = list(self.timing_event_ids)
        return value


@dataclass(frozen=True)
class ChannelCalibrationReviewResult:
    profile: DualResponseCalibrationProfile
    review: ChannelCalibrationReviewRecord
    semantics_version: str = CHANNEL_CALIBRATION_REVIEW_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "profile": self.profile.to_dict(),
            "review": self.review.to_dict(),
            "semantics_version": self.semantics_version,
        }


def approve_channel_calibration(
    profile: DualResponseCalibrationProfile,
    *,
    channel: str,
    timing_evidence: ObservedResponseTimingEvidence,
    confidence_evidence: ChannelConfidenceEvidence,
    response_config: Mapping[str, Any],
    confidence: float,
    human_approved: bool,
    reviewer_id: str,
    review_time: Any,
) -> ChannelCalibrationReviewResult:
    """Review one channel and promote only that channel to CALIBRATED."""
    if not bool(human_approved):
        raise ValueError("explicit human channel calibration approval is required")
    reviewer = str(reviewer_id or "").strip()
    if not reviewer:
        raise ValueError("reviewer_id is required")
    reviewed_at = _timestamp_text(review_time)
    channel_name = str(channel or "").upper()
    if channel_name not in {"SO2", "PH"}:
        raise ValueError("channel must be SO2 or PH")

    base: DualResponseChannelCalibration = profile.so2 if channel_name == "SO2" else profile.ph
    if base.status != CHANNEL_LOCAL_GAIN_READY:
        if base.status == CHANNEL_CALIBRATED:
            raise ValueError("channel is already CALIBRATED")
        raise ValueError("channel must be LOCAL_GAIN_READY before calibration review")

    if timing_evidence.channel.upper() != channel_name:
        raise ValueError("timing evidence channel mismatch")
    if timing_evidence.condition_snapshot_version != profile.condition_snapshot_version:
        raise ValueError("timing evidence condition snapshot mismatch")
    if timing_evidence.mfac_context_id != profile.mfac_context_id:
        raise ValueError("timing evidence MFAC context mismatch")
    timing_provenance = _timing_extraction_provenance(timing_evidence)

    gain_ids = set(base.evidence_event_ids)
    timing_ids = set(timing_evidence.event_ids)
    if not timing_ids.issubset(gain_ids):
        raise ValueError("observed timing evidence must come from the reviewed LOCAL_GAIN cohort")
    if timing_evidence.independent_days > base.independent_days:
        raise ValueError("timing independent days exceed LOCAL_GAIN evidence days")

    if confidence_evidence.semantics_version != CHANNEL_CONFIDENCE_EVIDENCE_VERSION:
        raise ValueError("unsupported confidence evidence semantics")
    if confidence_evidence.status != "READY_FOR_CONFIDENCE_REVIEW":
        raise ValueError("confidence evidence is not ready for review")
    if confidence_evidence.channel.upper() != channel_name:
        raise ValueError("confidence evidence channel mismatch")
    if confidence_evidence.condition_snapshot_version != profile.condition_snapshot_version:
        raise ValueError("confidence evidence condition snapshot mismatch")
    if confidence_evidence.mfac_context_id != profile.mfac_context_id:
        raise ValueError("confidence evidence MFAC context mismatch")
    if confidence_evidence.timing_evidence_id != timing_evidence.evidence_id:
        raise ValueError("confidence evidence does not bind the supplied timing evidence")
    if set(confidence_evidence.timing_event_ids) != timing_ids:
        raise ValueError("confidence/timing event IDs mismatch")
    if set(confidence_evidence.cohort_event_ids) != gain_ids:
        raise ValueError("confidence evidence cohort does not match LOCAL_GAIN evidence")
    if confidence_evidence.cohort_bootstrap_review_approved is not True:
        raise ValueError("confidence evidence requires human cohort bootstrap approval")
    if confidence_evidence.human_review_required is not True:
        raise ValueError("confidence evidence must remain human-review gated")
    if confidence_evidence.confidence_candidate_is_probability:
        raise ValueError("confidence candidate cannot be treated as a probability")

    confidence_value = _finite(confidence)
    if confidence_value is None or not (0.0 < confidence_value <= 1.0):
        raise ValueError("reviewed confidence must be finite within (0, 1]")

    config = dict(response_config or {})
    _validate_response_config(channel_name, config)
    _validate_delay_profile(timing_evidence.delay_profile)

    review_id = "CHANNEL-CAL-%s-%s-%s" % (
        profile.profile_id,
        channel_name,
        reviewed_at.replace(":", "").replace("+", "P"),
    )
    review_metadata = {
        "calibration_review_approved": True,
        "calibration_review_semantics": CHANNEL_CALIBRATION_REVIEW_VERSION,
        "calibration_review_id": review_id,
        "calibration_reviewer_id": reviewer,
        "calibration_review_time": reviewed_at,
        "timing_evidence_review_approved": True,
        "timing_evidence_id": timing_evidence.evidence_id,
        "timing_evidence_semantics": timing_evidence.semantics_version,
        "timing_evidence_event_ids": list(timing_evidence.event_ids),
        "timing_observed_event_count": timing_evidence.observed_event_count,
        "timing_independent_days": timing_evidence.independent_days,
        "timing_onset_source": timing_evidence.onset_source,
        "timing_response_source": timing_evidence.response_source,
        "timing_extraction_profile_reviewed": True,
        "timing_extraction_profile_id": timing_provenance["timing_extraction_profile_id"],
        "timing_extraction_profile_semantics": timing_provenance["timing_extraction_profile_semantics"],
        "timing_extraction_reviewer_id": timing_provenance["timing_extraction_reviewer_id"],
        "timing_extraction_review_time": timing_provenance["timing_extraction_review_time"],
        "configured_window_boundary_used_as_observed_timing": False,
        "response_config_review_approved": True,
        "confidence_review_approved": True,
        "confidence_evidence_id": confidence_evidence.evidence_id,
        "confidence_evidence_semantics": confidence_evidence.semantics_version,
        "confidence_review_candidate": confidence_evidence.conservative_confidence_candidate,
        "confidence_candidate_is_probability": False,
        "cohort_bootstrap_review_approved": True,
        "cohort_review_id": confidence_evidence.cohort_review_id,
        "cohort_review_reviewer_id": confidence_evidence.cohort_review_reviewer_id,
        "cohort_review_time": confidence_evidence.cohort_review_time,
        "automatic_online_adaptation_allowed": False,
        "normal_runtime_activation_allowed": False,
        "separate_activation_review_required": True,
    }
    calibrated = _build_reviewed_calibrated_channel(
        base,
        confidence=confidence_value,
        delay_profile=timing_evidence.delay_profile,
        response_config=config,
        review_metadata=review_metadata,
    )

    profile_metadata = dict(profile.metadata or {})
    profile_metadata.update(
        {
            "last_channel_calibration_review_id": review_id,
            "last_calibrated_channel": channel_name,
            "separate_activation_artifact_required": True,
        }
    )
    updated_profile = (
        replace(profile, so2=calibrated, metadata=profile_metadata)
        if channel_name == "SO2"
        else replace(profile, ph=calibrated, metadata=profile_metadata)
    )

    review = ChannelCalibrationReviewRecord(
        review_id=review_id,
        profile_id=profile.profile_id,
        channel=channel_name,
        status="CHANNEL_CALIBRATION_REVIEW_APPROVED",
        reviewer_id=reviewer,
        review_time=reviewed_at,
        timing_evidence_id=timing_evidence.evidence_id,
        timing_event_ids=tuple(timing_evidence.event_ids),
        confidence_evidence_id=confidence_evidence.evidence_id,
        confidence_candidate=float(confidence_evidence.conservative_confidence_candidate),
        confidence=confidence_value,
        response_config=config,
        activation_status="NOT_ACTIVATABLE",
        learning_enabled=False,
        residual_control_enabled=False,
        dcs_write_enabled=False,
        metadata={
            "local_gain_event_ids": list(base.evidence_event_ids),
            "timing_event_ids_are_local_gain_subset": True,
            "timing_extraction_profile_reviewed": True,
            "timing_extraction_profile_id": timing_provenance["timing_extraction_profile_id"],
            "configured_window_is_not_observed_timing": True,
            "cohort_bootstrap_review_approved": True,
            "confidence_candidate_is_not_probability": True,
            "human_confidence_value_explicitly_reviewed": True,
            "other_channel_status_unchanged": True,
        },
    )
    return ChannelCalibrationReviewResult(profile=updated_profile, review=review)


__all__ = [
    "CHANNEL_CALIBRATION_REVIEW_VERSION",
    "TIMING_SOURCE_OBSERVED_PROCESS_TRACE",
    "ObservedResponseTimingEvidence",
    "ChannelCalibrationReviewRecord",
    "ChannelCalibrationReviewResult",
    "approve_channel_calibration",
]

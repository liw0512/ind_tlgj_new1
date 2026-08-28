# -*- coding: utf-8 -*-
"""Review-only confidence evidence for Scheme-2 calibrated response channels.

Confidence is an auditable review candidate, not a probability. It requires an
adequate quantitative LOCAL_GAIN cohort, human cohort-bootstrap approval, and
observed timing produced under a reviewed timing-extraction profile.

No runtime permission is granted here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import math
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

from .dual_response_calibration_profile import (
    CHANNEL_CONFIDENCE_EVIDENCE_SEMANTICS_VERSION,
    OBSERVED_RESPONSE_TIMING_SEMANTICS_VERSION,
)
from .local_gain_cohort_review import (
    LocalGainCohortConsistencyConfig,
    LocalGainCohortReview,
)
from .mfac_schema import ActionResponseEvent


CHANNEL_CONFIDENCE_EVIDENCE_VERSION = CHANNEL_CONFIDENCE_EVIDENCE_SEMANTICS_VERSION


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _sufficiency(actual: int, required: Optional[int]) -> float:
    if required is None or int(required) <= 0:
        raise ValueError("reviewed count/day requirement is missing")
    return _clip01(float(actual) / float(required))


def _consistency_score(metric: float, reviewed_limit: float) -> float:
    metric_value = _finite(metric)
    limit_value = _finite(reviewed_limit)
    if metric_value is None or metric_value < 0.0:
        raise ValueError("consistency metric must be finite and >= 0")
    if limit_value is None or limit_value <= 0.0:
        raise ValueError("reviewed consistency limit must be finite and > 0")
    return _clip01(1.0 / (1.0 + max(0.0, metric_value / limit_value)))


def _same(left: float, right: float) -> bool:
    return math.isclose(float(left), float(right), rel_tol=1e-9, abs_tol=1e-12)


def _iso_timestamp(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("cohort review time is required")
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat()
    except ValueError as exc:
        raise ValueError("cohort review time must be a valid ISO timestamp") from exc


def _validate_timing_provenance(timing_evidence: Any) -> Dict[str, str]:
    metadata = dict(getattr(timing_evidence, "metadata", {}) or {})
    if metadata.get("timing_extraction_profile_reviewed") is not True:
        raise ValueError("confidence evidence requires reviewed timing extraction")
    if metadata.get("calibration_review_eligible") is not True:
        raise ValueError("timing evidence is not calibration-review eligible")
    if metadata.get("candidate_parameters_used_for_extraction") is not False:
        raise ValueError("confidence evidence cannot use candidate timing parameters")
    required = (
        "timing_extraction_profile_id",
        "timing_extraction_profile_semantics",
        "timing_extraction_reviewer_id",
        "timing_extraction_review_time",
    )
    result: Dict[str, str] = {}
    for key in required:
        value = str(metadata.get(key) or "").strip()
        if not value:
            raise ValueError("timing evidence is missing %s" % key)
        result[key] = value
    result["timing_extraction_review_time"] = _iso_timestamp(
        result["timing_extraction_review_time"]
    )
    reviewed = metadata.get("reviewed_extraction_parameters")
    if not isinstance(reviewed, Mapping) or not dict(reviewed):
        raise ValueError("timing evidence is missing reviewed extraction parameters")
    return result


@dataclass(frozen=True)
class ChannelConfidenceEvidence:
    evidence_id: str
    channel: str
    condition_snapshot_version: str
    mfac_context_id: str
    cohort_review_id: str
    cohort_bootstrap_review_approved: bool
    cohort_review_reviewer_id: str
    cohort_review_time: str
    timing_evidence_id: str
    cohort_event_ids: Tuple[str, ...]
    timing_event_ids: Tuple[str, ...]
    valid_event_count: int
    required_valid_trials: int
    independent_days: int
    required_independent_days: int
    event_count_sufficiency: float
    independent_day_sufficiency: float
    timing_coverage_ratio: float
    phi_relative_mad: float
    reviewed_phi_relative_mad_limit: float
    phi_max_relative_deviation: float
    reviewed_phi_max_relative_deviation_limit: float
    phi_mad_consistency_score: float
    phi_max_deviation_consistency_score: float
    conservative_confidence_candidate: float
    status: str = "READY_FOR_CONFIDENCE_REVIEW"
    human_review_required: bool = True
    confidence_candidate_is_probability: bool = False
    learning_enabled: bool = False
    residual_control_enabled: bool = False
    dcs_write_enabled: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = CHANNEL_CONFIDENCE_EVIDENCE_VERSION

    def __post_init__(self) -> None:
        for name in (
            "evidence_id",
            "condition_snapshot_version",
            "mfac_context_id",
            "cohort_review_id",
            "cohort_review_reviewer_id",
            "cohort_review_time",
            "timing_evidence_id",
        ):
            if not str(getattr(self, name) or "").strip():
                raise ValueError("%s is required" % name)
        _iso_timestamp(self.cohort_review_time)
        if self.cohort_bootstrap_review_approved is not True:
            raise ValueError("confidence evidence requires human cohort bootstrap approval")
        if str(self.channel or "").upper() not in {"SO2", "PH"}:
            raise ValueError("confidence channel must be SO2 or PH")
        if self.status != "READY_FOR_CONFIDENCE_REVIEW":
            raise ValueError("unsupported confidence evidence status")
        if not self.human_review_required:
            raise ValueError("confidence evidence must remain human-review gated")
        if self.confidence_candidate_is_probability:
            raise ValueError("confidence review candidate must not be labelled a probability")
        if self.learning_enabled or self.residual_control_enabled or self.dcs_write_enabled:
            raise ValueError("confidence evidence cannot enable runtime permissions")
        if self.semantics_version != CHANNEL_CONFIDENCE_EVIDENCE_VERSION:
            raise ValueError("unsupported confidence evidence semantics")

        cohort_ids = tuple(str(value or "").strip() for value in self.cohort_event_ids)
        timing_ids = tuple(str(value or "").strip() for value in self.timing_event_ids)
        if not cohort_ids or any(not value for value in cohort_ids):
            raise ValueError("confidence evidence requires cohort event IDs")
        if len(set(cohort_ids)) != len(cohort_ids):
            raise ValueError("confidence evidence contains duplicate cohort event IDs")
        if not timing_ids or any(not value for value in timing_ids):
            raise ValueError("confidence evidence requires timing event IDs")
        if len(set(timing_ids)) != len(timing_ids):
            raise ValueError("confidence evidence contains duplicate timing event IDs")
        if not set(timing_ids).issubset(set(cohort_ids)):
            raise ValueError("timing evidence must be a subset of the cohort")
        if int(self.valid_event_count) != len(cohort_ids) or int(self.valid_event_count) <= 0:
            raise ValueError("valid_event_count must equal cohort event ID count")
        if int(self.required_valid_trials) <= 0 or int(self.required_independent_days) <= 0:
            raise ValueError("reviewed trial/day requirements must be > 0")
        if int(self.independent_days) <= 0 or int(self.independent_days) > int(self.valid_event_count):
            raise ValueError("independent_days must be within valid event count")

        for name in ("phi_relative_mad", "phi_max_relative_deviation"):
            number = _finite(getattr(self, name))
            if number is None or number < 0.0:
                raise ValueError("%s must be finite and >= 0" % name)
        for name in (
            "reviewed_phi_relative_mad_limit",
            "reviewed_phi_max_relative_deviation_limit",
        ):
            number = _finite(getattr(self, name))
            if number is None or number <= 0.0:
                raise ValueError("%s must be finite and > 0" % name)
        for name in (
            "event_count_sufficiency",
            "independent_day_sufficiency",
            "timing_coverage_ratio",
            "phi_mad_consistency_score",
            "phi_max_deviation_consistency_score",
            "conservative_confidence_candidate",
        ):
            number = _finite(getattr(self, name))
            if number is None or not (0.0 <= number <= 1.0):
                raise ValueError("%s must be within [0,1]" % name)

        expected = {
            "event_count_sufficiency": _sufficiency(self.valid_event_count, self.required_valid_trials),
            "independent_day_sufficiency": _sufficiency(self.independent_days, self.required_independent_days),
            "timing_coverage_ratio": _clip01(float(len(timing_ids)) / float(self.valid_event_count)),
            "phi_mad_consistency_score": _consistency_score(self.phi_relative_mad, self.reviewed_phi_relative_mad_limit),
            "phi_max_deviation_consistency_score": _consistency_score(self.phi_max_relative_deviation, self.reviewed_phi_max_relative_deviation_limit),
        }
        expected["conservative_confidence_candidate"] = min(expected.values())
        for name, value in expected.items():
            if not _same(getattr(self, name), value):
                raise ValueError("%s is inconsistent with source evidence" % name)

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["cohort_event_ids"] = list(self.cohort_event_ids)
        value["timing_event_ids"] = list(self.timing_event_ids)
        return value


def _validate_approved_events(
    review: LocalGainCohortReview,
    approved_events: Iterable[ActionResponseEvent],
) -> Tuple[Tuple[str, ...], str, str]:
    events = list(approved_events)
    ids = tuple(str(event.event_id or "") for event in events)
    if len(ids) != len(review.event_ids) or set(ids) != set(review.event_ids):
        raise ValueError("human-approved cohort event set does not match quantitative review")
    reviewers = set()
    review_times = set()
    for event in events:
        metadata = dict(event.metadata or {})
        if event.learning_eligible is not True:
            raise ValueError("cohort-approved event must be bootstrap learning-eligible")
        if metadata.get("cohort_bootstrap_review_approved") is not True:
            raise ValueError("cohort-approved event is missing human bootstrap approval")
        if metadata.get("offline_bootstrap_evidence_allowed") is not True:
            raise ValueError("cohort-approved event is not allowed for offline bootstrap")
        if metadata.get("automatic_online_adaptation_allowed") is not False:
            raise ValueError("cohort-approved event cannot allow automatic online adaptation")
        if str(metadata.get("cohort_review_id") or "") != review.review_id:
            raise ValueError("cohort-approved event review ID mismatch")
        reviewer = str(metadata.get("cohort_review_reviewer_id") or "").strip()
        review_time = str(metadata.get("cohort_review_time") or "").strip()
        if not reviewer or not review_time:
            raise ValueError("cohort-approved event is missing reviewer/time")
        reviewers.add(reviewer)
        review_times.add(_iso_timestamp(review_time))
    if len(reviewers) != 1 or len(review_times) != 1:
        raise ValueError("cohort-approved event copies must share one reviewer/time")
    return ids, next(iter(reviewers)), next(iter(review_times))


def build_channel_confidence_evidence(
    cohort_review: LocalGainCohortReview,
    *,
    approved_events: Iterable[ActionResponseEvent],
    channel: str,
    consistency_config: LocalGainCohortConsistencyConfig,
    timing_evidence: Any,
    evidence_id: str,
) -> ChannelConfidenceEvidence:
    """Build a review candidate from quantitatively and human-reviewed evidence."""
    channel_name = str(channel or "").upper()
    if channel_name not in {"SO2", "PH"}:
        raise ValueError("channel must be SO2 or PH")
    if cohort_review.status != "ADEQUATE_FOR_BOOTSTRAP_REVIEW" or not cohort_review.adequate_for_bootstrap_review:
        raise ValueError("cohort must be quantitatively adequate before confidence review")

    approved_ids, cohort_reviewer, cohort_review_time = _validate_approved_events(
        cohort_review, approved_events
    )
    if getattr(timing_evidence, "semantics_version", None) != OBSERVED_RESPONSE_TIMING_SEMANTICS_VERSION:
        raise ValueError("confidence evidence requires observed process timing evidence")
    if str(getattr(timing_evidence, "channel", "")).upper() != channel_name:
        raise ValueError("timing evidence channel mismatch")
    if getattr(timing_evidence, "condition_snapshot_version", None) != cohort_review.condition_snapshot_version:
        raise ValueError("timing/cohort condition snapshot mismatch")
    if getattr(timing_evidence, "mfac_context_id", None) != cohort_review.mfac_context_id:
        raise ValueError("timing/cohort MFAC context mismatch")
    timing_provenance = _validate_timing_provenance(timing_evidence)

    cohort_ids = tuple(str(value) for value in cohort_review.event_ids)
    if set(approved_ids) != set(cohort_ids):
        raise ValueError("approved cohort IDs differ from quantitative cohort IDs")
    timing_ids = tuple(str(value) for value in getattr(timing_evidence, "event_ids", ()))
    if not timing_ids or not set(timing_ids).issubset(set(cohort_ids)):
        raise ValueError("timing evidence must come from the reviewed cohort")

    required_trials = cohort_review.required_valid_trials
    required_days = cohort_review.required_independent_days
    count_score = _sufficiency(cohort_review.valid_event_count, required_trials)
    day_score = _sufficiency(cohort_review.independent_days, required_days)
    timing_coverage = _clip01(float(len(timing_ids)) / float(max(1, cohort_review.valid_event_count)))
    distribution = cohort_review.phi_so2_distribution if channel_name == "SO2" else cohort_review.phi_ph_distribution
    relative_mad = _finite(distribution.get("relative_mad"))
    max_deviation = _finite(distribution.get("max_relative_deviation"))
    if relative_mad is None or max_deviation is None:
        raise ValueError("cohort phi consistency distribution is incomplete")
    mad_limit = consistency_config.max_relative_mad_phi_so2 if channel_name == "SO2" else consistency_config.max_relative_mad_phi_ph
    deviation_limit = consistency_config.max_relative_deviation_phi_so2 if channel_name == "SO2" else consistency_config.max_relative_deviation_phi_ph
    if relative_mad > float(mad_limit) or max_deviation > float(deviation_limit):
        raise ValueError("cohort phi dispersion exceeds reviewed consistency limits")
    mad_score = _consistency_score(relative_mad, float(mad_limit))
    deviation_score = _consistency_score(max_deviation, float(deviation_limit))
    candidate = min(count_score, day_score, timing_coverage, mad_score, deviation_score)

    return ChannelConfidenceEvidence(
        evidence_id=str(evidence_id or "").strip(),
        channel=channel_name,
        condition_snapshot_version=cohort_review.condition_snapshot_version,
        mfac_context_id=cohort_review.mfac_context_id,
        cohort_review_id=cohort_review.review_id,
        cohort_bootstrap_review_approved=True,
        cohort_review_reviewer_id=cohort_reviewer,
        cohort_review_time=cohort_review_time,
        timing_evidence_id=str(getattr(timing_evidence, "evidence_id", "")),
        cohort_event_ids=cohort_ids,
        timing_event_ids=timing_ids,
        valid_event_count=int(cohort_review.valid_event_count),
        required_valid_trials=int(required_trials),
        independent_days=int(cohort_review.independent_days),
        required_independent_days=int(required_days),
        event_count_sufficiency=count_score,
        independent_day_sufficiency=day_score,
        timing_coverage_ratio=timing_coverage,
        phi_relative_mad=float(relative_mad),
        reviewed_phi_relative_mad_limit=float(mad_limit),
        phi_max_relative_deviation=float(max_deviation),
        reviewed_phi_max_relative_deviation_limit=float(deviation_limit),
        phi_mad_consistency_score=mad_score,
        phi_max_deviation_consistency_score=deviation_score,
        conservative_confidence_candidate=float(candidate),
        status="READY_FOR_CONFIDENCE_REVIEW",
        human_review_required=True,
        confidence_candidate_is_probability=False,
        learning_enabled=False,
        residual_control_enabled=False,
        dcs_write_enabled=False,
        metadata={
            "candidate_definition": "MIN_OF_COUNT_DAY_TIMING_COVERAGE_AND_ROBUST_PHI_CONSISTENCY_SCORES",
            "consistency_score_definition": "1/(1+observed_metric/reviewed_limit)",
            "candidate_is_not_probability": True,
            "candidate_is_not_runtime_confidence": True,
            "quantitative_cohort_review_id": cohort_review.review_id,
            "human_cohort_bootstrap_review_approved": True,
            "timing_extraction_profile_reviewed": True,
            "timing_extraction_profile_id": timing_provenance["timing_extraction_profile_id"],
            "timing_extraction_profile_semantics": timing_provenance["timing_extraction_profile_semantics"],
            "timing_extraction_reviewer_id": timing_provenance["timing_extraction_reviewer_id"],
            "timing_extraction_review_time": timing_provenance["timing_extraction_review_time"],
            "explicit_human_confidence_review_required": True,
            "automatic_online_adaptation_allowed": False,
            "normal_runtime_activation_allowed": False,
        },
    )


__all__ = [
    "CHANNEL_CONFIDENCE_EVIDENCE_VERSION",
    "ChannelConfidenceEvidence",
    "build_channel_confidence_evidence",
]

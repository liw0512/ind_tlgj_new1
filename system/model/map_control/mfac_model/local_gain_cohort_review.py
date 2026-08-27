# -*- coding: utf-8 -*-
"""Review-gated multi-trial consistency check for Scheme-2 LOCAL_GAIN evidence.

One successful manual local-step trial is intentionally insufficient to seed
MFAC.  Individual trials are first reviewed into canonical evidence records
with ``learning_eligible=False``.  This module then checks a same-context cohort
against the reviewed Trial Matrix evidence count/day requirements and separately
reviewed robust consistency limits.  Passing the quantitative gate only creates
``ADEQUATE_FOR_BOOTSTRAP_REVIEW``; a further explicit human cohort review is
required before copies of those events become bootstrap eligible.

The module has no actuator path, cannot write DCS, and cannot enable online
adaptation or normal runtime control.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
import math
from statistics import median
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .local_step_trial_matrix import LocalStepTrialLevel
from .mfac_schema import ActionResponseEvent


LOCAL_GAIN_COHORT_REVIEW_VERSION = (
    "SCHEME2_LOCAL_GAIN_COHORT_REVIEW_V1_MULTI_TRIAL_GATE"
)
LOCAL_GAIN_COHORT_PROFILE_VERSION = (
    "SCHEME2_LOCAL_GAIN_COHORT_PROFILE_V1_REVIEW_GATED"
)


_CONSISTENCY_KEYS: Tuple[str, ...] = (
    "max_relative_mad_delta_q",
    "max_relative_mad_phi_so2",
    "max_relative_mad_phi_ph",
    "max_relative_deviation_delta_q",
    "max_relative_deviation_phi_so2",
    "max_relative_deviation_phi_ph",
)


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _timestamp(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if hasattr(value, "to_pydatetime"):
        converted = value.to_pydatetime()
        if isinstance(converted, datetime):
            return converted
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _quantile(values: Sequence[float], q: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("quantile requires at least one value")
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * float(q)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _distribution(values: Sequence[float]) -> Dict[str, float]:
    if not values:
        raise ValueError("distribution requires at least one value")
    center = float(median(values))
    absolute_deviations = [abs(float(value) - center) for value in values]
    mad = float(median(absolute_deviations))
    denominator = max(abs(center), 1e-12)
    return {
        "count": float(len(values)),
        "p10": _quantile(values, 0.10),
        "p25": _quantile(values, 0.25),
        "median": center,
        "p75": _quantile(values, 0.75),
        "p90": _quantile(values, 0.90),
        "mad": mad,
        "relative_mad": mad / denominator,
        "max_relative_deviation": max(absolute_deviations) / denominator,
        "min": float(min(values)),
        "max": float(max(values)),
    }


def _phi_so2(event: ActionResponseEvent) -> Optional[float]:
    direct = _finite(event.phi_event)
    if direct is not None:
        return direct
    delta_q = _finite(event.delta_q_actual)
    delta_so2 = _finite(event.delta_so2)
    if delta_q is None or delta_so2 is None or abs(delta_q) <= 1e-12:
        return None
    value = delta_so2 / delta_q
    return value if math.isfinite(value) else None


def _phi_ph(event: ActionResponseEvent) -> Optional[float]:
    metadata_value = _finite((event.metadata or {}).get("phi_ph_event"))
    if metadata_value is not None:
        return metadata_value
    delta_q = _finite(event.delta_q_actual)
    delta_ph = _finite(event.delta_ph)
    if delta_q is None or delta_ph is None or abs(delta_q) <= 1e-12:
        return None
    value = delta_ph / delta_q
    return value if math.isfinite(value) else None


@dataclass(frozen=True)
class LocalGainCohortConsistencyConfig:
    """Reviewed cross-trial consistency limits; no site defaults exist."""

    max_relative_mad_delta_q: float
    max_relative_mad_phi_so2: float
    max_relative_mad_phi_ph: float
    max_relative_deviation_delta_q: float
    max_relative_deviation_phi_so2: float
    max_relative_deviation_phi_ph: float

    def __post_init__(self) -> None:
        for name in _CONSISTENCY_KEYS:
            value = _finite(getattr(self, name))
            if value is None or value <= 0.0:
                raise ValueError("%s must be finite and > 0" % name)


@dataclass(frozen=True)
class LocalGainCohortReviewProfile:
    """Typed human-review boundary for cohort consistency parameters."""

    profile_id: str
    status: str
    activation_status: str
    review_candidate_parameters: Dict[str, Any] = field(default_factory=dict)
    reviewed_parameters: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    automatic_execution_allowed: bool = False
    dcs_write_enabled: bool = False
    learning_permission: bool = False
    semantics_version: str = LOCAL_GAIN_COHORT_PROFILE_VERSION

    def __post_init__(self) -> None:
        if not str(self.profile_id or "").strip():
            raise ValueError("profile_id is required")
        if str(self.activation_status) != "NOT_ACTIVATABLE":
            raise ValueError("local-gain cohort profile must remain NOT_ACTIVATABLE")
        if str(self.semantics_version) != LOCAL_GAIN_COHORT_PROFILE_VERSION:
            raise ValueError("unsupported local-gain cohort profile semantics")
        if self.automatic_execution_allowed or self.dcs_write_enabled or self.learning_permission:
            raise ValueError("cohort profile cannot grant execution, DCS or learning authority")

    @property
    def missing_reviewed_keys(self) -> Tuple[str, ...]:
        values = dict(self.reviewed_parameters or {})
        return tuple(key for key in _CONSISTENCY_KEYS if values.get(key) is None)

    @property
    def can_build_config(self) -> bool:
        return (
            not self.missing_reviewed_keys
            and str(self.status) == "REVIEWED_MANUAL_ONLY"
            and str(self.activation_status) == "NOT_ACTIVATABLE"
        )

    def build_config(self) -> LocalGainCohortConsistencyConfig:
        if not self.can_build_config:
            raise ValueError(
                "local-gain cohort consistency profile is not fully reviewed; missing=%s"
                % ",".join(self.missing_reviewed_keys)
            )
        values = dict(self.reviewed_parameters)
        return LocalGainCohortConsistencyConfig(
            **{key: values[key] for key in _CONSISTENCY_KEYS}
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "LocalGainCohortReviewProfile":
        payload = dict(value or {})
        return cls(
            profile_id=str(payload.get("profile_id") or ""),
            status=str(payload.get("status") or ""),
            activation_status=str(payload.get("activation_status") or ""),
            review_candidate_parameters=dict(
                payload.get("review_candidate_parameters") or {}
            ),
            reviewed_parameters=dict(payload.get("reviewed_parameters") or {}),
            metadata={
                "source_design_id": payload.get("source_design_id"),
                "source_trial_matrix_version": payload.get("source_trial_matrix_version"),
                "notes": list(payload.get("notes") or []),
            },
            automatic_execution_allowed=bool(
                payload.get("automatic_execution_allowed", False)
            ),
            dcs_write_enabled=bool(payload.get("dcs_write_enabled", False)),
            learning_permission=bool(payload.get("learning_permission", False)),
            semantics_version=str(
                payload.get("semantics_version") or LOCAL_GAIN_COHORT_PROFILE_VERSION
            ),
        )


@dataclass(frozen=True)
class LocalGainCohortReview:
    review_id: str
    status: str
    condition_snapshot_version: str
    mfac_context_id: str
    level_id: str
    event_ids: Tuple[str, ...]
    valid_event_count: int
    independent_days: int
    required_valid_trials: Optional[int]
    required_independent_days: Optional[int]
    delta_q_distribution: Dict[str, float] = field(default_factory=dict)
    phi_so2_distribution: Dict[str, float] = field(default_factory=dict)
    phi_ph_distribution: Dict[str, float] = field(default_factory=dict)
    reasons: Tuple[str, ...] = ()
    adequate_for_bootstrap_review: bool = False
    bootstrap_review_approved: bool = False
    learning_permission: bool = False
    automatic_online_adaptation_allowed: bool = False
    dcs_write_enabled: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = LOCAL_GAIN_COHORT_REVIEW_VERSION

    def __post_init__(self) -> None:
        if self.bootstrap_review_approved or self.learning_permission:
            raise ValueError("quantitative cohort review cannot self-approve learning")
        if self.automatic_online_adaptation_allowed or self.dcs_write_enabled:
            raise ValueError("cohort review cannot enable online adaptation or DCS")

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["event_ids"] = list(self.event_ids)
        value["reasons"] = list(self.reasons)
        return value


def evaluate_local_gain_cohort(
    events: Iterable[ActionResponseEvent],
    *,
    level: LocalStepTrialLevel,
    config: LocalGainCohortConsistencyConfig,
    condition_snapshot_version: str,
    mfac_context_id: str,
) -> LocalGainCohortReview:
    """Evaluate one same-context reviewed local-gain cohort.

    Trial-count and independent-day requirements come exclusively from the
    supplied reviewed ``LocalStepTrialLevel``.  This function never invents or
    overrides them.
    """

    snapshot = str(condition_snapshot_version or "").strip()
    context = str(mfac_context_id or "").strip()
    reasons: List[str] = []
    supplied = list(events)

    if not snapshot:
        reasons.append("CONDITION_SNAPSHOT_REQUIRED")
    if not context:
        reasons.append("MFAC_CONTEXT_REQUIRED")
    if not level.ready_for_manual_session:
        reasons.append("TRIAL_LEVEL_NOT_REVIEWED")
        if level.required_valid_trials is None:
            reasons.append("REQUIRED_VALID_TRIALS_UNRESOLVED")
        if level.required_independent_days is None:
            reasons.append("REQUIRED_INDEPENDENT_DAYS_UNRESOLVED")

    event_ids = [str(event.event_id or "") for event in supplied]
    if len(set(event_ids)) != len(event_ids):
        reasons.append("DUPLICATE_EVENT_ID")

    trial_ids = [
        str((event.metadata or {}).get("identification_trial_id") or "")
        for event in supplied
    ]
    known_trial_ids = [value for value in trial_ids if value]
    if len(set(known_trial_ids)) != len(known_trial_ids):
        reasons.append("DUPLICATE_IDENTIFICATION_TRIAL")

    delta_q_values: List[float] = []
    phi_so2_values: List[float] = []
    phi_ph_values: List[float] = []
    days = set()
    legacy_premature_learning_ids: List[str] = []

    for event in supplied:
        metadata = dict(event.metadata or {})
        if event.condition_snapshot_version != snapshot:
            reasons.append("MIXED_CONDITION_SNAPSHOT")
        if event.mfac_context_id != context:
            reasons.append("MIXED_MFAC_CONTEXT")
        if str(event.action_source or "") != "MANUAL_LOCAL_STEP_IDENTIFICATION_REVIEWED":
            reasons.append("NON_MANUAL_REVIEWED_LOCAL_GAIN_EVENT")
        if metadata.get("evidence_role") != "LOCAL_GAIN":
            reasons.append("EVIDENCE_ROLE_NOT_LOCAL_GAIN")
        if metadata.get("manual_evidence_review_approved") is not True:
            reasons.append("INDIVIDUAL_EVIDENCE_REVIEW_MISSING")
        if metadata.get("cohort_bootstrap_review_approved") is True:
            reasons.append("EVENT_ALREADY_COHORT_APPROVED")
        elif event.learning_eligible:
            # V2 promoted events used learning_eligible=True before the cohort
            # gate existed.  They remain reviewable but are recorded so the
            # migration is explicit; bootstrap trainers independently reject
            # them until cohort approval metadata exists.
            legacy_premature_learning_ids.append(event.event_id)

        approved_step = _finite(metadata.get("approved_step_up_m3_h"))
        if approved_step is not None and abs(approved_step - float(level.step_up_m3_h)) > 1e-9:
            reasons.append("TRIAL_LEVEL_STEP_MISMATCH")

        delta_q = _finite(event.delta_q_actual)
        phi_so2 = _phi_so2(event)
        phi_ph = _phi_ph(event)
        if delta_q is None or delta_q <= 0.0:
            reasons.append("INVALID_DELTA_Q")
        else:
            delta_q_values.append(delta_q)
        if phi_so2 is None or phi_so2 >= 0.0:
            reasons.append("INVALID_PHI_SO2_DIRECTION")
        else:
            phi_so2_values.append(phi_so2)
        if phi_ph is None or phi_ph <= 0.0:
            reasons.append("INVALID_PHI_PH_DIRECTION")
        else:
            phi_ph_values.append(phi_ph)

        timestamp = _timestamp(event.action_start_time)
        if timestamp is None:
            reasons.append("INVALID_ACTION_START_TIME")
        else:
            days.add(timestamp.date().isoformat())

    reasons = list(dict.fromkeys(reasons))
    event_count = len(supplied)
    independent_days = len(days)
    required_trials = level.required_valid_trials
    required_days = level.required_independent_days

    delta_q_distribution = _distribution(delta_q_values) if len(delta_q_values) == event_count and event_count else {}
    phi_so2_distribution = _distribution(phi_so2_values) if len(phi_so2_values) == event_count and event_count else {}
    phi_ph_distribution = _distribution(phi_ph_values) if len(phi_ph_values) == event_count and event_count else {}

    structural_reasons = list(reasons)
    count_reasons: List[str] = []
    if required_trials is not None and event_count < int(required_trials):
        count_reasons.append("VALID_TRIAL_COUNT_BELOW_REVIEWED_REQUIREMENT")
    if required_days is not None and independent_days < int(required_days):
        count_reasons.append("INDEPENDENT_DAYS_BELOW_REVIEWED_REQUIREMENT")

    consistency_reasons: List[str] = []
    if not structural_reasons and not count_reasons and event_count:
        checks = (
            (
                delta_q_distribution,
                config.max_relative_mad_delta_q,
                config.max_relative_deviation_delta_q,
                "DELTA_Q",
            ),
            (
                phi_so2_distribution,
                config.max_relative_mad_phi_so2,
                config.max_relative_deviation_phi_so2,
                "PHI_SO2",
            ),
            (
                phi_ph_distribution,
                config.max_relative_mad_phi_ph,
                config.max_relative_deviation_phi_ph,
                "PHI_PH",
            ),
        )
        for distribution, mad_limit, deviation_limit, prefix in checks:
            if distribution["relative_mad"] > float(mad_limit):
                consistency_reasons.append("%s_RELATIVE_MAD_TOO_LARGE" % prefix)
            if distribution["max_relative_deviation"] > float(deviation_limit):
                consistency_reasons.append("%s_MAX_RELATIVE_DEVIATION_TOO_LARGE" % prefix)

    if structural_reasons:
        status = "REJECTED_INVALID_COHORT"
    elif count_reasons:
        status = "INSUFFICIENT_EVIDENCE"
    elif consistency_reasons:
        status = "INCONSISTENT_LOCAL_GAIN"
    else:
        status = "ADEQUATE_FOR_BOOTSTRAP_REVIEW"

    all_reasons = tuple(
        dict.fromkeys(structural_reasons + count_reasons + consistency_reasons)
    )
    adequate = status == "ADEQUATE_FOR_BOOTSTRAP_REVIEW"
    review_id = "LOCAL-GAIN-COHORT-%s-%s-%s" % (
        str(level.level_id or ""),
        snapshot,
        context,
    )
    return LocalGainCohortReview(
        review_id=review_id,
        status=status,
        condition_snapshot_version=snapshot,
        mfac_context_id=context,
        level_id=level.level_id,
        event_ids=tuple(event_ids),
        valid_event_count=event_count,
        independent_days=independent_days,
        required_valid_trials=required_trials,
        required_independent_days=required_days,
        delta_q_distribution=delta_q_distribution,
        phi_so2_distribution=phi_so2_distribution,
        phi_ph_distribution=phi_ph_distribution,
        reasons=all_reasons,
        adequate_for_bootstrap_review=adequate,
        bootstrap_review_approved=False,
        learning_permission=False,
        automatic_online_adaptation_allowed=False,
        dcs_write_enabled=False,
        metadata={
            "trial_matrix_is_count_day_authority": True,
            "robust_statistics": "MEDIAN_MAD_AND_MAX_RELATIVE_DEVIATION",
            "legacy_premature_learning_event_ids": legacy_premature_learning_ids,
            "individual_event_review_required": True,
            "explicit_cohort_human_review_required": True,
            "normal_runtime_activation_allowed": False,
        },
    )


def approve_local_gain_cohort_for_bootstrap(
    review: LocalGainCohortReview,
    events: Iterable[ActionResponseEvent],
    *,
    human_approved: bool,
    reviewer_id: str,
    review_time: Any,
) -> List[ActionResponseEvent]:
    """Create bootstrap-eligible copies after explicit cohort review.

    The returned events remain forbidden from automatic online adaptation and
    grant no runtime/DCS authority.  The original individual evidence objects
    are not mutated.
    """

    if not bool(human_approved):
        raise ValueError("explicit human cohort bootstrap approval is required")
    reviewer = str(reviewer_id or "").strip()
    if not reviewer:
        raise ValueError("reviewer_id is required")
    reviewed_at = _timestamp(review_time)
    if reviewed_at is None:
        raise ValueError("valid review_time is required")
    if review.status != "ADEQUATE_FOR_BOOTSTRAP_REVIEW" or not review.adequate_for_bootstrap_review:
        raise ValueError("local-gain cohort is not adequate for bootstrap review")

    supplied = list(events)
    supplied_ids = [str(event.event_id or "") for event in supplied]
    if len(set(supplied_ids)) != len(supplied_ids):
        raise ValueError("duplicate event ids supplied for cohort approval")
    if set(supplied_ids) != set(review.event_ids):
        raise ValueError("cohort approval events do not match reviewed event ids")

    approved: List[ActionResponseEvent] = []
    for event in supplied:
        if event.condition_snapshot_version != review.condition_snapshot_version:
            raise ValueError("cohort approval condition snapshot mismatch")
        if event.mfac_context_id != review.mfac_context_id:
            raise ValueError("cohort approval MFAC context mismatch")
        metadata = dict(event.metadata or {})
        if metadata.get("manual_evidence_review_approved") is not True:
            raise ValueError("individual evidence review is missing")
        if str(event.action_source or "") != "MANUAL_LOCAL_STEP_IDENTIFICATION_REVIEWED":
            raise ValueError("only reviewed manual local-gain events can be cohort approved")
        metadata.update(
            {
                "cohort_bootstrap_review_required": True,
                "cohort_bootstrap_review_approved": True,
                "cohort_review_id": review.review_id,
                "cohort_review_level_id": review.level_id,
                "cohort_review_reviewer_id": reviewer,
                "cohort_review_time": reviewed_at.isoformat(),
                "cohort_valid_event_count": review.valid_event_count,
                "cohort_independent_days": review.independent_days,
                "offline_bootstrap_evidence_allowed": True,
                "automatic_online_adaptation_allowed": False,
                "normal_runtime_activation_allowed": False,
            }
        )
        approved.append(
            replace(
                event,
                learning_eligible=True,
                metadata=metadata,
            )
        )
    return approved


__all__ = [
    "LOCAL_GAIN_COHORT_REVIEW_VERSION",
    "LOCAL_GAIN_COHORT_PROFILE_VERSION",
    "LocalGainCohortConsistencyConfig",
    "LocalGainCohortReviewProfile",
    "LocalGainCohortReview",
    "evaluate_local_gain_cohort",
    "approve_local_gain_cohort_for_bootstrap",
]

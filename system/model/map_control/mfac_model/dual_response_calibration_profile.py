# -*- coding: utf-8 -*-
"""Fail-closed calibration profile for Scheme-2 SO2 + pH response channels.

The profile separates local-gain evidence from full channel calibration.  A
CALIBRATED channel must carry explicit reviewed timing-extraction provenance,
human cohort-bootstrap provenance, confidence evidence and response-config
review.  Even two CALIBRATED channels remain non-activating.
"""

from __future__ import annotations

from dataclasses import InitVar, asdict, dataclass, field
import math
from typing import Any, Dict, Mapping, Optional, Tuple

from .dual_response_bootstrap import DualResponseBootstrapBundle
from .mfac_schema import DelayProfile
from .ph_response import PHResponseConfig
from .process_response import ProcessResponseConfig


LEGACY_DUAL_RESPONSE_CALIBRATION_PROFILE_VERSION = (
    "SCHEME2_DUAL_RESPONSE_CALIBRATION_PROFILE_V1_FAIL_CLOSED"
)
LEGACY_DUAL_RESPONSE_CALIBRATION_PROFILE_V2_VERSION = (
    "SCHEME2_DUAL_RESPONSE_CALIBRATION_PROFILE_V2_REVIEW_SEALED_FAIL_CLOSED"
)
DUAL_RESPONSE_CALIBRATION_PROFILE_VERSION = (
    "SCHEME2_DUAL_RESPONSE_CALIBRATION_PROFILE_V3_TIMING_CONFIDENCE_EVIDENCE_SEALED"
)
CHANNEL_CALIBRATION_REVIEW_AUTHORITY_VERSION = (
    "SCHEME2_CHANNEL_CALIBRATION_REVIEW_V3_REVIEWED_TIMING_PROVENANCE"
)
OBSERVED_RESPONSE_TIMING_SEMANTICS_VERSION = (
    "SCHEME2_OBSERVED_RESPONSE_TIMING_V1_PROCESS_TRACE"
)
CHANNEL_CONFIDENCE_EVIDENCE_SEMANTICS_VERSION = (
    "SCHEME2_CHANNEL_CONFIDENCE_EVIDENCE_V1_REVIEW_CANDIDATE"
)

CHANNEL_UNCONFIGURED = "UNCONFIGURED"
CHANNEL_INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
CHANNEL_REVIEW_REQUIRED = "REVIEW_REQUIRED"
CHANNEL_LOCAL_GAIN_READY = "LOCAL_GAIN_READY"
CHANNEL_CALIBRATED = "CALIBRATED"

_ALLOWED_CHANNEL_STATUSES = {
    CHANNEL_UNCONFIGURED,
    CHANNEL_INSUFFICIENT_EVIDENCE,
    CHANNEL_REVIEW_REQUIRED,
    CHANNEL_LOCAL_GAIN_READY,
    CHANNEL_CALIBRATED,
}

_REQUIRED_RESPONSE_KEYS: Tuple[str, ...] = (
    "baseline_window_seconds",
    "delay_onset_seconds",
    "observation_seconds",
    "measurement_window_seconds",
    "max_sample_gap_seconds",
    "target_change_tolerance",
    "min_baseline_samples",
    "min_response_samples",
)

_DELAY_PROFILE_KEYS: Tuple[str, ...] = (
    "onset_p50_seconds",
    "onset_p90_seconds",
    "response_p50_seconds",
    "response_p90_seconds",
)

_CALIBRATION_REVIEW_AUTHORITY = object()


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _valid_confidence(value: Optional[float]) -> bool:
    if value is None:
        return False
    number = _finite(value)
    return number is not None and 0.0 <= number <= 1.0


def _validate_response_config(channel: str, value: Mapping[str, Any]) -> None:
    payload = dict(value or {})
    missing = [key for key in _REQUIRED_RESPONSE_KEYS if payload.get(key) is None]
    if missing:
        raise ValueError(
            "CALIBRATED channel is missing response config: %s"
            % ",".join(missing)
        )
    try:
        if str(channel).upper() == "SO2":
            ProcessResponseConfig(**payload)
        else:
            PHResponseConfig(**payload)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "%s CALIBRATED response config is invalid: %s"
            % (str(channel).upper(), exc)
        ) from exc


def _validate_delay_profile(value: DelayProfile) -> None:
    parsed: Dict[str, float] = {}
    for key in _DELAY_PROFILE_KEYS:
        number = _finite(getattr(value, key, None))
        if number is None or number < 0.0:
            raise ValueError(
                "CALIBRATED channel requires finite nonnegative %s" % key
            )
        parsed[key] = number
    if parsed["onset_p90_seconds"] < parsed["onset_p50_seconds"]:
        raise ValueError("delay onset P90 cannot be below P50")
    if parsed["response_p90_seconds"] < parsed["response_p50_seconds"]:
        raise ValueError("response timing P90 cannot be below P50")
    if parsed["response_p50_seconds"] < parsed["onset_p50_seconds"]:
        raise ValueError("response P50 cannot precede onset P50")
    if parsed["response_p90_seconds"] < parsed["onset_p90_seconds"]:
        raise ValueError("response P90 cannot precede onset P90")


def _validate_calibration_review_metadata(value: Mapping[str, Any]) -> None:
    metadata = dict(value or {})
    required_true = (
        "calibration_review_approved",
        "timing_evidence_review_approved",
        "timing_extraction_profile_reviewed",
        "response_config_review_approved",
        "confidence_review_approved",
        "cohort_bootstrap_review_approved",
    )
    for key in required_true:
        if metadata.get(key) is not True:
            raise ValueError("CALIBRATED channel is missing approved %s" % key)

    required_text = (
        "calibration_review_id",
        "calibration_reviewer_id",
        "calibration_review_time",
        "timing_evidence_id",
        "timing_extraction_profile_id",
        "timing_extraction_profile_semantics",
        "timing_extraction_reviewer_id",
        "timing_extraction_review_time",
        "confidence_evidence_id",
        "cohort_review_id",
        "cohort_review_reviewer_id",
        "cohort_review_time",
    )
    for key in required_text:
        if not str(metadata.get(key) or "").strip():
            raise ValueError("CALIBRATED channel is missing %s" % key)

    if metadata.get("calibration_review_semantics") != CHANNEL_CALIBRATION_REVIEW_AUTHORITY_VERSION:
        raise ValueError("CALIBRATED channel review semantics are invalid")
    if metadata.get("timing_evidence_semantics") != OBSERVED_RESPONSE_TIMING_SEMANTICS_VERSION:
        raise ValueError("CALIBRATED channel requires observed timing evidence")
    if metadata.get("confidence_evidence_semantics") != CHANNEL_CONFIDENCE_EVIDENCE_SEMANTICS_VERSION:
        raise ValueError("CALIBRATED channel requires confidence evidence")
    if metadata.get("configured_window_boundary_used_as_observed_timing") is not False:
        raise ValueError("configured response-window boundaries cannot be used as observed timing")
    if metadata.get("timing_extraction_candidate_parameters_used") is not False:
        raise ValueError("CALIBRATED timing cannot use candidate extraction parameters")
    reviewed_parameters = metadata.get("timing_extraction_reviewed_parameters")
    if not isinstance(reviewed_parameters, Mapping) or not dict(reviewed_parameters):
        raise ValueError("CALIBRATED channel requires reviewed timing extraction parameters")
    if metadata.get("confidence_candidate_is_probability") is not False:
        raise ValueError("confidence candidate cannot be treated as a probability")
    candidate = _finite(metadata.get("confidence_review_candidate"))
    if candidate is None or not (0.0 <= candidate <= 1.0):
        raise ValueError("CALIBRATED channel requires finite confidence review candidate")
    if metadata.get("automatic_online_adaptation_allowed") is not False:
        raise ValueError("channel calibration cannot enable online adaptation")
    if metadata.get("normal_runtime_activation_allowed") is not False:
        raise ValueError("channel calibration cannot enable normal runtime")


@dataclass(frozen=True)
class DualResponseChannelCalibration:
    channel: str
    status: str
    phi_prior: Optional[float] = None
    phi_live0: Optional[float] = None
    confidence: Optional[float] = None
    valid_event_count: int = 0
    independent_days: int = 0
    delay_profile: DelayProfile = field(default_factory=DelayProfile)
    response_config: Dict[str, Any] = field(default_factory=dict)
    evidence_event_ids: Tuple[str, ...] = ()
    reason_codes: Tuple[str, ...] = ()
    metadata: Dict[str, Any] = field(default_factory=dict)
    _calibration_authority: InitVar[Any] = None

    def __post_init__(self, _calibration_authority: Any) -> None:
        channel = str(self.channel or "").upper()
        if channel not in {"SO2", "PH"}:
            raise ValueError("channel must be SO2 or PH")
        if self.status not in _ALLOWED_CHANNEL_STATUSES:
            raise ValueError("unsupported channel calibration status")
        if int(self.valid_event_count) < 0 or int(self.independent_days) < 0:
            raise ValueError("event/day counts must be >= 0")

        phi_prior = _finite(self.phi_prior)
        phi_live0 = _finite(self.phi_live0)
        for name, value in (("phi_prior", phi_prior), ("phi_live0", phi_live0)):
            if value is None and getattr(self, name) is not None:
                raise ValueError("%s must be finite when provided" % name)
            if value is not None:
                if channel == "SO2" and value >= 0.0:
                    raise ValueError("SO2 phi must remain negative")
                if channel == "PH" and value <= 0.0:
                    raise ValueError("pH phi must remain positive")

        if self.confidence is not None and not _valid_confidence(self.confidence):
            raise ValueError("confidence must be within [0, 1]")

        if self.status in {CHANNEL_LOCAL_GAIN_READY, CHANNEL_CALIBRATED}:
            if phi_prior is None or phi_live0 is None:
                raise ValueError("local-gain-ready channel requires phi_prior and phi_live0")
            if int(self.valid_event_count) <= 0 or int(self.independent_days) <= 0:
                raise ValueError("local-gain-ready channel requires positive evidence counts")
            if not self.evidence_event_ids:
                raise ValueError("local-gain-ready channel requires evidence event IDs")

        if self.status == CHANNEL_CALIBRATED:
            if _calibration_authority is not _CALIBRATION_REVIEW_AUTHORITY:
                raise ValueError(
                    "CALIBRATED channel must be created by explicit channel calibration review"
                )
            if not _valid_confidence(self.confidence):
                raise ValueError("CALIBRATED channel requires reviewed confidence")
            _validate_delay_profile(self.delay_profile)
            _validate_response_config(channel, self.response_config)
            _validate_calibration_review_metadata(self.metadata)

    @property
    def is_calibrated(self) -> bool:
        return self.status == CHANNEL_CALIBRATED

    @property
    def has_local_gain(self) -> bool:
        return self.status in {CHANNEL_LOCAL_GAIN_READY, CHANNEL_CALIBRATED}

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["delay_profile"] = self.delay_profile.to_dict()
        value["evidence_event_ids"] = list(self.evidence_event_ids)
        value["reason_codes"] = list(self.reason_codes)
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DualResponseChannelCalibration":
        payload = dict(value or {})
        payload["delay_profile"] = DelayProfile.from_dict(payload.get("delay_profile"))
        payload["evidence_event_ids"] = tuple(payload.get("evidence_event_ids") or ())
        payload["reason_codes"] = tuple(payload.get("reason_codes") or ())
        if payload.get("status") == CHANNEL_CALIBRATED:
            payload["_calibration_authority"] = _CALIBRATION_REVIEW_AUTHORITY
        return cls(**payload)


def _build_reviewed_calibrated_channel(
    base: DualResponseChannelCalibration,
    *,
    confidence: float,
    delay_profile: DelayProfile,
    response_config: Mapping[str, Any],
    review_metadata: Mapping[str, Any],
) -> DualResponseChannelCalibration:
    if base.status != CHANNEL_LOCAL_GAIN_READY:
        raise ValueError("channel must be LOCAL_GAIN_READY before calibration review")
    metadata = dict(base.metadata or {})
    metadata.update(dict(review_metadata or {}))
    return DualResponseChannelCalibration(
        channel=base.channel,
        status=CHANNEL_CALIBRATED,
        phi_prior=base.phi_prior,
        phi_live0=base.phi_live0,
        confidence=float(confidence),
        valid_event_count=base.valid_event_count,
        independent_days=base.independent_days,
        delay_profile=delay_profile,
        response_config=dict(response_config or {}),
        evidence_event_ids=tuple(base.evidence_event_ids),
        reason_codes=("CHANNEL_CALIBRATION_REVIEWED",),
        metadata=metadata,
        _calibration_authority=_CALIBRATION_REVIEW_AUTHORITY,
    )


@dataclass(frozen=True)
class DualResponseCalibrationProfile:
    profile_id: str
    condition_snapshot_version: str
    mfac_context_id: str
    so2: DualResponseChannelCalibration
    ph: DualResponseChannelCalibration
    activation_status: str = "NOT_ACTIVATABLE"
    learning_review_status: str = "REVIEW_REQUIRED"
    residual_review_status: str = "REVIEW_REQUIRED"
    learning_enabled: bool = False
    residual_control_enabled: bool = False
    dcs_write_enabled: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = DUAL_RESPONSE_CALIBRATION_PROFILE_VERSION

    def __post_init__(self) -> None:
        if not str(self.profile_id or "").strip():
            raise ValueError("profile_id is required")
        if not str(self.condition_snapshot_version or "").strip():
            raise ValueError("condition_snapshot_version is required")
        if not str(self.mfac_context_id or "").strip():
            raise ValueError("mfac_context_id is required")
        if self.so2.channel.upper() != "SO2" or self.ph.channel.upper() != "PH":
            raise ValueError("dual profile requires SO2 and PH channel sections")
        if self.activation_status != "NOT_ACTIVATABLE":
            raise ValueError("V3 dual calibration profile must remain NOT_ACTIVATABLE")
        if self.learning_enabled or self.residual_control_enabled or self.dcs_write_enabled:
            raise ValueError("dual calibration profile cannot enable production permissions")
        if self.semantics_version != DUAL_RESPONSE_CALIBRATION_PROFILE_VERSION:
            raise ValueError("unsupported dual-response calibration profile semantics")

        if self.so2.has_local_gain and self.ph.has_local_gain:
            if self.so2.evidence_event_ids != self.ph.evidence_event_ids:
                raise ValueError("SO2 and pH local-gain evidence IDs must match")
            if self.so2.valid_event_count != self.ph.valid_event_count:
                raise ValueError("SO2 and pH local-gain event counts must match")
            if self.so2.independent_days != self.ph.independent_days:
                raise ValueError("SO2 and pH independent-day counts must match")

    @property
    def so2_calibrated(self) -> bool:
        return self.so2.is_calibrated

    @property
    def ph_calibrated(self) -> bool:
        return self.ph.is_calibrated

    @property
    def both_channels_calibrated(self) -> bool:
        return self.so2_calibrated and self.ph_calibrated

    @property
    def can_enable_learning(self) -> bool:
        return False

    @property
    def can_enable_residual(self) -> bool:
        return False

    @property
    def can_enable_dcs(self) -> bool:
        return False

    def to_runtime_config(self) -> Dict[str, Any]:
        raise ValueError(
            "dual-response calibration profile is review-only; a separate activation artifact is required"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "condition_snapshot_version": self.condition_snapshot_version,
            "mfac_context_id": self.mfac_context_id,
            "so2": self.so2.to_dict(),
            "ph": self.ph.to_dict(),
            "activation_status": self.activation_status,
            "learning_review_status": self.learning_review_status,
            "residual_review_status": self.residual_review_status,
            "learning_enabled": self.learning_enabled,
            "residual_control_enabled": self.residual_control_enabled,
            "dcs_write_enabled": self.dcs_write_enabled,
            "metadata": dict(self.metadata),
            "semantics_version": self.semantics_version,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DualResponseCalibrationProfile":
        payload = dict(value or {})
        semantics = str(payload.get("semantics_version") or "")
        if semantics in {
            LEGACY_DUAL_RESPONSE_CALIBRATION_PROFILE_VERSION,
            LEGACY_DUAL_RESPONSE_CALIBRATION_PROFILE_V2_VERSION,
        }:
            if (payload.get("so2") or {}).get("status") == CHANNEL_CALIBRATED or (
                payload.get("ph") or {}
            ).get("status") == CHANNEL_CALIBRATED:
                raise ValueError(
                    "legacy CALIBRATED profile lacks V3 timing/confidence-evidence seal and must be re-reviewed"
                )
            payload["semantics_version"] = DUAL_RESPONSE_CALIBRATION_PROFILE_VERSION
        payload["so2"] = DualResponseChannelCalibration.from_dict(payload.get("so2") or {})
        payload["ph"] = DualResponseChannelCalibration.from_dict(payload.get("ph") or {})
        return cls(**payload)


def build_calibration_profile_from_dual_bootstrap(
    bundle: DualResponseBootstrapBundle,
    *,
    profile_id: str,
) -> DualResponseCalibrationProfile:
    so2 = DualResponseChannelCalibration(
        channel="SO2",
        status=CHANNEL_LOCAL_GAIN_READY,
        phi_prior=bundle.so2.phi_seed,
        phi_live0=bundle.so2.phi_replayed,
        confidence=None,
        valid_event_count=bundle.valid_event_count,
        independent_days=bundle.independent_days,
        delay_profile=bundle.so2.delay_profile,
        response_config={},
        evidence_event_ids=bundle.event_ids,
        reason_codes=("RESPONSE_TIMING_AND_CONFIDENCE_REVIEW_REQUIRED",),
        metadata={
            "bootstrap_semantics_version": bundle.so2.semantics_version,
            "bootstrap_status": bundle.status,
            "bootstrap_delay_profile_is_evidence_not_reviewed_calibration": True,
        },
    )
    ph = DualResponseChannelCalibration(
        channel="PH",
        status=CHANNEL_LOCAL_GAIN_READY,
        phi_prior=bundle.ph.phi_seed,
        phi_live0=bundle.ph.phi_replayed,
        confidence=None,
        valid_event_count=bundle.valid_event_count,
        independent_days=bundle.independent_days,
        delay_profile=DelayProfile(),
        response_config={},
        evidence_event_ids=bundle.event_ids,
        reason_codes=("RESPONSE_TIMING_AND_CONFIDENCE_REVIEW_REQUIRED",),
        metadata={
            "bootstrap_semantics_version": bundle.ph.semantics_version,
            "bootstrap_status": bundle.status,
            "ph_delay_profile_not_inferred_from_gain_bootstrap": True,
        },
    )
    return DualResponseCalibrationProfile(
        profile_id=profile_id,
        condition_snapshot_version=bundle.condition_snapshot_version,
        mfac_context_id=bundle.mfac_context_id,
        so2=so2,
        ph=ph,
        activation_status="NOT_ACTIVATABLE",
        learning_review_status="REVIEW_REQUIRED",
        residual_review_status="REVIEW_REQUIRED",
        learning_enabled=False,
        residual_control_enabled=False,
        dcs_write_enabled=False,
        metadata={
            "source_dual_bootstrap_semantics": bundle.semantics_version,
            "same_physical_cohort": True,
            "local_gain_ready_is_not_full_calibration": True,
            "separate_activation_artifact_required": True,
        },
    )


__all__ = [
    "LEGACY_DUAL_RESPONSE_CALIBRATION_PROFILE_VERSION",
    "LEGACY_DUAL_RESPONSE_CALIBRATION_PROFILE_V2_VERSION",
    "DUAL_RESPONSE_CALIBRATION_PROFILE_VERSION",
    "CHANNEL_CALIBRATION_REVIEW_AUTHORITY_VERSION",
    "OBSERVED_RESPONSE_TIMING_SEMANTICS_VERSION",
    "CHANNEL_CONFIDENCE_EVIDENCE_SEMANTICS_VERSION",
    "CHANNEL_UNCONFIGURED",
    "CHANNEL_INSUFFICIENT_EVIDENCE",
    "CHANNEL_REVIEW_REQUIRED",
    "CHANNEL_LOCAL_GAIN_READY",
    "CHANNEL_CALIBRATED",
    "DualResponseChannelCalibration",
    "DualResponseCalibrationProfile",
    "build_calibration_profile_from_dual_bootstrap",
]

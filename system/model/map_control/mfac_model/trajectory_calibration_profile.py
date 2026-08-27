# -*- coding: utf-8 -*-
"""Audit-only calibration profiles for delayed pH rise, recovery and trajectory planning.

The profile separates three distinct physical questions:

* PendingDoseGuard: how long until a new delta-Q starts affecting pH and reaches
  its full step-response effect (onset/peak);
* pulse recovery: what happens after a later negative return step starts
  cancelling an earlier positive step (half-decay/recovery-to-baseline band);
* trajectory planning: how long to HOLD before observing enough SO2 response and
  what step magnitudes are supported.

No object in this module can activate runtime control or silently convert audit
evidence into production calibration.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any, Dict, Mapping, Optional


TRAJECTORY_CALIBRATION_PROFILE_VERSION = (
    "SCHEME2_TRAJECTORY_CALIBRATION_PROFILE_V2_RECOVERY_SEPARATED"
)
LEGACY_TRAJECTORY_CALIBRATION_PROFILE_VERSION = (
    "SCHEME2_TRAJECTORY_CALIBRATION_PROFILE_V1_AUDIT_ONLY"
)


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


@dataclass(frozen=True)
class CalibrationQuantiles:
    count: int
    p10: Optional[float] = None
    p25: Optional[float] = None
    p50: Optional[float] = None
    p75: Optional[float] = None
    p90: Optional[float] = None
    p95: Optional[float] = None
    unit: str = "seconds"

    def __post_init__(self) -> None:
        if int(self.count) < 0:
            raise ValueError("count must be >= 0")
        previous: Optional[float] = None
        for name in ("p10", "p25", "p50", "p75", "p90", "p95"):
            value = getattr(self, name)
            if value is None:
                continue
            parsed = _finite(value)
            if parsed is None:
                raise ValueError("%s must be finite when provided" % name)
            if previous is not None and parsed < previous:
                raise ValueError("quantiles must be monotonic")
            previous = parsed

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CalibrationQuantiles":
        payload = dict(value or {})
        return cls(**payload)


@dataclass(frozen=True)
class PendingDoseCalibrationCandidate:
    """Only onset/peak belong to pending future-effect prediction."""

    ph_onset_seconds: CalibrationQuantiles
    ph_peak_seconds: CalibrationQuantiles
    response_onset_candidate_seconds: Optional[float]
    response_peak_candidate_seconds: Optional[float]
    status: str = "REVIEW_REQUIRED"
    reason: str = "PENDING_RISE_TIMING_REQUIRES_REVIEW"

    def __post_init__(self) -> None:
        onset = _finite(self.response_onset_candidate_seconds)
        peak = _finite(self.response_peak_candidate_seconds)
        if self.response_onset_candidate_seconds is not None and (
            onset is None or onset < 0.0
        ):
            raise ValueError("response_onset_candidate_seconds must be >= 0")
        if self.response_peak_candidate_seconds is not None and (
            peak is None or peak <= 0.0
        ):
            raise ValueError("response_peak_candidate_seconds must be > 0")
        if onset is not None and peak is not None and peak <= onset:
            raise ValueError("pending response peak must be after onset")


@dataclass(frozen=True)
class PHRecoveryCalibrationAudit:
    """Pulse-recovery evidence; never a PendingDoseGuard response-memory input."""

    pulse_end_to_peak_seconds: CalibrationQuantiles
    peak_to_half_decay_seconds: CalibrationQuantiles
    pulse_end_to_half_decay_seconds: CalibrationQuantiles
    pulse_end_to_recovery_band_seconds: CalibrationQuantiles
    analyzed_event_count: int
    half_decay_observed_count: int
    recovery_observed_count: int
    recovery_band_above_baseline: float
    recovery_sustain_seconds: float
    quiet_time_review_candidate_seconds: Optional[float] = None
    status: str = "REVIEW_REQUIRED"
    reason: str = "PULSE_RECOVERY_IS_NOT_PENDING_STEP_MEMORY"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        total = int(self.analyzed_event_count)
        half = int(self.half_decay_observed_count)
        recovered = int(self.recovery_observed_count)
        if total < 0 or half < 0 or recovered < 0:
            raise ValueError("recovery event counts must be >= 0")
        if half > total or recovered > total:
            raise ValueError("recovery observed counts cannot exceed total")
        band = _finite(self.recovery_band_above_baseline)
        sustain = _finite(self.recovery_sustain_seconds)
        if band is None or band < 0.0:
            raise ValueError("recovery_band_above_baseline must be >= 0")
        if sustain is None or sustain <= 0.0:
            raise ValueError("recovery_sustain_seconds must be > 0")
        quiet = _finite(self.quiet_time_review_candidate_seconds)
        if self.quiet_time_review_candidate_seconds is not None and (
            quiet is None or quiet <= 0.0
        ):
            raise ValueError("quiet_time_review_candidate_seconds must be > 0")

    @property
    def half_decay_right_censored_ratio(self) -> float:
        if self.analyzed_event_count <= 0:
            return 1.0
        return 1.0 - float(self.half_decay_observed_count) / float(
            self.analyzed_event_count
        )

    @property
    def recovery_right_censored_ratio(self) -> float:
        if self.analyzed_event_count <= 0:
            return 1.0
        return 1.0 - float(self.recovery_observed_count) / float(
            self.analyzed_event_count
        )


@dataclass(frozen=True)
class TrajectoryPlannerCalibrationCandidate:
    so2_onset_seconds: CalibrationQuantiles
    so2_trough_seconds: CalibrationQuantiles
    min_hold_evidence_floor_seconds: Optional[float]
    min_hold_candidate_seconds: Optional[float]
    max_step_up_candidate: Optional[float] = None
    max_step_down_candidate: Optional[float] = None
    demand_deadband_candidate: Optional[float] = None
    status: str = "INSUFFICIENT_LOCAL_STEP_EVIDENCE"
    reason: str = "HISTORICAL_PULSES_DO_NOT_IDENTIFY_SAFE_LOCAL_STEP_SIZE"


@dataclass(frozen=True)
class HistoricalSafetyEvidenceSummary:
    event_count: int
    operating_ph_max: float
    safe_ph_max: float
    dose_risk_table: tuple[Dict[str, Any], ...] = ()
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if int(self.event_count) < 0:
            raise ValueError("event_count must be >= 0")
        operating = _finite(self.operating_ph_max)
        safe = _finite(self.safe_ph_max)
        if operating is None or safe is None or operating >= safe:
            raise ValueError("invalid pH upper envelope")


@dataclass(frozen=True)
class Scheme2TrajectoryCalibrationProfile:
    profile_id: str
    source_file: str
    source_sha256: str
    source_rows: int
    source_start_time: str
    source_end_time: str
    source_cadence_seconds: float
    extraction_method: str
    pump_segment_count: int
    clean_dynamic_candidate_count: int
    validated_dynamic_event_count: int
    pending_dose: PendingDoseCalibrationCandidate
    ph_recovery: PHRecoveryCalibrationAudit
    trajectory_planner: TrajectoryPlannerCalibrationCandidate
    safety: HistoricalSafetyEvidenceSummary
    local_gain_status: str = "INSUFFICIENT_EVIDENCE"
    activation_status: str = "NOT_ACTIVATABLE"
    review_status: str = "REVIEW_REQUIRED"
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = TRAJECTORY_CALIBRATION_PROFILE_VERSION

    def __post_init__(self) -> None:
        if not str(self.profile_id or "").strip():
            raise ValueError("profile_id is required")
        if int(self.source_rows) <= 0:
            raise ValueError("source_rows must be > 0")
        cadence = _finite(self.source_cadence_seconds)
        if cadence is None or cadence <= 0.0:
            raise ValueError("source_cadence_seconds must be > 0")
        if str(self.activation_status) != "NOT_ACTIVATABLE":
            raise ValueError("historical candidate profile must remain NOT_ACTIVATABLE")
        if str(self.semantics_version) not in {
            TRAJECTORY_CALIBRATION_PROFILE_VERSION,
            LEGACY_TRAJECTORY_CALIBRATION_PROFILE_VERSION,
        }:
            raise ValueError("unsupported trajectory calibration profile semantics")

    @staticmethod
    def _legacy_recovery(pending: Mapping[str, Any]) -> PHRecoveryCalibrationAudit:
        """Preserve V1 audit history without treating it as a runtime memory."""
        total = int(pending.get("memory_event_count", 0))
        half = int(pending.get("memory_half_decay_observed_count", 0))
        return PHRecoveryCalibrationAudit(
            pulse_end_to_peak_seconds=CalibrationQuantiles(count=0),
            peak_to_half_decay_seconds=CalibrationQuantiles(count=0),
            pulse_end_to_half_decay_seconds=CalibrationQuantiles(count=0),
            pulse_end_to_recovery_band_seconds=CalibrationQuantiles(count=0),
            analyzed_event_count=total,
            half_decay_observed_count=half,
            recovery_observed_count=0,
            recovery_band_above_baseline=0.05,
            recovery_sustain_seconds=120.0,
            quiet_time_review_candidate_seconds=None,
            status="LEGACY_AUDIT_ONLY",
            reason="V1_MEMORY_FIELDS_RETAINED_FOR_TRACEABILITY_NOT_PENDING_CONTROL",
            metadata={
                "legacy_memory_observation_window_seconds": pending.get(
                    "memory_observation_window_seconds"
                ),
                "legacy_response_memory_candidate_seconds": pending.get(
                    "response_memory_candidate_seconds"
                ),
                "legacy_response_memory_lower_bound_seconds": pending.get(
                    "response_memory_lower_bound_seconds"
                ),
                "legacy_memory_right_censored_ratio": pending.get(
                    "memory_right_censored_ratio"
                ),
            },
        )

    @classmethod
    def from_audit_mapping(
        cls,
        value: Mapping[str, Any],
    ) -> "Scheme2TrajectoryCalibrationProfile":
        payload = dict(value or {})
        source = dict(payload.get("source") or {})
        extraction = dict(payload.get("extraction") or {})
        timing = dict(payload.get("observed_timing_seconds") or {})
        pending = dict(payload.get("pending_dose_candidate") or {})
        recovery = dict(payload.get("ph_recovery_audit") or {})
        planner = dict(payload.get("trajectory_planner_candidate") or {})
        safety = dict(payload.get("safety") or {})
        semantics = str(
            payload.get("semantics_version")
            or TRAJECTORY_CALIBRATION_PROFILE_VERSION
        )

        ph_onset = CalibrationQuantiles.from_mapping(
            dict(timing.get("ph_turn_onset") or {})
        )
        ph_peak = CalibrationQuantiles.from_mapping(
            dict(timing.get("ph_peak") or {})
        )
        so2_onset = CalibrationQuantiles.from_mapping(
            dict(timing.get("so2_turn_onset") or {})
        )
        so2_trough = CalibrationQuantiles.from_mapping(
            dict(timing.get("so2_trough") or {})
        )

        pending_candidate = PendingDoseCalibrationCandidate(
            ph_onset_seconds=ph_onset,
            ph_peak_seconds=ph_peak,
            response_onset_candidate_seconds=pending.get(
                "response_onset_candidate_seconds"
            ),
            response_peak_candidate_seconds=pending.get(
                "response_peak_candidate_seconds"
            ),
            status=str(pending.get("status") or "REVIEW_REQUIRED"),
            reason=str(pending.get("reason") or ""),
        )

        if recovery:
            recovery_audit = PHRecoveryCalibrationAudit(
                pulse_end_to_peak_seconds=CalibrationQuantiles.from_mapping(
                    dict(recovery.get("pulse_end_to_peak_seconds") or {})
                ),
                peak_to_half_decay_seconds=CalibrationQuantiles.from_mapping(
                    dict(recovery.get("peak_to_half_decay_seconds") or {})
                ),
                pulse_end_to_half_decay_seconds=CalibrationQuantiles.from_mapping(
                    dict(recovery.get("pulse_end_to_half_decay_seconds") or {})
                ),
                pulse_end_to_recovery_band_seconds=CalibrationQuantiles.from_mapping(
                    dict(recovery.get("pulse_end_to_recovery_band_seconds") or {})
                ),
                analyzed_event_count=int(recovery.get("analyzed_event_count", 0)),
                half_decay_observed_count=int(
                    recovery.get("half_decay_observed_count", 0)
                ),
                recovery_observed_count=int(
                    recovery.get("recovery_observed_count", 0)
                ),
                recovery_band_above_baseline=float(
                    recovery.get("recovery_band_above_baseline", 0.05)
                ),
                recovery_sustain_seconds=float(
                    recovery.get("recovery_sustain_seconds", 120.0)
                ),
                quiet_time_review_candidate_seconds=recovery.get(
                    "quiet_time_review_candidate_seconds"
                ),
                status=str(recovery.get("status") or "REVIEW_REQUIRED"),
                reason=str(recovery.get("reason") or ""),
                metadata=dict(recovery.get("metadata") or {}),
            )
        else:
            recovery_audit = cls._legacy_recovery(pending)

        planner_candidate = TrajectoryPlannerCalibrationCandidate(
            so2_onset_seconds=so2_onset,
            so2_trough_seconds=so2_trough,
            min_hold_evidence_floor_seconds=planner.get(
                "min_hold_evidence_floor_seconds"
            ),
            min_hold_candidate_seconds=planner.get("min_hold_candidate_seconds"),
            max_step_up_candidate=planner.get("max_step_up_candidate"),
            max_step_down_candidate=planner.get("max_step_down_candidate"),
            demand_deadband_candidate=planner.get("demand_deadband_candidate"),
            status=str(
                planner.get("status") or "INSUFFICIENT_LOCAL_STEP_EVIDENCE"
            ),
            reason=str(planner.get("reason") or ""),
        )
        safety_summary = HistoricalSafetyEvidenceSummary(
            event_count=int(safety.get("validated_event_count", 0)),
            operating_ph_max=float(safety.get("operating_ph_max")),
            safe_ph_max=float(safety.get("safe_ph_max")),
            dose_risk_table=tuple(
                dict(item) for item in (safety.get("dose_risk_examples") or [])
            ),
            metadata={
                key: safety.get(key)
                for key in (
                    "p_ph_gt_6_4",
                    "p_ph_gt_6_8",
                    "p_ph_gt_6_9",
                    "p_ph_gt_7_0",
                )
                if key in safety
            },
        )
        return cls(
            profile_id=str(payload.get("profile_id") or ""),
            source_file=str(source.get("file") or ""),
            source_sha256=str(source.get("sha256") or ""),
            source_rows=int(source.get("rows", 0)),
            source_start_time=str(source.get("start_time") or ""),
            source_end_time=str(source.get("end_time") or ""),
            source_cadence_seconds=float(source.get("median_cadence_seconds")),
            extraction_method=str(extraction.get("method") or ""),
            pump_segment_count=int(extraction.get("pump_segment_count", 0)),
            clean_dynamic_candidate_count=int(
                extraction.get("clean_dynamic_candidate_count", 0)
            ),
            validated_dynamic_event_count=int(
                extraction.get("validated_dynamic_event_count", 0)
            ),
            pending_dose=pending_candidate,
            ph_recovery=recovery_audit,
            trajectory_planner=planner_candidate,
            safety=safety_summary,
            local_gain_status=str(
                payload.get("local_gain_status") or "INSUFFICIENT_EVIDENCE"
            ),
            activation_status=str(
                payload.get("activation_status") or "NOT_ACTIVATABLE"
            ),
            review_status=str(payload.get("review_status") or "REVIEW_REQUIRED"),
            metadata={
                "extraction": extraction,
                "permissions": dict(payload.get("permissions") or {}),
                "notes": list(payload.get("notes") or []),
                "actual_flow_reach": dict(timing.get("actual_flow_reach") or {}),
                "legacy_profile_loaded": semantics
                == LEGACY_TRAJECTORY_CALIBRATION_PROFILE_VERSION,
            },
            semantics_version=semantics,
        )

    @property
    def can_build_runtime_config(self) -> bool:
        return False

    def to_runtime_config(self) -> Dict[str, Any]:
        raise ValueError(
            "historical trajectory calibration profile is audit-only; "
            "review and separately approve runtime calibration first"
        )

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["ph_recovery"]["half_decay_right_censored_ratio"] = (
            self.ph_recovery.half_decay_right_censored_ratio
        )
        value["ph_recovery"]["recovery_right_censored_ratio"] = (
            self.ph_recovery.recovery_right_censored_ratio
        )
        return value


__all__ = [
    "TRAJECTORY_CALIBRATION_PROFILE_VERSION",
    "LEGACY_TRAJECTORY_CALIBRATION_PROFILE_VERSION",
    "CalibrationQuantiles",
    "PendingDoseCalibrationCandidate",
    "PHRecoveryCalibrationAudit",
    "TrajectoryPlannerCalibrationCandidate",
    "HistoricalSafetyEvidenceSummary",
    "Scheme2TrajectoryCalibrationProfile",
]

# -*- coding: utf-8 -*-
"""Audit-only calibration profiles for delayed pH memory and staircase planning.

The profile deliberately separates observed historical evidence from reviewed
runtime calibration. A profile produced by this module is NEVER activatable by
itself and cannot silently become ``PendingDoseGuardConfig`` or
``FlowTrajectoryPlannerConfig``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any, Dict, Mapping, Optional


TRAJECTORY_CALIBRATION_PROFILE_VERSION = (
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
    ph_onset_seconds: CalibrationQuantiles
    ph_peak_seconds: CalibrationQuantiles
    memory_observation_window_seconds: float
    memory_event_count: int
    memory_half_decay_observed_count: int
    memory_right_censored_ratio: float
    response_onset_candidate_seconds: Optional[float]
    response_peak_candidate_seconds: Optional[float]
    response_memory_candidate_seconds: Optional[float]
    response_memory_lower_bound_seconds: Optional[float]
    status: str = "REVIEW_REQUIRED"
    reason: str = "FULL_PH_MEMORY_NOT_IDENTIFIED"

    def __post_init__(self) -> None:
        if int(self.memory_event_count) < 0:
            raise ValueError("memory_event_count must be >= 0")
        observed = int(self.memory_half_decay_observed_count)
        if observed < 0 or observed > int(self.memory_event_count):
            raise ValueError("invalid memory_half_decay_observed_count")
        ratio = _finite(self.memory_right_censored_ratio)
        if ratio is None or not 0.0 <= ratio <= 1.0:
            raise ValueError("memory_right_censored_ratio must be within [0, 1]")
        window = _finite(self.memory_observation_window_seconds)
        if window is None or window <= 0.0:
            raise ValueError("memory_observation_window_seconds must be > 0")


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
            raise ValueError("V1 historical candidate profile must remain NOT_ACTIVATABLE")
        if str(self.semantics_version) != TRAJECTORY_CALIBRATION_PROFILE_VERSION:
            raise ValueError("unsupported trajectory calibration profile semantics")

    @classmethod
    def from_audit_mapping(
        cls,
        value: Mapping[str, Any],
    ) -> "Scheme2TrajectoryCalibrationProfile":
        """Validate one serialized historical audit artifact.

        This loader intentionally does not expose an activation conversion. It
        only turns the audit JSON into a typed, fail-closed object.
        """
        payload = dict(value or {})
        source = dict(payload.get("source") or {})
        extraction = dict(payload.get("extraction") or {})
        timing = dict(payload.get("observed_timing_seconds") or {})
        pending = dict(payload.get("pending_dose_candidate") or {})
        planner = dict(payload.get("trajectory_planner_candidate") or {})
        safety = dict(payload.get("safety") or {})

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
            memory_observation_window_seconds=pending.get(
                "memory_observation_window_seconds"
            ),
            memory_event_count=int(pending.get("memory_event_count", 0)),
            memory_half_decay_observed_count=int(
                pending.get("memory_half_decay_observed_count", 0)
            ),
            memory_right_censored_ratio=pending.get(
                "memory_right_censored_ratio"
            ),
            response_onset_candidate_seconds=pending.get(
                "response_onset_candidate_seconds"
            ),
            response_peak_candidate_seconds=pending.get(
                "response_peak_candidate_seconds"
            ),
            response_memory_candidate_seconds=pending.get(
                "response_memory_candidate_seconds"
            ),
            response_memory_lower_bound_seconds=pending.get(
                "response_memory_lower_bound_seconds"
            ),
            status=str(pending.get("status") or "REVIEW_REQUIRED"),
            reason=str(pending.get("reason") or ""),
        )
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
            },
            semantics_version=str(
                payload.get("semantics_version")
                or TRAJECTORY_CALIBRATION_PROFILE_VERSION
            ),
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
        return asdict(self)


__all__ = [
    "TRAJECTORY_CALIBRATION_PROFILE_VERSION",
    "CalibrationQuantiles",
    "PendingDoseCalibrationCandidate",
    "TrajectoryPlannerCalibrationCandidate",
    "HistoricalSafetyEvidenceSummary",
    "Scheme2TrajectoryCalibrationProfile",
]

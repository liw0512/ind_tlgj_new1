# -*- coding: utf-8 -*-
"""Audit-only calibration profiles for delayed pH memory and staircase planning.

The profile deliberately separates observed historical evidence from reviewed
runtime calibration.  A profile produced by this module is NEVER activatable by
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

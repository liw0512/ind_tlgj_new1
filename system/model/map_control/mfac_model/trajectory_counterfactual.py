# -*- coding: utf-8 -*-
"""Audit-only counterfactual contracts for pulse-vs-staircase comparison.

This module does not contain a learned process model. It defines candidate
trajectories, historical-support checks and the metrics that any future replay
model must report so human pulses and staircase candidates are compared on the
same basis without turning extrapolation into calibration evidence.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any, Dict, Optional, Sequence, Tuple


TRAJECTORY_COUNTERFACTUAL_VERSION = "SCHEME2_TRAJECTORY_COUNTERFACTUAL_V2_SUPPORT_GATED"


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


@dataclass(frozen=True)
class StaircaseStage:
    """One absolute extra-flow plateau above the pre-event baseline."""

    extra_flow_m3_h: float
    hold_seconds: float

    def __post_init__(self) -> None:
        flow = _finite(self.extra_flow_m3_h)
        hold = _finite(self.hold_seconds)
        if flow is None or flow < 0.0:
            raise ValueError("extra_flow_m3_h must be finite and >= 0")
        if hold is None or hold <= 0.0:
            raise ValueError("hold_seconds must be finite and > 0")

    @property
    def extra_volume_m3(self) -> float:
        return float(self.extra_flow_m3_h) * float(self.hold_seconds) / 3600.0


@dataclass(frozen=True)
class StaircaseTrajectoryCandidate:
    candidate_id: str
    advance_seconds: float
    stages: Tuple[StaircaseStage, ...]
    reference_extra_volume_m3: Optional[float] = None
    dose_match_tolerance_m3: Optional[float] = None
    source: str = "AUDIT_CANDIDATE"
    shadow_only: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = TRAJECTORY_COUNTERFACTUAL_VERSION

    def __post_init__(self) -> None:
        if not str(self.candidate_id or "").strip():
            raise ValueError("candidate_id is required")
        advance = _finite(self.advance_seconds)
        if advance is None or advance < 0.0:
            raise ValueError("advance_seconds must be finite and >= 0")
        if not self.stages:
            raise ValueError("at least one staircase stage is required")
        if not bool(self.shadow_only):
            raise ValueError("counterfactual trajectory candidates must remain shadow_only")
        reference = _finite(self.reference_extra_volume_m3)
        tolerance = _finite(self.dose_match_tolerance_m3)
        if self.reference_extra_volume_m3 is not None and (
            reference is None or reference < 0.0
        ):
            raise ValueError("reference_extra_volume_m3 must be >= 0")
        if self.dose_match_tolerance_m3 is not None and (
            tolerance is None or tolerance < 0.0
        ):
            raise ValueError("dose_match_tolerance_m3 must be >= 0")
        if reference is not None and tolerance is not None:
            if abs(self.total_extra_volume_m3 - reference) > tolerance:
                raise ValueError("candidate does not satisfy equal-dose audit constraint")

    @property
    def total_extra_volume_m3(self) -> float:
        return float(sum(stage.extra_volume_m3 for stage in self.stages))

    @property
    def peak_extra_flow_m3_h(self) -> float:
        return float(max(stage.extra_flow_m3_h for stage in self.stages))

    @property
    def total_duration_seconds(self) -> float:
        return float(sum(stage.hold_seconds for stage in self.stages))

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["total_extra_volume_m3"] = self.total_extra_volume_m3
        value["peak_extra_flow_m3_h"] = self.peak_extra_flow_m3_h
        value["total_duration_seconds"] = self.total_duration_seconds
        return value


@dataclass(frozen=True)
class HistoricalTrajectorySupport:
    sustained_flow_p05_m3_h: float
    sustained_flow_p95_m3_h: float
    action_duration_p05_seconds: float
    action_duration_p95_seconds: float
    max_observed_proactive_advance_seconds: float = 0.0
    source_event_count: int = 0
    source: str = "HISTORICAL_DYNAMIC_EVIDENCE"

    def __post_init__(self) -> None:
        flow_low = _finite(self.sustained_flow_p05_m3_h)
        flow_high = _finite(self.sustained_flow_p95_m3_h)
        duration_low = _finite(self.action_duration_p05_seconds)
        duration_high = _finite(self.action_duration_p95_seconds)
        advance = _finite(self.max_observed_proactive_advance_seconds)
        if flow_low is None or flow_high is None or flow_low >= flow_high:
            raise ValueError("invalid sustained-flow support")
        if duration_low is None or duration_high is None or duration_low >= duration_high:
            raise ValueError("invalid duration support")
        if advance is None or advance < 0.0:
            raise ValueError("max_observed_proactive_advance_seconds must be >= 0")
        if int(self.source_event_count) < 0:
            raise ValueError("source_event_count must be >= 0")


@dataclass(frozen=True)
class CounterfactualSupportAssessment:
    candidate_id: str
    stage_level_support_fraction: float
    sustained_level_supported: bool
    duration_supported: bool
    proactive_advance_supported: bool
    extrapolation_required: bool
    eligible_for_step_calibration_evidence: bool
    reasons: Tuple[str, ...] = ()
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = TRAJECTORY_COUNTERFACTUAL_VERSION

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["reasons"] = list(self.reasons)
        return value


def assess_historical_support(
    candidate: StaircaseTrajectoryCandidate,
    support: HistoricalTrajectorySupport,
) -> CounterfactualSupportAssessment:
    flow_low = float(support.sustained_flow_p05_m3_h)
    flow_high = float(support.sustained_flow_p95_m3_h)
    supported_stages = [
        flow_low <= float(stage.extra_flow_m3_h) <= flow_high
        for stage in candidate.stages
    ]
    stage_fraction = float(sum(supported_stages) / len(supported_stages))
    sustained_supported = bool(all(supported_stages))
    duration = float(candidate.total_duration_seconds)
    duration_supported = bool(
        float(support.action_duration_p05_seconds)
        <= duration
        <= float(support.action_duration_p95_seconds)
    )
    advance_supported = bool(
        float(candidate.advance_seconds)
        <= float(support.max_observed_proactive_advance_seconds)
    )
    reasons = []
    if not sustained_supported:
        reasons.append("SUSTAINED_FLOW_LEVEL_OUT_OF_HISTORICAL_SUPPORT")
    if not duration_supported:
        reasons.append("TOTAL_DURATION_OUT_OF_HISTORICAL_SUPPORT")
    if not advance_supported:
        reasons.append("PROACTIVE_ADVANCE_OUT_OF_HISTORICAL_SUPPORT")
    extrapolation = bool(reasons)
    return CounterfactualSupportAssessment(
        candidate_id=candidate.candidate_id,
        stage_level_support_fraction=stage_fraction,
        sustained_level_supported=sustained_supported,
        duration_supported=duration_supported,
        proactive_advance_supported=advance_supported,
        extrapolation_required=extrapolation,
        eligible_for_step_calibration_evidence=not extrapolation,
        reasons=tuple(reasons),
        metadata={
            "sustained_flow_support_m3_h": [flow_low, flow_high],
            "duration_support_seconds": [
                float(support.action_duration_p05_seconds),
                float(support.action_duration_p95_seconds),
            ],
            "max_observed_proactive_advance_seconds": float(
                support.max_observed_proactive_advance_seconds
            ),
            "support_source_event_count": int(support.source_event_count),
        },
    )


@dataclass(frozen=True)
class TrajectoryCounterfactualMetrics:
    candidate_id: str
    model_id: str
    outlet_so2_peak: Optional[float]
    outlet_so2_exceedance_seconds: Optional[float]
    outlet_so2_integral_error: Optional[float]
    ph_peak: Optional[float]
    ph_over_operating_max_seconds: Optional[float]
    ph_over_safe_max_seconds: Optional[float]
    max_supply_flow_m3_h: Optional[float]
    total_extra_volume_m3: Optional[float]
    valid: bool
    invalid_reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = TRAJECTORY_COUNTERFACTUAL_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TrajectoryCounterfactualComparison:
    reference_id: str
    candidate_metrics: Tuple[TrajectoryCounterfactualMetrics, ...]
    status: str = "AUDIT_ONLY"
    activatable: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = TRAJECTORY_COUNTERFACTUAL_VERSION

    def __post_init__(self) -> None:
        if bool(self.activatable):
            raise ValueError("counterfactual comparison cannot activate runtime control")

    def ranked_valid_candidates(self) -> Tuple[TrajectoryCounterfactualMetrics, ...]:
        """Return deterministic safety-first ranking without claiming causality."""
        def key(item: TrajectoryCounterfactualMetrics):
            def value(number: Optional[float]) -> float:
                parsed = _finite(number)
                return parsed if parsed is not None else float("inf")
            return (
                value(item.ph_over_safe_max_seconds),
                value(item.ph_over_operating_max_seconds),
                value(item.outlet_so2_exceedance_seconds),
                value(item.total_extra_volume_m3),
                str(item.candidate_id),
            )

        return tuple(sorted((item for item in self.candidate_metrics if item.valid), key=key))


def build_equal_dose_candidate(
    candidate_id: str,
    levels_m3_h: Sequence[float],
    hold_seconds: Sequence[float] | float,
    *,
    reference_extra_volume_m3: float,
    advance_seconds: float,
    dose_match_tolerance_m3: float = 0.05,
    metadata: Optional[Dict[str, Any]] = None,
) -> StaircaseTrajectoryCandidate:
    if isinstance(hold_seconds, (int, float)):
        holds = [float(hold_seconds)] * len(levels_m3_h)
    else:
        holds = list(hold_seconds)
    if len(levels_m3_h) != len(holds):
        raise ValueError("levels_m3_h and hold_seconds must have the same length")
    stages = tuple(
        StaircaseStage(extra_flow_m3_h=float(level), hold_seconds=float(hold))
        for level, hold in zip(levels_m3_h, holds)
    )
    return StaircaseTrajectoryCandidate(
        candidate_id=str(candidate_id),
        advance_seconds=float(advance_seconds),
        stages=stages,
        reference_extra_volume_m3=float(reference_extra_volume_m3),
        dose_match_tolerance_m3=float(dose_match_tolerance_m3),
        metadata=dict(metadata or {}),
    )


__all__ = [
    "TRAJECTORY_COUNTERFACTUAL_VERSION",
    "StaircaseStage",
    "StaircaseTrajectoryCandidate",
    "HistoricalTrajectorySupport",
    "CounterfactualSupportAssessment",
    "assess_historical_support",
    "TrajectoryCounterfactualMetrics",
    "TrajectoryCounterfactualComparison",
    "build_equal_dose_candidate",
]

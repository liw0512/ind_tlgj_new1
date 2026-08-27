# -*- coding: utf-8 -*-
"""Observed response-timing extraction from raw manual local-step traces.

Configured response-window boundaries are not process observations.  This module
extracts channel timing from raw, quality-screened process traces anchored to the
real ``actual_flow_reached_time``.  All extraction thresholds are explicit review
inputs; there are no plant defaults.

The extractor is offline/review-only.  It does not issue commands, write DCS,
update online phi, or activate runtime control.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
import math
from statistics import median
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .channel_calibration_review import ObservedResponseTimingEvidence
from .mfac_schema import DelayProfile


OBSERVED_TIMING_EXTRACTOR_VERSION = (
    "SCHEME2_OBSERVED_TIMING_EXTRACTOR_V1_RAW_PROCESS_TRACE"
)
TRACE_SOURCE_MANUAL_LOCAL_STEP_RAW_PROCESS = "MANUAL_LOCAL_STEP_RAW_PROCESS_TRACE"
RESPONSE_TIMING_DEFINITION = "FIRST_SUSTAINED_REVIEWED_FRACTION_OF_OBSERVED_EXTREMUM"
SMOOTHING_DEFINITION = "TRAILING_MEDIAN"


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if hasattr(value, "to_pydatetime"):
        converted = value.to_pydatetime()
        if isinstance(converted, datetime):
            return converted
    text = str(value or "").strip()
    if not text:
        raise ValueError("timestamp is required")
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid ISO timestamp") from exc


def _quantile(values: Sequence[float], q: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("quantile requires values")
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * float(q)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


@dataclass(frozen=True)
class ObservedTraceSample:
    timestamp: str
    value: float
    data_quality_ok: bool = True

    def __post_init__(self) -> None:
        _timestamp(self.timestamp)
        if _finite(self.value) is None:
            raise ValueError("trace sample value must be finite")

    @property
    def timestamp_value(self) -> datetime:
        return _timestamp(self.timestamp)


@dataclass(frozen=True)
class ObservedProcessTrace:
    trace_id: str
    event_id: str
    trial_id: str
    channel: str
    condition_snapshot_version: str
    mfac_context_id: str
    actual_flow_reached_time: str
    samples: Tuple[ObservedTraceSample, ...]
    source: str = TRACE_SOURCE_MANUAL_LOCAL_STEP_RAW_PROCESS
    configured_window_boundary_used_as_observed_timing: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        channel = str(self.channel or "").upper()
        if channel not in {"SO2", "PH"}:
            raise ValueError("trace channel must be SO2 or PH")
        for name in (
            "trace_id",
            "event_id",
            "trial_id",
            "condition_snapshot_version",
            "mfac_context_id",
        ):
            if not str(getattr(self, name) or "").strip():
                raise ValueError("%s is required" % name)
        _timestamp(self.actual_flow_reached_time)
        if self.source != TRACE_SOURCE_MANUAL_LOCAL_STEP_RAW_PROCESS:
            raise ValueError("observed timing trace must be a manual local-step raw trace")
        if self.configured_window_boundary_used_as_observed_timing:
            raise ValueError("configured window boundaries cannot be raw timing evidence")
        if not self.samples:
            raise ValueError("observed trace requires samples")
        times = [sample.timestamp_value for sample in self.samples]
        for left, right in zip(times, times[1:]):
            try:
                delta = (right - left).total_seconds()
            except TypeError as exc:
                raise ValueError("trace timestamps must use consistent timezone semantics") from exc
            if delta <= 0.0:
                raise ValueError("trace sample timestamps must be strictly increasing")


@dataclass(frozen=True)
class ObservedTimingExtractionConfig:
    baseline_window_seconds: float
    max_observation_seconds: float
    max_sample_gap_seconds: float
    smoothing_window_samples: int
    onset_abs_threshold: float
    onset_sustain_samples: int
    response_fraction_of_extremum: float
    response_sustain_samples: int
    min_response_abs_amplitude: float
    min_baseline_samples: int
    min_post_reach_samples: int

    def __post_init__(self) -> None:
        positive = (
            "baseline_window_seconds",
            "max_observation_seconds",
            "max_sample_gap_seconds",
            "onset_abs_threshold",
            "min_response_abs_amplitude",
        )
        for name in positive:
            value = _finite(getattr(self, name))
            if value is None or value <= 0.0:
                raise ValueError("%s must be finite and > 0" % name)
        for name in (
            "smoothing_window_samples",
            "onset_sustain_samples",
            "response_sustain_samples",
            "min_baseline_samples",
            "min_post_reach_samples",
        ):
            if int(getattr(self, name)) <= 0:
                raise ValueError("%s must be > 0" % name)
        fraction = _finite(self.response_fraction_of_extremum)
        if fraction is None or not (0.0 < fraction <= 1.0):
            raise ValueError("response_fraction_of_extremum must be within (0, 1]")


@dataclass(frozen=True)
class ObservedTimingEventResult:
    trace_id: str
    event_id: str
    trial_id: str
    channel: str
    status: str
    actual_flow_reached_time: str
    baseline_value: Optional[float] = None
    observed_onset_seconds: Optional[float] = None
    observed_response_seconds: Optional[float] = None
    observed_directional_extremum: Optional[float] = None
    response_threshold_value: Optional[float] = None
    baseline_sample_count: int = 0
    post_reach_sample_count: int = 0
    reasons: Tuple[str, ...] = ()
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = OBSERVED_TIMING_EXTRACTOR_VERSION

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["reasons"] = list(self.reasons)
        return value


@dataclass(frozen=True)
class ObservedTimingExtractionResult:
    channel: str
    condition_snapshot_version: str
    mfac_context_id: str
    event_results: Tuple[ObservedTimingEventResult, ...]
    timing_evidence: Optional[ObservedResponseTimingEvidence]
    status: str
    reasons: Tuple[str, ...] = ()
    semantics_version: str = OBSERVED_TIMING_EXTRACTOR_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "channel": self.channel,
            "condition_snapshot_version": self.condition_snapshot_version,
            "mfac_context_id": self.mfac_context_id,
            "event_results": [item.to_dict() for item in self.event_results],
            "timing_evidence": (
                self.timing_evidence.to_dict() if self.timing_evidence is not None else None
            ),
            "status": self.status,
            "reasons": list(self.reasons),
            "semantics_version": self.semantics_version,
        }


def _direction(channel: str) -> float:
    return -1.0 if str(channel).upper() == "SO2" else 1.0


def _trailing_median(samples: Sequence[ObservedTraceSample], window: int) -> List[Tuple[datetime, float]]:
    output: List[Tuple[datetime, float]] = []
    width = int(window)
    values: List[float] = []
    for sample in samples:
        values.append(float(sample.value))
        current = values[max(0, len(values) - width):]
        output.append((sample.timestamp_value, float(median(current))))
    return output


def _first_sustained(
    values: Sequence[Tuple[datetime, float]],
    *,
    threshold: float,
    sustain_samples: int,
) -> Optional[int]:
    needed = int(sustain_samples)
    for start in range(0, len(values) - needed + 1):
        if all(float(values[index][1]) >= float(threshold) for index in range(start, start + needed)):
            return start
    return None


def extract_observed_timing_from_trace(
    trace: ObservedProcessTrace,
    config: ObservedTimingExtractionConfig,
) -> ObservedTimingEventResult:
    """Extract one event's observed onset and response timing from raw trace data."""

    reached = _timestamp(trace.actual_flow_reached_time)
    baseline_start = reached - timedelta(seconds=float(config.baseline_window_seconds))
    observation_end = reached + timedelta(seconds=float(config.max_observation_seconds))
    relevant = [
        sample
        for sample in trace.samples
        if baseline_start <= sample.timestamp_value <= observation_end
        and sample.data_quality_ok
    ]
    reasons: List[str] = []
    if not relevant:
        reasons.append("NO_QUALITY_SAMPLES")
    for left, right in zip(relevant, relevant[1:]):
        gap = (right.timestamp_value - left.timestamp_value).total_seconds()
        if gap > float(config.max_sample_gap_seconds):
            reasons.append("SAMPLE_GAP")
            break

    baseline = [sample for sample in relevant if baseline_start <= sample.timestamp_value <= reached]
    post = [sample for sample in relevant if reached < sample.timestamp_value <= observation_end]
    if len(baseline) < int(config.min_baseline_samples):
        reasons.append("INSUFFICIENT_BASELINE_SAMPLES")
    if len(post) < int(config.min_post_reach_samples):
        reasons.append("INSUFFICIENT_POST_REACH_SAMPLES")
    if reasons:
        return ObservedTimingEventResult(
            trace_id=trace.trace_id,
            event_id=trace.event_id,
            trial_id=trace.trial_id,
            channel=str(trace.channel).upper(),
            status="REJECTED",
            actual_flow_reached_time=trace.actual_flow_reached_time,
            baseline_sample_count=len(baseline),
            post_reach_sample_count=len(post),
            reasons=tuple(dict.fromkeys(reasons)),
        )

    baseline_value = float(median([float(sample.value) for sample in baseline]))
    sign = _direction(trace.channel)
    smoothed = _trailing_median(post, int(config.smoothing_window_samples))
    directional = [
        (timestamp, sign * (value - baseline_value))
        for timestamp, value in smoothed
    ]

    onset_index = _first_sustained(
        directional,
        threshold=float(config.onset_abs_threshold),
        sustain_samples=int(config.onset_sustain_samples),
    )
    if onset_index is None:
        reasons.append("OBSERVED_ONSET_NOT_FOUND")

    extremum = max((value for _, value in directional), default=float("-inf"))
    if not math.isfinite(extremum) or extremum < float(config.min_response_abs_amplitude):
        reasons.append("RESPONSE_AMPLITUDE_BELOW_REVIEWED_MINIMUM")

    response_index: Optional[int] = None
    response_threshold = None
    if not reasons:
        response_threshold = float(extremum) * float(config.response_fraction_of_extremum)
        response_index = _first_sustained(
            directional[onset_index:],
            threshold=response_threshold,
            sustain_samples=int(config.response_sustain_samples),
        )
        if response_index is None:
            reasons.append("OBSERVED_RESPONSE_TIMING_NOT_FOUND")
        else:
            response_index += int(onset_index)

    if reasons:
        return ObservedTimingEventResult(
            trace_id=trace.trace_id,
            event_id=trace.event_id,
            trial_id=trace.trial_id,
            channel=str(trace.channel).upper(),
            status="REJECTED",
            actual_flow_reached_time=trace.actual_flow_reached_time,
            baseline_value=baseline_value,
            observed_directional_extremum=(extremum if math.isfinite(extremum) else None),
            response_threshold_value=response_threshold,
            baseline_sample_count=len(baseline),
            post_reach_sample_count=len(post),
            reasons=tuple(dict.fromkeys(reasons)),
        )

    onset_time = directional[int(onset_index)][0]
    response_time = directional[int(response_index)][0]
    onset_seconds = (onset_time - reached).total_seconds()
    response_seconds = (response_time - reached).total_seconds()
    if onset_seconds < 0.0 or response_seconds < onset_seconds:
        raise ValueError("extracted observed timing is physically invalid")

    return ObservedTimingEventResult(
        trace_id=trace.trace_id,
        event_id=trace.event_id,
        trial_id=trace.trial_id,
        channel=str(trace.channel).upper(),
        status="EXTRACTED",
        actual_flow_reached_time=trace.actual_flow_reached_time,
        baseline_value=baseline_value,
        observed_onset_seconds=float(onset_seconds),
        observed_response_seconds=float(response_seconds),
        observed_directional_extremum=float(extremum),
        response_threshold_value=float(response_threshold),
        baseline_sample_count=len(baseline),
        post_reach_sample_count=len(post),
        reasons=(),
        metadata={
            "trace_source": trace.source,
            "smoothing_definition": SMOOTHING_DEFINITION,
            "response_timing_definition": RESPONSE_TIMING_DEFINITION,
            "configured_window_boundary_used_as_observed_timing": False,
            "automatic_online_adaptation_allowed": False,
            "normal_runtime_activation_allowed": False,
        },
    )


def build_observed_response_timing_evidence(
    traces: Iterable[ObservedProcessTrace],
    *,
    config: ObservedTimingExtractionConfig,
    evidence_id: str,
) -> ObservedTimingExtractionResult:
    """Aggregate same-channel raw traces into reviewed-timing *candidate* evidence."""

    supplied = list(traces)
    if not supplied:
        raise ValueError("at least one observed process trace is required")
    channels = {str(trace.channel or "").upper() for trace in supplied}
    snapshots = {trace.condition_snapshot_version for trace in supplied}
    contexts = {trace.mfac_context_id for trace in supplied}
    if len(channels) != 1:
        raise ValueError("timing extraction requires one channel at a time")
    if len(snapshots) != 1 or len(contexts) != 1:
        raise ValueError("timing extraction cannot mix condition/context")
    event_ids = [trace.event_id for trace in supplied]
    if len(set(event_ids)) != len(event_ids):
        raise ValueError("timing extraction cannot contain duplicate event IDs")

    results = tuple(extract_observed_timing_from_trace(trace, config) for trace in supplied)
    accepted = [item for item in results if item.status == "EXTRACTED"]
    channel = next(iter(channels))
    snapshot = next(iter(snapshots))
    context = next(iter(contexts))
    reasons: List[str] = []
    if len(accepted) < 2:
        reasons.append("INSUFFICIENT_EXTRACTED_TIMING_EVENTS")
        return ObservedTimingExtractionResult(
            channel=channel,
            condition_snapshot_version=snapshot,
            mfac_context_id=context,
            event_results=results,
            timing_evidence=None,
            status="INSUFFICIENT_EVIDENCE",
            reasons=tuple(reasons),
        )

    onset_values = [float(item.observed_onset_seconds) for item in accepted]
    response_values = [float(item.observed_response_seconds) for item in accepted]
    days = {
        _timestamp(item.actual_flow_reached_time).date().isoformat()
        for item in accepted
    }
    evidence = ObservedResponseTimingEvidence(
        evidence_id=str(evidence_id or "").strip(),
        channel=channel,
        condition_snapshot_version=snapshot,
        mfac_context_id=context,
        delay_profile=DelayProfile(
            onset_p50_seconds=_quantile(onset_values, 0.50),
            onset_p90_seconds=_quantile(onset_values, 0.90),
            response_p50_seconds=_quantile(response_values, 0.50),
            response_p90_seconds=_quantile(response_values, 0.90),
        ),
        event_ids=tuple(item.event_id for item in accepted),
        observed_event_count=len(accepted),
        independent_days=len(days),
        configured_window_boundary_used_as_observed_timing=False,
        metadata={
            "extractor_semantics": OBSERVED_TIMING_EXTRACTOR_VERSION,
            "trace_source": TRACE_SOURCE_MANUAL_LOCAL_STEP_RAW_PROCESS,
            "smoothing_definition": SMOOTHING_DEFINITION,
            "response_timing_definition": RESPONSE_TIMING_DEFINITION,
            "extraction_config": asdict(config),
            "rejected_trace_ids": [
                item.trace_id for item in results if item.status != "EXTRACTED"
            ],
            "review_candidate_only": True,
            "human_channel_calibration_review_required": True,
            "automatic_online_adaptation_allowed": False,
            "normal_runtime_activation_allowed": False,
        },
    )
    return ObservedTimingExtractionResult(
        channel=channel,
        condition_snapshot_version=snapshot,
        mfac_context_id=context,
        event_results=results,
        timing_evidence=evidence,
        status="OBSERVED_TIMING_REVIEW_CANDIDATE",
        reasons=(),
    )


__all__ = [
    "OBSERVED_TIMING_EXTRACTOR_VERSION",
    "TRACE_SOURCE_MANUAL_LOCAL_STEP_RAW_PROCESS",
    "RESPONSE_TIMING_DEFINITION",
    "SMOOTHING_DEFINITION",
    "ObservedTraceSample",
    "ObservedProcessTrace",
    "ObservedTimingExtractionConfig",
    "ObservedTimingEventResult",
    "ObservedTimingExtractionResult",
    "extract_observed_timing_from_trace",
    "build_observed_response_timing_evidence",
]

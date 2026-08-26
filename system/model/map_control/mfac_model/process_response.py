# -*- coding: utf-8 -*-
"""Observe SO2 process response after actual slurry flow has reached target.

This monitor intentionally starts from ``actual_flow_reached_time`` rather than
algorithm command time.  It produces auditable process-response evidence only;
learning eligibility and online ``phi`` adaptation remain separate stages.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
import math
from statistics import median
from typing import Any, Dict, List, Optional

from .continuous_target import COUNTERFACTUAL_SHADOW
from .supply_flow_tracking import SupplyFlowTrackingEvent


PROCESS_RESPONSE_SEMANTICS_VERSION = "SCHEME2_PROCESS_RESPONSE_V1"


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
        raise ValueError(f"invalid timestamp: {text}") from exc


def _time_text(value: datetime) -> str:
    return value.isoformat()


def _median(values: List[Optional[float]]) -> Optional[float]:
    finite = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return float(median(finite)) if finite else None


@dataclass(frozen=True)
class ProcessResponseConfig:
    """Response-window settings that must be calibrated from plant evidence."""

    baseline_window_seconds: float
    delay_onset_seconds: float
    observation_seconds: float
    measurement_window_seconds: float
    max_sample_gap_seconds: float
    target_change_tolerance: float
    min_baseline_samples: int
    min_response_samples: int

    def __post_init__(self) -> None:
        numeric = {
            "baseline_window_seconds": self.baseline_window_seconds,
            "delay_onset_seconds": self.delay_onset_seconds,
            "observation_seconds": self.observation_seconds,
            "measurement_window_seconds": self.measurement_window_seconds,
            "max_sample_gap_seconds": self.max_sample_gap_seconds,
            "target_change_tolerance": self.target_change_tolerance,
        }
        for name, value in numeric.items():
            number = _finite(value)
            if number is None or number < 0.0:
                raise ValueError(f"{name} must be finite and >= 0")
        if float(self.baseline_window_seconds) <= 0.0:
            raise ValueError("baseline_window_seconds must be > 0")
        if float(self.observation_seconds) <= 0.0:
            raise ValueError("observation_seconds must be > 0")
        if float(self.measurement_window_seconds) <= 0.0:
            raise ValueError("measurement_window_seconds must be > 0")
        if float(self.measurement_window_seconds) > float(self.observation_seconds):
            raise ValueError("measurement_window_seconds cannot exceed observation_seconds")
        if float(self.max_sample_gap_seconds) <= 0.0:
            raise ValueError("max_sample_gap_seconds must be > 0")
        if int(self.min_baseline_samples) <= 0 or int(self.min_response_samples) <= 0:
            raise ValueError("minimum sample counts must be > 0")


@dataclass
class ProcessSample:
    timestamp: str
    outlet_so2: Optional[float]
    inlet_so2: Optional[float] = None
    qbase_effective: Optional[float] = None
    ph: Optional[float] = None
    so2_target: Optional[float] = None
    actual_supply_flow_feedback: Optional[float] = None
    condition_snapshot_version: str = ""
    mfac_context_id: str = ""
    fast_active: bool = False
    data_quality_ok: bool = True

    @property
    def timestamp_value(self) -> datetime:
        return _timestamp(self.timestamp)


@dataclass
class ProcessResponseEvent:
    response_event_id: str
    tracking_event_id: str
    status: str
    condition_snapshot_version: str
    mfac_context_id: str
    target_change_time: str
    actual_flow_reached_time: str
    response_start_time: str
    response_end_time: str
    q_before: Optional[float] = None
    q_after: Optional[float] = None
    delta_q_actual: Optional[float] = None
    so2_target: Optional[float] = None
    so2_before: Optional[float] = None
    so2_after: Optional[float] = None
    delta_so2: Optional[float] = None
    qbase_before: Optional[float] = None
    qbase_after: Optional[float] = None
    qbase_drift: Optional[float] = None
    inlet_so2_before: Optional[float] = None
    inlet_so2_after: Optional[float] = None
    inlet_so2_change: Optional[float] = None
    ph_before: Optional[float] = None
    ph_after: Optional[float] = None
    delta_ph: Optional[float] = None
    actual_supply_flow_response: Optional[float] = None
    fast_overlap: bool = False
    condition_changed: bool = False
    target_changed: bool = False
    data_quality_ok: bool = True
    censor_reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = PROCESS_RESPONSE_SEMANTICS_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ProcessResponseUpdate:
    emitted_events: List[ProcessResponseEvent] = field(default_factory=list)
    active_tracking_event_id: str = ""


@dataclass
class _ActiveResponse:
    tracking_event: SupplyFlowTrackingEvent
    condition_snapshot_version: str
    mfac_context_id: str
    so2_target: Optional[float]
    reached_time: datetime
    response_start: datetime
    response_end: datetime
    baseline_samples: List[ProcessSample]


class ProcessResponseMonitor:
    """Stateful SO2 response observer anchored to real flow reach time."""

    def __init__(self, config: ProcessResponseConfig) -> None:
        self.config = config
        self._samples: List[ProcessSample] = []
        self._active: Optional[_ActiveResponse] = None
        self._sequence = 0
        self._last_sample_time: Optional[datetime] = None

    @property
    def active_tracking_event_id(self) -> str:
        if self._active is None:
            return ""
        return self._active.tracking_event.tracking_event_id

    def update(
        self,
        *,
        timestamp: Any,
        outlet_so2: Any,
        inlet_so2: Any = None,
        qbase_effective: Any = None,
        ph: Any = None,
        so2_target: Any = None,
        actual_supply_flow_feedback: Any = None,
        condition_snapshot_version: str = "",
        mfac_context_id: str = "",
        fast_active: bool = False,
        data_quality_ok: bool = True,
        reached_event: Optional[SupplyFlowTrackingEvent] = None,
    ) -> ProcessResponseUpdate:
        now = _timestamp(timestamp)
        emitted: List[ProcessResponseEvent] = []

        if self._active is not None and self._last_sample_time is not None:
            gap = self._elapsed_seconds(self._last_sample_time, now)
            if gap > float(self.config.max_sample_gap_seconds):
                emitted.append(self._censor_active("SAMPLE_GAP", now))

        sample = ProcessSample(
            timestamp=_time_text(now),
            outlet_so2=_finite(outlet_so2),
            inlet_so2=_finite(inlet_so2),
            qbase_effective=_finite(qbase_effective),
            ph=_finite(ph),
            so2_target=_finite(so2_target),
            actual_supply_flow_feedback=_finite(actual_supply_flow_feedback),
            condition_snapshot_version=str(condition_snapshot_version or ""),
            mfac_context_id=str(mfac_context_id or ""),
            fast_active=bool(fast_active),
            data_quality_ok=bool(data_quality_ok),
        )
        self._samples.append(sample)
        self._prune(now)

        if reached_event is not None:
            if self._active is not None:
                emitted.append(self._censor_active("SUPERSEDED_BY_NEW_REACHED_EVENT", now))
            start_result = self._start_from_reached_event(reached_event, sample)
            if isinstance(start_result, ProcessResponseEvent):
                emitted.append(start_result)
            else:
                self._active = start_result

        if self._active is not None:
            reason = self._censor_reason(sample)
            if reason:
                emitted.append(self._censor_active(reason, now))
            elif now >= self._active.response_end:
                emitted.append(self._complete_active(now))

        self._last_sample_time = now
        return ProcessResponseUpdate(
            emitted_events=emitted,
            active_tracking_event_id=self.active_tracking_event_id,
        )

    def _start_from_reached_event(
        self,
        event: SupplyFlowTrackingEvent,
        sample: ProcessSample,
    ) -> _ActiveResponse | ProcessResponseEvent:
        if event.status != "REACHED":
            raise ValueError("ProcessResponseMonitor requires a REACHED tracking event")
        if not event.target_was_applied:
            raise ValueError("REACHED tracking event must have target_was_applied=true")
        if event.replay_semantics == COUNTERFACTUAL_SHADOW:
            raise ValueError("counterfactual history cannot start causal process response")
        if not event.actual_flow_reached_time:
            raise ValueError("REACHED tracking event is missing actual_flow_reached_time")

        reached_time = _timestamp(event.actual_flow_reached_time)
        baseline_start = reached_time - timedelta(seconds=float(self.config.baseline_window_seconds))
        baseline = [
            item
            for item in self._samples
            if baseline_start <= item.timestamp_value <= reached_time
            and item.data_quality_ok
            and item.outlet_so2 is not None
        ]
        response_start = reached_time + timedelta(seconds=float(self.config.delay_onset_seconds))
        response_end = response_start + timedelta(seconds=float(self.config.observation_seconds))

        if len(baseline) < int(self.config.min_baseline_samples):
            return self._build_event(
                event=event,
                condition_snapshot_version=sample.condition_snapshot_version,
                mfac_context_id=sample.mfac_context_id,
                so2_target=sample.so2_target,
                response_start=response_start,
                response_end=response_end,
                baseline=baseline,
                response=[],
                status="INSUFFICIENT_BASELINE",
                censor_reason="INSUFFICIENT_BASELINE_SAMPLES",
            )

        return _ActiveResponse(
            tracking_event=event,
            condition_snapshot_version=sample.condition_snapshot_version,
            mfac_context_id=sample.mfac_context_id,
            so2_target=sample.so2_target,
            reached_time=reached_time,
            response_start=response_start,
            response_end=response_end,
            baseline_samples=baseline,
        )

    def _censor_reason(self, sample: ProcessSample) -> str:
        active = self._active
        if active is None:
            return ""
        if not sample.data_quality_ok or sample.outlet_so2 is None:
            return "DATA_QUALITY_INVALID"
        if sample.fast_active:
            return "FAST_OVERLAP"
        if sample.condition_snapshot_version != active.condition_snapshot_version:
            return "CONDITION_SNAPSHOT_CHANGED"
        if sample.mfac_context_id != active.mfac_context_id:
            return "MFAC_CONTEXT_CHANGED"
        if self._target_changed(active.so2_target, sample.so2_target):
            return "SO2_TARGET_CHANGED"
        return ""

    def _target_changed(
        self,
        before: Optional[float],
        current: Optional[float],
    ) -> bool:
        if before is None and current is None:
            return False
        if before is None or current is None:
            return True
        return abs(current - before) > float(self.config.target_change_tolerance)

    def _complete_active(self, now: datetime) -> ProcessResponseEvent:
        active = self._active
        if active is None:
            raise RuntimeError("no active process response")
        measurement_start = active.response_end - timedelta(
            seconds=float(self.config.measurement_window_seconds)
        )
        response = [
            item
            for item in self._samples
            if measurement_start <= item.timestamp_value <= active.response_end
            and item.data_quality_ok
            and item.outlet_so2 is not None
        ]
        if len(response) < int(self.config.min_response_samples):
            event = self._build_event(
                event=active.tracking_event,
                condition_snapshot_version=active.condition_snapshot_version,
                mfac_context_id=active.mfac_context_id,
                so2_target=active.so2_target,
                response_start=active.response_start,
                response_end=active.response_end,
                baseline=active.baseline_samples,
                response=response,
                status="INSUFFICIENT_RESPONSE_DATA",
                censor_reason="INSUFFICIENT_RESPONSE_SAMPLES",
            )
        else:
            event = self._build_event(
                event=active.tracking_event,
                condition_snapshot_version=active.condition_snapshot_version,
                mfac_context_id=active.mfac_context_id,
                so2_target=active.so2_target,
                response_start=active.response_start,
                response_end=active.response_end,
                baseline=active.baseline_samples,
                response=response,
                status="COMPLETED",
                censor_reason="",
            )
            event.metadata["completed_at"] = _time_text(now)
        self._active = None
        return event

    def _censor_active(self, reason: str, now: datetime) -> ProcessResponseEvent:
        active = self._active
        if active is None:
            raise RuntimeError("no active process response")
        event = self._build_event(
            event=active.tracking_event,
            condition_snapshot_version=active.condition_snapshot_version,
            mfac_context_id=active.mfac_context_id,
            so2_target=active.so2_target,
            response_start=active.response_start,
            response_end=active.response_end,
            baseline=active.baseline_samples,
            response=[],
            status="CENSORED",
            censor_reason=reason,
        )
        event.metadata["censored_at"] = _time_text(now)
        self._active = None
        return event

    def _build_event(
        self,
        *,
        event: SupplyFlowTrackingEvent,
        condition_snapshot_version: str,
        mfac_context_id: str,
        so2_target: Optional[float],
        response_start: datetime,
        response_end: datetime,
        baseline: List[ProcessSample],
        response: List[ProcessSample],
        status: str,
        censor_reason: str,
    ) -> ProcessResponseEvent:
        self._sequence += 1
        so2_before = _median([item.outlet_so2 for item in baseline])
        so2_after = _median([item.outlet_so2 for item in response])
        qbase_before = _median([item.qbase_effective for item in baseline])
        qbase_after = _median([item.qbase_effective for item in response])
        inlet_before = _median([item.inlet_so2 for item in baseline])
        inlet_after = _median([item.inlet_so2 for item in response])
        ph_before = _median([item.ph for item in baseline])
        ph_after = _median([item.ph for item in response])
        actual_response = _median([item.actual_supply_flow_feedback for item in response])
        delta_q = event.delta_q_actual

        return ProcessResponseEvent(
            response_event_id=f"S2-RESP-{self._sequence:08d}",
            tracking_event_id=event.tracking_event_id,
            status=status,
            condition_snapshot_version=condition_snapshot_version,
            mfac_context_id=mfac_context_id,
            target_change_time=event.target_change_time,
            actual_flow_reached_time=event.actual_flow_reached_time,
            response_start_time=_time_text(response_start),
            response_end_time=_time_text(response_end),
            q_before=event.actual_supply_flow_before,
            q_after=event.actual_supply_flow_feedback,
            delta_q_actual=delta_q,
            so2_target=so2_target,
            so2_before=so2_before,
            so2_after=so2_after,
            delta_so2=(so2_after - so2_before) if so2_before is not None and so2_after is not None else None,
            qbase_before=qbase_before,
            qbase_after=qbase_after,
            qbase_drift=(qbase_after - qbase_before) if qbase_before is not None and qbase_after is not None else None,
            inlet_so2_before=inlet_before,
            inlet_so2_after=inlet_after,
            inlet_so2_change=(inlet_after - inlet_before) if inlet_before is not None and inlet_after is not None else None,
            ph_before=ph_before,
            ph_after=ph_after,
            delta_ph=(ph_after - ph_before) if ph_before is not None and ph_after is not None else None,
            actual_supply_flow_response=actual_response,
            fast_overlap=censor_reason == "FAST_OVERLAP",
            condition_changed=censor_reason in {"CONDITION_SNAPSHOT_CHANGED", "MFAC_CONTEXT_CHANGED"},
            target_changed=censor_reason == "SO2_TARGET_CHANGED",
            data_quality_ok=censor_reason != "DATA_QUALITY_INVALID",
            censor_reason=censor_reason,
            metadata={
                "tracking_status": event.status,
                "execution_delay_seconds": event.metadata.get("execution_delay_seconds"),
                "baseline_sample_count": len(baseline),
                "response_sample_count": len(response),
                "delay_onset_seconds": float(self.config.delay_onset_seconds),
            },
        )

    def _prune(self, now: datetime) -> None:
        retention = (
            float(self.config.baseline_window_seconds)
            + float(self.config.delay_onset_seconds)
            + float(self.config.observation_seconds)
            + float(self.config.max_sample_gap_seconds)
        )
        cutoff = now - timedelta(seconds=max(retention, float(self.config.baseline_window_seconds) * 2.0))
        self._samples = [item for item in self._samples if item.timestamp_value >= cutoff]

    @staticmethod
    def _elapsed_seconds(start: datetime, end: datetime) -> float:
        try:
            seconds = (end - start).total_seconds()
        except TypeError as exc:
            raise ValueError("timestamps must use consistent timezone semantics") from exc
        if seconds < 0.0:
            raise ValueError("timestamps must be monotonic")
        return float(seconds)

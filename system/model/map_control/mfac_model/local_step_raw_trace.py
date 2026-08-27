# -*- coding: utf-8 -*-
"""Manual-only raw trace capture for controlled LOCAL_GAIN trials.

The existing SO2/pH response monitors summarize configured windows and therefore
cannot prove observed onset timing.  This recorder preserves the raw 10-second
process trajectory around a manually executed local-step trial so the separate
``observed_timing_extractor`` can later derive observed timing.

It has no actuator, DCS-write, learning or runtime-control authority.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import math
from typing import Any, Dict, List, Optional, Tuple

from .local_step_trial_protocol import LocalStepTrialPlan
from .observed_timing_extractor import (
    ObservedProcessTrace,
    ObservedTraceSample,
)


LOCAL_STEP_RAW_TRACE_VERSION = (
    "SCHEME2_LOCAL_STEP_RAW_TRACE_V1_MANUAL_EVIDENCE_CAPTURE"
)


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


@dataclass(frozen=True)
class LocalStepRawTracePoint:
    timestamp: str
    outlet_so2: Optional[float]
    ph: Optional[float]
    condition_snapshot_version: str
    mfac_context_id: str
    data_quality_ok: bool = True

    def __post_init__(self) -> None:
        _timestamp(self.timestamp)
        if self.outlet_so2 is not None and _finite(self.outlet_so2) is None:
            raise ValueError("outlet_so2 must be finite when provided")
        if self.ph is not None and _finite(self.ph) is None:
            raise ValueError("ph must be finite when provided")


@dataclass(frozen=True)
class LocalStepRawTraceBundle:
    trial_id: str
    event_id: str
    tracking_event_id: str
    condition_snapshot_version: str
    mfac_context_id: str
    actual_flow_reached_time: str
    so2_trace: Optional[ObservedProcessTrace]
    ph_trace: Optional[ObservedProcessTrace]
    status: str
    reasons: Tuple[str, ...] = ()
    sample_count: int = 0
    learning_enabled: bool = False
    residual_control_enabled: bool = False
    dcs_write_enabled: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = LOCAL_STEP_RAW_TRACE_VERSION

    def __post_init__(self) -> None:
        if self.learning_enabled or self.residual_control_enabled or self.dcs_write_enabled:
            raise ValueError("raw trace bundle cannot enable runtime permissions")
        if self.status not in {"TRACE_REVIEW_CANDIDATE", "INVALID_TRACE"}:
            raise ValueError("unsupported raw trace bundle status")

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["reasons"] = list(self.reasons)
        value["so2_trace"] = asdict(self.so2_trace) if self.so2_trace is not None else None
        value["ph_trace"] = asdict(self.ph_trace) if self.ph_trace is not None else None
        return value


class LocalStepRawTraceRecorder:
    """Capture one manually approved trial's raw SO2/pH process trajectory."""

    automatic_execution_allowed = False
    learning_enabled = False
    residual_control_enabled = False
    dcs_write_enabled = False

    def __init__(self, plan: LocalStepTrialPlan) -> None:
        if not isinstance(plan, LocalStepTrialPlan):
            raise TypeError("plan must be LocalStepTrialPlan")
        self.plan = plan
        self._points: List[LocalStepRawTracePoint] = []
        self._last_time: Optional[datetime] = None
        self._tracking_event_id = ""
        self._actual_flow_reached_time = ""
        self._reasons: List[str] = []
        self._finalized = False

    @property
    def actual_flow_reached_time(self) -> str:
        return self._actual_flow_reached_time

    def record(
        self,
        *,
        timestamp: Any,
        outlet_so2: Any,
        ph: Any,
        condition_snapshot_version: str,
        mfac_context_id: str,
        data_quality_ok: bool = True,
    ) -> None:
        if self._finalized:
            raise RuntimeError("raw trace recorder is already finalized")
        now = _timestamp(timestamp)
        if self._last_time is not None:
            try:
                delta = (now - self._last_time).total_seconds()
            except TypeError as exc:
                raise ValueError("trace timestamps must use consistent timezone semantics") from exc
            if delta <= 0.0:
                raise ValueError("raw trace timestamps must be strictly increasing")
        self._last_time = now

        snapshot = str(condition_snapshot_version or "")
        context = str(mfac_context_id or "")
        point_quality = bool(data_quality_ok)
        if snapshot != self.plan.condition_snapshot_version:
            self._reasons.append("CONDITION_SNAPSHOT_CHANGED")
            point_quality = False
        if context != self.plan.mfac_context_id:
            self._reasons.append("MFAC_CONTEXT_CHANGED")
            point_quality = False

        self._points.append(
            LocalStepRawTracePoint(
                timestamp=now.isoformat(),
                outlet_so2=_finite(outlet_so2),
                ph=_finite(ph),
                condition_snapshot_version=snapshot,
                mfac_context_id=context,
                data_quality_ok=point_quality,
            )
        )

    def mark_actual_flow_reached(
        self,
        *,
        tracking_event_id: str,
        actual_flow_reached_time: Any,
    ) -> None:
        if self._finalized:
            raise RuntimeError("raw trace recorder is already finalized")
        if self._actual_flow_reached_time:
            raise ValueError("actual flow reached time is already recorded")
        tracking_id = str(tracking_event_id or "").strip()
        if not tracking_id:
            raise ValueError("tracking_event_id is required")
        reached = _timestamp(actual_flow_reached_time)
        self._tracking_event_id = tracking_id
        self._actual_flow_reached_time = reached.isoformat()

    def finalize(self, *, event_id: str) -> LocalStepRawTraceBundle:
        if self._finalized:
            raise RuntimeError("raw trace recorder is already finalized")
        self._finalized = True
        canonical_event_id = str(event_id or "").strip()
        reasons = list(dict.fromkeys(self._reasons))
        if not canonical_event_id:
            reasons.append("CANONICAL_EVENT_ID_REQUIRED")
        if not self._actual_flow_reached_time:
            reasons.append("ACTUAL_FLOW_REACHED_TIME_REQUIRED")
        if not self._tracking_event_id:
            reasons.append("TRACKING_EVENT_ID_REQUIRED")
        if not self._points:
            reasons.append("RAW_TRACE_EMPTY")

        reached = _timestamp(self._actual_flow_reached_time) if self._actual_flow_reached_time else None
        if reached is not None and self._points:
            before = [point for point in self._points if _timestamp(point.timestamp) <= reached]
            after = [point for point in self._points if _timestamp(point.timestamp) > reached]
            if not before:
                reasons.append("NO_PRE_REACH_TRACE")
            if not after:
                reasons.append("NO_POST_REACH_TRACE")

        so2_samples = tuple(
            ObservedTraceSample(
                timestamp=point.timestamp,
                value=float(point.outlet_so2),
                data_quality_ok=point.data_quality_ok,
            )
            for point in self._points
            if point.outlet_so2 is not None
        )
        ph_samples = tuple(
            ObservedTraceSample(
                timestamp=point.timestamp,
                value=float(point.ph),
                data_quality_ok=point.data_quality_ok,
            )
            for point in self._points
            if point.ph is not None
        )
        if not so2_samples:
            reasons.append("SO2_TRACE_EMPTY")
        if not ph_samples:
            reasons.append("PH_TRACE_EMPTY")

        status = "TRACE_REVIEW_CANDIDATE" if not reasons else "INVALID_TRACE"
        so2_trace = None
        ph_trace = None
        if canonical_event_id and self._actual_flow_reached_time and so2_samples:
            so2_trace = ObservedProcessTrace(
                trace_id="RAW-SO2-%s" % self.plan.trial_id,
                event_id=canonical_event_id,
                trial_id=self.plan.trial_id,
                channel="SO2",
                condition_snapshot_version=self.plan.condition_snapshot_version,
                mfac_context_id=self.plan.mfac_context_id,
                actual_flow_reached_time=self._actual_flow_reached_time,
                samples=so2_samples,
                metadata={"tracking_event_id": self._tracking_event_id},
            )
        if canonical_event_id and self._actual_flow_reached_time and ph_samples:
            ph_trace = ObservedProcessTrace(
                trace_id="RAW-PH-%s" % self.plan.trial_id,
                event_id=canonical_event_id,
                trial_id=self.plan.trial_id,
                channel="PH",
                condition_snapshot_version=self.plan.condition_snapshot_version,
                mfac_context_id=self.plan.mfac_context_id,
                actual_flow_reached_time=self._actual_flow_reached_time,
                samples=ph_samples,
                metadata={"tracking_event_id": self._tracking_event_id},
            )

        return LocalStepRawTraceBundle(
            trial_id=self.plan.trial_id,
            event_id=canonical_event_id,
            tracking_event_id=self._tracking_event_id,
            condition_snapshot_version=self.plan.condition_snapshot_version,
            mfac_context_id=self.plan.mfac_context_id,
            actual_flow_reached_time=self._actual_flow_reached_time,
            so2_trace=so2_trace,
            ph_trace=ph_trace,
            status=status,
            reasons=tuple(dict.fromkeys(reasons)),
            sample_count=len(self._points),
            learning_enabled=False,
            residual_control_enabled=False,
            dcs_write_enabled=False,
            metadata={
                "manual_evidence_capture_only": True,
                "configured_window_boundary_used_as_observed_timing": False,
                "automatic_online_adaptation_allowed": False,
                "normal_runtime_activation_allowed": False,
            },
        )


__all__ = [
    "LOCAL_STEP_RAW_TRACE_VERSION",
    "LocalStepRawTracePoint",
    "LocalStepRawTraceBundle",
    "LocalStepRawTraceRecorder",
]

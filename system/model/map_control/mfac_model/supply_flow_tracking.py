# -*- coding: utf-8 -*-
"""Track DCS-applied slurry-flow targets to actual feedback for Scheme 2.

The tracker sits strictly between continuous target publication and future SO2
process-response attribution::

    algorithm target
    -> DCS applied target/readback
    -> actual slurry-flow feedback
    -> actual_flow_reached_time

Historical replay remains ``COUNTERFACTUAL_SHADOW`` and therefore never creates
causal execution evidence.  Online causal tracking is created only when the
caller confirms that the target was applied and provides a finite DCS-applied
setpoint/readback.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime
import math
from typing import Any, Dict, List, Optional

from .continuous_target import COUNTERFACTUAL_SHADOW, ONLINE_SHADOW


SUPPLY_FLOW_TRACKING_SEMANTICS_VERSION = "SCHEME2_SUPPLY_FLOW_TRACKING_V1"


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


@dataclass(frozen=True)
class SupplyFlowTrackingConfig:
    """Parameters that must be calibrated against formal DCS feedback."""

    target_change_deadband: float
    reach_tolerance: float
    required_sustain_seconds: float
    execution_timeout_seconds: float
    max_sample_gap_seconds: float

    def __post_init__(self) -> None:
        values = {
            "target_change_deadband": self.target_change_deadband,
            "reach_tolerance": self.reach_tolerance,
            "required_sustain_seconds": self.required_sustain_seconds,
            "execution_timeout_seconds": self.execution_timeout_seconds,
            "max_sample_gap_seconds": self.max_sample_gap_seconds,
        }
        for name, value in values.items():
            number = _finite(value)
            if number is None or number < 0.0:
                raise ValueError(f"{name} must be finite and >= 0")
        if float(self.execution_timeout_seconds) <= 0.0:
            raise ValueError("execution_timeout_seconds must be > 0")
        if float(self.max_sample_gap_seconds) <= 0.0:
            raise ValueError("max_sample_gap_seconds must be > 0")


@dataclass
class SupplyFlowTrackingEvent:
    tracking_event_id: str
    algorithm_target_supply_flow: float
    target_change_time: str
    status: str
    dcs_applied_target_supply_flow: Optional[float] = None
    actual_supply_flow_feedback: Optional[float] = None
    target_actual_gap: Optional[float] = None
    actual_flow_reached_time: str = ""
    terminal_time: str = ""
    target_was_applied: bool = False
    replay_semantics: str = ONLINE_SHADOW
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = SUPPLY_FLOW_TRACKING_SEMANTICS_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SupplyFlowTrackingUpdate:
    emitted_events: List[SupplyFlowTrackingEvent] = field(default_factory=list)
    active_event: Optional[SupplyFlowTrackingEvent] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "emitted_events": [event.to_dict() for event in self.emitted_events],
            "active_event": self.active_event.to_dict() if self.active_event else None,
        }


class SupplyFlowTrackingMonitor:
    """Stateful target-to-actual execution tracker.

    A tracking event becomes ``REACHED`` only after actual feedback remains
    within ``reach_tolerance`` of the *DCS-applied target* for the configured
    sustained duration.  The resulting ``actual_flow_reached_time`` is the
    future SO2 ProcessResponseMonitor anchor.
    """

    TERMINAL_STATUSES = {
        "REACHED",
        "SUPERSEDED",
        "TIMEOUT",
        "FEEDBACK_MISSING",
        "SAMPLE_GAP",
        "COUNTERFACTUAL_SHADOW",
        "NOT_APPLIED",
    }

    def __init__(self, config: SupplyFlowTrackingConfig) -> None:
        self.config = config
        self._sequence = 0
        self._tracked_algorithm_target: Optional[float] = None
        self._active_event: Optional[SupplyFlowTrackingEvent] = None
        self._active_started_at: Optional[datetime] = None
        self._within_tolerance_since: Optional[datetime] = None
        self._last_sample_time: Optional[datetime] = None

    @property
    def active_event(self) -> Optional[SupplyFlowTrackingEvent]:
        return self._active_event

    def update(
        self,
        *,
        timestamp: Any,
        algorithm_target_supply_flow: Any,
        algorithm_target_valid: bool,
        target_was_applied: bool = False,
        dcs_applied_target_supply_flow: Any = None,
        actual_supply_flow_feedback: Any = None,
        replay_semantics: str = ONLINE_SHADOW,
    ) -> SupplyFlowTrackingUpdate:
        now = _timestamp(timestamp)
        algorithm_target = _finite(algorithm_target_supply_flow)
        dcs_target = _finite(dcs_applied_target_supply_flow)
        actual = _finite(actual_supply_flow_feedback)
        semantics = str(replay_semantics or ONLINE_SHADOW)
        emitted: List[SupplyFlowTrackingEvent] = []

        if self._active_event is not None and self._last_sample_time is not None:
            gap = self._elapsed_seconds(self._last_sample_time, now)
            if gap > float(self.config.max_sample_gap_seconds):
                emitted.append(self._terminate_active("SAMPLE_GAP", now))

        target_changed = (
            bool(algorithm_target_valid)
            and algorithm_target is not None
            and self._is_material_target_change(algorithm_target)
        )

        if target_changed:
            if self._active_event is not None:
                emitted.append(self._terminate_active("SUPERSEDED", now))

            self._tracked_algorithm_target = algorithm_target

            if semantics == COUNTERFACTUAL_SHADOW:
                emitted.append(
                    self._new_terminal_event(
                        algorithm_target=algorithm_target,
                        now=now,
                        status="COUNTERFACTUAL_SHADOW",
                        dcs_target=dcs_target,
                        actual=actual,
                        target_was_applied=False,
                        replay_semantics=semantics,
                    )
                )
            elif not bool(target_was_applied) or dcs_target is None:
                emitted.append(
                    self._new_terminal_event(
                        algorithm_target=algorithm_target,
                        now=now,
                        status="NOT_APPLIED",
                        dcs_target=dcs_target,
                        actual=actual,
                        target_was_applied=bool(target_was_applied),
                        replay_semantics=semantics,
                    )
                )
            else:
                self._active_event = self._new_event(
                    algorithm_target=algorithm_target,
                    now=now,
                    status="PENDING",
                    dcs_target=dcs_target,
                    actual=actual,
                    target_was_applied=True,
                    replay_semantics=semantics,
                )
                self._active_started_at = now
                self._within_tolerance_since = None

        if self._active_event is not None:
            if actual is None:
                emitted.append(self._terminate_active("FEEDBACK_MISSING", now))
            elif self._active_started_at is not None and self._elapsed_seconds(
                self._active_started_at,
                now,
            ) > float(self.config.execution_timeout_seconds):
                self._active_event.actual_supply_flow_feedback = actual
                self._active_event.target_actual_gap = (
                    self._active_event.algorithm_target_supply_flow - actual
                )
                emitted.append(self._terminate_active("TIMEOUT", now))
            else:
                self._update_active_feedback(actual, now, emitted)

        self._last_sample_time = now
        return SupplyFlowTrackingUpdate(
            emitted_events=emitted,
            active_event=self._active_event,
        )

    def _is_material_target_change(self, target: float) -> bool:
        if self._tracked_algorithm_target is None:
            return True
        return abs(target - self._tracked_algorithm_target) > float(
            self.config.target_change_deadband
        )

    def _update_active_feedback(
        self,
        actual: float,
        now: datetime,
        emitted: List[SupplyFlowTrackingEvent],
    ) -> None:
        event = self._active_event
        if event is None or event.dcs_applied_target_supply_flow is None:
            return

        event.actual_supply_flow_feedback = actual
        event.target_actual_gap = event.algorithm_target_supply_flow - actual
        execution_error = actual - event.dcs_applied_target_supply_flow
        event.metadata["dcs_applied_actual_error"] = execution_error

        if abs(execution_error) <= float(self.config.reach_tolerance):
            if self._within_tolerance_since is None:
                self._within_tolerance_since = now
            sustained = self._elapsed_seconds(self._within_tolerance_since, now)
            event.metadata["within_tolerance_seconds"] = sustained
            if sustained >= float(self.config.required_sustain_seconds):
                event.actual_flow_reached_time = _time_text(now)
                emitted.append(self._terminate_active("REACHED", now))
        else:
            self._within_tolerance_since = None
            event.metadata["within_tolerance_seconds"] = 0.0

    def _new_event(
        self,
        *,
        algorithm_target: float,
        now: datetime,
        status: str,
        dcs_target: Optional[float],
        actual: Optional[float],
        target_was_applied: bool,
        replay_semantics: str,
    ) -> SupplyFlowTrackingEvent:
        self._sequence += 1
        gap = algorithm_target - actual if actual is not None else None
        return SupplyFlowTrackingEvent(
            tracking_event_id=f"S2-FLOW-{self._sequence:08d}",
            algorithm_target_supply_flow=algorithm_target,
            target_change_time=_time_text(now),
            status=status,
            dcs_applied_target_supply_flow=dcs_target,
            actual_supply_flow_feedback=actual,
            target_actual_gap=gap,
            target_was_applied=target_was_applied,
            replay_semantics=replay_semantics,
        )

    def _new_terminal_event(self, **kwargs: Any) -> SupplyFlowTrackingEvent:
        event = self._new_event(**kwargs)
        event.terminal_time = kwargs["now"].isoformat()
        return event

    def _terminate_active(
        self,
        status: str,
        now: datetime,
    ) -> SupplyFlowTrackingEvent:
        event = self._active_event
        if event is None:
            raise RuntimeError("no active supply-flow tracking event")
        event.status = status
        event.terminal_time = _time_text(now)
        self._active_event = None
        self._active_started_at = None
        self._within_tolerance_since = None
        return event

    @staticmethod
    def _elapsed_seconds(start: datetime, end: datetime) -> float:
        try:
            seconds = (end - start).total_seconds()
        except TypeError as exc:
            raise ValueError("timestamps must use consistent timezone semantics") from exc
        if seconds < 0.0:
            raise ValueError("timestamps must be monotonic")
        return float(seconds)

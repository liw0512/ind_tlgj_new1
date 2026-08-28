# -*- coding: utf-8 -*-
"""Decision cadence gate for the SO2-led Scheme-2 residual hold.

The MFAC residual controller computes an *absolute desired residual*.  This gate
answers a different question: when may that desired value replace the currently
held residual?

The cadence is intentionally step-and-observe:

* one clean initial decision may be accepted;
* once the held residual materially changes, another change is forbidden until
  the corresponding dual response is complete;
* minimum HOLD time, data quality, FAST/equipment stability and pending-pH
  direction limits must also pass;
* no permission here enables production control.  It only governs a coordinator
  whose external LEARN/Residual/DCS permissions remain separately fail-closed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import math
from typing import Any, Optional, Tuple


DECISION_PERMISSION_GATE_VERSION = (
    "SCHEME2_RESIDUAL_DECISION_PERMISSION_V1_STEP_AND_OBSERVE"
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
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


@dataclass(frozen=True)
class ResidualDecisionPermission:
    allowed: bool
    status: str
    requested_delta_residual: float
    hold_remaining_seconds: float
    awaiting_response: bool
    response_ready: bool
    pending_status: str
    reason_codes: Tuple[str, ...]
    semantics_version: str = DECISION_PERMISSION_GATE_VERSION

    def to_dict(self):
        value = asdict(self)
        value["reason_codes"] = list(self.reason_codes)
        return value


class ResidualDecisionPermissionGate:
    """Stateful step/HOLD/response gate using planner-owned HOLD duration."""

    def __init__(self, min_hold_seconds: float) -> None:
        hold = _finite(min_hold_seconds)
        if hold is None or hold < 0.0:
            raise ValueError("min_hold_seconds must be finite and >= 0")
        self.min_hold_seconds = float(hold)
        self._last_change_time: Optional[datetime] = None
        self._awaiting_response = False
        self._last_reset_reason = ""

    @property
    def awaiting_response(self) -> bool:
        return bool(self._awaiting_response)

    def reset(self, reason: str = "RESET") -> None:
        self._last_change_time = None
        self._awaiting_response = False
        self._last_reset_reason = str(reason)

    def evaluate(
        self,
        *,
        timestamp: Any,
        residual_control_enabled: bool,
        qbase_inputs_valid: bool,
        data_quality_ok: bool,
        fast_active: bool,
        equipment_changed: bool,
        held_residual: Any,
        proposed_residual: Any,
        response_ready: bool,
        pending_status: str = "",
    ) -> ResidualDecisionPermission:
        try:
            now = _timestamp(timestamp)
        except (TypeError, ValueError):
            return self._decision(
                False, "BLOCK_INVALID_TIMESTAMP", 0.0, 0.0,
                bool(response_ready), str(pending_status or ""),
                ("INVALID_TIMESTAMP",),
            )
        held = _finite(held_residual)
        proposed = _finite(proposed_residual)
        if held is None or proposed is None:
            return self._decision(
                False, "BLOCK_INVALID_RESIDUAL", 0.0, 0.0,
                bool(response_ready), str(pending_status or ""),
                ("INVALID_HELD_OR_PROPOSED_RESIDUAL",),
            )
        delta = float(proposed - held)

        blockers = []
        if not bool(residual_control_enabled):
            blockers.append("RESIDUAL_CONTROL_DISABLED")
        if not bool(qbase_inputs_valid):
            blockers.append("QBASE_INPUTS_INVALID")
        if not bool(data_quality_ok):
            blockers.append("DATA_QUALITY_INVALID")
        if bool(fast_active):
            blockers.append("FAST_ACTIVE")
        if bool(equipment_changed):
            blockers.append("EQUIPMENT_CHANGED")
        if blockers:
            return self._decision(
                False, "BLOCK_RUNTIME_GUARD", delta, 0.0,
                bool(response_ready), str(pending_status or ""), tuple(blockers),
            )

        # A completed response releases the previous step-and-observe latch.
        if bool(response_ready):
            self._awaiting_response = False

        if abs(delta) <= 1e-12:
            return self._decision(
                False, "NO_CHANGE_REQUIRED", delta, 0.0,
                bool(response_ready), str(pending_status or ""),
                ("PROPOSED_EQUALS_HELD",),
            )

        if self._awaiting_response:
            return self._decision(
                False, "HOLD_WAITING_RESPONSE", delta, 0.0,
                bool(response_ready), str(pending_status or ""),
                ("PREVIOUS_RESIDUAL_CHANGE_NOT_OBSERVED",),
            )

        pending = str(pending_status or "")
        if delta > 0.0 and pending in {"LIMIT_POSITIVE", "WATCH_HIGH"}:
            return self._decision(
                False, "HOLD_PENDING_PH", delta, 0.0,
                bool(response_ready), pending,
                ("PENDING_PH_LIMITS_POSITIVE_RESIDUAL_CHANGE",),
            )
        if delta < 0.0 and pending in {"LIMIT_NEGATIVE", "WATCH_LOW"}:
            return self._decision(
                False, "HOLD_PENDING_PH", delta, 0.0,
                bool(response_ready), pending,
                ("PENDING_PH_LIMITS_NEGATIVE_RESIDUAL_CHANGE",),
            )

        hold_remaining = 0.0
        if self._last_change_time is not None:
            elapsed = (now - self._last_change_time).total_seconds()
            if elapsed < 0.0:
                return self._decision(
                    False, "BLOCK_TIME_REGRESSION", delta, 0.0,
                    bool(response_ready), pending,
                    ("TIMESTAMP_BEFORE_LAST_RESIDUAL_CHANGE",),
                )
            hold_remaining = max(0.0, self.min_hold_seconds - elapsed)
            if hold_remaining > 0.0:
                return self._decision(
                    False, "HOLD_MIN_DURATION", delta, hold_remaining,
                    bool(response_ready), pending,
                    ("MINIMUM_HOLD_NOT_ELAPSED",),
                )

        status = "ALLOW_AFTER_RESPONSE" if bool(response_ready) else "ALLOW_INITIAL_DECISION"
        return self._decision(
            True, status, delta, 0.0,
            bool(response_ready), pending, (),
        )

    def record_residual_change(
        self,
        *,
        timestamp: Any,
        previous_residual: Any,
        new_residual: Any,
    ) -> None:
        previous = _finite(previous_residual)
        new = _finite(new_residual)
        if previous is None or new is None:
            raise ValueError("residual values must be finite")
        if abs(new - previous) <= 1e-12:
            return
        now = _timestamp(timestamp)
        self._last_change_time = now
        self._awaiting_response = True

    def _decision(
        self,
        allowed: bool,
        status: str,
        delta: float,
        hold_remaining: float,
        response_ready: bool,
        pending_status: str,
        reasons: Tuple[str, ...],
    ) -> ResidualDecisionPermission:
        return ResidualDecisionPermission(
            allowed=bool(allowed),
            status=str(status),
            requested_delta_residual=float(delta),
            hold_remaining_seconds=float(hold_remaining),
            awaiting_response=bool(self._awaiting_response),
            response_ready=bool(response_ready),
            pending_status=str(pending_status),
            reason_codes=tuple(reasons),
        )


__all__ = [
    "DECISION_PERMISSION_GATE_VERSION",
    "ResidualDecisionPermission",
    "ResidualDecisionPermissionGate",
]

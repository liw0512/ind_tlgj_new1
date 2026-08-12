from __future__ import annotations

from collections import deque
from typing import Any, Deque, Dict, List, Tuple

import numpy as np
import pandas as pd

try:
    from _engine.disturbance_classifier import (
        classify_disturbance,
        is_fast_disturbance,
    )
except ImportError:  # pragma: no cover
    from .._engine.disturbance_classifier import (
        classify_disturbance,
        is_fast_disturbance,
    )


class DisturbanceMonitor:
    def __init__(
        self,
        effective_disturbance: dict,
        online_config: dict,
        runtime_state: Dict[str, Any],
    ) -> None:
        self.effective = effective_disturbance
        self.fast_cfg = online_config["fast_mode"]
        self.state = runtime_state.setdefault("fast_mode", {})
        # timestamp, first axis, second axis (NaN for one-axis), outlet SO2
        self.history: Deque[
            Tuple[pd.Timestamp, float, float, float]
        ] = deque()

    @staticmethod
    def _slope(points: List[Tuple[pd.Timestamp, float]]) -> float:
        clean = [(t, v) for t, v in points if np.isfinite(v)]
        if len(clean) < 2:
            return 0.0
        start = clean[0][0]
        x = np.asarray(
            [(t - start).total_seconds() / 60.0 for t, _ in clean],
            dtype=float,
        )
        y = np.asarray([v for _, v in clean], dtype=float)
        if np.ptp(x) <= 0:
            return 0.0
        return float(np.polyfit(x, y, 1)[0])

    def update(
        self,
        timestamp: pd.Timestamp,
        first_axis_value: float,
        second_axis_value: float | None,
        outlet_so2: float,
    ) -> Dict[str, Any]:
        window_minutes = float(
            self.effective.get("trend_window_minutes", 5.0)
        )
        second_value = (
            float("nan")
            if second_axis_value is None
            else float(second_axis_value)
        )
        self.history.append(
            (
                timestamp,
                float(first_axis_value),
                second_value,
                float(outlet_so2),
            )
        )
        cutoff = timestamp - pd.Timedelta(
            minutes=max(window_minutes * 2.0, 10.0)
        )
        while self.history and self.history[0][0] < cutoff:
            self.history.popleft()
        trend_cutoff = timestamp - pd.Timedelta(minutes=window_minutes)
        window = [item for item in self.history if item[0] >= trend_cutoff]
        first_rate = self._slope([(x[0], x[1]) for x in window])
        second_rate = self._slope([(x[0], x[2]) for x in window])
        outlet_rate = self._slope([(x[0], x[3]) for x in window])
        threshold_count = len(self.effective.get("axis_thresholds") or [])
        second_for_classifier = (
            second_rate
            if threshold_count != 1
            else None
        )
        raw_mode = classify_disturbance(
            first_rate, second_for_classifier, self.effective
        )
        raw_fast = is_fast_disturbance(raw_mode)

        mode = str(self.state.get("mode", "REGULAR"))
        reasons: List[str] = []
        if raw_fast:
            if mode != "FAST_CHANGE":
                self.state["fast_started_at"] = timestamp.isoformat()
                reasons.append("FAST_MODE_ENTERED")
            mode = "FAST_CHANGE"
            self.state["last_fast_seen_at"] = timestamp.isoformat()
            self.state["last_fast_disturbance_mode"] = raw_mode
            self.state["exit_stable_count"] = 0
            self.state.pop("recovery_until", None)
        elif mode == "FAST_CHANGE":
            started_text = self.state.get("fast_started_at")
            started = (
                pd.Timestamp(started_text) if started_text else timestamp
            )
            held = (timestamp - started).total_seconds() / 60.0
            if held < float(self.fast_cfg["minimum_hold_minutes"]):
                reasons.append("FAST_MINIMUM_HOLD_ACTIVE")
            else:
                count = int(self.state.get("exit_stable_count", 0)) + 1
                self.state["exit_stable_count"] = count
                if count >= int(self.fast_cfg["exit_stable_cycles"]):
                    mode = "FAST_RECOVERY"
                    recovery_until = timestamp + pd.Timedelta(
                        minutes=float(
                            self.fast_cfg["recovery_hold_minutes"]
                        )
                    )
                    self.state["recovery_until"] = (
                        recovery_until.isoformat()
                    )
                    reasons.append("FAST_RECOVERY_ENTERED")
        elif mode == "FAST_RECOVERY":
            until_text = self.state.get("recovery_until")
            until = pd.Timestamp(until_text) if until_text else timestamp
            if timestamp >= until:
                mode = "REGULAR"
                self.state["exit_stable_count"] = 0
                reasons.append("FAST_RECOVERY_COMPLETED")
            else:
                reasons.append("FAST_RECOVERY_HOLD_ACTIVE")
        else:
            mode = "REGULAR"

        self.state["mode"] = mode
        return {
            "condition_axis_1_rate": first_rate,
            "condition_axis_2_rate": second_rate,
            "outlet_so2_rate": outlet_rate,
            "disturbance_mode": raw_mode,
            "control_mode": mode,
            "history_sample_count": len(window),
            "reason_codes": reasons,
        }

from __future__ import annotations

import datetime as dt
import threading
from typing import Any, Dict, Optional


class PersistenceHealthTracker:
    """Thread-safe health state for the two asynchronous history writers."""

    TARGETS = ("filter", "model_result")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._targets = {
            target: {
                "schema_status": "WAITING",
                "write_status": "WAITING",
                "table": "",
                "last_success_time": None,
                "last_failure_time": None,
                "last_recovery_time": None,
                "last_error": "",
                "consecutive_failures": 0,
                "recovery_count": 0,
            }
            for target in self.TARGETS
        }

    @staticmethod
    def _timestamp(value: Optional[Any] = None) -> str:
        if value is None:
            value = dt.datetime.now()
        if isinstance(value, dt.datetime):
            return value.isoformat()
        return str(value)

    def record_schema(
        self,
        target: str,
        success: bool,
        *,
        table: str = "",
        error: Any = "",
        timestamp: Optional[Any] = None,
    ) -> Dict[str, Any]:
        if target not in self._targets:
            raise ValueError("unknown persistence target: %s" % target)
        with self._lock:
            state = self._targets[target]
            schema_recovered = success and state["schema_status"] == "ERROR"
            if table:
                state["table"] = str(table)
            state["schema_status"] = "READY" if success else "ERROR"
            if success:
                if state["write_status"] != "ERROR":
                    state["last_error"] = ""
                if schema_recovered:
                    recovery_stamp = self._timestamp(timestamp)
                    state["last_recovery_time"] = recovery_stamp
                    state["recovery_count"] += 1
            else:
                state["last_failure_time"] = self._timestamp(timestamp)
                state["last_error"] = str(error or "schema initialization failed")
            return self._snapshot_locked()

    def record_write(
        self,
        target: str,
        success: bool,
        *,
        table: str = "",
        error: Any = "",
        timestamp: Optional[Any] = None,
    ) -> Dict[str, Any]:
        if target not in self._targets:
            raise ValueError("unknown persistence target: %s" % target)
        stamp = self._timestamp(timestamp)
        with self._lock:
            state = self._targets[target]
            if table:
                state["table"] = str(table)
            if success:
                recovered = (
                    state["write_status"] == "ERROR"
                    or state["schema_status"] == "ERROR"
                )
                state["schema_status"] = "READY"
                state["write_status"] = "HEALTHY"
                state["last_success_time"] = stamp
                state["last_error"] = ""
                state["consecutive_failures"] = 0
                if recovered:
                    state["last_recovery_time"] = stamp
                    state["recovery_count"] += 1
            else:
                state["write_status"] = "ERROR"
                state["last_failure_time"] = stamp
                state["last_error"] = str(error or "write failed")
                state["consecutive_failures"] += 1
            return self._snapshot_locked()

    def _snapshot_locked(self) -> Dict[str, Any]:
        targets = {
            key: dict(value)
            for key, value in self._targets.items()
        }
        states = list(targets.values())
        if any(
            item["schema_status"] == "ERROR"
            or item["write_status"] == "ERROR"
            for item in states
        ):
            overall = "ERROR"
        else:
            healthy_count = sum(
                item["write_status"] == "HEALTHY"
                for item in states
            )
            if healthy_count == len(states):
                overall = "HEALTHY"
            elif healthy_count:
                overall = "DEGRADED"
            else:
                overall = "WAITING"
        return {
            "overall_status": overall,
            "non_blocking": True,
            "targets": targets,
        }

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return self._snapshot_locked()

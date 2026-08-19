"""独立报警规则、触发防抖与恢复状态机。

本模块不依赖 PyQt，也不修改控制结果。外层只需周期调用 ``evaluate_global_data``，
即可得到当前活动报警和 start/update/recover 事件。GUI 仅展示这些结果。
"""
from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional

from system.model.alarm.alarm_config import (
    ALARM_RUNTIME_CONFIG,
    outlet_so2_limits,
    ph_alarm_specs,
    required_alarm_fields,
)
from system.model.config.process4map_config import PROCESS4MAP_CONFIG


_LEVEL_PRIORITY = {"CRITICAL": 0, "ALARM": 1, "NOTICE": 2}


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return not math.isfinite(number)


def _as_float(value: Any) -> Optional[float]:
    if _is_missing(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _as_datetime(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        result = value
    elif hasattr(value, "to_pydatetime"):
        try:
            result = value.to_pydatetime()
        except Exception:
            return None
    else:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            result = datetime.fromisoformat(text)
        except Exception:
            return None
    if result.tzinfo is not None:
        result = result.replace(tzinfo=None)
    return result


def _as_reason_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("[") and text.endswith("]"):
            try:
                import json

                decoded = json.loads(text)
                if isinstance(decoded, list):
                    return [str(item) for item in decoded if str(item).strip()]
            except Exception:
                pass
        return [item.strip() for item in text.split(",") if item.strip()]
    return [str(value)]


def _latest_raw(global_data: Mapping[str, Any]) -> Dict[str, Any]:
    store = global_data.get("data")
    try:
        if isinstance(store, Mapping):
            return dict(store)
        if store is not None and len(store):
            row = store[-1]
            return dict(row) if isinstance(row, Mapping) else {}
    except Exception:
        pass
    return {}


def build_alarm_snapshot(global_data: Mapping[str, Any]) -> Dict[str, Any]:
    """构造报警用快照，并保留原始实时帧时间，避免模型停写时把实时年龄误判为过期。"""
    raw = _latest_raw(global_data)
    try:
        model = dict(global_data.get("map_control") or {})
    except Exception:
        model = {}

    merged = dict(raw)
    merged.update(model)
    connection_status = global_data.get("connection_status")
    if connection_status is None:
        connection_status = raw.get("connection_status", model.get("connection_status"))

    return {
        "raw": raw,
        "model": model,
        "merged": merged,
        "connection_status": connection_status,
        "realtime_timestamp": raw.get("date"),
        "model_timestamp": model.get("date", model.get("slurry_policy_timestamp")),
    }


@dataclass
class _Tracker:
    trigger_since: Optional[float] = None
    recovery_since: Optional[float] = None
    event: Optional[Dict[str, Any]] = None
    last_persist_monotonic: float = 0.0


class AlarmManager:
    """报警事件状态机。"""

    def __init__(self) -> None:
        self.config = ALARM_RUNTIME_CONFIG
        self._trackers: Dict[str, _Tracker] = {}
        self._started_monotonic = time.monotonic()
        self._unit_stop_since_monotonic: Optional[float] = None
        self._unit_stopped = False
        self._so2_limits = outlet_so2_limits()
        self._ph_specs = ph_alarm_specs()
        self._required_fields = required_alarm_fields()

    def _tracker(self, key: str) -> _Tracker:
        if key not in self._trackers:
            self._trackers[key] = _Tracker()
        return self._trackers[key]

    def _event_active(self, key: str) -> bool:
        tracker = self._trackers.get(key)
        return bool(tracker and tracker.event is not None)

    def _detect_unit_stop(self, raw: Mapping[str, Any], now_monotonic: float) -> bool:
        cfg = PROCESS4MAP_CONFIG.unit_stop
        if not cfg.enabled:
            self._unit_stop_since_monotonic = None
            self._unit_stopped = False
            return False

        value = _as_float(raw.get(cfg.field))
        if value is None:
            if cfg.invalid_value_resets_timer:
                self._unit_stop_since_monotonic = None
                self._unit_stopped = False
            return self._unit_stopped

        threshold = float(cfg.threshold)
        comparisons = {
            "lt": value < threshold,
            "le": value <= threshold,
            "gt": value > threshold,
            "ge": value >= threshold,
            "eq": value == threshold,
            "ne": value != threshold,
        }
        condition = bool(comparisons.get(str(cfg.comparison).lower(), False))
        if not condition:
            self._unit_stop_since_monotonic = None
            self._unit_stopped = False
            return False

        if self._unit_stop_since_monotonic is None:
            self._unit_stop_since_monotonic = now_monotonic
        self._unit_stopped = (
            now_monotonic - self._unit_stop_since_monotonic >= float(cfg.hold_seconds)
        )
        return self._unit_stopped

    @staticmethod
    def _rule(
        key: str,
        active: bool,
        *,
        level: str,
        category: str,
        object_name: str,
        message: str,
        reason_code: str,
        trigger_seconds: float,
        recovery_seconds: float,
        current_value: Optional[float] = None,
        threshold_text: str = "",
        unit: str = "",
        extreme_mode: str = "none",
        recovery_message: str = "报警条件已恢复",
        suggestion: str = "",
        detail: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload = dict(detail or {})
        if suggestion:
            payload["suggestion"] = suggestion
        return {
            "alarm_key": key,
            "active": bool(active),
            "level": str(level),
            "category": str(category),
            "object_name": str(object_name),
            "message": str(message),
            "reason_code": str(reason_code),
            "trigger_seconds": max(0.0, float(trigger_seconds)),
            "recovery_seconds": max(0.0, float(recovery_seconds)),
            "current_value": current_value,
            "threshold_text": str(threshold_text),
            "unit": str(unit),
            "extreme_mode": str(extreme_mode),
            "recovery_message": str(recovery_message),
            "detail": payload,
        }

    def _evaluate_rules(
        self,
        snapshot: Mapping[str, Any],
        now: datetime,
        now_monotonic: float,
    ) -> Dict[str, Any]:
        raw = snapshot.get("raw") if isinstance(snapshot.get("raw"), Mapping) else {}
        model = snapshot.get("model") if isinstance(snapshot.get("model"), Mapping) else {}
        merged = snapshot.get("merged") if isinstance(snapshot.get("merged"), Mapping) else {}

        unit_stopped = self._detect_unit_stop(raw, now_monotonic)
        calibration_code = int(PROCESS4MAP_CONFIG.data_validation.calibration_code)
        try:
            calibration_active = int(raw.get("jym", -1)) == calibration_code
        except Exception:
            calibration_active = False

        connection = snapshot.get("connection_status")
        disconnected = connection is False

        raw_stamp = _as_datetime(snapshot.get("realtime_timestamp"))
        age_seconds: Optional[float] = None
        if raw_stamp is not None:
            age_seconds = max(0.0, (now - raw_stamp).total_seconds())
        no_raw_too_long = (
            not raw
            and now_monotonic - self._started_monotonic >= float(self.config.realtime_timeout_seconds)
        )
        stale = (
            not disconnected
            and (
                no_raw_too_long
                or (
                    age_seconds is not None
                    and age_seconds > float(self.config.realtime_timeout_seconds)
                )
            )
        )

        rules: List[Dict[str, Any]] = []
        rules.append(
            self._rule(
                "DCS_CONNECTION_LOST",
                disconnected,
                level="ALARM",
                category="DATA",
                object_name="DCS 通讯",
                message="DCS 通讯连接异常",
                reason_code="DCS_CONNECTION_LOST",
                trigger_seconds=self.config.connection_trigger_seconds,
                recovery_seconds=self.config.connection_recovery_seconds,
                recovery_message="DCS 通讯连接已恢复",
                suggestion="检查通讯客户端、网络链路以及现场 DCS/PLC 连接状态。",
            )
        )
        rules.append(
            self._rule(
                "REALTIME_DATA_TIMEOUT",
                stale,
                level="ALARM",
                category="DATA",
                object_name="实时数据",
                message="实时数据长时间未更新",
                reason_code="REALTIME_DATA_TIMEOUT",
                trigger_seconds=self.config.realtime_timeout_trigger_seconds,
                recovery_seconds=self.config.realtime_timeout_recovery_seconds,
                current_value=age_seconds,
                threshold_text=f"数据年龄 > {self.config.realtime_timeout_seconds:.0f} s",
                unit="s",
                extreme_mode="max",
                recovery_message="实时数据更新已恢复",
                suggestion="检查通讯链路、数据客户端和 GLOBAL_DATA 实时帧是否持续更新。",
                detail={"realtime_timestamp": snapshot.get("realtime_timestamp")},
            )
        )

        # 通讯/数据不可信、明确停机或测点校验期间，不继续产生下游工艺/控制连锁报警。
        process_enabled = bool(raw) and not disconnected and not stale and not unit_stopped and not calibration_active

        so2_value = _as_float(raw.get("jyq_SO2"))
        so2_key = "OUTLET_SO2_HIGH"
        if process_enabled and so2_value is not None:
            if self._event_active(so2_key):
                so2_active = so2_value > float(self._so2_limits["recover_high"])
            else:
                so2_active = so2_value > float(self._so2_limits["high"])
        else:
            so2_active = False
        rules.append(
            self._rule(
                so2_key,
                so2_active,
                level="CRITICAL",
                category="PROCESS",
                object_name="净烟气 SO₂",
                message="净烟气 SO₂ 超过安全上限",
                reason_code=so2_key,
                trigger_seconds=self.config.process_trigger_seconds,
                recovery_seconds=self.config.process_recovery_seconds,
                current_value=so2_value,
                threshold_text=f"安全上限 {self._so2_limits['high']:.1f} mg/Nm³；恢复 ≤ {self._so2_limits['recover_high']:.1f}",
                unit="mg/Nm³",
                extreme_mode="max",
                recovery_message="净烟气 SO₂ 已恢复到安全范围",
                suggestion="优先确认脱硫系统运行状态、供浆能力以及净烟气 SO₂ 测点有效性。",
            )
        )

        for spec in self._ph_specs:
            column = str(spec["column"])
            tower_name = str(spec["display_name"])
            key = f"PH_OUT_OF_RANGE:{column}"
            value = _as_float(raw.get(column))
            low = float(spec["low"])
            high = float(spec["high"])
            recover_low = float(spec["recover_low"])
            recover_high = float(spec["recover_high"])
            message = f"{tower_name}浆液 pH 超出安全范围"
            extreme_mode = "none"
            if process_enabled and value is not None:
                if value < low:
                    message = f"{tower_name}浆液 pH 低于安全下限"
                    extreme_mode = "min"
                elif value > high:
                    message = f"{tower_name}浆液 pH 高于安全上限"
                    extreme_mode = "max"

                if self._event_active(key):
                    ph_active = not (recover_low <= value <= recover_high)
                else:
                    ph_active = value < low or value > high
            else:
                ph_active = False
            rules.append(
                self._rule(
                    key,
                    ph_active,
                    level="ALARM",
                    category="PROCESS",
                    object_name=f"{tower_name}浆液 pH",
                    message=message,
                    reason_code="PH_OUT_OF_SAFE_RANGE",
                    trigger_seconds=self.config.process_trigger_seconds,
                    recovery_seconds=self.config.process_recovery_seconds,
                    current_value=value,
                    threshold_text=f"安全范围 {low:.2f}～{high:.2f}；恢复范围 {recover_low:.2f}～{recover_high:.2f}",
                    unit="pH",
                    extreme_mode=extreme_mode,
                    recovery_message=f"{tower_name}浆液 pH 已恢复到安全范围",
                    suggestion="检查石灰石浆液供给、塔内反应状态及 pH 测点有效性。",
                    detail={"column": column},
                )
            )

        blocked_status = str(model.get("slurry_policy_decision_status", "")).upper()
        control_mode = str(model.get("slurry_policy_control_mode", "")).upper()
        integration_valid = model.get("slurry_policy_integration_valid")
        control_blocked = bool(
            process_enabled
            and model
            and (
                integration_valid is False
                or blocked_status == "BLOCKED"
                or control_mode == "BLOCKED"
            )
        )
        reason_codes = _as_reason_list(model.get("slurry_policy_reason_codes"))
        rules.append(
            self._rule(
                "SLURRY_CONTROL_BLOCKED",
                control_blocked,
                level="ALARM",
                category="CONTROL",
                object_name="智能供浆控制",
                message="智能供浆控制被阻断",
                reason_code="SLURRY_CONTROL_BLOCKED",
                trigger_seconds=self.config.control_block_trigger_seconds,
                recovery_seconds=self.config.control_block_recovery_seconds,
                recovery_message="智能供浆控制阻断已解除",
                suggestion="查看供浆控制页的中文阻断原因，确认关键输入、工况和策略版本是否有效。",
                detail={
                    "decision_status": blocked_status,
                    "control_mode": control_mode,
                    "integration_valid": integration_valid,
                    "integration_error": model.get("slurry_policy_integration_error"),
                    "reason_codes": reason_codes,
                },
            )
        )

        for field_spec in self._required_fields:
            column = str(field_spec["column"])
            display_name = str(field_spec.get("display_name") or column)
            missing = bool(process_enabled and _is_missing(raw.get(column)))
            rules.append(
                self._rule(
                    f"MISSING_FIELD:{column}",
                    missing,
                    level="ALARM",
                    category="DATA",
                    object_name=display_name,
                    message=f"关键测点无有效数据：{display_name}",
                    reason_code="CRITICAL_FIELD_MISSING",
                    trigger_seconds=self.config.missing_field_trigger_seconds,
                    recovery_seconds=self.config.missing_field_recovery_seconds,
                    recovery_message=f"关键测点数据已恢复：{display_name}",
                    suggestion="检查 DCS 点位映射、通讯质量及该测点当前值是否为 NaN/空值。",
                    detail={"column": column},
                )
            )

        return {
            "rules": rules,
            "unit_stopped": unit_stopped,
            "calibration_active": calibration_active,
            "realtime_age_seconds": age_seconds,
        }

    @staticmethod
    def _update_extreme(event: Dict[str, Any], rule: Mapping[str, Any]) -> None:
        value = rule.get("current_value")
        if value is None:
            return
        try:
            value = float(value)
        except (TypeError, ValueError):
            return
        mode = str(rule.get("extreme_mode") or "none")
        current = event.get("extreme_value")
        if current is None:
            event["extreme_value"] = value
        elif mode == "max":
            event["extreme_value"] = max(float(current), value)
        elif mode == "min":
            event["extreme_value"] = min(float(current), value)

    def _new_event(self, rule: Mapping[str, Any], now: datetime) -> Dict[str, Any]:
        event = {
            "id": str(uuid.uuid4()),
            "alarm_key": str(rule.get("alarm_key")),
            "start_time": now,
            "end_time": None,
            "last_time": now,
            "level": str(rule.get("level") or "ALARM"),
            "category": str(rule.get("category") or "SYSTEM"),
            "object_name": str(rule.get("object_name") or "--"),
            "message": str(rule.get("message") or "报警"),
            "state": "ACTIVE",
            "current_value": rule.get("current_value"),
            "extreme_value": None,
            "threshold_text": str(rule.get("threshold_text") or ""),
            "unit": str(rule.get("unit") or ""),
            "reason_code": str(rule.get("reason_code") or ""),
            "recovery_message": "",
            "duration_seconds": 0.0,
            "detail": dict(rule.get("detail") or {}),
        }
        self._update_extreme(event, rule)
        return event

    @staticmethod
    def _event_view(event: Mapping[str, Any], now: datetime) -> Dict[str, Any]:
        result = dict(event)
        start = result.get("start_time")
        if isinstance(start, datetime):
            end = result.get("end_time") if isinstance(result.get("end_time"), datetime) else now
            result["duration_seconds"] = max(0.0, (end - start).total_seconds())
        return result

    def evaluate_global_data(
        self,
        global_data: Mapping[str, Any],
        *,
        now: Optional[datetime] = None,
        now_monotonic: Optional[float] = None,
    ) -> Dict[str, Any]:
        now = now or datetime.now()
        now_monotonic = time.monotonic() if now_monotonic is None else float(now_monotonic)
        snapshot = build_alarm_snapshot(global_data)
        evaluation = self._evaluate_rules(snapshot, now, now_monotonic)
        transitions: List[Dict[str, Any]] = []

        for rule in evaluation["rules"]:
            key = str(rule["alarm_key"])
            tracker = self._tracker(key)
            if bool(rule.get("active")):
                tracker.recovery_since = None
                if tracker.event is None:
                    if tracker.trigger_since is None:
                        tracker.trigger_since = now_monotonic
                    if now_monotonic - tracker.trigger_since >= float(rule["trigger_seconds"]):
                        tracker.event = self._new_event(rule, now)
                        tracker.last_persist_monotonic = now_monotonic
                        tracker.trigger_since = None
                        transitions.append({"action": "start", "event": dict(tracker.event)})
                else:
                    event = tracker.event
                    event["last_time"] = now
                    event["level"] = str(rule.get("level") or event.get("level"))
                    event["category"] = str(rule.get("category") or event.get("category"))
                    event["object_name"] = str(rule.get("object_name") or event.get("object_name"))
                    event["message"] = str(rule.get("message") or event.get("message"))
                    event["current_value"] = rule.get("current_value")
                    event["threshold_text"] = str(rule.get("threshold_text") or event.get("threshold_text") or "")
                    event["unit"] = str(rule.get("unit") or event.get("unit") or "")
                    event["detail"] = dict(rule.get("detail") or {})
                    self._update_extreme(event, rule)
                    if now_monotonic - tracker.last_persist_monotonic >= float(self.config.persistence_refresh_seconds):
                        tracker.last_persist_monotonic = now_monotonic
                        transitions.append(
                            {"action": "update", "event": self._event_view(event, now)}
                        )
            else:
                tracker.trigger_since = None
                if tracker.event is None:
                    tracker.recovery_since = None
                    continue
                if tracker.recovery_since is None:
                    tracker.recovery_since = now_monotonic
                if now_monotonic - tracker.recovery_since >= float(rule["recovery_seconds"]):
                    event = tracker.event
                    event["last_time"] = now
                    event["end_time"] = now
                    event["state"] = "RECOVERED"
                    event["recovery_message"] = str(rule.get("recovery_message") or "报警条件已恢复")
                    event["current_value"] = rule.get("current_value")
                    event["detail"] = dict(rule.get("detail") or event.get("detail") or {})
                    event = self._event_view(event, now)
                    transitions.append({"action": "recover", "event": event})
                    tracker.event = None
                    tracker.recovery_since = None
                    tracker.last_persist_monotonic = 0.0

        active = [
            self._event_view(tracker.event, now)
            for tracker in self._trackers.values()
            if tracker.event is not None
        ]
        active.sort(
            key=lambda item: (
                _LEVEL_PRIORITY.get(str(item.get("level")), 9),
                item.get("start_time") or now,
            )
        )
        return {
            "active_alarms": active,
            "transitions": transitions,
            "unit_stopped": bool(evaluation["unit_stopped"]),
            "calibration_active": bool(evaluation["calibration_active"]),
            "realtime_age_seconds": evaluation["realtime_age_seconds"],
        }

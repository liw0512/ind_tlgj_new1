from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any, Dict, Mapping, Optional

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from system.model.config.plant_config import PLANT_CONFIG

from ..reason_text import (
    summarize_reason_codes,
    translate_control_mode,
    translate_decision_state,
    translate_experience_source,
    translate_magnitude,
    translate_reason_codes,
)
from .target_flow_view_model import normalize_target_flow_status


class GlobalDataAdapter(QObject):
    """把现有 GLOBAL_DATA 转换成正式前端使用的稳定字段。

    读取优先级：
    1. GLOBAL_DATA["map_control"]：过滤/特征/第一模块/第二模块统一在线输出；
    2. GLOBAL_DATA["data"][-1]：现场最新原始帧，作为实时测点兜底。

    适配器只读 GLOBAL_DATA，不修改后端状态，也不参与控制计算。

    除首页固定摘要字段外，``realtime_values`` 会根据 ``PLANT_CONFIG`` 动态收集
    当前厂配置的烟气侧、塔体、阀门、供浆流量、供浆泵和浆液循环泵测点，
    因此实时监控页不需要把设备数量写死。

    第二模块的英文机器标识仍原样保存在 ``*_code`` / ``reason_codes`` 中；
    面向操作员的首页与供浆控制页使用本适配器生成的中文展示字段。
    """

    data_ready = pyqtSignal(dict)
    adapter_error = pyqtSignal(str)

    def __init__(
        self,
        global_data: Dict[str, Any],
        parent: Optional[QObject] = None,
        *,
        interval_ms: int = 500,
    ) -> None:
        super().__init__(parent)
        self.global_data = global_data
        self._last_fingerprint = None
        self._monitor_columns = self._configured_monitor_columns()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.poll)
        self._timer.start(max(100, int(interval_ms)))
        QTimer.singleShot(0, self.poll)

    @staticmethod
    def _is_missing(value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            return not value.strip()
        if isinstance(value, float):
            return not math.isfinite(value)
        return False

    @classmethod
    def _pick(cls, data: Mapping[str, Any], *keys: str, default=None):
        for key in keys:
            if key not in data:
                continue
            value = data.get(key)
            if not cls._is_missing(value):
                return value
        return default

    @staticmethod
    def _as_mapping(value: Any) -> Dict[str, Any]:
        if isinstance(value, Mapping):
            return dict(value)
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return {}
            try:
                decoded = json.loads(text)
            except Exception:
                return {}
            if isinstance(decoded, Mapping):
                return dict(decoded)
        return {}

    @staticmethod
    def _as_list(value: Any) -> list:
        if value is None:
            return []
        if isinstance(value, (list, tuple, set)):
            return list(value)
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            try:
                decoded = json.loads(text)
            except Exception:
                decoded = None
            if isinstance(decoded, list):
                return decoded
            return [item.strip() for item in text.split(",") if item.strip()]
        return [value]

    @staticmethod
    def _configured_monitor_columns() -> tuple[str, ...]:
        """按厂级配置收集实时监控页需要的全部现场字段。"""
        columns = []

        monitor = PLANT_CONFIG.get("realtime_monitor", {}) or {}
        for group_name in (
            "inlet_signals",
            "outlet_signals",
            "auxiliary_signals",
        ):
            for item in monitor.get(group_name, []) or []:
                column = str(item.get("column", "")).strip()
                if column:
                    columns.append(column)

        for tower in PLANT_CONFIG.get("towers", []) or []:
            if not tower.get("enabled", True):
                continue

            ph_column = str(tower.get("ph_column", "")).strip()
            if ph_column:
                columns.append(ph_column)

            for item in tower.get("monitor_fields", []) or []:
                column = str(item.get("column", "")).strip()
                if column:
                    columns.append(column)

            for valve in tower.get("valves", []) or []:
                column = str(valve.get("column", "")).strip()
                if column:
                    columns.append(column)

            for flow in tower.get("supply_flows", []) or []:
                column = str(flow.get("column", "")).strip()
                if column:
                    columns.append(column)

            for pump in tower.get("monitor_supply_pumps", []) or []:
                column = str(pump.get("value_column", "")).strip()
                if column:
                    columns.append(column)

            for pump in tower.get("circulation_pumps", []) or []:
                column = str(pump.get("value_column", "")).strip()
                if column:
                    columns.append(column)

            # 固定频供浆泵若没有单独 monitor 配置，也把控制约束所需电流留给前端。
            for pump in tower.get("supply_pumps", []) or []:
                column = str(pump.get("current_column", "")).strip()
                if column:
                    columns.append(column)

        # 保持配置顺序并去重。
        return tuple(dict.fromkeys(columns))

    def _latest_raw(self) -> Dict[str, Any]:
        store = self.global_data.get("data")
        try:
            if isinstance(store, Mapping):
                return dict(store)
            if store is not None and len(store):
                row = store[-1]
                return dict(row) if isinstance(row, Mapping) else {}
        except Exception:
            return {}
        return {}

    def _snapshot(self) -> Dict[str, Any]:
        raw = self._latest_raw()
        try:
            map_control = dict(self.global_data.get("map_control") or {})
        except Exception:
            map_control = {}
        merged = dict(raw)
        # 模型使用的过滤/特征结果和在线决策优先于原始帧。
        merged.update(map_control)

        # connection_status 通常位于 GLOBAL_DATA 顶层，不强迫后端复制进每一帧。
        if "connection_status" not in merged:
            merged["connection_status"] = self.global_data.get("connection_status")
        return merged

    @classmethod
    def _pump_text(cls, data: Mapping[str, Any]) -> str:
        # 当前现场：供浆泵 A/B 频率反馈。
        a_freq = cls._pick(data, "xstgjb_APL")
        b_freq = cls._pick(data, "xstgjb_BPL")
        parts = []
        if not cls._is_missing(a_freq):
            try:
                parts.append(f"泵A {float(a_freq):.1f} Hz")
            except (TypeError, ValueError):
                parts.append(f"泵A {a_freq}")
        if not cls._is_missing(b_freq):
            try:
                parts.append(f"泵B {float(b_freq):.1f} Hz")
            except (TypeError, ValueError):
                parts.append(f"泵B {b_freq}")
        if parts:
            return " / ".join(parts)

        # 兼容旧现场固定频泵电流字段，只展示测量值，不在 UI 层自行定义启停阈值。
        a_current = cls._pick(data, "xstgjb_ADL")
        b_current = cls._pick(data, "xstgjb_BDL")
        if not cls._is_missing(a_current):
            try:
                parts.append(f"泵A {float(a_current):.1f} A")
            except (TypeError, ValueError):
                parts.append(f"泵A {a_current}")
        if not cls._is_missing(b_current):
            try:
                parts.append(f"泵B {float(b_current):.1f} A")
            except (TypeError, ValueError):
                parts.append(f"泵B {b_current}")
        return " / ".join(parts) if parts else "--"

    @staticmethod
    def _action_text(family: Any, direction: Any) -> str:
        family_text = str(family or "").upper()
        direction_text = str(direction or "").upper()
        if family_text == "HOLD" or direction_text == "HOLD":
            return "保持当前供浆"
        if direction_text == "INCREASE":
            return "增加供浆"
        if direction_text == "DECREASE":
            return "减少供浆"
        if direction_text == "MIXED":
            return "供浆重分配"
        if family:
            return str(family)
        return "保持当前供浆"

    @classmethod
    def _delta_text(cls, value: Any) -> str:
        mapping = cls._as_mapping(value)
        if not mapping:
            return "0.0 %"
        formatted = []
        for valve_id, delta in mapping.items():
            try:
                number = float(delta)
                formatted.append(f"{valve_id} {number:+.1f} %")
            except (TypeError, ValueError):
                formatted.append(f"{valve_id} {delta}")
        if len(formatted) == 1:
            only_value = next(iter(mapping.values()))
            try:
                return f"{float(only_value):+.1f} %"
            except (TypeError, ValueError):
                return str(only_value)
        return " / ".join(formatted)

    @classmethod
    def _status(cls, data: Mapping[str, Any]) -> tuple[str, str]:
        decision_status = str(
            cls._pick(data, "slurry_policy_decision_status", default="")
        ).upper()
        control_mode = str(
            cls._pick(data, "slurry_policy_control_mode", default="")
        ).upper()
        integration_valid = cls._pick(
            data, "slurry_policy_integration_valid", default=None
        )
        condition_stable = bool(cls._pick(data, "condition_stable", default=False))

        if integration_valid is False or decision_status == "BLOCKED" or control_mode == "BLOCKED":
            return "danger", "控制阻断"
        if "FAST" in control_mode:
            return "warning", "快速扰动"
        if not cls._pick(data, "condition_label", default=None):
            return "warning", "等待模型"
        if not condition_stable:
            return "warning", "工况切换"
        return "normal", "正常"

    @staticmethod
    def _data_age_seconds(value: Any) -> Optional[float]:
        if value in (None, ""):
            return None
        try:
            if isinstance(value, datetime):
                timestamp = value
            else:
                timestamp = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
                if timestamp.tzinfo is not None:
                    timestamp = timestamp.replace(tzinfo=None)
            return max(0.0, (datetime.now() - timestamp).total_seconds())
        except Exception:
            return None

    def _monitor_values(self, data: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            column: self._pick(data, column)
            for column in self._monitor_columns
        }

    @classmethod
    def _persistence_summary(cls, value: Any) -> Dict[str, Any]:
        health = cls._as_mapping(value)
        targets = cls._as_mapping(health.get("targets"))
        filter_state = cls._as_mapping(targets.get("filter"))
        model_state = cls._as_mapping(targets.get("model_result"))
        status = str(health.get("overall_status") or "WAITING").upper()
        text = {
            "HEALTHY": "正常",
            "DEGRADED": "部分就绪",
            "ERROR": "异常",
            "WAITING": "等待首次写入",
        }.get(status, "未知")
        recovery_count = sum(
            int(item.get("recovery_count") or 0)
            for item in (filter_state, model_state)
        )
        if status == "HEALTHY" and recovery_count:
            text = "正常（已恢复）"
        errors = []
        for item in (filter_state, model_state):
            error = str(item.get("last_error") or "").strip()
            if error and error not in errors:
                errors.append(error)
        return {
            "status_code": status,
            "status_text": text,
            "last_filter_write_time": filter_state.get("last_success_time"),
            "last_model_write_time": model_state.get("last_success_time"),
            "last_error": errors[0] if errors else "",
            "raw": health,
        }

    def _build_ui_data(self, data: Mapping[str, Any]) -> Dict[str, Any]:
        persistence = self._persistence_summary(
            self._pick(data, "persistence_health", default={})
        )
        target_supply_flow = self._as_mapping(
            self._pick(data, "slurry_policy_target_supply_flow", default={})
        )
        control_recommendation = self._as_mapping(
            self._pick(data, "slurry_policy_control_recommendation", default={})
        )
        target_flow_execution_preview = self._as_mapping(
            self._pick(
                data,
                "slurry_policy_target_flow_execution_preview",
                default={},
            )
        )
        target_flow_status = normalize_target_flow_status(
            target_supply_flow,
            control_recommendation,
            target_flow_execution_preview,
        )
        family = self._pick(data, "slurry_policy_action_family", default="HOLD")
        direction = self._pick(data, "slurry_policy_action_direction", default="HOLD")
        magnitude_code = self._pick(
            data, "slurry_policy_action_magnitude", default="HOLD"
        )
        if target_flow_status.get("primary_type") == "TARGET_SUPPLY_FLOW":
            direction = target_flow_status.get("action_direction") or direction
            magnitude_code = target_flow_status.get("flow_shape") or magnitude_code
        experience_source_code = self._pick(
            data, "slurry_policy_experience_source", default="NONE"
        )
        decision_state_code = self._pick(
            data, "slurry_policy_decision_status", default="WAITING"
        )
        control_mode_code = self._pick(
            data, "slurry_policy_control_mode", default="WAITING"
        )
        reason_codes = self._as_list(
            self._pick(data, "slurry_policy_reason_codes", default=[])
        )
        action_text = self._action_text(family, direction)
        reason_details = translate_reason_codes(reason_codes)
        reason_summary = summarize_reason_codes(
            reason_codes,
            action=action_text,
            magnitude=magnitude_code,
            decision_state=decision_state_code,
            control_mode=control_mode_code,
        )
        safety_state, safety_text = self._status(data)

        ph = self._pick(data, "xstjy_PH")
        valve = self._pick(data, "xst_FMKD", "xst_FMKD1")
        flow = self._pick(data, "xstshsjy_LL")
        tower_running = any(
            not self._is_missing(value) for value in (ph, valve, flow)
        )

        date_value = self._pick(
            data,
            "date",
            "slurry_policy_timestamp",
            default=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        monitor_values = self._monitor_values(data)

        result = {
            "date": date_value,
            "yyq_SO2": self._pick(data, "yyq_SO2"),
            "jyq_SO2": self._pick(data, "jyq_SO2"),
            "target": self._pick(
                data,
                "slurry_policy_effective_target",
                "slurry_policy_commanded_target",
                "outlet_so2_target",
                "target_so2",
            ),
            "condition_label": self._pick(
                data, "stable_condition_label", "condition_label", default="--"
            ),
            "condition_stable": bool(
                self._pick(data, "condition_stable", default=False)
            ),
            "condition_switch_state": self._pick(
                data, "condition_switch_state", default="UNKNOWN"
            ),
            "integrated_version": self._pick(
                data,
                "integrated_active_version",
                "slurry_policy_model_version",
                "condition_snapshot_version",
                default="--",
            ),
            "xstjy_PH": ph,
            "xst_FMKD": valve,
            "xstshsjy_LL": flow,
            "pump": self._pump_text(data),
            "tower_running": tower_running,
            "experience_source": translate_experience_source(experience_source_code),
            "experience_source_code": experience_source_code,
            "action": action_text,
            "action_family": family,
            "action_direction": direction,
            "magnitude": translate_magnitude(magnitude_code),
            "magnitude_code": magnitude_code,
            "target_flow_status": target_flow_status,
            "decision_state": translate_decision_state(decision_state_code),
            "decision_state_code": decision_state_code,
            "control_mode": translate_control_mode(control_mode_code),
            "control_mode_code": control_mode_code,
            "reason_codes": reason_codes,
            "reason_details": reason_details,
            "reason": reason_summary,
            "historical_reliability": self._pick(
                data, "slurry_policy_historical_reliability"
            ),
            "historical_safety_score": self._pick(
                data, "slurry_policy_historical_safety_score"
            ),
            "historical_direction_consistency": self._pick(
                data, "slurry_policy_historical_direction_consistency"
            ),
            "integration_valid": self._pick(
                data, "slurry_policy_integration_valid", default=None
            ),
            "safety_state": safety_state,
            "safety_text": safety_text,
            "connection_status": self._pick(data, "connection_status", default=None),
            "data_expired": bool(self._pick(data, "data_expired", default=False)),
            "data_age_seconds": self._data_age_seconds(date_value),
            "persistence_health": persistence,
            "jym": self._pick(data, "jym"),
            "realtime_values": monitor_values,
            "ui_data_source": "GLOBAL_DATA",
        }
        return result

    def poll(self) -> None:
        try:
            merged = self._snapshot()
            if not merged:
                return
            ui_data = self._build_ui_data(merged)
            monitor_fingerprint = tuple(
                (key, str(value))
                for key, value in sorted(ui_data.get("realtime_values", {}).items())
            )
            fingerprint = (
                str(ui_data.get("date")),
                str(merged.get("realtime_seq", "")),
                str(merged.get("model_seq", "")),
                str(merged.get("slurry_policy_decision_id", "")),
                ui_data.get("yyq_SO2"),
                ui_data.get("jyq_SO2"),
                ui_data.get("xstjy_PH"),
                ui_data.get("xst_FMKD"),
                ui_data.get("xstshsjy_LL"),
                str(ui_data.get("persistence_health")),
                monitor_fingerprint,
            )
            if fingerprint == self._last_fingerprint:
                return
            self._last_fingerprint = fingerprint
            self.data_ready.emit(ui_data)
        except Exception as exc:
            self.adapter_error.emit(str(exc))

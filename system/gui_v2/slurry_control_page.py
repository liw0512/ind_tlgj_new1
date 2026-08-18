from __future__ import annotations

import math
from typing import Any, Mapping, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from system.model.config.plant_config import PLANT_CONFIG

from .widgets import CardFrame, KeyValueRow, StatusPill


class ContextItem(QWidget):
    """第三页顶部的轻量上下文项，不重复首页大指标卡。"""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 4, 10, 4)
        root.setSpacing(3)
        self.title = QLabel(title)
        self.title.setProperty("role", "muted")
        self.value = QLabel("--")
        self.value.setStyleSheet("font-weight: 700; font-size: 15px;")
        root.addWidget(self.title)
        root.addWidget(self.value)

    def set_value(self, value: Any) -> None:
        self.value.setText("--" if value in (None, "") else str(value))


class DecisionContextCard(CardFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        root = QHBoxLayout(self)
        root.setContentsMargins(16, 10, 16, 10)
        root.setSpacing(8)

        self.condition = ContextItem("当前工况")
        self.mode = ContextItem("控制模式")
        self.so2 = ContextItem("净烟气 SO₂")
        self.target = ContextItem("目标 SO₂")
        for item in (self.condition, self.mode, self.so2, self.target):
            root.addWidget(item, 1)

    def update_data(self, data: Mapping[str, Any]) -> None:
        condition = data.get("condition_label")
        self.condition.set_value(
            "等待工况" if condition in (None, "", "--") else str(condition)
        )
        self.mode.set_value(data.get("control_mode", "--"))

        current = _number(data.get("jyq_SO2"))
        target = _number(data.get("target"))
        self.so2.set_value("--" if current is None else f"{current:.1f} mg/Nm³")
        self.target.set_value("--" if target is None else f"{target:.1f} mg/Nm³")


class DecisionDetailCard(CardFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("本次供浆决策")
        title.setProperty("role", "sectionTitle")
        self.status = StatusPill("等待", "warning")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.status)
        root.addLayout(header)

        self.action = QLabel("保持当前供浆")
        self.action.setStyleSheet("font-size: 24px; font-weight: 700;")
        root.addWidget(self.action)

        self.source = KeyValueRow("经验来源", "无可用经验")
        self.magnitude = KeyValueRow("动作强度", "保持")
        self.state = KeyValueRow("控制状态", "等待")
        self.mode = KeyValueRow("控制模式", "等待")
        for row in (self.source, self.magnitude, self.state, self.mode):
            root.addWidget(row)
        root.addStretch(1)

    def update_data(self, data: Mapping[str, Any]) -> None:
        self.action.setText(str(data.get("action") or "保持当前供浆"))
        self.source.set_value(data.get("experience_source", "无可用经验"))
        self.magnitude.set_value(data.get("magnitude", "保持"))
        self.state.set_value(data.get("decision_state", "等待"))
        self.mode.set_value(data.get("control_mode", "等待"))

        raw_state = str(data.get("decision_state_code") or "").upper()
        raw_mode = str(data.get("control_mode_code") or "").upper()
        if "BLOCKED" in raw_state or "BLOCKED" in raw_mode:
            pill_state = "danger"
        elif "FAST" in raw_mode or raw_state in {"INITIALIZING", "WAITING"}:
            pill_state = "warning"
        else:
            pill_state = "normal"
        self.status.set_state(pill_state, str(data.get("decision_state") or "等待"))


class ExecutionSuggestionCard(CardFrame):
    """执行器无关的建议区域。

    当前第二模块输出仍是 valve_id -> delta / projected opening；GUI 不直接展示
    ``xst_v1`` 这类机器标识，而是从 plant_config 的 valves[].display_name 动态解析。
    当前值同样根据 valves[].column 从实时帧读取，因此单塔多阀/双塔多阀无需改页面。

    后续若某厂改为泵频率控制，只需让 Adapter 提供对应执行器结构，不需要重做
    本页整体布局。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        title = QLabel("执行建议")
        title.setProperty("role", "sectionTitle")
        root.addWidget(title)

        self.object = KeyValueRow("执行对象", "--")
        self.current = KeyValueRow("当前值", "--")
        self.delta = KeyValueRow("建议调整", "--")
        self.target = KeyValueRow("建议目标", "--")
        self.note = QLabel("当前按第二模块实际输出展示执行建议。")
        self.note.setProperty("role", "muted")
        self.note.setWordWrap(True)
        for row in (self.object, self.current, self.delta, self.target):
            root.addWidget(row)
        root.addWidget(self.note)
        root.addStretch(1)

        self._valves = _configured_valves()

    def _display_name(self, valve_id: Any) -> str:
        key = str(valve_id)
        valve = self._valves.get(key) or {}
        return str(valve.get("display_name") or key)

    def _format_mapping(
        self,
        mapping: Mapping[str, Any],
        *,
        signed: bool = False,
    ) -> str:
        parts = []
        for key, value in mapping.items():
            name = self._display_name(key)
            number = _number(value)
            if number is None:
                parts.append(f"{name}: --")
            elif signed:
                parts.append(f"{name}: {number:+.1f} %")
            else:
                parts.append(f"{name}: {number:.1f} %")
        return " / ".join(parts) if parts else "--"

    def _current_openings(
        self,
        actuator_ids: list[str],
        data: Mapping[str, Any],
    ) -> dict[str, Optional[float]]:
        realtime = data.get("realtime_values")
        if not isinstance(realtime, Mapping):
            realtime = {}

        values: dict[str, Optional[float]] = {}
        for valve_id in actuator_ids:
            valve = self._valves.get(str(valve_id)) or {}
            column = str(valve.get("column") or "").strip()
            raw = None
            if column:
                raw = data.get(column)
                if raw in (None, ""):
                    raw = realtime.get(column)
            # 首页兼容字段：当前单阀现场 Adapter 会把 xst_FMKD 提到顶层。
            if raw in (None, "") and len(actuator_ids) == 1:
                raw = data.get("xst_FMKD")
            values[str(valve_id)] = _number(raw)
        return values

    def update_data(self, data: Mapping[str, Any]) -> None:
        deltas = data.get("recommended_valve_deltas")
        if not isinstance(deltas, Mapping):
            deltas = {}
        projected = data.get("projected_valve_openings")
        if not isinstance(projected, Mapping):
            projected = {}

        actuator_ids = [str(item) for item in (list(deltas.keys()) or list(projected.keys()))]
        actuator_names = [self._display_name(item) for item in actuator_ids]
        self.object.set_value(" / ".join(actuator_names) if actuator_names else "--")

        current = self._current_openings(actuator_ids, data)
        if current:
            self.current.set_value(self._format_mapping(current))
        else:
            self.current.set_value("--")

        if deltas:
            self.delta.set_value(self._format_mapping(deltas, signed=True))
        else:
            self.delta.set_value(str(data.get("delta") or "0.0 %"))
        self.target.set_value(self._format_mapping(projected))


class ExperienceEvidenceCard(CardFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        title = QLabel("历史经验依据")
        title.setProperty("role", "sectionTitle")
        root.addWidget(title)

        self.source = KeyValueRow("经验来源", "--")
        self.reliability = KeyValueRow("历史可靠性", "--")
        self.safety = KeyValueRow("历史安全评分", "--")
        self.consistency = KeyValueRow("方向一致性", "--")
        self.version = KeyValueRow("模型版本", "--")
        for row in (
            self.source,
            self.reliability,
            self.safety,
            self.consistency,
            self.version,
        ):
            root.addWidget(row)
        root.addStretch(1)

    def update_data(self, data: Mapping[str, Any]) -> None:
        self.source.set_value(data.get("experience_source", "--"))
        self.reliability.set_value(_score_text(data.get("historical_reliability")))
        self.safety.set_value(_score_text(data.get("historical_safety_score")))
        self.consistency.set_value(_ratio_text(data.get("historical_direction_consistency")))
        self.version.set_value(data.get("integrated_version", "--"))


class ControlGuardCard(CardFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        title = QLabel("当前控制条件")
        title.setProperty("role", "sectionTitle")
        root.addWidget(title)

        self.condition = KeyValueRow("工况状态", "--")
        self.integration = KeyValueRow("模型链路", "--")
        self.communication = KeyValueRow("DCS 通讯", "--")
        self.data_state = KeyValueRow("实时数据", "--")
        self.mode = KeyValueRow("控制模式", "--")
        for row in (
            self.condition,
            self.integration,
            self.communication,
            self.data_state,
            self.mode,
        ):
            root.addWidget(row)
        root.addStretch(1)

    def update_data(self, data: Mapping[str, Any]) -> None:
        self.condition.set_value("稳定" if data.get("condition_stable") else "切换/初始化中")
        integration = data.get("integration_valid")
        if integration is None:
            self.integration.set_value("等待确认")
        else:
            self.integration.set_value("正常" if bool(integration) else "异常")

        connection = data.get("connection_status")
        if connection is None:
            self.communication.set_value("未知")
        else:
            self.communication.set_value("正常" if bool(connection) else "中断")
        self.data_state.set_value("过期" if data.get("data_expired") else "正常")
        self.mode.set_value(data.get("control_mode", "--"))


class ReasonDetailCard(CardFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(9)

        title = QLabel("决策原因与控制说明")
        title.setProperty("role", "sectionTitle")
        root.addWidget(title)

        self.summary = QLabel("等待模型在线结果。")
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("font-size: 15px; font-weight: 600;")
        root.addWidget(self.summary)

        self.detail_labels = []
        for _ in range(6):
            label = QLabel("")
            label.setWordWrap(True)
            label.setProperty("role", "muted")
            label.hide()
            self.detail_labels.append(label)
            root.addWidget(label)
        root.addStretch(1)

    def update_data(self, data: Mapping[str, Any]) -> None:
        self.summary.setText(
            str(data.get("reason") or "当前暂无需要向操作员提示的特殊决策原因。")
        )
        details = data.get("reason_details")
        if not isinstance(details, (list, tuple)):
            details = []
        details = [str(item) for item in details if str(item).strip()]

        for index, label in enumerate(self.detail_labels):
            if index < len(details):
                label.setText(f"• {details[index]}")
                label.show()
            else:
                label.hide()


class SlurryControlPage(QWidget):
    """第三页：只展示供浆算法决策详情，不重复实时设备监控。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        title = QLabel("智能供浆控制决策")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 22px; font-weight: 700; padding: 6px;")
        root.addWidget(title)

        self.context = DecisionContextCard()
        root.addWidget(self.context)

        upper = QHBoxLayout()
        upper.setSpacing(12)
        self.decision = DecisionDetailCard()
        self.execution = ExecutionSuggestionCard()
        upper.addWidget(self.decision, 1)
        upper.addWidget(self.execution, 1)
        root.addLayout(upper)

        middle = QHBoxLayout()
        middle.setSpacing(12)
        self.experience = ExperienceEvidenceCard()
        self.guards = ControlGuardCard()
        middle.addWidget(self.experience, 1)
        middle.addWidget(self.guards, 1)
        root.addLayout(middle)

        self.reasons = ReasonDetailCard()
        root.addWidget(self.reasons)
        root.addStretch(1)

    def update_data(self, data: Mapping[str, Any]) -> None:
        self.context.update_data(data)
        self.decision.update_data(data)
        self.execution.update_data(data)
        self.experience.update_data(data)
        self.guards.update_data(data)
        self.reasons.update_data(data)


def _configured_valves() -> dict[str, dict[str, Any]]:
    """按 plant_config 建立 valve_id -> 阀门配置映射。"""
    result: dict[str, dict[str, Any]] = {}
    for tower in PLANT_CONFIG.get("towers", []) or []:
        if not tower.get("enabled", True):
            continue
        for valve in tower.get("valves", []) or []:
            valve_id = str(valve.get("valve_id") or "").strip()
            if valve_id:
                result[valve_id] = dict(valve)
    return result


def _number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _score_text(value: Any) -> str:
    number = _number(value)
    return "--" if number is None else f"{number:.1f} / 100"


def _ratio_text(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "--"
    percent = number * 100.0 if abs(number) <= 1.0 else number
    return f"{percent:.1f} %"

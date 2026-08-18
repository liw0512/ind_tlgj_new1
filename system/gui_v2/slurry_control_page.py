from __future__ import annotations

import math
from typing import Any, Mapping, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

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
        self.version = ContextItem("模型版本")
        self.so2 = ContextItem("净烟气 SO₂ / 目标")
        for item in (self.condition, self.mode, self.version, self.so2):
            root.addWidget(item, 1)

    def update_data(self, data: Mapping[str, Any]) -> None:
        condition = data.get("condition_label")
        if condition in (None, "", "--"):
            condition_text = "等待工况"
        else:
            condition_text = str(condition)
            condition_text += " · 稳定" if data.get("condition_stable") else " · 切换中"
        self.condition.set_value(condition_text)
        self.mode.set_value(data.get("control_mode", "--"))
        self.version.set_value(data.get("integrated_version", "--"))

        current = _number(data.get("jyq_SO2"))
        target = _number(data.get("target"))
        if current is None and target is None:
            self.so2.set_value("--")
        elif current is None:
            self.so2.set_value(f"-- / {target:.1f} mg/Nm³")
        elif target is None:
            self.so2.set_value(f"{current:.1f} / -- mg/Nm³")
        else:
            self.so2.set_value(f"{current:.1f} / {target:.1f} mg/Nm³")


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

    当前第二模块输出仍是阀门 delta/投影值；以后若某厂改为泵频率控制，只需让
    Adapter 提供新的执行对象/当前值/目标值，不需要重做本页布局。
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

    @staticmethod
    def _format_mapping(mapping: Mapping[str, Any], *, signed: bool = False) -> str:
        parts = []
        for key, value in mapping.items():
            number = _number(value)
            if number is None:
                parts.append(f"{key}: --")
            elif signed:
                parts.append(f"{key}: {number:+.1f} %")
            else:
                parts.append(f"{key}: {number:.1f} %")
        return " / ".join(parts) if parts else "--"

    def update_data(self, data: Mapping[str, Any]) -> None:
        deltas = data.get("recommended_valve_deltas")
        if not isinstance(deltas, Mapping):
            deltas = {}
        projected = data.get("projected_valve_openings")
        if not isinstance(projected, Mapping):
            projected = {}

        actuator_ids = list(deltas.keys()) or list(projected.keys())
        self.object.set_value(" / ".join(actuator_ids) if actuator_ids else "--")

        current_valve = _number(data.get("xst_FMKD"))
        if len(actuator_ids) == 1 and current_valve is not None:
            self.current.set_value(f"{current_valve:.1f} %")
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

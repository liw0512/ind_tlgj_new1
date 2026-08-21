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

from .reason_text import translate_reason_codes
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
    """Display the canonical supply-flow action, never valve deltas."""

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        title = QLabel("供浆流量动作")
        title.setProperty("role", "sectionTitle")
        root.addWidget(title)

        self.object = KeyValueRow("目标吸收塔", "--")
        self.current = KeyValueRow("当前供浆流量", "--")
        self.delta = KeyValueRow("动作形态", "--")
        self.target = KeyValueRow("目标供浆流量", "--")
        self.note = QLabel("目标流量建议由历史供浆动作原型生成；当前执行适配器为 DRY_RUN。")
        self.note.setProperty("role", "muted")
        self.note.setWordWrap(True)
        for row in (self.object, self.current, self.delta, self.target):
            root.addWidget(row)
        root.addWidget(self.note)
        root.addStretch(1)

    def update_data(self, data: Mapping[str, Any]) -> None:
        flow = data.get("target_flow_status")
        if not isinstance(flow, Mapping):
            flow = {}
        self.object.set_value(_tower_text(flow.get("tower_id")))
        self.current.set_value(_flow_text(flow.get("current_flow"), flow.get("tower_id")))
        self.delta.set_value(_flow_shape_text(flow.get("flow_shape")))
        self.target.set_value(_target_flow_text(flow))


class TargetFlowStatusCard(CardFrame):
    """Target-flow recommendation, execution preview and feedback state."""

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(9)

        header = QHBoxLayout()
        title = QLabel("目标供浆流量执行状态")
        title.setProperty("role", "sectionTitle")
        self.status = StatusPill("等待候选", "warning")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.status)
        root.addLayout(header)

        columns = QHBoxLayout()
        columns.setSpacing(28)
        left_widget = QWidget()
        left = QVBoxLayout(left_widget)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(8)
        right_widget = QWidget()
        right = QVBoxLayout(right_widget)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(8)

        self.output_mode = KeyValueRow("规范输出", "目标供浆流量")
        self.prototype = KeyValueRow("历史原型", "--")
        self.current_flow = KeyValueRow("当前总流量", "--")
        self.target_flow = KeyValueRow("建议目标", "--")
        self.execution_plan = KeyValueRow("DRY_RUN 阶段", "--")
        self.tracking = KeyValueRow("轨迹跟踪", "--")
        self.validation = KeyValueRow("效果验证", "--")
        self.blocker = KeyValueRow("保持/阻断原因", "--")
        for row in (
            self.output_mode,
            self.prototype,
            self.current_flow,
            self.target_flow,
        ):
            left.addWidget(row)
        for row in (
            self.execution_plan,
            self.tracking,
            self.validation,
            self.blocker,
        ):
            right.addWidget(row)
        columns.addWidget(left_widget, 1)
        columns.addWidget(right_widget, 1)
        root.addLayout(columns)

        note = QLabel("当前只生成目标流量建议和 DRY_RUN 预演，不执行任何 DCS 写操作。")
        note.setProperty("role", "muted")
        note.setWordWrap(True)
        root.addWidget(note)

    def update_data(self, data: Mapping[str, Any]) -> None:
        flow = data.get("target_flow_status")
        if not isinstance(flow, Mapping):
            flow = {}

        preview_status = str(flow.get("preview_status") or "").upper()
        if preview_status == "BLOCKED":
            self.status.set_state("danger", "预演阻断")
        elif preview_status == "PREVIEW_READY":
            self.status.set_state("warning", "建议就绪")
        elif flow.get("available"):
            self.status.set_state("warning", "流量建议")
        else:
            self.status.set_state("warning", "等待候选")

        self.output_mode.set_value(_output_mode_text(flow))
        tower = _tower_text(flow.get("tower_id"))
        prototype = str(flow.get("prototype_id") or "").strip()
        evidence = tower
        if prototype:
            evidence = f"{tower} / {prototype}" if tower != "--" else prototype
        self.prototype.set_value(evidence)
        self.current_flow.set_value(
            _flow_text(flow.get("current_flow"), flow.get("tower_id"))
        )
        self.target_flow.set_value(_target_flow_text(flow))
        self.execution_plan.set_value(_preview_phase_text(flow))
        self.tracking.set_value(_tracking_text(flow.get("tracking_state")))
        self.validation.set_value(_validation_text(flow))

        self.blocker.set_value(_target_flow_blocker_text(flow))


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

        self.target_flow = TargetFlowStatusCard()
        root.addWidget(self.target_flow)

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
        self.target_flow.update_data(data)
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


def _flow_text(value: Any, tower_id: Any = None) -> str:
    number = _number(value)
    if number is None:
        return "--"
    unit = _flow_unit(tower_id)
    return f"{number:.1f} {unit}".rstrip()


def _flow_unit(tower_id: Any) -> str:
    tower_code = str(tower_id or "").strip()
    for tower in PLANT_CONFIG.get("towers", []) or []:
        if str(tower.get("tower_id") or "").strip() != tower_code:
            continue
        units = {
            str(item.get("unit") or "").strip()
            for item in tower.get("supply_flows", []) or []
            if str(item.get("unit") or "").strip()
        }
        return next(iter(units)) if len(units) == 1 else ""
    return ""


def _tower_text(value: Any) -> str:
    code = str(value or "").strip().lower()
    return {"xst": "吸收塔", "apt": "二级塔"}.get(
        code, f"塔 {value}" if code else "--"
    )


def _output_mode_text(flow: Mapping[str, Any]) -> str:
    primary = str(flow.get("primary_type") or "").upper()
    return "目标供浆流量" if primary == "TARGET_SUPPLY_FLOW" else "保持供浆"


def _flow_shape_text(value: Any) -> str:
    code = str(value or "").upper()
    return {
        "STEP": "阶跃供浆",
        "PULSE": "脉冲供浆",
        "BOOST_STEP": "强化阶跃供浆",
    }.get(code, code or "--")


def _target_flow_text(flow: Mapping[str, Any]) -> str:
    peak = _number(flow.get("target_peak_flow"))
    final = _number(flow.get("target_final_flow"))
    shape = str(flow.get("flow_shape") or "").upper()
    if final is None:
        return "--"
    unit = _flow_unit(flow.get("tower_id"))
    suffix = f" {unit}" if unit else ""
    if shape in {"PULSE", "BOOST_STEP"} and peak is not None:
        return f"峰值 {peak:.1f} → 最终 {final:.1f}{suffix}"
    return f"最终 {final:.1f}{suffix}"


def _preview_phase_text(flow: Mapping[str, Any]) -> str:
    phases = flow.get("preview_phases")
    if not isinstance(phases, list) or not phases:
        return "未生成"
    names = {"PEAK_TARGET": "峰值", "FINAL_TARGET": "最终"}
    parts = []
    for phase in phases:
        if not isinstance(phase, Mapping):
            continue
        name = names.get(str(phase.get("phase") or "").upper(), "阶段")
        target = _number(phase.get("target_flow"))
        parts.append(name if target is None else f"{name} {target:.1f}")
    return " → ".join(parts) if parts else "未生成"


def _tracking_text(value: Any) -> str:
    code = str(value or "IDLE").upper()
    return {
        "IDLE": "空闲",
        "TARGET_FLOW_RECOMMENDED": "已生成流量建议",
        "WAITING_FLOW_START": "等待现场动作",
        "WAITING_PEAK_TARGET": "跟踪峰值阶段",
        "WAITING_FINAL_TARGET": "跟踪最终阶段",
        "WAITING_EFFECT": "等待 SO₂ / pH 效果",
        "EVALUATING_EFFECT": "正在评估效果",
        "EFFECT_COMPLETED": "效果评估完成",
        "RECOMMENDATION_TIMED_OUT": "建议已超时",
        "FLOW_FEEDBACK_MISSING": "流量反馈缺失",
        "SKIPPED": "本次未跟踪",
    }.get(code, code or "--")


def _validation_text(flow: Mapping[str, Any]) -> str:
    names = {
        "WARMUP": "积累样本",
        "NOT_READY": "未达标",
        "READY_FOR_REVIEW": "可人工评审",
    }
    global_status = str(flow.get("global_validation_status") or "WARMUP").upper()
    prototype_status = str(
        flow.get("prototype_validation_status") or "WARMUP"
    ).upper()
    metrics = flow.get("validation_metrics")
    count = metrics.get("evaluated_plan_count") if isinstance(metrics, Mapping) else None
    suffix = f"（{int(count)} 次）" if _number(count) is not None else ""
    return (
        f"全局 {names.get(global_status, global_status)} / "
        f"原型 {names.get(prototype_status, prototype_status)}{suffix}"
    )


def _target_flow_blocker_text(flow: Mapping[str, Any]) -> str:
    codes = [str(item) for item in (flow.get("reason_codes") or [])]
    blockers = [
        item
        for item in codes
        if item in {
            "FLOW_CANDIDATE_UNAVAILABLE",
            "FLOW_METER_SET_INCOMPLETE",
            "TOWER_PH_OUTSIDE_SAFE_RANGE",
            "TARGET_FLOW_NOT_FINITE",
            "TARGET_FLOW_DIRECTION_INCONSISTENT",
            "FINAL_TARGET_EVIDENCE_RANGE_MISSING",
            "PEAK_TARGET_EVIDENCE_RANGE_MISSING",
            "TARGET_FLOW_TOLERANCE_INVALID",
            "ENGINEERING_FLOW_LIMITS_NOT_CONFIGURED",
        }
    ]
    translated = translate_reason_codes(blockers)
    if translated:
        return translated[0]
    if flow.get("primary_type") == "TARGET_SUPPLY_FLOW":
        return "无"
    return "当前保持供浆"

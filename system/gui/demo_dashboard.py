from __future__ import annotations

import random
import os
import sys
from datetime import datetime
from typing import Any, Dict, Optional

# 固定正式界面的缩放基准，保证 Windows 与国产 Linux 上布局一致。
os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "0")
os.environ.setdefault("QT_SCALE_FACTOR", "1")
os.environ.setdefault("QT_FONT_DPI", "96")

from PyQt5.QtCore import QObject, QTimer, Qt, pyqtSignal
from PyQt5.QtGui import QFont, QFontDatabase
from PyQt5.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .adapters.global_data_adapter import GlobalDataAdapter
from .decision_widgets import OperatorActionCard
from .realtime_page import RealtimePage
from .reason_text import (
    summarize_reason_codes,
    translate_control_mode,
    translate_decision_state,
    translate_experience_source,
    translate_magnitude,
    translate_reason_codes,
)
from .slurry_control_page import SlurryControlPage
from .theme import build_stylesheet
from .widgets import CardFrame, MetricCard, StatusPill, TowerCard, TrendWidget


class MockDataSource(QObject):
    data_ready = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._target = 20.0
        self._jyq = 22.4
        self._yyq = 2380.0
        self._ph = 5.22
        self._valve = 31.5
        self._flow = 42.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        QTimer.singleShot(0, self._tick)

    def _tick(self):
        # 仅用于验证前端刷新，不代表真实控制算法。
        self._yyq = max(500.0, self._yyq + random.uniform(-45.0, 45.0))
        self._jyq = max(5.0, min(34.0, self._jyq + random.uniform(-0.45, 0.35)))
        self._ph = max(4.7, min(5.55, self._ph + random.uniform(-0.02, 0.02)))
        self._valve = max(5.0, min(95.0, self._valve + random.uniform(-0.25, 0.35)))
        self._flow = max(10.0, self._flow + random.uniform(-0.4, 0.4))

        error = self._jyq - self._target
        experience_code = "SUPPLY_FLOW_PROTOTYPE"
        control_mode_code = "REGULAR"
        if abs(error) <= 1.0:
            action = "保持当前供浆"
            direction = "HOLD"
            magnitude_code = "HOLD"
            target_flow = self._flow
            decision_code = "HOLD"
            reason_codes = ["SO2_INSIDE_TARGET_DEADBAND", "HOLD_ACTION"]
        elif error > 0:
            action = "增加供浆"
            direction = "INCREASE"
            magnitude_code = "STEP"
            target_flow = self._flow + (1.2 if error <= 3.0 else 2.5)
            decision_code = "RECOMMENDED"
            reason_codes = ["OUTLET_SO2_ABOVE_TARGET", "SO2_ABOVE_TARGET", "EXPERIENCE_SOURCE:SUPPLY_FLOW_PROTOTYPE"]
        else:
            action = "减少供浆"
            direction = "DECREASE"
            magnitude_code = "STEP"
            target_flow = max(0.0, self._flow - 0.6)
            decision_code = "RECOMMENDED"
            reason_codes = ["OUTLET_SO2_BELOW_TARGET", "SO2_BELOW_TARGET", "EXPERIENCE_SOURCE:SUPPLY_FLOW_PROTOTYPE"]

        safety = "normal"
        safety_text = "正常"
        if self._jyq >= 34.0:
            safety = "danger"
            safety_text = "紧急"
        elif self._jyq >= 30.0:
            safety = "warning"
            safety_text = "预警"

        now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        realtime_values = {
            "yyq_SO2": self._yyq,
            "yyq_LL": 1250000.0 + random.uniform(-5000.0, 5000.0),
            "yyq_O2": 5.8 + random.uniform(-0.08, 0.08),
            "tlrkyq_YL": -0.82 + random.uniform(-0.03, 0.03),
            "jyq_SO2": self._jyq,
            "jyq_LL": 1180000.0 + random.uniform(-5000.0, 5000.0),
            "tlckyq_YL": -0.35 + random.uniform(-0.02, 0.02),
            "yhfjmg_YL": 68.0 + random.uniform(-1.0, 1.0),
            "xstjy_PH": self._ph,
            "xstshsjy_MD": 1125.0 + random.uniform(-2.0, 2.0),
            "xst_YW": 7.42 + random.uniform(-0.03, 0.03),
            "xst_FMKD": self._valve,
            "xstshsjy_LL": self._flow,
            "xstgjb_APL": 45.2,
            "xstgjb_BPL": 0.0,
            "xstjyxhb_ADL": 36.5,
            "xstjyxhb_BDL": 35.8,
            "xstjyxhb_CDL": 34.7,
            "xstjyxhb_DDL": 33.9,
            "xstjyxhb_EDL": 0.2,
        }

        reason_summary = summarize_reason_codes(
            reason_codes,
            action=action,
            magnitude=magnitude_code,
            decision_state=decision_code,
            control_mode=control_mode_code,
        )

        self.data_ready.emit(
            {
                "date": now_text,
                "yyq_SO2": self._yyq,
                "jyq_SO2": self._jyq,
                "target": self._target,
                "condition_label": "C023",
                "condition_stable": True,
                "integrated_version": "v006",
                "xstjy_PH": self._ph,
                "xst_FMKD": self._valve,
                "xstshsjy_LL": self._flow,
                "pump": "2A 45.2 Hz / 2B 0.0 Hz",
                "tower_running": True,
                "experience_source": translate_experience_source(experience_code),
                "experience_source_code": experience_code,
                "action": action,
                "action_family": "HOLD" if direction == "HOLD" else "TOWER:xst|SUPPLY_FLOW",
                "action_direction": direction,
                "magnitude": translate_magnitude(magnitude_code),
                "magnitude_code": magnitude_code,
                "target_flow_status": {
                    "available": direction != "HOLD",
                    "requested_mode": "TARGET_SUPPLY_FLOW",
                    "effective_mode": "TARGET_SUPPLY_FLOW",
                    "primary_type": (
                        "TARGET_SUPPLY_FLOW" if direction != "HOLD" else "HOLD"
                    ),
                    "tower_id": "xst",
                    "prototype_id": "DEMO_FLOW_PROTO",
                    "action_direction": direction,
                    "flow_shape": "STEP" if direction != "HOLD" else "",
                    "current_flow": self._flow,
                    "target_peak_flow": target_flow,
                    "target_final_flow": target_flow,
                    "preview_status": "PREVIEW_READY" if direction != "HOLD" else "",
                    "preview_phases": [],
                },
                "decision_state": translate_decision_state(decision_code),
                "decision_state_code": decision_code,
                "control_mode": translate_control_mode(control_mode_code),
                "control_mode_code": control_mode_code,
                "reason_codes": reason_codes,
                "reason_details": translate_reason_codes(reason_codes),
                "reason": reason_summary,
                "historical_reliability": 82.4,
                "historical_safety_score": 94.0,
                "historical_direction_consistency": 0.86,
                "integration_valid": True,
                "safety_state": safety,
                "safety_text": safety_text,
                "connection_status": True,
                "data_expired": False,
                "data_age_seconds": 0.0,
                "jym": 0,
                "realtime_values": realtime_values,
                "ui_data_source": "MOCK",
            }
        )


class PlaceholderPage(CardFrame):
    def __init__(self, title: str, description: str, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(10)
        title_label = QLabel(title)
        title_label.setProperty("role", "sectionTitle")
        desc = QLabel(description)
        desc.setProperty("role", "muted")
        desc.setWordWrap(True)
        layout.addWidget(title_label)
        layout.addWidget(desc)
        layout.addStretch(1)


class OverviewPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)

        metric_grid = QGridLayout()
        metric_grid.setHorizontalSpacing(14)
        metric_grid.setVerticalSpacing(14)
        self.yyq = MetricCard("原烟气 SO₂", "--", "mg/Nm³")
        self.jyq = MetricCard("净烟气 SO₂", "--", "mg/Nm³")
        self.target = MetricCard("目标 SO₂", "--", "mg/Nm³")
        self.condition = MetricCard("当前工况", "--", "等待模型")
        for index, card in enumerate((self.yyq, self.jyq, self.target, self.condition)):
            metric_grid.addWidget(card, 0, index)
            metric_grid.setColumnStretch(index, 1)
        root.addLayout(metric_grid)

        middle = QHBoxLayout()
        middle.setSpacing(14)
        self.tower = TowerCard("吸收塔")
        self.action = OperatorActionCard()
        middle.addWidget(self.tower, 1)
        middle.addWidget(self.action, 1)
        root.addLayout(middle)

        self.trend = TrendWidget("SO₂ 24小时趋势")
        root.addWidget(self.trend, 1)

    @staticmethod
    def _to_float(value) -> Optional[float]:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _fmt(cls, value, digits=1):
        number = cls._to_float(value)
        return "--" if number is None else f"{number:.{digits}f}"

    def update_data(self, data: dict):
        self.yyq.set_value(self._fmt(data.get("yyq_SO2"), 0))
        self.jyq.set_value(self._fmt(data.get("jyq_SO2"), 1))
        self.target.set_value(self._fmt(data.get("target"), 1))

        condition_value = data.get("condition_label")
        condition = "--" if condition_value in (None, "") else str(condition_value)
        if condition == "--":
            condition_suffix = "等待模型"
        else:
            condition_suffix = "稳定" if data.get("condition_stable") else "切换中"
        self.condition.set_value(condition, condition_suffix)

        self.tower.update_values(
            ph=self._fmt(data.get("xstjy_PH"), 2),
            valve=(
                "--"
                if self._to_float(data.get("xst_FMKD")) is None
                else f"{self._fmt(data.get('xst_FMKD'), 1)} %"
            ),
            flow=(
                "--"
                if self._to_float(data.get("xstshsjy_LL")) is None
                else f"{self._fmt(data.get('xstshsjy_LL'), 1)} m³/h"
            ),
            pump=str(data.get("pump", "--")),
            running=bool(data.get("tower_running", False)),
        )
        self.action.update_values(
            source=str(data.get("experience_source", "无可用经验")),
            action=str(data.get("action", "保持当前供浆")),
            magnitude=str(data.get("magnitude", "保持")),
            target_flow=(
                "保持当前流量"
                if not (data.get("target_flow_status") or {}).get("available")
                else "%s m³/h"
                % self._fmt(
                    (data.get("target_flow_status") or {}).get(
                        "target_final_flow"
                    ),
                    1,
                )
            ),
            state=str(data.get("decision_state", "等待")),
            mode=str(data.get("control_mode", "等待")),
            state_code=str(data.get("decision_state_code", "WAITING")),
            mode_code=str(data.get("control_mode_code", "WAITING")),
            reason=str(data.get("reason", "")),
        )

        yyq = self._to_float(data.get("yyq_SO2"))
        jyq = self._to_float(data.get("jyq_SO2"))
        target = self._to_float(data.get("target"))
        if yyq is not None and jyq is not None:
            self.trend.append(
                yyq,
                jyq,
                target,
                timestamp=data.get("date"),
            )


class DashboardWindow(QMainWindow):
    NAV_ITEMS = [
        ("运行总览", "overview"),
        ("实时监控", "realtime"),
        ("供浆控制", "slurry"),
        ("历史趋势", "history"),
        ("报警信息", "alarm"),
        ("系统配置", "settings"),
    ]

    def __init__(
        self,
        global_data: Optional[Dict[str, Any]] = None,
        *,
        data_mode: str = "mock",
    ):
        super().__init__()
        self.data_mode = str(data_mode).strip().lower()
        if self.data_mode not in {"mock", "live"}:
            raise ValueError("data_mode 只能是 mock 或 live")
        if self.data_mode == "live" and global_data is None:
            raise ValueError("live 模式必须传入现有后端 GLOBAL_DATA")
        self.global_data = global_data

        mode_title = "正式运行" if self.data_mode == "live" else "界面演示"
        self.setWindowTitle(f"湿法脱硫智能控制系统 - {mode_title}")
        self.resize(1540, 920)
        self.setMinimumSize(1180, 720)

        root_widget = QWidget()
        root_widget.setObjectName("appRoot")
        self.setCentralWidget(root_widget)
        root = QHBoxLayout(root_widget)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_sidebar())

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self._build_top_bar())

        self.stack = QStackedWidget()
        self.overview = OverviewPage()
        self.realtime = RealtimePage()
        self.slurry = SlurryControlPage()
        self.stack.addWidget(self._scroll_wrap(self.overview))
        self.stack.addWidget(self._scroll_wrap(self.realtime))
        self.stack.addWidget(self._scroll_wrap(self.slurry))
        self.stack.addWidget(
            self._scroll_wrap(
                PlaceholderPage(
                    "历史趋势",
                    "后续接历史数据库，展示 SO₂、pH、阀位、供浆流量并标记动作事件。",
                )
            )
        )
        self.stack.addWidget(
            self._scroll_wrap(
                PlaceholderPage(
                    "报警信息",
                    "集中显示数据异常、模型阻断、pH 安全边界、SO₂ 预警以及设备不可用原因。",
                )
            )
        )
        self.stack.addWidget(
            self._scroll_wrap(
                PlaceholderPage(
                    "系统配置",
                    "最终只放允许操作员/工程师调整的配置，不直接暴露算法内部所有参数。",
                )
            )
        )
        content_layout.addWidget(self.stack, 1)
        root.addWidget(content, 1)

        self._buttons[0].setChecked(True)
        self._switch_page(0)

        if self.data_mode == "live":
            self.source = GlobalDataAdapter(self.global_data, self, interval_ms=500)
            self.source.adapter_error.connect(self._on_adapter_error)
        else:
            self.source = MockDataSource(self)
        self.source.data_ready.connect(self._on_data)

        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start(1000)
        self._update_clock()

    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(220)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(18, 24, 18, 20)
        layout.setSpacing(8)

        title = QLabel("湿法脱硫智能控制")
        title.setObjectName("brandTitle")
        subtitle = QLabel("WFGD Control Console")
        subtitle.setObjectName("brandSubtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(24)

        self._buttons = []
        for index, (text, _key) in enumerate(self.NAV_ITEMS):
            button = QPushButton(text)
            button.setProperty("role", "nav")
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.clicked.connect(
                lambda _checked=False, i=index: self._switch_page(i)
            )
            layout.addWidget(button)
            self._buttons.append(button)

        layout.addStretch(1)
        source_name = "GLOBAL_DATA / LIVE" if self.data_mode == "live" else "MOCK"
        version = QLabel(f"正式前端\n数据源：{source_name}")
        version.setProperty("role", "muted")
        layout.addWidget(version)
        return sidebar

    def _build_top_bar(self) -> QFrame:
        top = QFrame()
        top.setObjectName("topBar")
        top.setFixedHeight(78)
        layout = QHBoxLayout(top)
        layout.setContentsMargins(24, 12, 24, 12)

        left = QVBoxLayout()
        self.page_title = QLabel("运行总览")
        self.page_title.setProperty("role", "sectionTitle")
        self.page_subtitle = QLabel("智能供浆控制")
        self.page_subtitle.setProperty("role", "muted")
        left.addWidget(self.page_title)
        left.addWidget(self.page_subtitle)
        layout.addLayout(left)
        layout.addStretch(1)

        self.safety = StatusPill("等待数据", "warning")
        self.version = QLabel("模型 --")
        self.version.setProperty("role", "muted")
        self.clock = QLabel("--")
        self.clock.setMinimumWidth(145)
        self.clock.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self.safety)
        layout.addSpacing(12)
        layout.addWidget(self.version)
        layout.addSpacing(16)
        layout.addWidget(self.clock)
        return top

    @staticmethod
    def _scroll_wrap(widget: QWidget) -> QScrollArea:
        holder = QWidget()
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(24, 20, 24, 24)
        holder_layout.addWidget(widget)
        holder_layout.addStretch(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(holder)
        return scroll

    def _switch_page(self, index: int):
        if not 0 <= index < self.stack.count():
            return
        self.stack.setCurrentIndex(index)
        self.page_title.setText(self.NAV_ITEMS[index][0])
        if 0 <= index < len(self._buttons):
            self._buttons[index].setChecked(True)

    def _update_clock(self):
        self.clock.setText(datetime.now().strftime("%Y-%m-%d  %H:%M:%S"))

    def _on_data(self, data: dict):
        self.overview.update_data(data)
        self.realtime.update_data(data)
        self.slurry.update_data(data)
        self.safety.set_state(
            str(data.get("safety_state", "warning")),
            str(data.get("safety_text", "等待数据")),
        )
        self.version.setText("模型 %s" % data.get("integrated_version", "--"))

    def _on_adapter_error(self, message: str):
        self.safety.set_state("danger", "前端数据异常")
        self.statusBar().showMessage(f"GlobalDataAdapter: {message}", 5000)


def _apply_application_font(app: QApplication) -> None:
    available = set(QFontDatabase().families())
    for family in (
        "Noto Sans CJK SC",
        "Source Han Sans SC",
        "WenQuanYi Micro Hei",
        "Microsoft YaHei UI",
        "Microsoft YaHei",
        "SimHei",
        "Sans Serif",
    ):
        if family in available:
            app.setFont(QFont(family, 10))
            return


def build_application() -> QApplication:
    app = QApplication.instance()
    if app is None:
        for attribute_name in ("AA_DisableHighDpiScaling", "AA_Use96Dpi"):
            attribute = getattr(Qt, attribute_name, None)
            if attribute is not None:
                QApplication.setAttribute(attribute, True)
        app = QApplication(sys.argv)
    _apply_application_font(app)
    app.setStyleSheet(build_stylesheet())
    return app


def main() -> int:
    app = build_application()
    window = DashboardWindow(data_mode="mock")
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())

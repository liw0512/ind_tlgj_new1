from __future__ import annotations

from collections import deque
from typing import Iterable, Optional

from PyQt5.QtCore import QPointF, Qt
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .theme import TOKENS


class CardFrame(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("role", "card")


class StatusPill(QLabel):
    def __init__(self, text: str = "NORMAL", state: str = "normal", parent=None):
        super().__init__(text, parent)
        self.setProperty("role", "pill")
        self.setAlignment(Qt.AlignCenter)
        self.set_state(state, text)

    def set_state(self, state: str, text: Optional[str] = None) -> None:
        self.setProperty("state", str(state).lower())
        if text is not None:
            self.setText(str(text))
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()


class MetricCard(CardFrame):
    def __init__(self, title: str, value: str = "--", unit: str = "", parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(5)

        self.title_label = QLabel(title)
        self.title_label.setProperty("role", "metricTitle")
        self.value_label = QLabel(value)
        self.value_label.setProperty("role", "metricValue")
        self.unit_label = QLabel(unit)
        self.unit_label.setProperty("role", "metricUnit")

        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)
        layout.addWidget(self.unit_label)
        layout.addStretch(1)
        self.setMinimumHeight(118)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_value(self, value, unit: Optional[str] = None) -> None:
        self.value_label.setText(str(value))
        if unit is not None:
            self.unit_label.setText(unit)


class KeyValueRow(QWidget):
    def __init__(self, key: str, value: str = "--", parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        key_label = QLabel(key)
        key_label.setProperty("role", "muted")
        self.value_label = QLabel(value)
        self.value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.value_label.setStyleSheet("font-weight: 600;")
        layout.addWidget(key_label, 1)
        layout.addWidget(self.value_label, 1)

    def set_value(self, value) -> None:
        self.value_label.setText(str(value))


class TowerCard(CardFrame):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        header = QHBoxLayout()
        self.title_label = QLabel(title)
        self.title_label.setProperty("role", "sectionTitle")
        self.status = StatusPill("运行", "normal")
        header.addWidget(self.title_label)
        header.addStretch(1)
        header.addWidget(self.status)
        layout.addLayout(header)

        self.ph = KeyValueRow("浆液 pH")
        self.valve = KeyValueRow("供浆阀位")
        self.flow = KeyValueRow("供浆流量")
        self.pump = KeyValueRow("供浆泵")
        for row in (self.ph, self.valve, self.flow, self.pump):
            layout.addWidget(row)
        layout.addStretch(1)
        self.setMinimumHeight(205)

    def update_values(self, *, ph, valve, flow, pump, running: bool = True) -> None:
        self.ph.set_value(ph)
        self.valve.set_value(valve)
        self.flow.set_value(flow)
        self.pump.set_value(pump)
        self.status.set_state(
            "normal" if running else "offline",
            "运行" if running else "等待数据",
        )


class ActionCard(CardFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(9)

        header = QHBoxLayout()
        title = QLabel("智能供浆建议")
        title.setProperty("role", "sectionTitle")
        self.mode = StatusPill("WAITING", "warning")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.mode)
        layout.addLayout(header)

        self.source = KeyValueRow("经验来源", "NONE")
        self.action = KeyValueRow("推荐动作", "HOLD")
        self.magnitude = KeyValueRow("动作强度", "HOLD")
        self.delta = KeyValueRow("建议阀位", "0.0 %")
        self.state = KeyValueRow("控制状态", "WAITING")
        self.reason = QLabel("等待模型在线结果。")
        self.reason.setWordWrap(True)
        self.reason.setProperty("role", "muted")

        for row in (self.source, self.action, self.magnitude, self.delta, self.state):
            layout.addWidget(row)
        layout.addSpacing(4)
        layout.addWidget(self.reason)
        layout.addStretch(1)
        self.setMinimumHeight(205)

    def update_values(
        self,
        *,
        source: str,
        action: str,
        magnitude: str,
        delta: str,
        state: str,
        mode: str,
        reason: str,
    ) -> None:
        self.source.set_value(source)
        self.action.set_value(action)
        self.magnitude.set_value(magnitude)
        self.delta.set_value(delta)
        self.state.set_value(state)
        self.reason.setText(reason)

        mode_upper = str(mode).upper()
        state_upper = str(state).upper()
        if "BLOCKED" in mode_upper or "BLOCKED" in state_upper:
            pill_state = "danger"
        elif "FAST" in mode_upper or mode_upper in {"WAITING", "INITIALIZING"}:
            pill_state = "warning"
        else:
            pill_state = "normal"
        self.mode.set_state(pill_state, mode_upper)


class TrendWidget(CardFrame):
    """轻量趋势图，不依赖 matplotlib。"""

    def __init__(self, title: str = "SO₂ 实时趋势", parent=None):
        super().__init__(parent)
        self.title = title
        self.yyq = deque(maxlen=90)
        self.jyq = deque(maxlen=90)
        self.target = 20.0
        self.setMinimumHeight(270)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def append(self, yyq: float, jyq: float, target: float) -> None:
        self.yyq.append(float(yyq))
        self.jyq.append(float(jyq))
        self.target = float(target)
        self.update()

    @staticmethod
    def _points(values: Iterable[float], rect, lo: float, hi: float) -> list[QPointF]:
        values = list(values)
        if len(values) < 2:
            return []
        span = max(hi - lo, 1e-6)
        step = rect.width() / max(len(values) - 1, 1)
        points = []
        for index, value in enumerate(values):
            x = rect.left() + index * step
            y = rect.bottom() - (float(value) - lo) / span * rect.height()
            points.append(QPointF(x, y))
        return points

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        painter.setPen(QColor(TOKENS["text"]))
        font = painter.font()
        font.setPointSize(11)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(18, 28, self.title)

        plot = self.rect().adjusted(18, 48, -18, -22)
        painter.setPen(QPen(QColor(TOKENS["border"]), 1))
        for i in range(5):
            y = plot.top() + i * plot.height() / 4
            painter.drawLine(plot.left(), int(y), plot.right(), int(y))

        if len(self.jyq) < 2:
            painter.setPen(QColor(TOKENS["muted"]))
            painter.drawText(plot, Qt.AlignCenter, "等待数据...")
            return

        # 两条曲线量级差异较大，因此分别归一化到同一绘图区；正式历史页再显示真实坐标轴。
        series = [list(self.yyq), list(self.jyq)]
        colors = [TOKENS["accent"], TOKENS["success"]]
        widths = [2, 3]
        for values, color, width in zip(series, colors, widths):
            lo = min(values)
            hi = max(values)
            if abs(hi - lo) < 1e-9:
                hi = lo + 1.0
            points = self._points(values, plot, lo, hi)
            if len(points) < 2:
                continue
            path = QPainterPath(points[0])
            for point in points[1:]:
                path.lineTo(point)
            painter.setPen(QPen(QColor(color), width))
            painter.drawPath(path)

        painter.setPen(QColor(TOKENS["muted"]))
        painter.drawText(plot.left(), plot.bottom() + 17, "蓝：原烟气 SO₂")
        painter.drawText(plot.left() + 140, plot.bottom() + 17, "绿：净烟气 SO₂")

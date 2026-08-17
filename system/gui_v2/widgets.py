from __future__ import annotations

import math
from collections import deque
from datetime import datetime, timedelta
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
    """首页轻量 24 小时 SO₂ 双纵轴趋势图，不依赖 matplotlib。

    - 横轴：滚动最近 24 小时；
    - 左轴：原烟气 SO₂，默认 0~5000 mg/Nm³，超限按 1000 自动扩展；
    - 右轴：净烟气 SO₂，默认 0~100 mg/Nm³，超限按 20 自动扩展；
    - 趋势缓存每 30 秒最多保存一个点，24 小时最多 2880 点/曲线；
    - 页面本身仍可高频刷新，不让图表缓存跟随 500ms/1s 频率无限增长。
    """

    WINDOW_HOURS = 24
    SAMPLE_SECONDS = 30
    MAX_POINTS = WINDOW_HOURS * 60 * 60 // SAMPLE_SECONDS
    YYQ_BASE_MAX = 5000.0
    YYQ_EXPAND_STEP = 1000.0
    JYQ_BASE_MAX = 100.0
    JYQ_EXPAND_STEP = 20.0

    def __init__(self, title: str = "SO₂ 24小时趋势", parent=None):
        super().__init__(parent)
        self.title = title
        self.samples = deque(maxlen=self.MAX_POINTS)
        self._last_sample_time: Optional[datetime] = None
        self._latest_target: Optional[float] = None
        self.setMinimumHeight(310)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    @staticmethod
    def _coerce_time(value) -> datetime:
        if isinstance(value, datetime):
            return value
        if value not in (None, ""):
            text = str(value).strip()
            try:
                return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
            except Exception:
                pass
        return datetime.now()

    @staticmethod
    def _finite_float(value) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    def append(
        self,
        yyq: float,
        jyq: float,
        target: Optional[float],
        timestamp=None,
    ) -> None:
        sample_time = self._coerce_time(timestamp)
        yyq_value = self._finite_float(yyq)
        jyq_value = self._finite_float(jyq)
        target_value = self._finite_float(target)
        if yyq_value is None or jyq_value is None:
            return

        self._latest_target = target_value

        # 页面可 500ms/1s 更新，但趋势缓存固定 30 秒采一个点。
        if self._last_sample_time is not None:
            elapsed = (sample_time - self._last_sample_time).total_seconds()
            if 0 <= elapsed < self.SAMPLE_SECONDS:
                self.update()
                return

        self.samples.append((sample_time, yyq_value, jyq_value, target_value))
        self._last_sample_time = sample_time
        self._prune(sample_time)
        self.update()

    def _prune(self, end_time: datetime) -> None:
        cutoff = end_time - timedelta(hours=self.WINDOW_HOURS)
        while self.samples and self.samples[0][0] < cutoff:
            self.samples.popleft()

    @staticmethod
    def _expanded_max(values: Iterable[float], base_max: float, step: float) -> float:
        finite_values = [float(value) for value in values if value is not None and math.isfinite(float(value))]
        observed = max(finite_values, default=0.0)
        if observed <= base_max:
            return base_max
        return max(base_max, math.ceil(observed / step) * step)

    @staticmethod
    def _value_y(value: float, rect, axis_max: float) -> float:
        clamped = max(0.0, min(float(value), axis_max))
        return rect.bottom() - (clamped / max(axis_max, 1e-9)) * rect.height()

    @staticmethod
    def _time_x(value: datetime, rect, start_time: datetime, end_time: datetime) -> float:
        total = max((end_time - start_time).total_seconds(), 1.0)
        elapsed = (value - start_time).total_seconds()
        fraction = max(0.0, min(1.0, elapsed / total))
        return rect.left() + fraction * rect.width()

    def _series_points(
        self,
        samples,
        value_index: int,
        rect,
        axis_max: float,
        start_time: datetime,
        end_time: datetime,
    ) -> list[QPointF]:
        points = []
        for sample in samples:
            value = sample[value_index]
            if value is None:
                continue
            x = self._time_x(sample[0], rect, start_time, end_time)
            y = self._value_y(value, rect, axis_max)
            points.append(QPointF(x, y))
        return points

    @staticmethod
    def _draw_path(painter: QPainter, points: list[QPointF], pen: QPen) -> None:
        if len(points) < 2:
            return
        path = QPainterPath(points[0])
        for point in points[1:]:
            path.lineTo(point)
        painter.setPen(pen)
        painter.drawPath(path)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # 标题。
        painter.setPen(QColor(TOKENS["text"]))
        title_font = painter.font()
        title_font.setPointSize(11)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.drawText(18, 27, self.title)

        samples = list(self.samples)
        if samples:
            end_time = max(datetime.now(), samples[-1][0])
        else:
            end_time = datetime.now()
        start_time = end_time - timedelta(hours=self.WINDOW_HOURS)

        # 给左右 Y 轴、标题、横轴时间标签留空间。
        plot = self.rect().adjusted(68, 54, -68, -50)
        if plot.width() <= 20 or plot.height() <= 20:
            return

        yyq_values = [sample[1] for sample in samples]
        jyq_values = [sample[2] for sample in samples]
        target_values = [sample[3] for sample in samples if sample[3] is not None]
        if self._latest_target is not None:
            target_values.append(self._latest_target)
        yyq_max = self._expanded_max(yyq_values, self.YYQ_BASE_MAX, self.YYQ_EXPAND_STEP)
        jyq_max = self._expanded_max(
            list(jyq_values) + list(target_values),
            self.JYQ_BASE_MAX,
            self.JYQ_EXPAND_STEP,
        )

        muted = QColor(TOKENS["muted"])
        border = QColor(TOKENS["border"])
        yyq_color = QColor(TOKENS["accent"])
        jyq_color = QColor(TOKENS["success"])
        target_color = QColor(TOKENS["warning"])

        axis_font = painter.font()
        axis_font.setPointSize(8)
        axis_font.setBold(False)
        painter.setFont(axis_font)

        # 水平网格 + 双 Y 轴刻度。两侧都是 0~100% 的同一网格比例，但各自显示真实量程。
        grid_count = 5
        for index in range(grid_count + 1):
            fraction = index / grid_count
            y = plot.bottom() - fraction * plot.height()
            painter.setPen(QPen(border, 1))
            painter.drawLine(plot.left(), int(y), plot.right(), int(y))

            left_value = yyq_max * fraction
            right_value = jyq_max * fraction
            painter.setPen(yyq_color)
            painter.drawText(
                6,
                int(y - 8),
                54,
                16,
                Qt.AlignRight | Qt.AlignVCenter,
                f"{left_value:.0f}",
            )
            painter.setPen(jyq_color)
            painter.drawText(
                plot.right() + 8,
                int(y - 8),
                52,
                16,
                Qt.AlignLeft | Qt.AlignVCenter,
                f"{right_value:.0f}",
            )

        # 左右坐标轴边界。
        painter.setPen(QPen(border, 1))
        painter.drawLine(plot.left(), plot.top(), plot.left(), plot.bottom())
        painter.drawLine(plot.right(), plot.top(), plot.right(), plot.bottom())
        painter.drawLine(plot.left(), plot.bottom(), plot.right(), plot.bottom())

        # 纵轴标题和单位。
        painter.setPen(yyq_color)
        painter.drawText(plot.left(), 43, "原烟气 SO₂  mg/Nm³")
        right_title = "净烟气 SO₂  mg/Nm³"
        right_width = painter.fontMetrics().horizontalAdvance(right_title)
        painter.setPen(jyq_color)
        painter.drawText(plot.right() - right_width, 43, right_title)

        # 横轴固定最近24小时，4小时一个主刻度，共7个标签。
        tick_count = 6
        for index in range(tick_count + 1):
            fraction = index / tick_count
            tick_time = start_time + timedelta(hours=self.WINDOW_HOURS * fraction)
            x = plot.left() + fraction * plot.width()
            painter.setPen(QPen(border, 1))
            painter.drawLine(int(x), plot.top(), int(x), plot.bottom())
            label = tick_time.strftime("%H:%M")
            label_width = 48
            if index == 0:
                label_x = int(x)
                align = Qt.AlignLeft | Qt.AlignVCenter
            elif index == tick_count:
                label_x = int(x) - label_width
                align = Qt.AlignRight | Qt.AlignVCenter
            else:
                label_x = int(x) - label_width // 2
                align = Qt.AlignCenter
            painter.setPen(muted)
            painter.drawText(label_x, plot.bottom() + 8, label_width, 18, align, label)

        painter.setPen(muted)
        painter.drawText(
            int(plot.center().x()) - 40,
            plot.bottom() + 29,
            80,
            16,
            Qt.AlignCenter,
            "最近24小时",
        )

        # 目标 SO₂ 参考线：跟随右侧净烟气坐标轴。
        if self._latest_target is not None:
            target_y = self._value_y(self._latest_target, plot, jyq_max)
            target_pen = QPen(target_color, 1)
            target_pen.setStyle(Qt.DashLine)
            painter.setPen(target_pen)
            painter.drawLine(plot.left(), int(target_y), plot.right(), int(target_y))
            painter.setPen(target_color)
            painter.drawText(
                plot.right() - 88,
                int(target_y) - 18,
                84,
                16,
                Qt.AlignRight | Qt.AlignVCenter,
                f"目标 {self._latest_target:.1f}",
            )

        if not samples:
            painter.setPen(muted)
            painter.drawText(plot, Qt.AlignCenter, "等待 SO₂ 趋势数据...")
        elif len(samples) == 1:
            painter.setPen(muted)
            painter.drawText(plot, Qt.AlignCenter, "已收到首个点，30秒后形成趋势...")
        else:
            yyq_points = self._series_points(
                samples,
                1,
                plot,
                yyq_max,
                start_time,
                end_time,
            )
            jyq_points = self._series_points(
                samples,
                2,
                plot,
                jyq_max,
                start_time,
                end_time,
            )
            self._draw_path(painter, yyq_points, QPen(yyq_color, 2))
            self._draw_path(painter, jyq_points, QPen(jyq_color, 3))

        # 图例与当前轴量程，便于现场直接判断图表尺度是否发生了自动扩展。
        legend_y = 28
        painter.setFont(axis_font)
        painter.setPen(yyq_color)
        painter.drawText(plot.left() + 210, legend_y, "● 原烟气")
        painter.setPen(jyq_color)
        painter.drawText(plot.left() + 285, legend_y, "● 净烟气")
        painter.setPen(target_color)
        painter.drawText(plot.left() + 360, legend_y, "-- 目标")
        painter.setPen(muted)
        range_text = f"量程：原烟气 0~{yyq_max:.0f} / 净烟气 0~{jyq_max:.0f}"
        range_width = painter.fontMetrics().horizontalAdvance(range_text)
        painter.drawText(plot.right() - range_width, legend_y, range_text)

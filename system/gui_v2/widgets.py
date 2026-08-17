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
    """首页轻量 SO₂ 双纵轴日趋势图，不依赖 matplotlib。

    - 横轴固定为当天 00:00~24:00，每 3 小时一个主刻度；
    - 左轴原烟气 SO₂、右轴净烟气 SO₂ 均根据当天实际数据动态量程；
    - 动态量程保留最小跨度和边缘余量，避免单点/小波动时图形失真；
    - 趋势缓存每 30 秒最多保存一个点，当天最多 2880 点/曲线；
    - 跨到新的一天后自动清空前一天内存趋势，从 00:00 重新开始。
    """

    DAY_HOURS = 24
    TICK_HOURS = 3
    SAMPLE_SECONDS = 30
    MAX_POINTS = DAY_HOURS * 60 * 60 // SAMPLE_SECONDS

    YYQ_MIN_SPAN = 500.0
    YYQ_ROUND_STEP = 100.0
    JYQ_MIN_SPAN = 20.0
    JYQ_ROUND_STEP = 5.0
    AXIS_PADDING_RATIO = 0.10

    def __init__(self, title: str = "SO₂ 24小时趋势", parent=None):
        super().__init__(parent)
        self.title = title
        self.samples = deque(maxlen=self.MAX_POINTS)
        self._display_date = None
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

    @staticmethod
    def _day_bounds(value: datetime):
        start = value.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, start + timedelta(days=1)

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

        sample_date = sample_time.date()
        if self._display_date != sample_date:
            self.samples.clear()
            self._last_sample_time = None
            self._display_date = sample_date

        self._latest_target = target_value

        # 页面可 500ms/1s 更新，但趋势缓存固定 30 秒采一个点。
        if self._last_sample_time is not None:
            elapsed = (sample_time - self._last_sample_time).total_seconds()
            if 0 <= elapsed < self.SAMPLE_SECONDS:
                self.update()
                return

        self.samples.append((sample_time, yyq_value, jyq_value, target_value))
        self._last_sample_time = sample_time
        self._prune_to_day(sample_time)
        self.update()

    def _prune_to_day(self, reference_time: datetime) -> None:
        day_start, day_end = self._day_bounds(reference_time)
        valid = [
            sample for sample in self.samples
            if day_start <= sample[0] < day_end
        ]
        if len(valid) != len(self.samples):
            self.samples = deque(valid, maxlen=self.MAX_POINTS)

    @classmethod
    def _dynamic_range(
        cls,
        values: Iterable[float],
        min_span: float,
        round_step: float,
    ) -> tuple[float, float]:
        finite_values = []
        for value in values:
            try:
                number = float(value)
            except (TypeError, ValueError, OverflowError):
                continue
            if math.isfinite(number):
                finite_values.append(number)

        if not finite_values:
            return 0.0, float(min_span)

        observed_min = min(finite_values)
        observed_max = max(finite_values)
        observed_span = observed_max - observed_min

        if observed_span < min_span:
            center = (observed_min + observed_max) / 2.0
            lo = center - min_span / 2.0
            hi = center + min_span / 2.0
            span_for_padding = min_span
        else:
            lo = observed_min
            hi = observed_max
            span_for_padding = observed_span

        padding = max(span_for_padding * cls.AXIS_PADDING_RATIO, round_step)
        lo -= padding
        hi += padding

        # SO₂ 浓度不显示负量程；上下限做整刻度处理，读数更稳定。
        lo = max(0.0, math.floor(lo / round_step) * round_step)
        hi = math.ceil(hi / round_step) * round_step
        if hi <= lo:
            hi = lo + max(min_span, round_step)
        return lo, hi

    @staticmethod
    def _value_y(value: float, rect, axis_min: float, axis_max: float) -> float:
        span = max(axis_max - axis_min, 1e-9)
        clamped = max(axis_min, min(float(value), axis_max))
        return rect.bottom() - ((clamped - axis_min) / span) * rect.height()

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
        axis_min: float,
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
            y = self._value_y(value, rect, axis_min, axis_max)
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

        painter.setPen(QColor(TOKENS["text"]))
        title_font = painter.font()
        title_font.setPointSize(11)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.drawText(18, 27, self.title)

        reference = datetime.now()
        if self._display_date is not None:
            reference = datetime.combine(self._display_date, datetime.min.time())
        start_time, end_time = self._day_bounds(reference)
        samples = [
            sample for sample in self.samples
            if start_time <= sample[0] < end_time
        ]

        # 给左右 Y 轴和横轴时间标签留空间；顶部只保留图例和单位。
        plot = self.rect().adjusted(68, 54, -68, -50)
        if plot.width() <= 20 or plot.height() <= 20:
            return

        yyq_values = [sample[1] for sample in samples]
        jyq_values = [sample[2] for sample in samples]
        target_values = [sample[3] for sample in samples if sample[3] is not None]
        if self._latest_target is not None:
            target_values.append(self._latest_target)

        yyq_min, yyq_max = self._dynamic_range(
            yyq_values,
            self.YYQ_MIN_SPAN,
            self.YYQ_ROUND_STEP,
        )
        jyq_min, jyq_max = self._dynamic_range(
            list(jyq_values) + list(target_values),
            self.JYQ_MIN_SPAN,
            self.JYQ_ROUND_STEP,
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

        # 水平网格 + 动态双 Y 轴刻度。
        grid_count = 5
        for index in range(grid_count + 1):
            fraction = index / grid_count
            y = plot.bottom() - fraction * plot.height()
            painter.setPen(QPen(border, 1))
            painter.drawLine(plot.left(), int(y), plot.right(), int(y))

            left_value = yyq_min + (yyq_max - yyq_min) * fraction
            right_value = jyq_min + (jyq_max - jyq_min) * fraction
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

        painter.setPen(QPen(border, 1))
        painter.drawLine(plot.left(), plot.top(), plot.left(), plot.bottom())
        painter.drawLine(plot.right(), plot.top(), plot.right(), plot.bottom())
        painter.drawLine(plot.left(), plot.bottom(), plot.right(), plot.bottom())

        # 横轴固定每天 00:00~24:00，每 3 小时一个区间。
        tick_hours = list(range(0, self.DAY_HOURS + 1, self.TICK_HOURS))
        for hour in tick_hours:
            fraction = hour / self.DAY_HOURS
            x = plot.left() + fraction * plot.width()
            painter.setPen(QPen(border, 1))
            painter.drawLine(int(x), plot.top(), int(x), plot.bottom())

            label = "24:00" if hour == self.DAY_HOURS else f"{hour:02d}:00"
            label_width = 48
            if hour == 0:
                label_x = int(x)
                align = Qt.AlignLeft | Qt.AlignVCenter
            elif hour == self.DAY_HOURS:
                label_x = int(x) - label_width
                align = Qt.AlignRight | Qt.AlignVCenter
            else:
                label_x = int(x) - label_width // 2
                align = Qt.AlignCenter
            painter.setPen(muted)
            painter.drawText(
                label_x,
                plot.bottom() + 8,
                label_width,
                18,
                align,
                label,
            )

        day_label = f"{start_time:%Y-%m-%d}  00:00–24:00"
        painter.setPen(muted)
        painter.drawText(
            int(plot.center().x()) - 85,
            plot.bottom() + 29,
            170,
            16,
            Qt.AlignCenter,
            day_label,
        )

        # 目标 SO₂ 参考线跟随右侧净烟气动态坐标轴。
        if self._latest_target is not None:
            target_y = self._value_y(
                self._latest_target,
                plot,
                jyq_min,
                jyq_max,
            )
            target_pen = QPen(target_color, 1)
            target_pen.setStyle(Qt.DashLine)
            painter.setPen(target_pen)
            painter.drawLine(plot.left(), int(target_y), plot.right(), int(target_y))
            painter.setPen(target_color)
            painter.drawText(
                plot.right() - 112,
                int(target_y) - 18,
                108,
                16,
                Qt.AlignRight | Qt.AlignVCenter,
                f"目标SO₂ {self._latest_target:.1f}",
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
                yyq_min,
                yyq_max,
                start_time,
                end_time,
            )
            jyq_points = self._series_points(
                samples,
                2,
                plot,
                jyq_min,
                jyq_max,
                start_time,
                end_time,
            )
            self._draw_path(painter, yyq_points, QPen(yyq_color, 2))
            self._draw_path(painter, jyq_points, QPen(jyq_color, 3))

        # 顶部只保留简洁图例和统一单位，不再重复写两套纵轴单位/量程。
        legend_y = 28
        painter.setFont(axis_font)
        painter.setPen(yyq_color)
        painter.drawText(plot.left() + 170, legend_y, "● 原烟气SO₂")
        painter.setPen(jyq_color)
        painter.drawText(plot.left() + 265, legend_y, "● 净烟气SO₂")
        painter.setPen(target_color)
        painter.drawText(plot.left() + 360, legend_y, "-- 目标SO₂")
        painter.setPen(muted)
        painter.drawText(plot.left() + 455, legend_y, "单位：mg/m³")

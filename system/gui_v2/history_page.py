from __future__ import annotations

import datetime as dt
import math
from collections import OrderedDict
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set

import pandas as pd
from PyQt5.QtCore import QDateTime, QPointF, QRectF, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCalendarWidget,
    QCheckBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from system.model.config.process4map_config import PROCESS4MAP_CONFIG
from system.model.config.standard_fields import TARGET_SO2_COLUMN

from .reason_text import (
    translate_control_mode,
    translate_decision_state,
    translate_experience_source,
    translate_magnitude,
    translate_reason_codes,
)
from .responsive_history_data_service import ResponsiveHistoryDataService
from .widgets import CardFrame


_SERIES_COLORS = (
    QColor("#4f8cff"),
    QColor("#23d5c3"),
    QColor("#f6b73c"),
    QColor("#b587ff"),
    QColor("#ff7f6e"),
    QColor("#70d36b"),
    QColor("#55b4ff"),
    QColor("#e58cff"),
)
_EVENT_COLORS = {
    "action": QColor("#23d5c3"),
    "condition": QColor("#4f8cff"),
    "fast": QColor("#f6b73c"),
    "recovery": QColor("#b587ff"),
    "blocked": QColor("#ff5f6d"),
    "target": QColor("#e6a23c"),
}


def _number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _to_datetime(value: Any) -> Optional[dt.datetime]:
    try:
        stamp = pd.to_datetime(value)
    except Exception:
        return None
    if pd.isna(stamp):
        return None
    return stamp.to_pydatetime() if hasattr(stamp, "to_pydatetime") else stamp


def _axis_range(values: Sequence[float], *, minimum_span: float = 1.0) -> tuple:
    finite = [float(value) for value in values if math.isfinite(float(value))]
    if not finite:
        return 0.0, minimum_span
    low = min(finite)
    high = max(finite)
    span = max(high - low, minimum_span)
    padding = max(span * 0.10, minimum_span * 0.05)
    low -= padding
    high += padding
    if low >= 0:
        low = max(0.0, low)
    if high <= low:
        high = low + minimum_span
    return low, high


def _format_duration(seconds: Any) -> str:
    value = _number(seconds)
    if value is None or value <= 0:
        return "0分钟"
    if value < 3600:
        return f"{value / 60.0:.0f}分钟"
    if value < 86400:
        return f"{value / 3600.0:.1f}小时"
    return f"{value / 86400.0:.1f}天"


class DateTimePicker(QDateTimeEdit):
    """带明确下拉标识的日期时间选择器。

    点击右侧三角按钮弹出“日期 + 时间”选择框，避免只看到纯色输入框却不知道可选择。
    """

    def __init__(self, value: QDateTime, parent=None):
        super().__init__(value, parent)
        self.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.setMinimumWidth(150)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setStyleSheet(
            """
            QDateTimeEdit {
                background-color: #0f1a2b;
                color: #e8f0ff;
                border: 1px solid #2a3b56;
                border-radius: 5px;
                padding: 5px 30px 5px 8px;
            }
            QDateTimeEdit:focus { border: 1px solid #4f8cff; }
            """
        )
        self.setToolTip("可直接编辑；点击右侧 ▼ 选择日期和时间")

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        x = float(self.width() - 17)
        y = float(self.height() / 2 + 1)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#9fb4d4" if self.isEnabled() else "#56657b"))
        painter.drawPolygon(
            QPolygonF(
                [
                    QPointF(x - 5, y - 3),
                    QPointF(x + 5, y - 3),
                    QPointF(x, y + 3),
                ]
            )
        )

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self.isEnabled() and event.pos().x() >= self.width() - 30:
            self._open_picker()
            event.accept()
            return
        super().mousePressEvent(event)

    def _open_picker(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("选择日期和时间")
        dialog.setModal(True)
        dialog.resize(340, 330)
        layout = QVBoxLayout(dialog)

        calendar = QCalendarWidget(dialog)
        calendar.setGridVisible(True)
        calendar.setSelectedDate(self.dateTime().date())
        layout.addWidget(calendar)

        time_row = QHBoxLayout()
        time_row.addWidget(QLabel("时间"))
        time_edit = QTimeEdit(self.dateTime().time(), dialog)
        time_edit.setDisplayFormat("HH:mm:ss")
        time_row.addWidget(time_edit, 1)
        layout.addLayout(time_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=dialog)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec_() == QDialog.Accepted:
            self.setDate(calendar.selectedDate())
            self.setTime(time_edit.time())


class HistoryQueryThread(QThread):
    result_ready = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, start: dt.datetime, end: dt.datetime, parent=None):
        super().__init__(parent)
        self.start_time = start
        self.end_time = end

    def run(self) -> None:
        service = None
        try:
            service = ResponsiveHistoryDataService()
            self.result_ready.emit(service.query(self.start_time, self.end_time))
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            if service is not None:
                service.close()


class SeriesSelector(QWidget):
    selection_changed = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QGridLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setHorizontalSpacing(12)
        self._layout.setVerticalSpacing(4)
        self._checks: Dict[str, QCheckBox] = {}
        self._known_state: Dict[str, bool] = {}

    def set_specs(self, specs: Sequence[Mapping[str, Any]]) -> None:
        wanted = [str(spec.get("column") or "") for spec in specs if str(spec.get("column") or "")]
        if wanted == list(self._checks.keys()):
            return

        for column, check in list(self._checks.items()):
            self._known_state[column] = check.isChecked()
            check.deleteLater()
        self._checks.clear()

        for index, spec in enumerate(specs):
            column = str(spec.get("column") or "")
            if not column:
                continue
            name = str(spec.get("name") or column)
            color_index = int(spec.get("color_index", index))
            color = _SERIES_COLORS[color_index % len(_SERIES_COLORS)]

            check = QCheckBox(f"● {name}")
            check.setChecked(self._known_state.get(column, True))
            check.setToolTip("勾选显示；取消勾选隐藏该历史曲线")
            check.setStyleSheet(
                "QCheckBox { color: %s; spacing: 6px; }"
                "QCheckBox::indicator { width: 14px; height: 14px; }" % color.name()
            )
            check.stateChanged.connect(self._emit_selection)
            check.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            self._checks[column] = check
            self._layout.addWidget(check, index // 3, index % 3)

        self._emit_selection()

    def selected_columns(self) -> Set[str]:
        return {column for column, check in self._checks.items() if check.isChecked()}

    def _emit_selection(self) -> None:
        self.selection_changed.emit(self.selected_columns())


class HistoryLineChart(QWidget):
    """高性能响应式历史曲线。

    数据坐标在 set_data 时预计算，窗口缩放时只根据新宽高组装 QPainterPath；不再逐事件
    画竖线，也不在 resize 时重新计算 pandas 数据。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(0)
        self.setMinimumHeight(255)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._times: List[dt.datetime] = []
        self._time_ratios: List[float] = []
        self._break_before: Set[int] = set()
        self._left_series: List[Dict[str, Any]] = []
        self._right_series: List[Dict[str, Any]] = []
        self._visible_columns: Set[str] = set()
        self._gaps: List[Dict[str, Any]] = []
        self._range_start: Optional[dt.datetime] = None
        self._range_end: Optional[dt.datetime] = None
        self._cursor_time: Optional[dt.datetime] = None
        self._left_unit = ""
        self._right_unit = ""
        self._left_range = (0.0, 1.0)
        self._right_range = (0.0, 10.0)

    def set_visible_columns(self, columns: Set[str]) -> None:
        self._visible_columns = set(columns)
        self._recalculate_ranges()
        self.update()

    def set_data(
        self,
        frame: pd.DataFrame,
        *,
        left_series: Sequence[Mapping[str, Any]],
        right_series: Sequence[Mapping[str, Any]],
        gaps: Sequence[Mapping[str, Any]],
        start: dt.datetime,
        end: dt.datetime,
        left_unit: str = "",
        right_unit: str = "",
    ) -> None:
        self._times = []
        self._time_ratios = []
        self._break_before = set()
        self._left_series = []
        self._right_series = []
        self._gaps = [dict(item) for item in gaps]
        self._range_start = start
        self._range_end = end
        self._left_unit = left_unit
        self._right_unit = right_unit

        valid = pd.DataFrame()
        if frame is not None and not frame.empty and "date" in frame.columns:
            valid = frame.copy()
            valid["date"] = pd.to_datetime(valid["date"], errors="coerce")
            valid = valid.dropna(subset=["date"]).reset_index(drop=True)
            self._times = [value.to_pydatetime() for value in valid["date"]]

            span = max(1.0, (end - start).total_seconds())
            self._time_ratios = [
                max(0.0, min(1.0, (value - start).total_seconds() / span))
                for value in self._times
            ]
            for index in range(1, len(self._times)):
                if self._crosses_gap(self._times[index - 1], self._times[index]):
                    self._break_before.add(index)

        all_specs = [dict(item) for item in left_series] + [dict(item) for item in right_series]
        if not self._visible_columns:
            self._visible_columns = {
                str(item.get("column") or "") for item in all_specs if str(item.get("column") or "")
            }

        for side, specs in (("left", left_series), ("right", right_series)):
            target = self._left_series if side == "left" else self._right_series
            for index, spec in enumerate(specs):
                column = str(spec.get("column") or "")
                if not column or column not in valid.columns:
                    continue
                values = [_number(value) for value in valid[column].tolist()]
                target.append(
                    {
                        "column": column,
                        "name": str(spec.get("name") or column),
                        "values": values,
                        "color": _SERIES_COLORS[int(spec.get("color_index", index)) % len(_SERIES_COLORS)],
                    }
                )

        self._recalculate_ranges()
        self.update()

    def set_cursor_time(self, value: Optional[dt.datetime]) -> None:
        self._cursor_time = value
        self.update()

    def _visible_series(self, series: Sequence[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
        return [item for item in series if str(item.get("column")) in self._visible_columns]

    @staticmethod
    def _collect_values(series: Sequence[Mapping[str, Any]]) -> List[float]:
        values: List[float] = []
        for item in series:
            for value in item.get("values", []):
                if value is not None:
                    values.append(float(value))
        return values

    def _recalculate_ranges(self) -> None:
        self._left_range = _axis_range(
            self._collect_values(self._visible_series(self._left_series)), minimum_span=1.0
        )
        self._right_range = _axis_range(
            self._collect_values(self._visible_series(self._right_series)), minimum_span=10.0
        )

    def _x(self, timestamp: dt.datetime, plot: QRectF) -> float:
        if self._range_start is None or self._range_end is None:
            return plot.left()
        span = max(1.0, (self._range_end - self._range_start).total_seconds())
        ratio = (timestamp - self._range_start).total_seconds() / span
        return plot.left() + max(0.0, min(1.0, ratio)) * plot.width()

    def _x_index(self, index: int, plot: QRectF) -> float:
        return plot.left() + self._time_ratios[index] * plot.width()

    @staticmethod
    def _y(value: float, low: float, high: float, plot: QRectF) -> float:
        ratio = (float(value) - low) / max(1e-12, high - low)
        return plot.bottom() - max(0.0, min(1.0, ratio)) * plot.height()

    def _crosses_gap(self, previous: dt.datetime, current: dt.datetime) -> bool:
        for gap in self._gaps:
            gap_start = _to_datetime(gap.get("start"))
            gap_end = _to_datetime(gap.get("end"))
            if gap_start is None or gap_end is None:
                continue
            if previous < gap_end and current > gap_start:
                return True
        return False

    def _draw_gap_regions(self, painter: QPainter, plot: QRectF) -> None:
        if self._range_start is None or self._range_end is None:
            return
        fill = QColor("#64748b")
        fill.setAlpha(42)
        text = QColor("#aebbd0")
        for gap in self._gaps:
            gap_start = _to_datetime(gap.get("start"))
            gap_end = _to_datetime(gap.get("end"))
            if gap_start is None or gap_end is None:
                continue
            clipped_start = max(self._range_start, gap_start)
            clipped_end = min(self._range_end, gap_end)
            if clipped_end <= clipped_start:
                continue
            x1 = self._x(clipped_start, plot)
            x2 = self._x(clipped_end, plot)
            region = QRectF(x1, plot.top(), max(1.0, x2 - x1), plot.height())
            painter.fillRect(region, fill)
            if region.width() >= 95:
                painter.setPen(text)
                painter.drawText(region, Qt.AlignCenter, "无历史数据\n未记录")

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setFont(QFont("Microsoft YaHei", 9))
        text_color = QColor("#9fb4d4")

        # 轴单位单独占顶部一行，选择器在图表控件之外，不再与图例挤在一起。
        plot = QRectF(self.rect()).adjusted(68, 34, -78, -40)
        if plot.width() <= 20 or plot.height() <= 20:
            return

        if self._range_start is None or self._range_end is None or self._range_end <= self._range_start:
            painter.setPen(text_color)
            painter.drawText(plot, Qt.AlignCenter, "暂无历史数据")
            return

        left_visible = bool(self._visible_series(self._left_series))
        right_visible = bool(self._visible_series(self._right_series))
        left_low, left_high = self._left_range
        right_low, right_high = self._right_range
        grid_pen = QPen(QColor("#23344d"), 1)

        if left_visible and self._left_unit:
            painter.setPen(text_color)
            painter.drawText(
                QRectF(plot.left(), 4, max(160.0, plot.width() * 0.45), 22),
                Qt.AlignLeft | Qt.AlignVCenter,
                f"左轴：{self._left_unit}",
            )
        if right_visible and self._right_unit:
            painter.setPen(text_color)
            painter.drawText(
                QRectF(plot.center().x(), 4, max(160.0, plot.width() * 0.5), 22),
                Qt.AlignRight | Qt.AlignVCenter,
                f"右轴：{self._right_unit}",
            )

        for index in range(6):
            ratio = index / 5.0
            y = plot.top() + ratio * plot.height()
            painter.setPen(grid_pen)
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(text_color)
            if left_visible:
                value = left_high - ratio * (left_high - left_low)
                painter.drawText(
                    QRectF(0, y - 9, plot.left() - 8, 18),
                    Qt.AlignRight | Qt.AlignVCenter,
                    f"{value:.1f}",
                )
            if right_visible:
                value = right_high - ratio * (right_high - right_low)
                painter.drawText(
                    QRectF(plot.right() + 8, y - 9, self.width() - plot.right() - 8, 18),
                    Qt.AlignLeft | Qt.AlignVCenter,
                    f"{value:.1f}",
                )

        span = self._range_end - self._range_start
        tick_count = 5 if self.width() < 950 else 7
        for index in range(tick_count):
            ratio = index / max(1, tick_count - 1)
            x = plot.left() + ratio * plot.width()
            painter.setPen(grid_pen)
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            stamp = self._range_start + span * ratio
            if span.total_seconds() > 86400:
                label = stamp.strftime("%m-%d\n%H:%M")
            else:
                label = stamp.strftime("%H:%M")
            painter.setPen(text_color)
            painter.drawText(
                QRectF(x - 42, plot.bottom() + 7, 84, 30),
                Qt.AlignHCenter | Qt.AlignTop,
                label,
            )

        self._draw_gap_regions(painter, plot)
        self._draw_series(painter, plot, self._visible_series(self._left_series), left_low, left_high)
        self._draw_series(painter, plot, self._visible_series(self._right_series), right_low, right_high)

        if not self._times:
            painter.setPen(text_color)
            painter.drawText(plot, Qt.AlignCenter, "暂无历史数据")

        if self._cursor_time is not None and self._range_start <= self._cursor_time <= self._range_end:
            x = self._x(self._cursor_time, plot)
            painter.setPen(QPen(QColor("#ffffff"), 1, Qt.DashLine))
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))

    def _draw_series(
        self,
        painter: QPainter,
        plot: QRectF,
        series: Sequence[Mapping[str, Any]],
        low: float,
        high: float,
    ) -> None:
        for item in series:
            path = QPainterPath()
            path_started = False
            values = item.get("values", [])
            for index, value in enumerate(values):
                if index >= len(self._times) or value is None:
                    path_started = False
                    continue
                point = QPointF(
                    self._x_index(index, plot),
                    self._y(float(value), low, high, plot),
                )
                if not path_started or index in self._break_before:
                    path.moveTo(point)
                    path_started = True
                else:
                    path.lineTo(point)
            painter.setPen(QPen(item["color"], 2))
            painter.drawPath(path)


class HistoryChartPanel(CardFrame):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(0)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(8)

        title_label = QLabel(title)
        title_label.setProperty("role", "sectionTitle")
        layout.addWidget(title_label)

        self.selector = SeriesSelector()
        layout.addWidget(self.selector)
        layout.addSpacing(4)

        self.chart = HistoryLineChart()
        layout.addWidget(self.chart)
        self.selector.selection_changed.connect(self.chart.set_visible_columns)

    def set_data(
        self,
        frame: pd.DataFrame,
        *,
        left_series: Sequence[Mapping[str, Any]],
        right_series: Sequence[Mapping[str, Any]],
        gaps: Sequence[Mapping[str, Any]],
        start: dt.datetime,
        end: dt.datetime,
        left_unit: str,
        right_unit: str,
    ) -> None:
        specs: List[Dict[str, Any]] = []
        for index, raw in enumerate([*left_series, *right_series]):
            spec = dict(raw)
            spec.setdefault("color_index", index)
            specs.append(spec)
        left_count = len(left_series)
        prepared_left = specs[:left_count]
        prepared_right = specs[left_count:]
        self.selector.set_specs(specs)
        self.chart.set_data(
            frame,
            left_series=prepared_left,
            right_series=prepared_right,
            gaps=gaps,
            start=start,
            end=end,
            left_unit=left_unit,
            right_unit=right_unit,
        )
        self.chart.set_visible_columns(self.selector.selected_columns())


class EventTimelineWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(0)
        self.setMinimumHeight(105)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._events: List[Dict[str, Any]] = []
        self._gaps: List[Dict[str, Any]] = []
        self._start: Optional[dt.datetime] = None
        self._end: Optional[dt.datetime] = None
        self._cursor_time: Optional[dt.datetime] = None

    def set_data(
        self,
        events: Sequence[Mapping[str, Any]],
        gaps: Sequence[Mapping[str, Any]],
        start: dt.datetime,
        end: dt.datetime,
    ) -> None:
        self._events = [dict(item) for item in events]
        self._gaps = [dict(item) for item in gaps]
        self._start = start
        self._end = end
        self.update()

    def set_cursor_time(self, value: Optional[dt.datetime]) -> None:
        self._cursor_time = value
        self.update()

    def _x(self, timestamp: dt.datetime, rect: QRectF) -> float:
        if self._start is None or self._end is None:
            return rect.left()
        span = max(1.0, (self._end - self._start).total_seconds())
        ratio = (timestamp - self._start).total_seconds() / span
        return rect.left() + max(0.0, min(1.0, ratio)) * rect.width()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setFont(QFont("Microsoft YaHei", 9))
        rect = QRectF(self.rect()).adjusted(20, 15, -20, -15)
        baseline_y = rect.center().y() + 8

        if self._start is None or self._end is None or self._end <= self._start:
            painter.setPen(QColor("#9fb4d4"))
            painter.drawText(rect, Qt.AlignCenter, "暂无控制事件")
            return

        gap_fill = QColor("#64748b")
        gap_fill.setAlpha(45)
        for gap in self._gaps:
            gap_start = _to_datetime(gap.get("start"))
            gap_end = _to_datetime(gap.get("end"))
            if gap_start is None or gap_end is None:
                continue
            clipped_start = max(self._start, gap_start)
            clipped_end = min(self._end, gap_end)
            if clipped_end <= clipped_start:
                continue
            x1 = self._x(clipped_start, rect)
            x2 = self._x(clipped_end, rect)
            painter.fillRect(QRectF(x1, rect.top(), max(1.0, x2 - x1), rect.height()), gap_fill)

        painter.setPen(QPen(QColor("#34445f"), 2))
        painter.drawLine(QPointF(rect.left(), baseline_y), QPointF(rect.right(), baseline_y))

        visible: List[tuple] = []
        occupied: Set[int] = set()
        for item in self._events:
            stamp = _to_datetime(item.get("time"))
            if stamp is None or stamp < self._start or stamp > self._end:
                continue
            x = self._x(stamp, rect)
            bin_id = int(x // 5)
            if bin_id in occupied:
                continue
            occupied.add(bin_id)
            visible.append((item, x))

        label_limit = 7 if self.width() < 1000 else 9
        label_step = max(1, math.ceil(max(1, len(visible)) / label_limit))
        labeled = 0
        for index, (item, x) in enumerate(visible):
            color = _EVENT_COLORS.get(str(item.get("type")), QColor("#64748b"))
            painter.setPen(QPen(color, 2))
            painter.setBrush(color)
            painter.drawEllipse(QPointF(x, baseline_y), 3.5, 3.5)
            if index % label_step == 0 and labeled < label_limit:
                painter.setPen(QColor("#c9d8ee"))
                painter.drawText(
                    QRectF(x - 55, baseline_y - 38, 110, 22),
                    Qt.AlignHCenter | Qt.AlignBottom,
                    str(item.get("title") or "事件"),
                )
                labeled += 1

        if not visible and not self._gaps:
            painter.setPen(QColor("#9fb4d4"))
            painter.drawText(rect, Qt.AlignCenter, "当前时间范围内没有控制事件")

        if self._cursor_time is not None and self._start <= self._cursor_time <= self._end:
            x = self._x(self._cursor_time, rect)
            painter.setPen(QPen(QColor("#ffffff"), 1, Qt.DashLine))
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))


class HistoryPage(QWidget):
    """数据库历史回放 + 实时跟随双模式页面。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._worker: Optional[HistoryQueryThread] = None
        self._cache: "OrderedDict[tuple, Dict[str, Any]]" = OrderedDict()
        self._events: List[Dict[str, Any]] = []
        self._gaps: List[Dict[str, Any]] = []
        self._queried_once = False
        self._live_mode = False
        self._active_span_seconds = 24 * 3600

        refresh_seconds = max(10.0, float(PROCESS4MAP_CONFIG.runtime.snapshot_interval_seconds))
        self._live_timer = QTimer(self)
        self._live_timer.setInterval(int(refresh_seconds * 1000))
        self._live_timer.timeout.connect(self._refresh_live)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        title = QLabel("历史趋势与控制回放")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 22px; font-weight: 700; padding: 6px;")
        root.addWidget(title)
        root.addWidget(self._build_query_bar())

        self.so2_panel = HistoryChartPanel("烟气 SO₂ 趋势")
        root.addWidget(self.so2_panel)

        self.supply_panel = HistoryChartPanel("供浆过程趋势")
        root.addWidget(self.supply_panel)

        self.circulation_panel = HistoryChartPanel("浆液循环泵历史趋势")
        root.addWidget(self.circulation_panel)

        event_card = CardFrame()
        event_layout = QVBoxLayout(event_card)
        event_layout.setContentsMargins(16, 14, 16, 12)
        event_layout.setSpacing(6)
        section = QLabel("智能控制事件时间线")
        section.setProperty("role", "sectionTitle")
        event_layout.addWidget(section)
        note = QLabel("灰色区间表示数据库无历史记录；事件只在本时间线展示，趋势图不重复绘制事件竖线。")
        note.setProperty("role", "muted")
        event_layout.addWidget(note)
        self.timeline = EventTimelineWidget()
        event_layout.addWidget(self.timeline)
        root.addWidget(event_card)

        table_card = CardFrame()
        table_layout = QVBoxLayout(table_card)
        table_layout.setContentsMargins(16, 14, 16, 12)
        section = QLabel("历史控制事件（最近100条）")
        section.setProperty("role", "sectionTitle")
        table_layout.addWidget(section)
        self.event_table = self._build_event_table()
        table_layout.addWidget(self.event_table)
        root.addWidget(table_card)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._queried_once:
            self._queried_once = True
            QTimer.singleShot(100, self.query_history)
        if self._live_mode and not self._live_timer.isActive():
            self._live_timer.start()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._live_timer.stop()
        super().hideEvent(event)

    def _build_query_bar(self) -> QWidget:
        card = CardFrame()
        outer = QVBoxLayout(card)
        outer.setContentsMargins(16, 10, 16, 10)
        outer.setSpacing(8)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(8)
        mode_row.addWidget(QLabel("模式"))
        self.history_mode_button = QPushButton("历史查询")
        self.live_mode_button = QPushButton("实时跟随")
        for button in (self.history_mode_button, self.live_mode_button):
            button.setCheckable(True)
        group = QButtonGroup(self)
        group.setExclusive(True)
        group.addButton(self.history_mode_button)
        group.addButton(self.live_mode_button)
        self.history_mode_button.setChecked(True)
        self.history_mode_button.clicked.connect(lambda: self._set_mode(False))
        self.live_mode_button.clicked.connect(lambda: self._set_mode(True))
        mode_row.addWidget(self.history_mode_button)
        mode_row.addWidget(self.live_mode_button)
        mode_row.addSpacing(14)
        mode_row.addWidget(QLabel("时间窗口"))

        self._range_buttons: Dict[int, QPushButton] = {}
        for label, seconds in (
            ("1小时", 3600),
            ("6小时", 6 * 3600),
            ("24小时", 24 * 3600),
            ("3天", 3 * 86400),
            ("7天", 7 * 86400),
        ):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, span=seconds: self._set_quick_range(span))
            self._range_buttons[seconds] = button
            mode_row.addWidget(button)
        mode_row.addStretch(1)
        outer.addLayout(mode_row)

        time_row = QGridLayout()
        time_row.setHorizontalSpacing(8)
        time_row.setColumnStretch(1, 1)
        time_row.setColumnStretch(3, 1)
        now = QDateTime.currentDateTime()
        self.start_edit = DateTimePicker(now.addSecs(-24 * 3600))
        self.end_edit = DateTimePicker(now)
        time_row.addWidget(QLabel("开始"), 0, 0)
        time_row.addWidget(self.start_edit, 0, 1)
        time_row.addWidget(QLabel("结束"), 0, 2)
        time_row.addWidget(self.end_edit, 0, 3)
        self.query_button = QPushButton("查询")
        self.query_button.clicked.connect(self.query_history)
        time_row.addWidget(self.query_button, 0, 4)
        outer.addLayout(time_row)

        self.status_label = QLabel("历史数据源：PostgreSQL")
        self.status_label.setProperty("role", "muted")
        self.status_label.setWordWrap(True)
        outer.addWidget(self.status_label)
        return card

    @staticmethod
    def _build_event_table() -> QTableWidget:
        table = QTableWidget(0, 7)
        table.setHorizontalHeaderLabels(("时间", "工况", "事件/动作", "强度", "经验来源", "控制状态", "中文说明"))
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.verticalHeader().setVisible(False)
        table.setMinimumWidth(0)
        table.setMinimumHeight(220)
        table.setStyleSheet(
            """
            QTableWidget {
                background-color: #0d1726;
                color: #dbe8fb;
                gridline-color: #23344d;
                border: 1px solid #23344d;
            }
            QTableWidget::item:selected { background-color: #24466f; color: #ffffff; }
            QHeaderView::section {
                background-color: #162338;
                color: #b9c9df;
                padding: 5px;
                border: 0px;
                border-right: 1px solid #263851;
                border-bottom: 1px solid #263851;
            }
            """
        )
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        return table

    def _set_mode(self, live: bool) -> None:
        self._live_mode = bool(live)
        self.start_edit.setEnabled(not live)
        self.end_edit.setEnabled(not live)
        self.query_button.setText("刷新" if live else "查询")

        # 实时跟随最多保留24小时窗口，避免每30秒重复查询超长周期历史。
        for seconds, button in self._range_buttons.items():
            button.setEnabled((not live) or seconds <= 24 * 3600)

        if live:
            if self._active_span_seconds > 24 * 3600:
                self._active_span_seconds = 3600
            self.live_mode_button.setChecked(True)
            self._refresh_live()
            if self.isVisible():
                self._live_timer.start()
        else:
            self.history_mode_button.setChecked(True)
            self._live_timer.stop()
            self.status_label.setText("历史模式 · 请选择时间范围后查询")

    def _set_quick_range(self, seconds: int) -> None:
        self._active_span_seconds = int(seconds)
        end = QDateTime.currentDateTime()
        self.end_edit.setDateTime(end)
        self.start_edit.setDateTime(end.addSecs(-int(seconds)))
        if self._live_mode:
            self._refresh_live()
        else:
            self.query_history()

    @staticmethod
    def _python_datetime(edit: QDateTimeEdit) -> dt.datetime:
        return edit.dateTime().toPyDateTime().replace(tzinfo=None)

    def _refresh_live(self) -> None:
        if not self._live_mode:
            return
        if self._worker is not None and self._worker.isRunning():
            return
        end = QDateTime.currentDateTime()
        start = end.addSecs(-int(self._active_span_seconds))
        self.end_edit.setDateTime(end)
        self.start_edit.setDateTime(start)
        self.query_history(force=True)

    def query_history(self, force: bool = False) -> None:
        if self._worker is not None and self._worker.isRunning():
            return

        start = self._python_datetime(self.start_edit)
        end = self._python_datetime(self.end_edit)
        if end <= start:
            self.status_label.setText("结束时间必须晚于开始时间")
            return

        key = (start.isoformat(), end.isoformat())
        if not force and not self._live_mode:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                self._apply_result(cached, source="页面缓存")
                return

        self.query_button.setEnabled(False)
        self.status_label.setText("实时跟随：正在读取最新历史…" if self._live_mode else "正在从 PostgreSQL 读取历史数据…")
        self._worker = HistoryQueryThread(start, end, self)
        self._worker.result_ready.connect(lambda result, cache_key=key: self._on_query_result(cache_key, result))
        self._worker.failed.connect(self._on_query_error)
        self._worker.finished.connect(self._on_query_finished)
        self._worker.start()

    def _on_query_result(self, key: tuple, result: Dict[str, Any]) -> None:
        if not self._live_mode:
            self._cache[key] = result
            self._cache.move_to_end(key)
            while len(self._cache) > 5:
                self._cache.popitem(last=False)
        self._apply_result(result, source="实时跟随" if self._live_mode else "PostgreSQL")

    def _on_query_error(self, message: str) -> None:
        self.status_label.setText(f"历史查询失败：{message}")

    def _on_query_finished(self) -> None:
        self.query_button.setEnabled(True)
        self._worker = None

    def _apply_result(self, result: Mapping[str, Any], *, source: str) -> None:
        process = result.get("process")
        if not isinstance(process, pd.DataFrame):
            process = pd.DataFrame()
        events = result.get("events") if isinstance(result.get("events"), list) else []
        gaps = result.get("gaps") if isinstance(result.get("gaps"), list) else []
        self._events = [dict(item) for item in events]
        self._gaps = [dict(item) for item in gaps]

        start = result.get("start")
        end = result.get("end")
        start = start if isinstance(start, dt.datetime) else self._python_datetime(self.start_edit)
        end = end if isinstance(end, dt.datetime) else self._python_datetime(self.end_edit)

        so2_left = [{"column": "yyq_SO2", "name": "原烟气 SO₂"}]
        so2_right = [
            {"column": "jyq_SO2", "name": "净烟气 SO₂"},
            {"column": TARGET_SO2_COLUMN, "name": "目标 SO₂"},
        ]
        self.so2_panel.set_data(
            process,
            left_series=so2_left,
            right_series=so2_right,
            gaps=self._gaps,
            start=start,
            end=end,
            left_unit="mg/Nm³（原烟气）",
            right_unit="mg/Nm³（净烟气 / 目标）",
        )

        meta = result.get("process_meta") if isinstance(result.get("process_meta"), Mapping) else {}
        ph_series = meta.get("ph_series")
        supply_left = [ph_series] if isinstance(ph_series, Mapping) else []
        supply_right = meta.get("supply_series") if isinstance(meta.get("supply_series"), list) else []
        self.supply_panel.set_data(
            process,
            left_series=supply_left,
            right_series=supply_right,
            gaps=self._gaps,
            start=start,
            end=end,
            left_unit="pH",
            right_unit="供浆流量 / 阀位 / 泵反馈（原值）",
        )

        circulation = meta.get("circulation_series") if isinstance(meta.get("circulation_series"), list) else []
        self.circulation_panel.set_data(
            process,
            left_series=circulation,
            right_series=[],
            gaps=self._gaps,
            start=start,
            end=end,
            left_unit="A（循环泵电流）",
            right_unit="",
        )

        self.timeline.set_data(self._events, self._gaps, start, end)
        self._populate_event_table(self._events)

        gap_count = int(result.get("gap_count", 0) or 0)
        gap_text = ""
        if gap_count:
            gap_text = f" · 数据缺口 {gap_count} 段（约{_format_duration(result.get('gap_duration_seconds'))}）"
        raw_points = int(result.get("raw_process_point_count", result.get("process_point_count", 0)) or 0)
        shown_points = int(result.get("process_point_count", 0) or 0)
        point_text = f"原始 {raw_points} 点"
        if shown_points != raw_points:
            point_text += f" / 展示 {shown_points} 点"
        refresh_text = " · 自动约每%d秒刷新" % max(10, int(PROCESS4MAP_CONFIG.runtime.snapshot_interval_seconds)) if self._live_mode else ""
        self.status_label.setText(
            f"{source} · {point_text} · 控制事件 {int(result.get('event_count', 0) or 0)} 条{gap_text}{refresh_text}"
        )

    def _populate_event_table(self, events: Sequence[Mapping[str, Any]]) -> None:
        self.event_table.setUpdatesEnabled(False)
        try:
            self.event_table.setRowCount(0)
            display_events = list(events)[-100:]
            for row_index, event in enumerate(display_events):
                self.event_table.insertRow(row_index)
                stamp = _to_datetime(event.get("time"))
                time_text = stamp.strftime("%Y-%m-%d %H:%M:%S") if stamp else "--"
                condition = str(event.get("condition") or "--")
                title = str(event.get("title") or "--")
                magnitude = translate_magnitude(event.get("magnitude")) if event.get("magnitude") else "--"
                source = translate_experience_source(event.get("source")) if event.get("source") else "--"
                status = (
                    translate_decision_state(event.get("status"))
                    if event.get("status")
                    else translate_control_mode(event.get("mode"))
                )
                reasons = event.get("reason_codes") if isinstance(event.get("reason_codes"), list) else []
                details = translate_reason_codes(reasons)
                explanation = details[0] if details else title
                values = (time_text, condition, title, magnitude, source, status or "--", explanation)
                for column, value in enumerate(values):
                    item = QTableWidgetItem(str(value))
                    item.setToolTip(str(value))
                    self.event_table.setItem(row_index, column, item)
        finally:
            self.event_table.setUpdatesEnabled(True)

        try:
            self.event_table.cellClicked.disconnect()
        except Exception:
            pass
        self.event_table.cellClicked.connect(
            lambda row, column, items=list(events)[-100:]: self._focus_event(items, row)
        )

    def _focus_event(self, events: Sequence[Mapping[str, Any]], row: int) -> None:
        if row < 0 or row >= len(events):
            return
        stamp = _to_datetime(events[row].get("time"))
        self.so2_panel.chart.set_cursor_time(stamp)
        self.supply_panel.chart.set_cursor_time(stamp)
        self.circulation_panel.chart.set_cursor_time(stamp)
        self.timeline.set_cursor_time(stamp)

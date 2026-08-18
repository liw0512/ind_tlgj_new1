from __future__ import annotations

import datetime as dt
import math
from collections import OrderedDict
from typing import Any, Dict, List, Mapping, Optional, Sequence

import pandas as pd
from PyQt5.QtCore import QDateTime, QPointF, QRectF, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter, QPen
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QDateTimeEdit,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from system.model.config.standard_fields import TARGET_SO2_COLUMN

from .history_data_service import HistoryDataService
from .reason_text import (
    summarize_reason_codes,
    translate_control_mode,
    translate_decision_state,
    translate_experience_source,
    translate_magnitude,
    translate_reason_codes,
)
from .widgets import CardFrame


_SERIES_COLORS = (
    QColor("#4f8cff"),
    QColor("#23d5c3"),
    QColor("#f6b73c"),
    QColor("#b587ff"),
    QColor("#ff7f6e"),
    QColor("#70d36b"),
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


def _axis_range(values: Sequence[float], *, minimum_span: float = 1.0) -> tuple[float, float]:
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
            service = HistoryDataService()
            self.result_ready.emit(service.query(self.start_time, self.end_time))
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            if service is not None:
                service.close()


class HistoryLineChart(QWidget):
    """轻量双 Y 轴历史曲线；不引入第三方绘图库。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(280)
        self._times: List[dt.datetime] = []
        self._left_series: List[Dict[str, Any]] = []
        self._right_series: List[Dict[str, Any]] = []
        self._events: List[Dict[str, Any]] = []
        self._cursor_time: Optional[dt.datetime] = None
        self._left_unit = ""
        self._right_unit = ""
        self._empty_text = "暂无历史数据"

    def set_data(
        self,
        frame: pd.DataFrame,
        *,
        left_series: Sequence[Mapping[str, Any]],
        right_series: Sequence[Mapping[str, Any]],
        events: Sequence[Mapping[str, Any]],
        left_unit: str = "",
        right_unit: str = "",
    ) -> None:
        self._times = []
        self._left_series = []
        self._right_series = []
        self._events = [dict(item) for item in events]
        self._left_unit = left_unit
        self._right_unit = right_unit

        if frame is not None and not frame.empty and "date" in frame.columns:
            self._times = [
                item for item in (_to_datetime(value) for value in frame["date"].tolist())
                if item is not None
            ]
            if len(self._times) != len(frame):
                valid = frame.copy()
                valid["date"] = pd.to_datetime(valid["date"], errors="coerce")
                valid = valid.dropna(subset=["date"])
                self._times = [value.to_pydatetime() for value in valid["date"]]
                frame = valid.reset_index(drop=True)

            for index, spec in enumerate(left_series):
                column = str(spec.get("column") or "")
                if column not in frame.columns:
                    continue
                values = [_number(value) for value in frame[column].tolist()]
                self._left_series.append({
                    "name": str(spec.get("name") or column),
                    "values": values,
                    "color": _SERIES_COLORS[index % len(_SERIES_COLORS)],
                })

            offset = len(self._left_series)
            for index, spec in enumerate(right_series):
                column = str(spec.get("column") or "")
                if column not in frame.columns:
                    continue
                values = [_number(value) for value in frame[column].tolist()]
                self._right_series.append({
                    "name": str(spec.get("name") or column),
                    "values": values,
                    "color": _SERIES_COLORS[(offset + index) % len(_SERIES_COLORS)],
                })
        self.update()

    def set_cursor_time(self, value: Optional[dt.datetime]) -> None:
        self._cursor_time = value
        self.update()

    @staticmethod
    def _collect_values(series: Sequence[Mapping[str, Any]]) -> List[float]:
        values: List[float] = []
        for item in series:
            for value in item.get("values", []):
                if value is not None:
                    values.append(float(value))
        return values

    def _x(self, timestamp: dt.datetime, plot: QRectF) -> float:
        if not self._times:
            return plot.left()
        start = self._times[0]
        end = self._times[-1]
        span = max(1.0, (end - start).total_seconds())
        ratio = (timestamp - start).total_seconds() / span
        return plot.left() + max(0.0, min(1.0, ratio)) * plot.width()

    @staticmethod
    def _y(value: float, low: float, high: float, plot: QRectF) -> float:
        ratio = (float(value) - low) / max(1e-12, high - low)
        return plot.bottom() - max(0.0, min(1.0, ratio)) * plot.height()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        plot = QRectF(self.rect()).adjusted(64, 38, -64, -42)
        if plot.width() <= 10 or plot.height() <= 10:
            return

        grid_pen = QPen(QColor("#23344d"), 1)
        text_color = QColor("#9fb4d4")
        painter.setFont(QFont("Microsoft YaHei", 9))

        if len(self._times) < 2:
            painter.setPen(text_color)
            painter.drawText(plot, Qt.AlignCenter, self._empty_text)
            return

        left_values = self._collect_values(self._left_series)
        right_values = self._collect_values(self._right_series)
        left_low, left_high = _axis_range(left_values, minimum_span=1.0)
        right_low, right_high = _axis_range(right_values, minimum_span=10.0)

        for index in range(6):
            ratio = index / 5.0
            y = plot.top() + ratio * plot.height()
            painter.setPen(grid_pen)
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))

            left_value = left_high - ratio * (left_high - left_low)
            right_value = right_high - ratio * (right_high - right_low)
            painter.setPen(text_color)
            painter.drawText(QRectF(0, y - 9, plot.left() - 8, 18), Qt.AlignRight | Qt.AlignVCenter, f"{left_value:.1f}")
            painter.drawText(QRectF(plot.right() + 8, y - 9, self.width() - plot.right() - 8, 18), Qt.AlignLeft | Qt.AlignVCenter, f"{right_value:.1f}")

        start = self._times[0]
        end = self._times[-1]
        span = end - start
        for index in range(7):
            ratio = index / 6.0
            x = plot.left() + ratio * plot.width()
            painter.setPen(grid_pen)
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            stamp = start + span * ratio
            label = stamp.strftime("%m-%d\n%H:%M") if span.total_seconds() > 86400 else stamp.strftime("%H:%M")
            painter.setPen(text_color)
            painter.drawText(QRectF(x - 38, plot.bottom() + 7, 76, 30), Qt.AlignHCenter | Qt.AlignTop, label)

        if self._left_unit:
            painter.setPen(text_color)
            painter.drawText(QRectF(plot.left(), 4, 120, 18), Qt.AlignLeft, f"左轴：{self._left_unit}")
        if self._right_unit:
            painter.setPen(text_color)
            painter.drawText(QRectF(plot.right() - 120, 4, 120, 18), Qt.AlignRight, f"右轴：{self._right_unit}")

        legend_x = plot.left() + 130
        legend_y = 13
        for item in [*self._left_series, *self._right_series]:
            painter.setPen(QPen(item["color"], 3))
            painter.drawLine(QPointF(legend_x, legend_y), QPointF(legend_x + 16, legend_y))
            painter.setPen(text_color)
            name = str(item["name"])
            painter.drawText(QRectF(legend_x + 21, 4, 120, 20), Qt.AlignLeft | Qt.AlignVCenter, name)
            legend_x += 28 + min(130, max(58, len(name) * 12))

        for event_item in self._events:
            timestamp = _to_datetime(event_item.get("time"))
            if timestamp is None or timestamp < start or timestamp > end:
                continue
            x = self._x(timestamp, plot)
            color = _EVENT_COLORS.get(str(event_item.get("type")), QColor("#64748b"))
            pen = QPen(color, 1, Qt.DashLine)
            painter.setPen(pen)
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))

        self._draw_series(painter, plot, self._left_series, left_low, left_high)
        self._draw_series(painter, plot, self._right_series, right_low, right_high)

        if self._cursor_time is not None and start <= self._cursor_time <= end:
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
            painter.setPen(QPen(item["color"], 2))
            previous: Optional[QPointF] = None
            values = item.get("values", [])
            for index, value in enumerate(values):
                if index >= len(self._times) or value is None:
                    previous = None
                    continue
                point = QPointF(
                    self._x(self._times[index], plot),
                    self._y(float(value), low, high, plot),
                )
                if previous is not None:
                    painter.drawLine(previous, point)
                previous = point


class EventTimelineWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(125)
        self._events: List[Dict[str, Any]] = []
        self._start: Optional[dt.datetime] = None
        self._end: Optional[dt.datetime] = None
        self._cursor_time: Optional[dt.datetime] = None

    def set_data(self, events: Sequence[Mapping[str, Any]], start: dt.datetime, end: dt.datetime) -> None:
        self._events = [dict(item) for item in events]
        self._start = start
        self._end = end
        self.update()

    def set_cursor_time(self, value: Optional[dt.datetime]) -> None:
        self._cursor_time = value
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setFont(QFont("Microsoft YaHei", 9))
        rect = QRectF(self.rect()).adjusted(24, 18, -24, -18)
        baseline_y = rect.center().y()
        painter.setPen(QPen(QColor("#34445f"), 2))
        painter.drawLine(QPointF(rect.left(), baseline_y), QPointF(rect.right(), baseline_y))

        if self._start is None or self._end is None or self._end <= self._start:
            painter.setPen(QColor("#9fb4d4"))
            painter.drawText(rect, Qt.AlignCenter, "暂无控制事件")
            return

        span = max(1.0, (self._end - self._start).total_seconds())
        labeled = 0
        label_limit = 10
        event_count = max(1, len(self._events))
        label_step = max(1, math.ceil(event_count / label_limit))

        for index, item in enumerate(self._events):
            stamp = _to_datetime(item.get("time"))
            if stamp is None or stamp < self._start or stamp > self._end:
                continue
            ratio = (stamp - self._start).total_seconds() / span
            x = rect.left() + ratio * rect.width()
            color = _EVENT_COLORS.get(str(item.get("type")), QColor("#64748b"))
            painter.setPen(QPen(color, 2))
            painter.setBrush(color)
            painter.drawEllipse(QPointF(x, baseline_y), 4, 4)

            if index % label_step == 0 and labeled < label_limit:
                painter.setPen(color)
                painter.drawLine(QPointF(x, baseline_y - 5), QPointF(x, baseline_y - 22))
                painter.setPen(QColor("#c9d8ee"))
                text = str(item.get("title") or "事件")
                painter.drawText(QRectF(x - 55, baseline_y - 48, 110, 24), Qt.AlignHCenter | Qt.AlignBottom, text)
                labeled += 1

        if not self._events:
            painter.setPen(QColor("#9fb4d4"))
            painter.drawText(rect, Qt.AlignCenter, "当前时间范围内没有控制事件")

        if self._cursor_time is not None and self._start <= self._cursor_time <= self._end:
            ratio = (self._cursor_time - self._start).total_seconds() / span
            x = rect.left() + ratio * rect.width()
            painter.setPen(QPen(QColor("#ffffff"), 1, Qt.DashLine))
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))


class HistoryPage(QWidget):
    """第四页：数据库历史回放、供浆过程与模型控制事件。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: Optional[HistoryQueryThread] = None
        self._cache: "OrderedDict[tuple[str, str], Dict[str, Any]]" = OrderedDict()
        self._events: List[Dict[str, Any]] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        title = QLabel("历史趋势与控制回放")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 22px; font-weight: 700; padding: 6px;")
        root.addWidget(title)

        root.addWidget(self._build_query_bar())

        so2_card = CardFrame()
        so2_layout = QVBoxLayout(so2_card)
        so2_layout.setContentsMargins(16, 14, 16, 12)
        section = QLabel("烟气 SO₂ 历史趋势")
        section.setProperty("role", "sectionTitle")
        so2_layout.addWidget(section)
        self.so2_chart = HistoryLineChart()
        so2_layout.addWidget(self.so2_chart)
        root.addWidget(so2_card)

        supply_card = CardFrame()
        supply_layout = QVBoxLayout(supply_card)
        supply_layout.setContentsMargins(16, 14, 16, 12)
        section = QLabel("供浆过程历史趋势")
        section.setProperty("role", "sectionTitle")
        supply_layout.addWidget(section)
        self.supply_chart = HistoryLineChart()
        supply_layout.addWidget(self.supply_chart)
        root.addWidget(supply_card)

        event_card = CardFrame()
        event_layout = QVBoxLayout(event_card)
        event_layout.setContentsMargins(16, 14, 16, 12)
        section = QLabel("智能控制事件时间线")
        section.setProperty("role", "sectionTitle")
        event_layout.addWidget(section)
        self.timeline = EventTimelineWidget()
        event_layout.addWidget(self.timeline)
        root.addWidget(event_card)

        table_card = CardFrame()
        table_layout = QVBoxLayout(table_card)
        table_layout.setContentsMargins(16, 14, 16, 12)
        section = QLabel("历史控制事件")
        section.setProperty("role", "sectionTitle")
        table_layout.addWidget(section)
        self.event_table = self._build_event_table()
        table_layout.addWidget(self.event_table)
        root.addWidget(table_card)

        QTimer.singleShot(200, self.query_history)

    def _build_query_bar(self) -> QWidget:
        card = CardFrame()
        layout = QHBoxLayout(card)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(8)

        now = QDateTime.currentDateTime()
        self.start_edit = QDateTimeEdit(now.addSecs(-24 * 3600))
        self.end_edit = QDateTimeEdit(now)
        for edit in (self.start_edit, self.end_edit):
            edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
            edit.setCalendarPopup(True)
            edit.setMinimumWidth(168)

        layout.addWidget(QLabel("开始"))
        layout.addWidget(self.start_edit)
        layout.addWidget(QLabel("结束"))
        layout.addWidget(self.end_edit)

        for label, seconds in (
            ("1小时", 3600),
            ("6小时", 6 * 3600),
            ("24小时", 24 * 3600),
            ("3天", 3 * 86400),
            ("7天", 7 * 86400),
        ):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, span=seconds: self._set_quick_range(span))
            layout.addWidget(button)

        self.query_button = QPushButton("查询")
        self.query_button.clicked.connect(self.query_history)
        layout.addWidget(self.query_button)
        layout.addStretch(1)

        self.status_label = QLabel("历史数据源：PostgreSQL")
        self.status_label.setProperty("role", "muted")
        layout.addWidget(self.status_label)
        return card

    @staticmethod
    def _build_event_table() -> QTableWidget:
        table = QTableWidget(0, 7)
        table.setHorizontalHeaderLabels(("时间", "工况", "事件/动作", "强度", "经验来源", "控制状态", "中文说明"))
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setAlternatingRowColors(False)
        table.verticalHeader().setVisible(False)
        table.setMinimumHeight(230)
        header = table.horizontalHeader()
        header.setStretchLastSection(True)
        for index, width in enumerate((145, 70, 110, 75, 120, 90)):
            table.setColumnWidth(index, width)
        return table

    def _set_quick_range(self, seconds: int) -> None:
        end = QDateTime.currentDateTime()
        self.end_edit.setDateTime(end)
        self.start_edit.setDateTime(end.addSecs(-int(seconds)))
        self.query_history()

    @staticmethod
    def _python_datetime(edit: QDateTimeEdit) -> dt.datetime:
        value = edit.dateTime().toPyDateTime()
        return value.replace(tzinfo=None)

    def query_history(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return

        start = self._python_datetime(self.start_edit)
        end = self._python_datetime(self.end_edit)
        if end <= start:
            self.status_label.setText("结束时间必须晚于开始时间")
            return

        key = (start.isoformat(), end.isoformat())
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            self._apply_result(cached, source="页面缓存")
            return

        self.query_button.setEnabled(False)
        self.status_label.setText("正在从 PostgreSQL 读取历史数据…")
        self._worker = HistoryQueryThread(start, end, self)
        self._worker.result_ready.connect(lambda result, cache_key=key: self._on_query_result(cache_key, result))
        self._worker.failed.connect(self._on_query_error)
        self._worker.finished.connect(self._on_query_finished)
        self._worker.start()

    def _on_query_result(self, key: tuple[str, str], result: Dict[str, Any]) -> None:
        self._cache[key] = result
        self._cache.move_to_end(key)
        while len(self._cache) > 5:
            self._cache.popitem(last=False)
        self._apply_result(result, source="PostgreSQL")

    def _on_query_error(self, message: str) -> None:
        self.status_label.setText(f"历史查询失败：{message}")

    def _on_query_finished(self) -> None:
        self.query_button.setEnabled(True)
        self._worker = None

    def _apply_result(self, result: Mapping[str, Any], *, source: str) -> None:
        process = result.get("process")
        if not isinstance(process, pd.DataFrame):
            process = pd.DataFrame()
        events = result.get("events")
        if not isinstance(events, list):
            events = []
        self._events = [dict(item) for item in events]

        so2_left = [{"column": "yyq_SO2", "name": "原烟气 SO₂"}]
        so2_right = [
            {"column": "jyq_SO2", "name": "净烟气 SO₂"},
            {"column": TARGET_SO2_COLUMN, "name": "目标 SO₂"},
        ]
        self.so2_chart.set_data(
            process,
            left_series=so2_left,
            right_series=so2_right,
            events=self._events,
            left_unit="mg/Nm³（原烟气）",
            right_unit="mg/Nm³（净烟气/目标）",
        )

        meta = result.get("process_meta")
        meta = meta if isinstance(meta, Mapping) else {}
        ph_series = meta.get("ph_series")
        left_supply = [ph_series] if isinstance(ph_series, Mapping) else []
        right_supply = meta.get("supply_series")
        if not isinstance(right_supply, list):
            right_supply = []
        self.supply_chart.set_data(
            process,
            left_series=left_supply,
            right_series=right_supply,
            events=self._events,
            left_unit="pH",
            right_unit="供浆流量 / 阀位 / 泵反馈",
        )

        start = result.get("start")
        end = result.get("end")
        start = start if isinstance(start, dt.datetime) else self._python_datetime(self.start_edit)
        end = end if isinstance(end, dt.datetime) else self._python_datetime(self.end_edit)
        self.timeline.set_data(self._events, start, end)
        self._populate_event_table(self._events)

        self.status_label.setText(
            f"{source} · 曲线 {int(result.get('process_point_count', 0))} 点 · 控制事件 {int(result.get('event_count', 0))} 条"
        )

    def _populate_event_table(self, events: Sequence[Mapping[str, Any]]) -> None:
        self.event_table.setRowCount(0)
        display_events = list(events)[-300:]
        for row_index, event in enumerate(display_events):
            self.event_table.insertRow(row_index)
            stamp = _to_datetime(event.get("time"))
            time_text = stamp.strftime("%Y-%m-%d %H:%M:%S") if stamp else "--"
            condition = str(event.get("condition") or "--")
            title = str(event.get("title") or "--")
            magnitude = translate_magnitude(event.get("magnitude")) if event.get("magnitude") else "--"
            source = translate_experience_source(event.get("source")) if event.get("source") else "--"
            status = translate_decision_state(event.get("status")) if event.get("status") else translate_control_mode(event.get("mode"))
            reasons = event.get("reason_codes")
            if not isinstance(reasons, list):
                reasons = []
            detail_items = translate_reason_codes(reasons)
            explanation = detail_items[0] if detail_items else title
            values = (time_text, condition, title, magnitude, source, status or "--", explanation)
            for column, value in enumerate(values):
                self.event_table.setItem(row_index, column, QTableWidgetItem(str(value)))

        try:
            self.event_table.cellClicked.disconnect()
        except Exception:
            pass
        self.event_table.cellClicked.connect(lambda row, column, items=display_events: self._focus_event(items, row))

    def _focus_event(self, events: Sequence[Mapping[str, Any]], row: int) -> None:
        if row < 0 or row >= len(events):
            return
        stamp = _to_datetime(events[row].get("time"))
        self.so2_chart.set_cursor_time(stamp)
        self.supply_chart.set_cursor_time(stamp)
        self.timeline.set_cursor_time(stamp)

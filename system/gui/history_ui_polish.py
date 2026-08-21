from __future__ import annotations

import re
from types import MethodType
from typing import Dict, Optional

from PyQt5.QtCore import QPoint, QPointF, QRectF, Qt, QTimer
from PyQt5.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPen,
    QTextCharFormat,
)
from PyQt5.QtWidgets import (
    QButtonGroup,
    QCalendarWidget,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from .history_page import HistoryLineChart


CUSTOM_RANGE_KEY = 0
CUSTOM_RANGE_MAX_SECONDS = 7 * 86400

_SELECTOR_STYLE = """
    QCheckBox {
        font-size: 18px;
        font-weight: 600;
        spacing: 9px;
        min-height: 36px;
        padding: 2px 4px;
    }
    QCheckBox::indicator {
        width: 21px;
        height: 21px;
    }
"""

_TITLE_STYLE = """
    QLabel {
        color: #8094b3;
        font-size: 12px;
        font-weight: 600;
        padding: 3px 2px;
    }
"""

_MODE_BUTTON_STYLE = """
    QPushButton {
        background-color: #111d30;
        color: #c3d1e5;
        border: 1px solid #2b3d59;
        border-radius: 6px;
        padding: 6px 14px;
        min-height: 20px;
        font-size: 12px;
        font-weight: 600;
    }
    QPushButton:hover {
        background-color: #172640;
        border-color: #49698f;
        color: #eef5ff;
    }
    QPushButton:checked {
        background-color: #173b5b;
        border: 1px solid #24c7bd;
        color: #69e3da;
    }
"""

_RANGE_BUTTON_STYLE = """
    QPushButton {
        background-color: #0f1a2b;
        color: #b8c8de;
        border: 1px solid #2a3b56;
        border-radius: 6px;
        padding: 6px 13px;
        min-width: 58px;
        min-height: 20px;
        font-size: 12px;
    }
    QPushButton:hover {
        background-color: #172640;
        border-color: #4f6f98;
        color: #ffffff;
    }
    QPushButton:checked {
        background-color: #214b78;
        border: 1px solid #5ba5ff;
        color: #ffffff;
        font-weight: 700;
    }
    QPushButton:disabled {
        background-color: #0c1421;
        color: #53647c;
        border-color: #1c293b;
    }
"""

_QUERY_BUTTON_STYLE = """
    QPushButton {
        background-color: #176d75;
        color: #ffffff;
        border: 1px solid #28b9b2;
        border-radius: 6px;
        padding: 6px 18px;
        min-height: 22px;
        font-size: 12px;
        font-weight: 700;
    }
    QPushButton:hover {
        background-color: #1b7f88;
        border-color: #5ee0d6;
    }
    QPushButton:pressed {
        background-color: #125c63;
    }
    QPushButton:disabled {
        background-color: #29434d;
        color: #859aa8;
        border-color: #36545f;
    }
"""

_DATETIME_STYLE = """
    QDateTimeEdit {
        background-color: #0f1a2b;
        color: #e8f0ff;
        border: 1px solid #2a3b56;
        border-radius: 5px;
        padding: 5px 30px 5px 8px;
    }
    QDateTimeEdit:focus {
        border: 1px solid #4f8cff;
    }
    QDateTimeEdit::up-button,
    QDateTimeEdit::down-button {
        width: 0px;
        height: 0px;
        border: none;
        image: none;
    }
"""

_DATETIME_POPUP_STYLE = """
    QFrame#historyDateTimePopup {
        background-color: #101b2d;
        border: 1px solid #385170;
        border-radius: 7px;
    }
    QCalendarWidget {
        background-color: #101b2d;
        color: #dbe8fb;
    }
    QCalendarWidget QWidget#qt_calendar_navigationbar {
        background-color: #14233a;
    }
    QCalendarWidget QToolButton {
        color: #f0f5ff;
        background-color: #14233a;
        border: none;
        padding: 6px;
        font-weight: 700;
    }
    QCalendarWidget QToolButton:hover {
        background-color: #1d3454;
    }
    QCalendarWidget QSpinBox {
        background-color: #14233a;
        color: #f0f5ff;
        selection-background-color: #235b8d;
        border: 1px solid #2c4667;
    }
    QCalendarWidget QTableView {
        background-color: #0d1726;
        alternate-background-color: #0d1726;
        color: #dbe8fb;
        gridline-color: #253852;
        selection-background-color: #2b659b;
        selection-color: #ffffff;
        border: none;
    }
    QCalendarWidget QTableView::item {
        background-color: #0d1726;
        color: #dbe8fb;
        padding: 4px;
    }
    QCalendarWidget QTableView::item:selected {
        background-color: #2b659b;
        color: #ffffff;
    }
    QLabel#historyPopupTimeLabel {
        color: #dbe8fb;
        font-size: 13px;
        font-weight: 600;
    }
    QTimeEdit {
        background-color: #0d1726;
        color: #e8f0ff;
        border: 1px solid #2a3b56;
        border-radius: 5px;
        padding: 5px 8px;
        min-height: 24px;
    }
    QPushButton#historyDateTimeConfirm {
        background-color: #176d75;
        color: #ffffff;
        border: 1px solid #28b9b2;
        border-radius: 6px;
        padding: 6px 18px;
        font-weight: 700;
    }
    QPushButton#historyDateTimeConfirm:hover {
        background-color: #1b7f88;
        border-color: #5ee0d6;
    }
"""


def _style_section_labels(history_page) -> None:
    for label in history_page.findChildren(QLabel):
        if label.text() in {"模式", "时间窗口"}:
            label.setStyleSheet(_TITLE_STYLE)
            label.setToolTip("这是分组标题，不是可点击选项")


def _style_series_selectors(history_page) -> None:
    for panel_name in ("so2_panel", "supply_panel", "circulation_panel"):
        panel = getattr(history_page, panel_name, None)
        selector = getattr(panel, "selector", None)
        if selector is not None:
            selector.setStyleSheet(_SELECTOR_STYLE)


def _sync_range_checked(history_page) -> None:
    active = int(getattr(history_page, "_active_span_seconds", 0) or 0)
    buttons: Dict[int, QPushButton] = getattr(history_page, "_range_buttons", {})
    for seconds, button in buttons.items():
        button.setChecked(int(seconds) == active)


def _live_interval_ms(history_page) -> int:
    """长实时窗口降低数据库刷新频率，避免3天/7天每30秒全量查询。"""
    base_ms = int(getattr(history_page, "_history_base_live_interval_ms", 30000) or 30000)
    span = int(getattr(history_page, "_active_span_seconds", 0) or 0)
    if span <= 24 * 3600:
        return base_ms
    if span <= 3 * 86400:
        return max(base_ms, 120000)
    return max(base_ms, 300000)


def _install_long_window_live_support(history_page) -> None:
    """允许实时跟随3天/7天，同时通过自适应刷新频率保护数据库和GUI。"""
    history_page._history_base_live_interval_ms = int(history_page._live_timer.interval())

    original_refresh_live = history_page._refresh_live
    original_apply_result = history_page._apply_result

    def refresh_live(self) -> None:
        if int(getattr(self, "_active_span_seconds", 0) or 0) <= 0:
            self._active_span_seconds = 3600
        self._live_timer.setInterval(_live_interval_ms(self))
        self._history_programmatic_time_change = True
        try:
            original_refresh_live()
        finally:
            self._history_programmatic_time_change = False
        _sync_range_checked(self)

    def set_mode(self, live: bool) -> None:
        self._live_mode = bool(live)
        self.start_edit.setEnabled(not live)
        self.end_edit.setEnabled(not live)
        self.query_button.setText("刷新" if live else "查询")

        for button in self._range_buttons.values():
            button.setEnabled(True)

        if live:
            if int(getattr(self, "_active_span_seconds", 0) or 0) <= 0:
                self._active_span_seconds = 3600
            self.live_mode_button.setChecked(True)
            self._live_timer.setInterval(_live_interval_ms(self))
            self._refresh_live()
            if self.isVisible():
                self._live_timer.start()
        else:
            self.history_mode_button.setChecked(True)
            self._live_timer.stop()
            self.status_label.setText("历史模式 · 请选择时间范围后查询")
        _sync_range_checked(self)

    def apply_result(self, result, *, source: str) -> None:
        original_apply_result(result, source=source)
        if self._live_mode:
            seconds = max(1, int(self._live_timer.interval() / 1000))
            text = self.status_label.text()
            text = re.sub(r"自动约每\d+秒刷新", f"自动约每{seconds}秒刷新", text)
            self.status_label.setText(text)

    history_page._refresh_live = MethodType(refresh_live, history_page)
    history_page._set_mode = MethodType(set_mode, history_page)
    history_page._apply_result = MethodType(apply_result, history_page)

    try:
        history_page._live_timer.timeout.disconnect()
    except Exception:
        pass
    history_page._live_timer.timeout.connect(history_page._refresh_live)

    for seconds, button in history_page._range_buttons.items():
        button.setEnabled(True)
        if seconds > 24 * 3600:
            button.setToolTip(
                "历史模式可直接查询；实时跟随也可使用，长窗口会自动降低刷新频率以保证性能"
            )


def _style_calendar(calendar: QCalendarWidget) -> None:
    calendar.setGridVisible(True)
    calendar.setHorizontalHeaderFormat(QCalendarWidget.ShortDayNames)
    calendar.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)

    header = QTextCharFormat()
    header.setForeground(QBrush(QColor("#dbe8fb")))
    header.setBackground(QBrush(QColor("#14233a")))
    header.setFontWeight(QFont.Bold)
    calendar.setHeaderTextFormat(header)

    weekday = QTextCharFormat()
    weekday.setForeground(QBrush(QColor("#dbe8fb")))
    weekday.setBackground(QBrush(QColor("#0d1726")))
    weekend = QTextCharFormat()
    weekend.setForeground(QBrush(QColor("#ff7f7f")))
    weekend.setBackground(QBrush(QColor("#0d1726")))
    for day in (Qt.Monday, Qt.Tuesday, Qt.Wednesday, Qt.Thursday, Qt.Friday):
        calendar.setWeekdayTextFormat(day, weekday)
    calendar.setWeekdayTextFormat(Qt.Saturday, weekend)
    calendar.setWeekdayTextFormat(Qt.Sunday, weekend)


def _install_datetime_dropdown(editor) -> None:
    """把日期时间控件改成单一 ▼ + 下拉日期时间面板。"""
    editor.setStyleSheet(_DATETIME_STYLE)
    editor.setToolTip("可直接编辑；点击右侧 ▼ 展开日期和时间选择")

    def open_picker(self) -> None:
        existing = getattr(self, "_history_datetime_popup", None)
        if existing is not None and existing.isVisible():
            existing.close()
            return

        popup = QFrame(None, Qt.Popup | Qt.FramelessWindowHint)
        popup.setObjectName("historyDateTimePopup")
        popup.setStyleSheet(_DATETIME_POPUP_STYLE)
        popup.setMinimumWidth(max(360, self.width()))

        layout = QVBoxLayout(popup)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        calendar = QCalendarWidget(popup)
        _style_calendar(calendar)
        calendar.setSelectedDate(self.dateTime().date())
        layout.addWidget(calendar)

        time_row = QHBoxLayout()
        time_row.setContentsMargins(0, 0, 0, 0)
        time_label = QLabel("时间", popup)
        time_label.setObjectName("historyPopupTimeLabel")
        time_row.addWidget(time_label)
        time_edit = QTimeEdit(self.dateTime().time(), popup)
        time_edit.setDisplayFormat("HH:mm:ss")
        time_row.addWidget(time_edit, 1)
        layout.addLayout(time_row)

        confirm = QPushButton("确定", popup)
        confirm.setObjectName("historyDateTimeConfirm")
        confirm.setCursor(Qt.PointingHandCursor)
        layout.addWidget(confirm)

        def apply_value() -> None:
            self.setDate(calendar.selectedDate())
            self.setTime(time_edit.time())
            popup.close()

        confirm.clicked.connect(apply_value)
        self._history_datetime_popup = popup

        anchor = self.mapToGlobal(QPoint(0, self.height() + 3))
        popup.move(anchor)
        popup.show()
        popup.raise_()

    editor._open_picker = MethodType(open_picker, editor)


def _find_layout_containing_widget(layout: Optional[QLayout], widget: QWidget) -> Optional[QLayout]:
    if layout is None:
        return None
    for index in range(layout.count()):
        item = layout.itemAt(index)
        if item.widget() is widget:
            return layout
        child_layout = item.layout()
        found = _find_layout_containing_widget(child_layout, widget)
        if found is not None:
            return found
    return None


def _install_custom_range_support(history_page) -> None:
    """加入“自定义”窗口，并确保手动时间与快捷按钮状态一致。"""
    history_page._history_programmatic_time_change = False

    first_button = next(iter(history_page._range_buttons.values()), None)
    if first_button is not None and CUSTOM_RANGE_KEY not in history_page._range_buttons:
        row_layout = _find_layout_containing_widget(first_button.parentWidget().layout(), first_button)
        if row_layout is not None:
            custom_button = QPushButton("自定义")
            custom_button.setCheckable(True)
            custom_button.setStyleSheet(_RANGE_BUTTON_STYLE)
            custom_button.setCursor(Qt.PointingHandCursor)
            custom_button.setToolTip("自定义开始/结束时间，单次查询范围最多7天")
            last_button = list(history_page._range_buttons.values())[-1]
            insert_index = row_layout.indexOf(last_button) + 1
            row_layout.insertWidget(insert_index, custom_button)
            history_page._range_buttons[CUSTOM_RANGE_KEY] = custom_button
            history_page.custom_range_button = custom_button

    original_set_quick_range = history_page._set_quick_range
    original_query_history = history_page.query_history

    def set_quick_range(self, seconds: int) -> None:
        self._history_programmatic_time_change = True
        try:
            original_set_quick_range(seconds)
        finally:
            self._history_programmatic_time_change = False
        self._active_span_seconds = int(seconds)
        _sync_range_checked(self)

    def query_history(self, force: bool = False) -> None:
        if not self._live_mode and int(getattr(self, "_active_span_seconds", 0) or 0) == CUSTOM_RANGE_KEY:
            start = self._python_datetime(self.start_edit)
            end = self._python_datetime(self.end_edit)
            span = (end - start).total_seconds()
            if span > CUSTOM_RANGE_MAX_SECONDS:
                self.status_label.setText("自定义时间窗口最多支持7天，请缩短开始/结束时间范围")
                return
        original_query_history(force=force)

    history_page._set_quick_range = MethodType(set_quick_range, history_page)
    history_page.query_history = MethodType(query_history, history_page)
    history_page.query_button.clicked.disconnect()
    history_page.query_button.clicked.connect(history_page.query_history)

    # 原快捷按钮是在 __init__ 时绑定到旧 bound method，重新连接到增强后的方法。
    for seconds, button in list(history_page._range_buttons.items()):
        if seconds == CUSTOM_RANGE_KEY:
            continue
        try:
            button.clicked.disconnect()
        except Exception:
            pass
        button.clicked.connect(
            lambda checked=False, span=seconds, page=history_page: page._set_quick_range(span)
        )

    def mark_custom() -> None:
        if getattr(history_page, "_history_programmatic_time_change", False):
            return
        if history_page._live_mode:
            return
        history_page._active_span_seconds = CUSTOM_RANGE_KEY
        _sync_range_checked(history_page)
        history_page.status_label.setText("自定义时间窗口 · 可选择7天以内任意开始/结束时间")

    history_page.start_edit.dateTimeChanged.connect(lambda _value: mark_custom())
    history_page.end_edit.dateTimeChanged.connect(lambda _value: mark_custom())

    custom_button = getattr(history_page, "custom_range_button", None)
    if custom_button is not None:
        def select_custom(_checked: bool = False) -> None:
            if history_page._live_mode:
                history_page._set_mode(False)
            history_page._active_span_seconds = CUSTOM_RANGE_KEY
            _sync_range_checked(history_page)
            history_page.status_label.setText("自定义时间窗口 · 可选择7天以内任意开始/结束时间")

        custom_button.clicked.connect(select_custom)


def _install_single_line_time_axis() -> None:
    """3天/7天横轴日期时间改成单行，避免第二行被裁掉。"""
    if getattr(HistoryLineChart, "_single_line_axis_installed", False):
        return

    def paint_event(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setFont(QFont("Microsoft YaHei", 9))
        text_color = QColor("#9fb4d4")

        plot = QRectF(self.rect()).adjusted(68, 34, -78, -38)
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
                label = stamp.strftime("%m-%d %H:%M")
                label_width = 112
            else:
                label = stamp.strftime("%H:%M")
                label_width = 84
            painter.setPen(text_color)
            painter.drawText(
                QRectF(x - label_width / 2.0, plot.bottom() + 7, label_width, 22),
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

    HistoryLineChart.paintEvent = paint_event
    HistoryLineChart._single_line_axis_installed = True


def apply_history_ui_polish(history_page) -> None:
    """历史页视觉、时间交互、自定义窗口和长窗口性能增强。"""

    _install_single_line_time_axis()
    _style_section_labels(history_page)
    _style_series_selectors(history_page)
    _install_long_window_live_support(history_page)
    _install_datetime_dropdown(history_page.start_edit)
    _install_datetime_dropdown(history_page.end_edit)
    _install_custom_range_support(history_page)

    for button_name in ("history_mode_button", "live_mode_button"):
        button = getattr(history_page, button_name, None)
        if button is not None:
            button.setStyleSheet(_MODE_BUTTON_STYLE)
            button.setCursor(Qt.PointingHandCursor)

    range_buttons: Dict[int, QPushButton] = getattr(history_page, "_range_buttons", {})
    range_group = QButtonGroup(history_page)
    range_group.setExclusive(True)
    for seconds, button in range_buttons.items():
        button.setCheckable(True)
        button.setStyleSheet(_RANGE_BUTTON_STYLE)
        button.setCursor(Qt.PointingHandCursor)
        if seconds == CUSTOM_RANGE_KEY:
            button.setToolTip("自定义开始/结束时间，单次历史查询最多7天")
        elif seconds <= 24 * 3600:
            button.setToolTip("点击切换历史显示时间窗口")
        range_group.addButton(button, int(seconds))
        button.clicked.connect(
            lambda checked=False, page=history_page: QTimer.singleShot(
                0, lambda: _sync_range_checked(page)
            )
        )

    history_page._history_range_button_group = range_group
    _sync_range_checked(history_page)

    query_button = getattr(history_page, "query_button", None)
    if query_button is not None:
        query_button.setStyleSheet(_QUERY_BUTTON_STYLE)
        query_button.setCursor(Qt.PointingHandCursor)
        query_button.setToolTip("按当前时间范围读取历史数据")

    for button_name in ("history_mode_button", "live_mode_button"):
        button = getattr(history_page, button_name, None)
        if button is not None:
            button.clicked.connect(
                lambda checked=False, page=history_page: QTimer.singleShot(
                    0, lambda: _sync_range_checked(page)
                )
            )

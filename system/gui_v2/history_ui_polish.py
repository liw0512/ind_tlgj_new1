from __future__ import annotations

import re
from types import MethodType
from typing import Dict

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QButtonGroup, QLabel, QPushButton


_SELECTOR_STYLE = """
    QCheckBox {
        font-size: 15px;
        spacing: 8px;
        min-height: 30px;
        padding: 2px 3px;
    }
    QCheckBox::indicator {
        width: 18px;
        height: 18px;
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
    button = buttons.get(active)
    if button is not None and button.isEnabled():
        button.setChecked(True)


def _live_interval_ms(history_page) -> int:
    """长实时窗口降低数据库刷新频率，避免3天/7天每30秒全量查询。"""
    base_ms = int(getattr(history_page, "_history_base_live_interval_ms", 30000) or 30000)
    span = int(getattr(history_page, "_active_span_seconds", 0) or 0)
    if span <= 24 * 3600:
        return base_ms
    if span <= 3 * 86400:
        return max(base_ms, 120000)  # 3天：至少2分钟刷新一次
    return max(base_ms, 300000)      # 7天：至少5分钟刷新一次


def _install_long_window_live_support(history_page) -> None:
    """允许实时跟随3天/7天，同时通过自适应刷新频率保护数据库和GUI。"""
    history_page._history_base_live_interval_ms = int(history_page._live_timer.interval())

    original_refresh_live = history_page._refresh_live
    original_apply_result = history_page._apply_result

    def refresh_live(self) -> None:
        self._live_timer.setInterval(_live_interval_ms(self))
        original_refresh_live()

    def set_mode(self, live: bool) -> None:
        self._live_mode = bool(live)
        self.start_edit.setEnabled(not live)
        self.end_edit.setEnabled(not live)
        self.query_button.setText("刷新" if live else "查询")

        # 3天/7天保持可点击；长窗口由刷新频率自动降级，而不是直接禁用。
        for button in self._range_buttons.values():
            button.setEnabled(True)

        if live:
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

    # QTimer 初始化时连接的是旧 bound method；重新绑定到新的自适应刷新方法。
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


def apply_history_ui_polish(history_page) -> None:
    """强化历史页的视觉层级，并补充长窗口实时跟随的性能保护。"""

    _style_section_labels(history_page)
    _style_series_selectors(history_page)
    _install_long_window_live_support(history_page)

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
        if seconds <= 24 * 3600:
            button.setToolTip("点击切换历史显示时间窗口")
        range_group.addButton(button, int(seconds))
        button.clicked.connect(
            lambda checked=False, page=history_page: QTimer.singleShot(
                0, lambda: _sync_range_checked(page)
            )
        )

    # 保存引用，防止 QButtonGroup 被 Python 回收。
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

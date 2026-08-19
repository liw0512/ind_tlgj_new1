from __future__ import annotations

from typing import Dict

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QButtonGroup, QLabel, QPushButton


_SELECTOR_STYLE = """
    QCheckBox {
        font-size: 13px;
        spacing: 7px;
        min-height: 26px;
        padding: 1px 2px;
    }
    QCheckBox::indicator {
        width: 16px;
        height: 16px;
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


def apply_history_ui_polish(history_page) -> None:
    """强化历史页的视觉层级，不改变历史查询/绘图业务逻辑。"""

    _style_section_labels(history_page)
    _style_series_selectors(history_page)

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

    # 模式切换可能把 3天/7天自动回退到 1小时；切换完成后同步高亮状态。
    for button_name in ("history_mode_button", "live_mode_button"):
        button = getattr(history_page, button_name, None)
        if button is not None:
            button.clicked.connect(
                lambda checked=False, page=history_page: QTimer.singleShot(
                    0, lambda: _sync_range_checked(page)
                )
            )

from __future__ import annotations

from typing import Dict

from PyQt5.QtCore import QDateTime
from PyQt5.QtWidgets import QPushButton


_CUSTOM_RANGE_KEY = 0

_RANGE_LABELS: Dict[int, str] = {
    3600: "1小时",
    6 * 3600: "6小时",
    24 * 3600: "24小时",
    3 * 86400: "3天",
    7 * 86400: "7天",
}


def _sync_range_checked(history_page) -> None:
    active = int(getattr(history_page, "_active_span_seconds", 0) or 0)
    buttons: Dict[int, QPushButton] = getattr(history_page, "_range_buttons", {})
    for seconds, button in buttons.items():
        button.setChecked(int(seconds) == active)


def _range_label(seconds: int) -> str:
    return _RANGE_LABELS.get(int(seconds), f"{int(seconds)}秒")


def apply_history_time_link(history_page) -> None:
    """固定时间窗口下联动开始/结束时间；自定义窗口保持两端独立。

    例如选中 6 小时：
    - 修改开始时间 -> 结束时间自动 = 开始 + 6 小时；
    - 修改结束时间 -> 开始时间自动 = 结束 - 6 小时。

    自定义窗口不做联动，仍由用户分别设置开始和结束时间。时间修改只更新输入框，
    不自动触发数据库查询，用户确认后再点击“查询”。
    """

    state = {"updating": False}

    # history_ui_polish 之前会把任何手动时间修改直接标记为“自定义”。
    # 这里替换为“固定窗口保持时长”的交互规则。
    for editor in (history_page.start_edit, history_page.end_edit):
        try:
            editor.dateTimeChanged.disconnect()
        except (TypeError, RuntimeError):
            pass

    def _can_handle() -> bool:
        if state["updating"]:
            return False
        if bool(getattr(history_page, "_history_programmatic_time_change", False)):
            return False
        if bool(getattr(history_page, "_live_mode", False)):
            return False
        return True

    def _set_counterpart(editor, value: QDateTime) -> None:
        state["updating"] = True
        history_page._history_programmatic_time_change = True
        try:
            editor.setDateTime(value)
        finally:
            history_page._history_programmatic_time_change = False
            state["updating"] = False

    def _custom_status() -> None:
        _sync_range_checked(history_page)
        history_page.status_label.setText(
            "自定义时间窗口 · 开始和结束时间独立设置 · 最多7天 · 设置完成后点击查询"
        )

    def _start_changed(_value: QDateTime) -> None:
        if not _can_handle():
            return
        span = int(getattr(history_page, "_active_span_seconds", 0) or 0)
        if span == _CUSTOM_RANGE_KEY:
            _custom_status()
            return
        if span <= 0:
            return

        new_end = history_page.start_edit.dateTime().addSecs(span)
        _set_counterpart(history_page.end_edit, new_end)
        _sync_range_checked(history_page)
        history_page.status_label.setText(
            f"{_range_label(span)}时间窗口 · 已按开始时间自动更新结束时间 · 点击查询"
        )

    def _end_changed(_value: QDateTime) -> None:
        if not _can_handle():
            return
        span = int(getattr(history_page, "_active_span_seconds", 0) or 0)
        if span == _CUSTOM_RANGE_KEY:
            _custom_status()
            return
        if span <= 0:
            return

        new_start = history_page.end_edit.dateTime().addSecs(-span)
        _set_counterpart(history_page.start_edit, new_start)
        _sync_range_checked(history_page)
        history_page.status_label.setText(
            f"{_range_label(span)}时间窗口 · 已按结束时间自动更新开始时间 · 点击查询"
        )

    history_page.start_edit.dateTimeChanged.connect(_start_changed)
    history_page.end_edit.dateTimeChanged.connect(_end_changed)

    # 保存引用，便于后续调试/扩展，也避免闭包被误清理。
    history_page._history_time_link_handlers = (_start_changed, _end_changed)

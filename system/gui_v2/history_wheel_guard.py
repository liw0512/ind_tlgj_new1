from __future__ import annotations

from types import MethodType


def _ignore_collapsed_editor_wheel(self, event) -> None:
    """收起状态下忽略鼠标滚轮，避免悬停误改日期/时间。

    日期时间下拉面板中的 QCalendarWidget / QTimeEdit 不受影响；用户点击右侧 ▼ 展开后，
    仍可在下拉面板内使用滚轮进行月份/时间操作。
    """
    event.ignore()


def apply_history_wheel_guard(history_page) -> None:
    """禁止开始/结束时间输入框在悬停时被鼠标滚轮修改。"""
    for editor in (history_page.start_edit, history_page.end_edit):
        editor.wheelEvent = MethodType(_ignore_collapsed_editor_wheel, editor)
        editor.setToolTip(
            "点击右侧 ▼ 选择日期和时间；输入框收起时鼠标滚轮不会修改时间"
        )

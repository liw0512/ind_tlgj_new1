"""LIVE 报警页细化：活动报警刷新不打断历史报警详情阅读。"""
from __future__ import annotations

from PyQt5.QtWidgets import QTableWidgetItem

from .alarm_page import (
    AlarmPage,
    _CATEGORY_TEXT,
    _LEVEL_TEXT,
    _fmt_duration,
    _fmt_time,
    _fmt_value,
)


class LiveAlarmPage(AlarmPage):
    """仅增强实时表刷新行为，不改变 AlarmPage 的历史查询接口。"""

    def _populate_current_table(self) -> None:
        selected_id = None
        row = self.current_table.currentRow()
        if 0 <= row < len(self._active_events):
            selected_id = self._active_events[row].get("id")

        self.current_table.setUpdatesEnabled(False)
        try:
            self.current_table.setRowCount(0)
            for row_index, event in enumerate(self._active_events):
                self.current_table.insertRow(row_index)
                values = (
                    _LEVEL_TEXT.get(str(event.get("level")), str(event.get("level") or "--")),
                    _fmt_time(event.get("start_time")),
                    _CATEGORY_TEXT.get(str(event.get("category")), str(event.get("category") or "--")),
                    str(event.get("object_name") or "--"),
                    str(event.get("message") or "--"),
                    _fmt_value(event),
                    _fmt_duration(event.get("duration_seconds")),
                    "活动",
                )
                for column, value in enumerate(values):
                    item = QTableWidgetItem(str(value))
                    item.setToolTip(str(value))
                    self.current_table.setItem(row_index, column, item)
        finally:
            self.current_table.setUpdatesEnabled(True)

        # 当前报警每秒刷新；只有用户正在“当前报警”页时才同步详情。
        # 在“历史报警”页阅读某条历史事件时，绝不能被实时刷新抢回第一条当前报警。
        if self.stack.currentIndex() != 0:
            return

        if selected_id:
            for index, event in enumerate(self._active_events):
                if event.get("id") == selected_id:
                    self.current_table.selectRow(index)
                    self._set_detail(event)
                    return

        if self._active_events:
            self.current_table.selectRow(0)
            self._set_detail(self._active_events[0])
        else:
            self._set_detail(None)

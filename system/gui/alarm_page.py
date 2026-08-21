"""正式报警信息页：当前活动报警 + PostgreSQL 历史报警。"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Mapping, Optional, Sequence

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from system.model.alarm.alarm_store import AlarmEventStore

from .reason_text import translate_reason_codes
from .widgets import CardFrame


_LEVEL_TEXT = {"CRITICAL": "紧急", "ALARM": "报警", "NOTICE": "提示"}
_LEVEL_COLOR = {"CRITICAL": "#ff6673", "ALARM": "#f6b73c", "NOTICE": "#55b4ff"}
_CATEGORY_TEXT = {
    "PROCESS": "工艺安全",
    "DATA": "数据质量",
    "CONTROL": "控制系统",
    "DEVICE": "设备",
    "SYSTEM": "系统",
}
_STATE_TEXT = {"ACTIVE": "活动", "RECOVERED": "已恢复", "INTERRUPTED": "已结束"}


def _fmt_time(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return "--" if value in (None, "") else str(value)


def _fmt_duration(seconds: Any) -> str:
    try:
        value = max(0.0, float(seconds))
    except (TypeError, ValueError):
        return "--"
    if value < 60:
        return f"{value:.0f}秒"
    if value < 3600:
        return f"{value / 60.0:.0f}分{value % 60:.0f}秒"
    if value < 86400:
        return f"{value / 3600.0:.1f}小时"
    return f"{value / 86400.0:.1f}天"


def _fmt_value(event: Mapping[str, Any]) -> str:
    value = event.get("current_value")
    if value is None:
        return "--"
    try:
        text = f"{float(value):.2f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        text = str(value)
    unit = str(event.get("unit") or "")
    return f"{text} {unit}".strip()


class AlarmHistoryThread(QThread):
    result_ready = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(
        self,
        start: datetime,
        end: datetime,
        *,
        level: str,
        category: str,
        state: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.start_time = start
        self.end_time = end
        self.level = level
        self.category = category
        self.state = state

    def run(self) -> None:
        store: Optional[AlarmEventStore] = None
        try:
            store = AlarmEventStore()
            store.ensure_table()
            rows = store.query_events(
                self.start_time,
                self.end_time,
                level=self.level,
                category=self.category,
                state=self.state,
                limit=1000,
            )
            self.result_ready.emit(rows)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            if store is not None:
                store.close()


class AlarmCountCard(CardFrame):
    def __init__(self, title: str, accent: str, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(4)
        self.title = QLabel(title)
        self.title.setStyleSheet("color:#8fa4c2;font-size:13px;font-weight:600;")
        self.value = QLabel("0")
        self.value.setStyleSheet(f"color:{accent};font-size:28px;font-weight:800;")
        layout.addWidget(self.title)
        layout.addWidget(self.value)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_count(self, count: int) -> None:
        self.value.setText(str(max(0, int(count))))


class AlarmPage(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(0)
        self._active_events: List[Dict[str, Any]] = []
        self._history_events: List[Dict[str, Any]] = []
        self._history_span_seconds = 24 * 3600
        self._history_worker: Optional[AlarmHistoryThread] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        title = QLabel("报警信息与事件追溯")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size:22px;font-weight:700;padding:6px;")
        root.addWidget(title)

        summary = QGridLayout()
        summary.setHorizontalSpacing(12)
        self.critical_card = AlarmCountCard("紧急报警", "#ff6673")
        self.alarm_card = AlarmCountCard("一般报警", "#f6b73c")
        self.notice_card = AlarmCountCard("提示信息", "#55b4ff")
        self.total_card = AlarmCountCard("当前活动报警", "#69e3da")
        for index, card in enumerate(
            (self.critical_card, self.alarm_card, self.notice_card, self.total_card)
        ):
            summary.addWidget(card, 0, index)
            summary.setColumnStretch(index, 1)
        root.addLayout(summary)

        mode_card = CardFrame()
        mode_layout = QHBoxLayout(mode_card)
        mode_layout.setContentsMargins(14, 8, 14, 8)
        mode_layout.setSpacing(8)
        mode_label = QLabel("查看")
        mode_label.setStyleSheet("color:#8094b3;font-weight:600;")
        mode_layout.addWidget(mode_label)
        self.current_button = QPushButton("当前报警")
        self.history_button = QPushButton("历史报警")
        for button in (self.current_button, self.history_button):
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setStyleSheet(self._mode_button_style())
        group = QButtonGroup(self)
        group.setExclusive(True)
        group.addButton(self.current_button)
        group.addButton(self.history_button)
        self.current_button.setChecked(True)
        mode_layout.addWidget(self.current_button)
        mode_layout.addWidget(self.history_button)
        mode_layout.addStretch(1)
        root.addWidget(mode_card)

        self.stack = QStackedWidget()
        self.current_page = self._build_current_page()
        self.history_page = self._build_history_page()
        self.stack.addWidget(self.current_page)
        self.stack.addWidget(self.history_page)
        root.addWidget(self.stack)

        self.detail_card = self._build_detail_card()
        root.addWidget(self.detail_card)

        self.current_button.clicked.connect(lambda: self._set_mode(0))
        self.history_button.clicked.connect(lambda: self._set_mode(1))
        self._set_detail(None)

    @staticmethod
    def _mode_button_style() -> str:
        return """
        QPushButton {
            background:#101c2e;color:#b9c9df;border:1px solid #2a3b56;
            border-radius:6px;padding:7px 18px;font-weight:600;
        }
        QPushButton:hover { background:#172640;color:#ffffff;border-color:#49698f; }
        QPushButton:checked {
            background:#173b5b;color:#69e3da;border:1px solid #24c7bd;font-weight:700;
        }
        """

    @staticmethod
    def _query_button_style() -> str:
        return """
        QPushButton {
            background:#176d75;color:#ffffff;border:1px solid #28b9b2;
            border-radius:6px;padding:6px 15px;font-weight:700;
        }
        QPushButton:hover { background:#1b7f88;border-color:#5ee0d6; }
        QPushButton:disabled { background:#29434d;color:#859aa8;border-color:#36545f; }
        """

    @staticmethod
    def _build_table(headers: Sequence[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(tuple(headers))
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.verticalHeader().setVisible(False)
        table.setMinimumHeight(250)
        table.setMinimumWidth(0)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.setStyleSheet(
            """
            QTableWidget {
                background:#0d1726;color:#dbe8fb;gridline-color:#23344d;
                border:1px solid #23344d;
            }
            QTableWidget::item { padding:5px; }
            QTableWidget::item:selected { background:#24466f;color:#ffffff; }
            QHeaderView::section {
                background:#162338;color:#b9c9df;padding:6px;border:0;
                border-right:1px solid #263851;border-bottom:1px solid #263851;
                font-weight:600;
            }
            """
        )
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        return table

    def _build_current_page(self) -> QWidget:
        card = CardFrame()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        title = QLabel("当前活动报警")
        title.setProperty("role", "sectionTitle")
        layout.addWidget(title)
        self.runtime_status = QLabel("报警服务正在等待实时数据…")
        self.runtime_status.setWordWrap(True)
        self.runtime_status.setStyleSheet("color:#8094b3;")
        layout.addWidget(self.runtime_status)
        self.current_table = self._build_table(
            ("等级", "发生时间", "类型", "对象", "报警内容", "当前值", "持续时间", "状态")
        )
        self.current_table.cellClicked.connect(self._current_clicked)
        layout.addWidget(self.current_table)
        return card

    def _build_history_page(self) -> QWidget:
        card = CardFrame()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        controls.addWidget(QLabel("时间范围"))
        self.history_range_buttons: Dict[int, QPushButton] = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        for label, seconds in (("24小时", 86400), ("3天", 3 * 86400), ("7天", 7 * 86400)):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setStyleSheet(self._mode_button_style())
            button.clicked.connect(lambda checked=False, span=seconds: self._set_history_span(span))
            self.history_range_buttons[seconds] = button
            group.addButton(button)
            controls.addWidget(button)
        self.history_range_buttons[86400].setChecked(True)

        self.level_combo = QComboBox()
        self.level_combo.addItem("全部等级", "ALL")
        self.level_combo.addItem("紧急", "CRITICAL")
        self.level_combo.addItem("报警", "ALARM")
        self.level_combo.addItem("提示", "NOTICE")
        self.category_combo = QComboBox()
        for text, code in (
            ("全部类型", "ALL"),
            ("工艺安全", "PROCESS"),
            ("数据质量", "DATA"),
            ("控制系统", "CONTROL"),
            ("设备", "DEVICE"),
            ("系统", "SYSTEM"),
        ):
            self.category_combo.addItem(text, code)
        self.state_combo = QComboBox()
        self.state_combo.addItem("全部状态", "ALL")
        self.state_combo.addItem("活动", "ACTIVE")
        self.state_combo.addItem("已结束", "CLOSED")
        for combo in (self.level_combo, self.category_combo, self.state_combo):
            combo.setMinimumWidth(110)
            combo.setStyleSheet(
                "QComboBox{background:#0f1a2b;color:#dbe8fb;border:1px solid #2a3b56;"
                "border-radius:5px;padding:5px 8px;}"
            )
            controls.addWidget(combo)

        self.history_query_button = QPushButton("查询")
        self.history_query_button.setCursor(Qt.PointingHandCursor)
        self.history_query_button.setStyleSheet(self._query_button_style())
        self.history_query_button.clicked.connect(self.query_history)
        controls.addWidget(self.history_query_button)
        controls.addStretch(1)
        layout.addLayout(controls)

        self.history_status = QLabel("历史报警数据源：PostgreSQL / t_alarm_event")
        self.history_status.setWordWrap(True)
        self.history_status.setStyleSheet("color:#8094b3;")
        layout.addWidget(self.history_status)

        self.history_table = self._build_table(
            ("开始时间", "恢复时间", "持续时间", "等级", "类型", "对象", "报警内容", "状态")
        )
        self.history_table.cellClicked.connect(self._history_clicked)
        layout.addWidget(self.history_table)
        return card

    def _build_detail_card(self) -> CardFrame:
        card = CardFrame()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        title = QLabel("报警详情")
        title.setProperty("role", "sectionTitle")
        layout.addWidget(title)

        grid = QGridLayout()
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(7)
        labels = (
            ("对象", "detail_object"),
            ("等级", "detail_level"),
            ("当前值", "detail_value"),
            ("报警阈值", "detail_threshold"),
            ("发生时间", "detail_start"),
            ("持续时间", "detail_duration"),
            ("状态", "detail_state"),
            ("恢复时间", "detail_end"),
        )
        for index, (caption, attr) in enumerate(labels):
            caption_label = QLabel(caption)
            caption_label.setStyleSheet("color:#8094b3;")
            value_label = QLabel("--")
            value_label.setStyleSheet("color:#e8f0ff;font-weight:600;")
            setattr(self, attr, value_label)
            row = index // 2
            col = (index % 2) * 2
            grid.addWidget(caption_label, row, col)
            grid.addWidget(value_label, row, col + 1)
            grid.setColumnStretch(col + 1, 1)
        layout.addLayout(grid)

        self.detail_message = QLabel("请选择一条报警查看详情")
        self.detail_message.setWordWrap(True)
        self.detail_message.setStyleSheet("color:#dbe8fb;font-size:14px;font-weight:600;padding-top:5px;")
        layout.addWidget(self.detail_message)
        self.detail_reason = QLabel("")
        self.detail_reason.setWordWrap(True)
        self.detail_reason.setStyleSheet("color:#b8c8de;line-height:1.4;")
        layout.addWidget(self.detail_reason)
        self.detail_suggestion = QLabel("")
        self.detail_suggestion.setWordWrap(True)
        self.detail_suggestion.setStyleSheet("color:#69e3da;")
        layout.addWidget(self.detail_suggestion)
        self.detail_code = QLabel("")
        self.detail_code.setWordWrap(True)
        self.detail_code.setStyleSheet("color:#657b9b;font-size:11px;")
        layout.addWidget(self.detail_code)
        return card

    def _set_mode(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.current_button.setChecked(index == 0)
        self.history_button.setChecked(index == 1)
        if index == 1 and not self._history_events:
            self.query_history()

    def _set_history_span(self, seconds: int) -> None:
        self._history_span_seconds = int(seconds)
        for span, button in self.history_range_buttons.items():
            button.setChecked(span == self._history_span_seconds)

    def update_runtime(self, payload: Mapping[str, Any]) -> None:
        events = payload.get("active_alarms") if isinstance(payload.get("active_alarms"), list) else []
        self._active_events = [dict(item) for item in events if isinstance(item, Mapping)]
        self._populate_current_table()
        self._update_summary()

        messages: List[str] = []
        if payload.get("unit_stopped"):
            messages.append("机组停运判定已成立：工艺、控制及关键测点报警暂时抑制")
        elif payload.get("calibration_active"):
            messages.append("测点校验状态：工艺、控制及关键测点报警暂时抑制")
        else:
            messages.append("报警服务运行中")
        age = payload.get("realtime_age_seconds")
        if age is not None:
            try:
                messages.append(f"实时数据年龄 {float(age):.0f} 秒")
            except (TypeError, ValueError):
                pass
        if payload.get("persistence_error"):
            messages.append("报警历史写库异常；当前活动报警仍在内存中继续管理")
        self.runtime_status.setText(" · ".join(messages))

    def show_runtime_error(self, message: str) -> None:
        self.runtime_status.setText(f"报警管理器异常：{message}")

    def _update_summary(self) -> None:
        counts = {"CRITICAL": 0, "ALARM": 0, "NOTICE": 0}
        for event in self._active_events:
            level = str(event.get("level") or "ALARM")
            counts[level] = counts.get(level, 0) + 1
        self.critical_card.set_count(counts.get("CRITICAL", 0))
        self.alarm_card.set_count(counts.get("ALARM", 0))
        self.notice_card.set_count(counts.get("NOTICE", 0))
        self.total_card.set_count(len(self._active_events))

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
                    if column == 0:
                        item.setForeground(Qt.white)
                        item.setBackground(Qt.transparent)
                    item.setToolTip(str(value))
                    self.current_table.setItem(row_index, column, item)
        finally:
            self.current_table.setUpdatesEnabled(True)

        if selected_id:
            for index, event in enumerate(self._active_events):
                if event.get("id") == selected_id:
                    self.current_table.selectRow(index)
                    self._set_detail(event)
                    break
        elif self._active_events:
            self.current_table.selectRow(0)
            self._set_detail(self._active_events[0])
        else:
            self._set_detail(None)

    def _current_clicked(self, row: int, _column: int) -> None:
        if 0 <= row < len(self._active_events):
            self._set_detail(self._active_events[row])

    def _history_clicked(self, row: int, _column: int) -> None:
        if 0 <= row < len(self._history_events):
            self._set_detail(self._history_events[row])

    def _set_detail(self, event: Optional[Mapping[str, Any]]) -> None:
        if not event:
            for attr in (
                "detail_object", "detail_level", "detail_value", "detail_threshold",
                "detail_start", "detail_duration", "detail_state", "detail_end",
            ):
                getattr(self, attr).setText("--")
            self.detail_message.setText("当前没有可查看的报警")
            self.detail_reason.setText("")
            self.detail_suggestion.setText("")
            self.detail_code.setText("")
            return

        level = str(event.get("level") or "ALARM")
        state = str(event.get("state") or "ACTIVE")
        self.detail_object.setText(str(event.get("object_name") or "--"))
        self.detail_level.setText(_LEVEL_TEXT.get(level, level))
        self.detail_level.setStyleSheet(
            f"color:{_LEVEL_COLOR.get(level, '#e8f0ff')};font-weight:700;"
        )
        self.detail_value.setText(_fmt_value(event))
        self.detail_threshold.setText(str(event.get("threshold_text") or "--"))
        self.detail_start.setText(_fmt_time(event.get("start_time")))
        self.detail_duration.setText(_fmt_duration(event.get("duration_seconds")))
        self.detail_state.setText(_STATE_TEXT.get(state, state))
        self.detail_end.setText(_fmt_time(event.get("end_time")))
        self.detail_message.setText(str(event.get("message") or "报警"))

        detail = event.get("detail") if isinstance(event.get("detail"), Mapping) else {}
        reason_codes = detail.get("reason_codes") if isinstance(detail.get("reason_codes"), list) else []
        translated = translate_reason_codes(reason_codes) if reason_codes else []
        integration_error = detail.get("integration_error")
        reason_lines: List[str] = []
        if translated:
            reason_lines.extend(f"• {text}" for text in translated[:5])
        if integration_error:
            reason_lines.append(f"• 控制链路信息：{integration_error}")
        if event.get("recovery_message"):
            reason_lines.append(f"• {event.get('recovery_message')}")
        self.detail_reason.setText("\n".join(reason_lines))

        suggestion = str(detail.get("suggestion") or "")
        self.detail_suggestion.setText(f"建议检查：{suggestion}" if suggestion else "")
        self.detail_code.setText(
            f"原始状态码：{event.get('reason_code')}" if event.get("reason_code") else ""
        )

    def query_history(self) -> None:
        if self._history_worker is not None and self._history_worker.isRunning():
            return
        end = datetime.now()
        start = end - timedelta(seconds=self._history_span_seconds)
        self.history_query_button.setEnabled(False)
        self.history_status.setText("正在从 PostgreSQL 读取历史报警事件…")
        self._history_worker = AlarmHistoryThread(
            start,
            end,
            level=str(self.level_combo.currentData() or "ALL"),
            category=str(self.category_combo.currentData() or "ALL"),
            state=str(self.state_combo.currentData() or "ALL"),
            parent=self,
        )
        self._history_worker.result_ready.connect(self._on_history_result)
        self._history_worker.failed.connect(self._on_history_error)
        self._history_worker.finished.connect(self._on_history_finished)
        self._history_worker.start()

    def _on_history_result(self, rows: Sequence[Mapping[str, Any]]) -> None:
        self._history_events = [dict(item) for item in rows if isinstance(item, Mapping)]
        self.history_table.setUpdatesEnabled(False)
        try:
            self.history_table.setRowCount(0)
            for row_index, event in enumerate(self._history_events):
                self.history_table.insertRow(row_index)
                values = (
                    _fmt_time(event.get("start_time")),
                    _fmt_time(event.get("end_time")),
                    _fmt_duration(event.get("duration_seconds")),
                    _LEVEL_TEXT.get(str(event.get("level")), str(event.get("level") or "--")),
                    _CATEGORY_TEXT.get(str(event.get("category")), str(event.get("category") or "--")),
                    str(event.get("object_name") or "--"),
                    str(event.get("message") or "--"),
                    _STATE_TEXT.get(str(event.get("state")), str(event.get("state") or "--")),
                )
                for column, value in enumerate(values):
                    item = QTableWidgetItem(str(value))
                    item.setToolTip(str(value))
                    self.history_table.setItem(row_index, column, item)
        finally:
            self.history_table.setUpdatesEnabled(True)
        self.history_status.setText(f"PostgreSQL · 查询到 {len(self._history_events)} 条报警事件")
        if self._history_events:
            self.history_table.selectRow(0)
            self._set_detail(self._history_events[0])

    def _on_history_error(self, message: str) -> None:
        self.history_status.setText(f"历史报警查询失败：{message}")

    def _on_history_finished(self) -> None:
        self.history_query_button.setEnabled(True)
        self._history_worker = None

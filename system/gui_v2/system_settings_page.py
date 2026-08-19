from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Mapping, Optional

from PyQt5.QtCore import QThread, QTimer, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy import create_engine

from system.base.config.SysConfig import config
from system.model.config.operator_settings import (
    effective_ph_safe_range,
    effective_so2_target,
    ph_safe_range_source,
    reset_operator_ph_safe_range,
    reset_operator_so2_target,
    set_operator_ph_safe_range,
    set_operator_so2_target,
    settings_snapshot,
    so2_target_allowed_range,
    so2_target_source,
)
from system.model.config.plant_config import PLANT_CONFIG, enabled_towers

from .reason_text import translate_control_mode
from .widgets import CardFrame, StatusPill


class DatabaseProbeThread(QThread):
    checked = pyqtSignal(bool, str)

    def run(self) -> None:
        engine = None
        try:
            engine = create_engine(
                str(config["dbconnetion"]),
                pool_pre_ping=True,
                pool_size=1,
                max_overflow=0,
            )
            engine.execute("SELECT 1")
            self.checked.emit(True, "")
        except Exception as exc:
            self.checked.emit(False, str(exc))
        finally:
            if engine is not None:
                try:
                    engine.dispose()
                except Exception:
                    pass


class _ValueRow(QWidget):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.title = QLabel(title)
        self.title.setStyleSheet("font-size: 15px; color: #a9bdd9;")
        self.value = QLabel("--")
        self.value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.value.setStyleSheet("font-size: 15px; font-weight: 600; color: #eef5ff;")
        layout.addWidget(self.title)
        layout.addStretch(1)
        layout.addWidget(self.value)


class SystemSettingsPage(QWidget):
    """面向操作员的精简系统设置。

    不展示内部字段、工况网格、训练参数和设备映射；只开放：
    - 净烟气 SO2 控制目标；
    - 吸收塔 pH 安全范围；
    - 只读系统状态。
    """

    settings_changed = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._last_runtime: Dict[str, Any] = {}
        self._alarm_ok = True
        self._alarm_error = ""
        self._db_worker: Optional[DatabaseProbeThread] = None
        self._db_ok: Optional[bool] = None
        self._db_message = "等待检测"

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)

        title = QLabel("系统配置")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 22px; font-weight: 700; padding: 6px;")
        root.addWidget(title)

        root.addWidget(self._build_target_card())
        root.addWidget(self._build_ph_card())
        root.addWidget(self._build_status_card())
        root.addStretch(1)

        self._db_timer = QTimer(self)
        self._db_timer.setInterval(15000)
        self._db_timer.timeout.connect(self._probe_database)

        self.reload_settings()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.reload_settings()
        self._probe_database()
        if not self._db_timer.isActive():
            self._db_timer.start()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._db_timer.stop()
        super().hideEvent(event)

    @staticmethod
    def _spin_box(decimals: int = 2) -> QDoubleSpinBox:
        box = QDoubleSpinBox()
        box.setDecimals(decimals)
        box.setSingleStep(0.1)
        box.setMinimumWidth(190)
        box.setMinimumHeight(38)
        box.setAlignment(Qt.AlignRight)
        box.setStyleSheet(
            "QDoubleSpinBox {"
            " background:#0f1a2b; color:#eef5ff; border:1px solid #2a3b56;"
            " border-radius:6px; padding:5px 9px; font-size:15px;"
            "}"
            "QDoubleSpinBox:focus { border:1px solid #23c7c9; }"
        )
        return box

    @staticmethod
    def _action_button(text: str, primary: bool = False) -> QPushButton:
        button = QPushButton(text)
        button.setMinimumHeight(36)
        if primary:
            button.setStyleSheet(
                "QPushButton {background:#147f88;color:white;border:1px solid #25b9bd;"
                "border-radius:6px;padding:6px 18px;font-weight:600;}"
                "QPushButton:hover {background:#19919b;}"
            )
        else:
            button.setStyleSheet(
                "QPushButton {background:#111d30;color:#b9cbe3;border:1px solid #2b3d59;"
                "border-radius:6px;padding:6px 16px;}"
                "QPushButton:hover {background:#182842;color:#eef5ff;}"
            )
        return button

    def _build_target_card(self) -> QWidget:
        card = CardFrame()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)

        section = QLabel("控制目标设置")
        section.setProperty("role", "sectionTitle")
        layout.addWidget(section)

        row = QHBoxLayout()
        label = QLabel("目标净烟气 SO₂")
        label.setStyleSheet("font-size: 16px; font-weight: 600;")
        self.target_edit = self._spin_box(1)
        lo, hi = so2_target_allowed_range()
        self.target_edit.setRange(lo, hi)
        unit = QLabel("mg/Nm³")
        unit.setProperty("role", "muted")
        row.addWidget(label)
        row.addStretch(1)
        row.addWidget(self.target_edit)
        row.addWidget(unit)
        layout.addLayout(row)

        self.target_source_label = QLabel("当前来源：--")
        self.target_source_label.setProperty("role", "muted")
        layout.addWidget(self.target_source_label)

        hint = QLabel("未设置现场值时自动使用算法内部默认目标；恢复默认不会写死默认值。")
        hint.setProperty("role", "muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        reset = self._action_button("恢复默认")
        apply_button = self._action_button("应用修改", True)
        reset.clicked.connect(self._reset_target)
        apply_button.clicked.connect(self._apply_target)
        buttons.addWidget(reset)
        buttons.addWidget(apply_button)
        layout.addLayout(buttons)
        return card

    def _build_ph_card(self) -> QWidget:
        card = CardFrame()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)

        towers = enabled_towers(PLANT_CONFIG)
        self._tower_id = str(towers[0].get("tower_id")) if towers else "xst"
        if len(towers) == 1:
            section_text = "吸收塔 pH 安全范围"
        else:
            section_text = "%s pH 安全范围" % str(towers[0].get("display_name") or "吸收塔")
        section = QLabel(section_text)
        section.setProperty("role", "sectionTitle")
        layout.addWidget(section)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(12)
        low_label = QLabel("安全下限")
        high_label = QLabel("安全上限")
        low_label.setStyleSheet("font-size:15px;")
        high_label.setStyleSheet("font-size:15px;")
        self.ph_low_edit = self._spin_box(2)
        self.ph_high_edit = self._spin_box(2)
        self.ph_low_edit.setRange(0.01, 13.99)
        self.ph_high_edit.setRange(0.01, 13.99)
        grid.addWidget(low_label, 0, 0)
        grid.addWidget(self.ph_low_edit, 0, 1)
        grid.addWidget(QLabel("pH"), 0, 2)
        grid.addWidget(high_label, 1, 0)
        grid.addWidget(self.ph_high_edit, 1, 1)
        grid.addWidget(QLabel("pH"), 1, 2)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)

        self.ph_source_label = QLabel("当前来源：--")
        self.ph_source_label.setProperty("role", "muted")
        layout.addWidget(self.ph_source_label)

        hint = QLabel("该范围同时用于在线供浆安全判断和 pH 报警；未设置现场值时使用内部默认范围。")
        hint.setProperty("role", "muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        reset = self._action_button("恢复默认")
        apply_button = self._action_button("应用修改", True)
        reset.clicked.connect(self._reset_ph)
        apply_button.clicked.connect(self._apply_ph)
        buttons.addWidget(reset)
        buttons.addWidget(apply_button)
        layout.addLayout(buttons)
        return card

    def _build_status_card(self) -> QWidget:
        card = CardFrame()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        section = QLabel("系统状态")
        section.setProperty("role", "sectionTitle")
        layout.addWidget(section)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(12)
        self.dcs_status = StatusPill("等待数据", "warning")
        self.db_status = StatusPill("检测中", "warning")
        self.control_status = StatusPill("等待模型", "warning")
        self.alarm_status = StatusPill("正常", "normal")
        entries = (
            ("DCS通讯", self.dcs_status),
            ("数据库", self.db_status),
            ("智能控制", self.control_status),
            ("报警服务", self.alarm_status),
        )
        for index, (name, widget) in enumerate(entries):
            label = QLabel(name)
            label.setStyleSheet("font-size:15px;color:#a9bdd9;")
            grid.addWidget(label, index // 2, (index % 2) * 2)
            grid.addWidget(widget, index // 2, (index % 2) * 2 + 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        layout.addLayout(grid)

        layout.addSpacing(6)
        self.condition_row = _ValueRow("当前工况")
        self.mode_row = _ValueRow("控制模式")
        self.update_row = _ValueRow("最后更新时间")
        layout.addWidget(self.condition_row)
        layout.addWidget(self.mode_row)
        layout.addWidget(self.update_row)
        return card

    def reload_settings(self) -> None:
        snapshot = settings_snapshot()
        self.target_edit.setValue(float(snapshot["outlet_so2_target"]))
        self.target_source_label.setText(
            "当前来源：%s" % snapshot["outlet_so2_target_source"]
        )

        low, high = effective_ph_safe_range(self._tower_id)
        self.ph_low_edit.setValue(low)
        self.ph_high_edit.setValue(high)
        self.ph_source_label.setText(
            "当前来源：%s" % ph_safe_range_source(self._tower_id)
        )

    def _apply_target(self) -> None:
        new_value = float(self.target_edit.value())
        old_value = effective_so2_target()
        answer = QMessageBox.question(
            self,
            "确认修改控制目标",
            "目标净烟气 SO₂ 将从 %.1f 修改为 %.1f mg/Nm³。\n\n确认应用吗？"
            % (old_value, new_value),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            set_operator_so2_target(new_value)
        except Exception as exc:
            QMessageBox.warning(self, "无法保存", str(exc))
            return
        self.reload_settings()
        self.settings_changed.emit(settings_snapshot())

    def _reset_target(self) -> None:
        if so2_target_source() == "默认配置":
            self.reload_settings()
            return
        answer = QMessageBox.question(
            self,
            "恢复默认目标",
            "将删除现场目标覆盖，并重新使用算法内部默认目标。\n\n确认恢复吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        reset_operator_so2_target()
        self.reload_settings()
        self.settings_changed.emit(settings_snapshot())

    def _apply_ph(self) -> None:
        low = float(self.ph_low_edit.value())
        high = float(self.ph_high_edit.value())
        old_low, old_high = effective_ph_safe_range(self._tower_id)
        if not (0.0 < low < high < 14.0):
            QMessageBox.warning(self, "范围无效", "pH 安全下限必须小于安全上限。")
            return
        answer = QMessageBox.question(
            self,
            "确认修改 pH 安全范围",
            "吸收塔 pH 安全范围将从 %.2f～%.2f 修改为 %.2f～%.2f。\n\n"
            "该范围会用于在线供浆安全判断和 pH 报警。确认应用吗？"
            % (old_low, old_high, low, high),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            set_operator_ph_safe_range(self._tower_id, low, high)
        except Exception as exc:
            QMessageBox.warning(self, "无法保存", str(exc))
            return
        self.reload_settings()
        self.settings_changed.emit(settings_snapshot())

    def _reset_ph(self) -> None:
        if ph_safe_range_source(self._tower_id) == "默认配置":
            self.reload_settings()
            return
        answer = QMessageBox.question(
            self,
            "恢复默认 pH 范围",
            "将删除现场 pH 安全范围覆盖，并重新使用内部默认范围。\n\n确认恢复吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        reset_operator_ph_safe_range(self._tower_id)
        self.reload_settings()
        self.settings_changed.emit(settings_snapshot())

    def update_runtime(self, data: Mapping[str, Any]) -> None:
        self._last_runtime = dict(data)
        connection = data.get("connection_status")
        if connection is False:
            self.dcs_status.set_state("danger", "异常")
        elif connection is True:
            self.dcs_status.set_state("normal", "正常")
        else:
            self.dcs_status.set_state("warning", "等待数据")

        integration_valid = data.get("integration_valid")
        decision = str(data.get("decision_state_code") or "").upper()
        if integration_valid is False or decision == "BLOCKED":
            self.control_status.set_state("danger", "控制阻断")
        elif integration_valid is True:
            self.control_status.set_state("normal", "正常")
        else:
            self.control_status.set_state("warning", "等待模型")

        condition = data.get("condition_label")
        self.condition_row.value.setText(
            "--" if condition in (None, "", "--") else str(condition)
        )
        self.mode_row.value.setText(str(data.get("control_mode") or "--"))
        stamp = data.get("date")
        self.update_row.value.setText(str(stamp or "--"))

    def update_alarm_runtime(self, payload: Mapping[str, Any]) -> None:
        error = str(payload.get("persistence_error") or "").strip()
        self._alarm_error = error
        self._alarm_ok = not bool(error)
        if self._alarm_ok:
            self.alarm_status.set_state("normal", "正常")
        else:
            self.alarm_status.set_state("danger", "异常")
            self.alarm_status.setToolTip(error)

    def show_alarm_runtime_error(self, message: str) -> None:
        self._alarm_ok = False
        self._alarm_error = str(message)
        self.alarm_status.set_state("danger", "异常")
        self.alarm_status.setToolTip(self._alarm_error)

    def _probe_database(self) -> None:
        if self._db_worker is not None and self._db_worker.isRunning():
            return
        self.db_status.set_state("warning", "检测中")
        worker = DatabaseProbeThread(self)
        worker.checked.connect(self._database_checked)
        worker.finished.connect(self._database_probe_finished)
        self._db_worker = worker
        worker.start()

    def _database_checked(self, ok: bool, message: str) -> None:
        self._db_ok = bool(ok)
        self._db_message = str(message or "")
        if ok:
            self.db_status.set_state("normal", "正常")
            self.db_status.setToolTip("PostgreSQL 连接正常")
        else:
            self.db_status.set_state("danger", "异常")
            self.db_status.setToolTip(self._db_message)

    def _database_probe_finished(self) -> None:
        worker = self._db_worker
        self._db_worker = None
        if worker is not None:
            worker.deleteLater()

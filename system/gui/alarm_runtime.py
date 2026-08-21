"""Qt 运行桥：周期调用独立 AlarmManager，并把事件异步写 PostgreSQL。"""
from __future__ import annotations

from typing import Any, Dict, Mapping

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from system.model.alarm import AlarmPersistenceWorker
from system.model.alarm.alarm_config import (
    ALARM_RUNTIME_CONFIG,
    outlet_so2_limits,
    ph_alarm_specs,
)
from system.model.alarm.arrival_alarm_manager import ArrivalAwareAlarmManager


class AlarmRuntime(QObject):
    alarms_updated = pyqtSignal(object)
    runtime_error = pyqtSignal(str)

    def __init__(self, global_data: Mapping[str, Any], parent: QObject = None) -> None:
        super().__init__(parent)
        self.global_data = global_data
        self.manager = ArrivalAwareAlarmManager()
        self.persistence = AlarmPersistenceWorker()
        self.persistence.start()

        self._timer = QTimer(self)
        self._timer.setInterval(
            max(250, int(ALARM_RUNTIME_CONFIG.evaluation_interval_seconds * 1000))
        )
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        QTimer.singleShot(0, self._tick)

    def _tick(self) -> None:
        try:
            # pH 安全范围允许系统配置页运行时修改；每次判定前刷新有效范围，
            # 无需重启 AlarmManager，也不会清空现有活动报警状态。
            self.manager._so2_limits = outlet_so2_limits()
            self.manager._ph_specs = ph_alarm_specs()

            result: Dict[str, Any] = self.manager.evaluate_global_data(self.global_data)
            for transition in result.get("transitions", []) or []:
                if not isinstance(transition, dict):
                    continue
                action = str(transition.get("action") or "")
                event = transition.get("event")
                if action and isinstance(event, dict):
                    self.persistence.submit(action, event)

            payload = dict(result)
            payload["persistence_error"] = self.persistence.last_error
            self.alarms_updated.emit(payload)
        except Exception as exc:
            self.runtime_error.emit(str(exc))

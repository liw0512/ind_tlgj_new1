from __future__ import annotations

import threading
import traceback
from typing import Any, Dict, List

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QLabel

from system.data_opts.DataClientMain import DataClientMain
from system.data_opts.DataHandler import DataHandler
from system.data_opts.client_helper.MokeSlaveClient import MokeSlaveClient

from .alarm_page_live import LiveAlarmPage
from .alarm_runtime import AlarmRuntime
from .demo_dashboard import DashboardWindow, build_application
from .history_gap_display import apply_history_gap_display
from .history_page import HistoryPage
from .history_time_link import apply_history_time_link
from .history_ui_polish import apply_history_ui_polish
from .history_wheel_guard import apply_history_wheel_guard
from .system_settings_page import SystemSettingsPage


def start_current_backend(global_data: Dict[str, Any]) -> List[Any]:
    """启动正式前端使用的数据、模型、输出和现场客户端链路。"""

    data_client = DataClientMain(global_data)
    handler = DataHandler(global_data)
    field_client = MokeSlaveClient(global_data=global_data)

    workers = [
        (data_client.start, "ui-v2-data-client"),
        (handler.start, "ui-v2-data-handler"),
        (data_client.send_cnn_to_dcs, "ui-v2-dcs-output"),
        (field_client.run, "ui-v2-field-client"),
    ]
    threads = []
    for target, name in workers:
        thread = threading.Thread(target=target, name=name, daemon=True)
        thread.start()
        threads.append(thread)

    return [data_client, handler, field_client, *threads]


def install_overview_title(window: DashboardWindow) -> None:
    overview_layout = window.overview.layout()
    if overview_layout is None:
        return

    title = QLabel("西热钢厂1号机组供浆控制系统", window.overview)
    title.setAlignment(Qt.AlignCenter)
    title.setMinimumHeight(54)
    title.setStyleSheet(
        "font-size: 24px; font-weight: 700; padding-top: 8px; padding-bottom: 6px;"
    )

    overview_layout.insertWidget(0, title)
    overview_layout.insertSpacing(1, 8)
    window.overview.condition.unit_label.hide()
    window.overview.system_title = title


def install_history_page(window: DashboardWindow) -> None:
    history_index = 3
    old_page = window.stack.widget(history_index)
    history_page = HistoryPage(window)
    apply_history_ui_polish(history_page)
    apply_history_time_link(history_page)
    apply_history_wheel_guard(history_page)
    apply_history_gap_display()
    wrapped = window._scroll_wrap(history_page)
    wrapped.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    if old_page is not None:
        window.stack.removeWidget(old_page)
        old_page.deleteLater()
    window.stack.insertWidget(history_index, wrapped)
    window.history = history_page


def install_alarm_page(window: DashboardWindow, global_data: Dict[str, Any]) -> None:
    alarm_index = 4
    old_page = window.stack.widget(alarm_index)
    alarm_page = LiveAlarmPage(window)
    wrapped = window._scroll_wrap(alarm_page)
    wrapped.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    if old_page is not None:
        window.stack.removeWidget(old_page)
        old_page.deleteLater()
    window.stack.insertWidget(alarm_index, wrapped)

    alarm_runtime = AlarmRuntime(global_data, window)
    alarm_runtime.alarms_updated.connect(alarm_page.update_runtime)
    alarm_runtime.runtime_error.connect(alarm_page.show_runtime_error)

    window.alarm = alarm_page
    window.alarm_runtime = alarm_runtime


def install_system_settings_page(window: DashboardWindow) -> None:
    """安装第6页操作员系统配置：目标SO2、pH安全范围、系统状态。"""
    settings_index = 5
    old_page = window.stack.widget(settings_index)
    settings_page = SystemSettingsPage(window)
    wrapped = window._scroll_wrap(settings_page)
    wrapped.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    if old_page is not None:
        window.stack.removeWidget(old_page)
        old_page.deleteLater()
    window.stack.insertWidget(settings_index, wrapped)

    # 运行状态继续来自现有 GUI Adapter，不重复建立新的实时数据轮询。
    window.source.data_ready.connect(settings_page.update_runtime)

    # 报警服务状态直接复用已经存在的 AlarmRuntime。
    if hasattr(window, "alarm_runtime"):
        window.alarm_runtime.alarms_updated.connect(settings_page.update_alarm_runtime)
        window.alarm_runtime.runtime_error.connect(settings_page.show_alarm_runtime_error)

    # 保存配置后立即刷新 GUI 与报警有效范围；在线第二模块会在下一决策周期读取覆盖值。
    def _settings_changed(_snapshot) -> None:
        try:
            if hasattr(window.source, "poll"):
                window.source.poll()
        except Exception:
            pass
        try:
            if hasattr(window, "alarm_runtime"):
                window.alarm_runtime._tick()
        except Exception:
            pass

    settings_page.settings_changed.connect(_settings_changed)
    window.settings_page = settings_page


def main() -> int:
    try:
        global_data: Dict[str, Any] = {"data": []}
        backend_refs = start_current_backend(global_data)

        app = build_application()
        window = DashboardWindow(global_data, data_mode="live")
        install_overview_title(window)
        install_history_page(window)
        install_alarm_page(window, global_data)
        install_system_settings_page(window)
        window._backend_refs = backend_refs
        window.show()
        return app.exec_()
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

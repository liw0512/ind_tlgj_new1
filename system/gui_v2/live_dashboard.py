from __future__ import annotations

import threading
import traceback
from typing import Any, Dict, List

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QLabel

from system.data_opts.DataClientMain import DataClientMain
from system.data_opts.DataHandler import DataHandler
from system.data_opts.client_helper.MokeSlaveClient import MokeSlaveClient

from .demo_dashboard import DashboardWindow, build_application


def start_current_backend(global_data: Dict[str, Any]) -> List[Any]:
    """启动与当前 Application.py 相同的后台链路，但不修改正式 Application.py。

    当前仓库正式入口默认使用 MokeSlaveClient；因此这个 LIVE 测试入口也保持一致。
    到现场改真实 Modbus 客户端时，应与 Application.py 的正式客户端选择保持同步。
    """

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

    # 保留对象引用，便于将来扩展 stop/health-check。
    return [data_client, handler, field_client, *threads]


def install_overview_title(window: DashboardWindow) -> None:
    """在运行总览内容顶部增加厂/机组级标题，并整体下移正文区域。"""

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

    # 保存引用，避免后续页面扩展时标题对象被误认为临时控件。
    window.overview.system_title = title


def main() -> int:
    try:
        global_data: Dict[str, Any] = {"data": []}
        backend_refs = start_current_backend(global_data)

        app = build_application()
        window = DashboardWindow(global_data, data_mode="live")
        install_overview_title(window)
        # 防止未来重构时误释放后端对象。
        window._backend_refs = backend_refs
        window.show()
        return app.exec_()
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

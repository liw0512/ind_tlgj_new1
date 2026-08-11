import _thread
import json
import os
import sys

# 固定 Qt 的缩放与字体 DPI，避免 Windows 125%/150% 缩放导致固定布局中的字体异常放大。
os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "0")
os.environ.setdefault("QT_SCALE_FACTOR", "1")
os.environ.setdefault("QT_FONT_DPI", "96")
import threading
import traceback
import time
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QFontDatabase
from PyQt5.QtWidgets import QApplication
from collections import deque
from system.data_opts.DataClientMain import DataClientMain
from system.data_opts.DataHandler import DataHandler
from system.data_opts.client_helper.ModbusRTUSlaveClient import ModbusRTUSlaveClient
from system.data_opts.client_helper.ModbusTCPSlaveClient import ModbusTCPSlaveClient
from system.data_opts.client_helper.MokeSlaveClient import MokeSlaveClient
# from system.gui.ExtDoubleWindow import ExtDoubleWindow
from system.gui.ExtSingleWindow import ExtSingleWindow
from system.gui.ExtSettingsWindow import ExtSettings
from system.data_opts.client_helper.ModbusTCPClient import ModbusTCPClient

##sys.path.append(r"E:\fengzhuang\ind_optim_serv\system\model\map_control\cluster")
if __name__ == "__main__":

    try:

        # GLOBAL_DATA = {"data": []}
        GLOBAL_DATA = {"data": []}
                       # "map_control": {}}
        double_win = DataClientMain(GLOBAL_DATA)
        handler = DataHandler(GLOBAL_DATA)
        # client = ModbusRTUSlaveClient(
        #     port='/dev/pts/3',  # 或 'COM3' (Windows)
        #     baudrate=19200,
        #     global_data=GLOBAL_DATA
        # )
        client = MokeSlaveClient(global_data=GLOBAL_DATA)
        # client = ModbusTCPClient(host="192.168.1.88", port=503, global_data=GLOBAL_DATA)
        # client = ModbusTCPClient(host="192.168.1.189", port=5001, global_data=GLOBAL_DATA)
        t1 = threading.Thread(target=double_win.start, args=())
        t1.start()

        t2 = threading.Thread(target=handler.start, args=())
        t2.start()
        t3 = threading.Thread(target=double_win.send_cnn_to_dcs, args=())
        t3.start()
        t4 = threading.Thread(target=client.run, args=())
        t4.start()

        # t3 = threading.Thread(target=client.send_cnn_to_dcs, args=())
        # t3.start()

        # t5 = threading.Thread(target=client.send_heart, args=())
        # t5.start()

        # QApplication 创建前设置属性，保证固定像素界面在不同电脑上使用一致的 96 DPI。
        for attribute_name in ("AA_DisableHighDpiScaling", "AA_Use96Dpi"):
            attribute = getattr(Qt, attribute_name, None)
            if attribute is not None:
                QApplication.setAttribute(attribute, True)

        app = QApplication(sys.argv)

        # 优先使用跨平台中文字体；动态创建的泵名称与状态将基于该字体显示。
        available_families = set(QFontDatabase().families())
        for family_name in (
            "Microsoft YaHei UI",
            "Microsoft YaHei",
            "Noto Sans CJK SC",
            "SimSun",
        ):
            if family_name in available_families:
                app.setFont(QFont(family_name, 10))
                break

        settings = ExtSettings()

        win = ExtSingleWindow(GLOBAL_DATA)
        # win.btn_settings.clicked.connect(settings._show)
        win.show()

        sys.exit(app.exec_())

    except Exception as e:
        traceback.print_exc()

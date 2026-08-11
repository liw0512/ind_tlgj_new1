import json
import os
import traceback

from PyQt5.QtWidgets import QMainWindow, QMessageBox

from system.base.config.SysConfig import config
from system.gui.base.SettingsWindow import Ui_MainWindow


class ExtSettings(QMainWindow, Ui_MainWindow):
    def __init__(self):
        try:

            super(ExtSettings, self).__init__()
            super().setupUi(self)

            self.setFixedSize(self.size())

            self.btn_range.clicked.connect(self.save)

        except Exception as e:
            traceback.print_exc()

    def _show(self):
        self.show()
        self.load()

    def load(self):

        _config_path = f'{config["base_path"]}/settings.json'
        if os.path.exists(_config_path):
            f = open(_config_path, "r")
            content = f.read()
            f.close()
        else:
            f = open(f'{config["base_path"]}/settings-defaults.json', "r")
            content = f.read()
            f.close()

        data_dict: dict = json.loads(content)

        self.load_min.setText(data_dict["load_min"])
        self.load_max.setText(data_dict["load_max"])

        self.s_in_cur_min.setText(data_dict["s_in_cur_min"])
        self.s_in_cur_max.setText(data_dict["s_in_cur_max"])

        self.s_out_cur_min.setText(data_dict["s_out_cur_min"])
        self.s_out_cur_max.setText(data_dict["s_out_cur_max"])

        self.P1_yxt_min.setText(data_dict["P1_yxt_min"])
        self.P1_yxt_max.setText(data_dict["P1_yxt_max"])

        self.P2_yxt_min.setText(data_dict["P2_yxt_min"])
        self.P2_yxt_max.setText(data_dict["P2_yxt_max"])

        self.yxtjymd_min.setText(data_dict["yxtjymd_min"])
        self.yxtjymd_max.setText(data_dict["yxtjymd_max"])

        self.yhfl_yxt_min.setText(data_dict["yhfl_yxt_min"])
        self.yhfl_yxt_max.setText(data_dict["yhfl_yxt_max"])

        self.P1_xst_min.setText(data_dict["P1_xst_min"])
        self.P1_xst_max.setText(data_dict["P1_xst_max"])

        self.P2_xst_min.setText(data_dict["P2_xst_min"])
        self.P2_xst_max.setText(data_dict["P2_xst_max"])

        self.xstjymd_min.setText(data_dict["xstjymd_min"])
        self.xstjymd_max.setText(data_dict["xstjymd_max"])

        self.yhfl_xst_min.setText(data_dict["yhfl_xst_min"])
        self.yhfl_xst_max.setText(data_dict["yhfl_xst_max"])

    def save(self):

        data = {
            "load_min": self.load_min.text().strip(),
            "load_max": self.load_max.text().strip(),
            "s_in_cur_min": self.s_in_cur_min.text().strip(),
            "s_in_cur_max": self.s_in_cur_max.text().strip(),
            "s_out_cur_min": self.s_out_cur_min.text().strip(),
            "s_out_cur_max": self.s_out_cur_max.text().strip(),
            "P1_yxt_min": self.P1_yxt_min.text().strip(),
            "P1_yxt_max": self.P1_yxt_max.text().strip(),
            "P2_yxt_min": self.P2_yxt_min.text().strip(),
            "P2_yxt_max": self.P2_yxt_max.text().strip(),
            "yxtjymd_min": self.yxtjymd_min.text().strip(),
            "yxtjymd_max": self.yxtjymd_max.text().strip(),
            "yhfl_yxt_min": self.yhfl_yxt_min.text().strip(),
            "yhfl_yxt_max": self.yhfl_yxt_max.text().strip(),
            "P1_xst_min": self.P1_xst_min.text().strip(),
            "P1_xst_max": self.P1_xst_max.text().strip(),
            "P2_xst_min": self.P2_xst_min.text().strip(),
            "P2_xst_max": self.P2_xst_max.text().strip(),
            "xstjymd_min": self.xstjymd_min.text().strip(),
            "xstjymd_max": self.xstjymd_max.text().strip(),
            "yhfl_xst_min": self.yhfl_xst_min.text().strip(),
            "yhfl_xst_max": self.yhfl_xst_max.text().strip()
        }

        content = json.dumps(data)
        _config_path = f'{config["base_path"]}/settings.json'
        f = open(_config_path, "w")
        f.write(content)
        f.close()

        QMessageBox.information(self, "提示", "保存成功！")

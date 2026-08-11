import traceback

from PyQt5.QtWidgets import QMainWindow, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QMessageBox
from PyQt5.QtCore import Qt, pyqtSignal

from system.gui.base.SystemInitRootWindow import Ui_SystemInit
from system.gui.base.SettingsWindow import Ui_MainWindow


class ExtPumpDialogWindow(QDialog):
    # 定义信号，用于向主窗口传递数据
    pump_data = pyqtSignal(dict)

    def __init__(self, parent=None):
        super(ExtPumpDialogWindow, self).__init__(parent)
        self.setup_ui()

    def setup_ui(self):
        self.setWindowTitle("添加循环泵")
        self.setFixedSize(400, 300)

        # 创建主布局
        layout = QVBoxLayout()

        # 创建表单项
        form_items = [
            ("泵名称:", "name"),
            ("功率(KW):", "power"),
            ("循环流量(m³/h):", "flow"),
            ("扬程(mH₂O):", "head"),
            ("电流范围(A):", "current_range")
        ]

        self.inputs = {}
        for label_text, field_name in form_items:
            # 创建水平布局
            h_layout = QHBoxLayout()

            # 添加标签
            label = QLabel(label_text)
            label.setFixedWidth(100)
            h_layout.addWidget(label)

            # 添加输入框
            line_edit = QLineEdit()
            line_edit.setObjectName(field_name)
            self.inputs[field_name] = line_edit
            h_layout.addWidget(line_edit)

            layout.addLayout(h_layout)

        # 添加按钮
        button_layout = QHBoxLayout()

        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.clicked.connect(self.reject)
        button_layout.addWidget(self.btn_cancel)

        self.btn_confirm = QPushButton("确定")
        self.btn_confirm.clicked.connect(self.confirm)
        button_layout.addWidget(self.btn_confirm)

        layout.addLayout(button_layout)

        self.setLayout(layout)

    def confirm(self):
        """确认添加"""
        try:
            # 收集数据
            data = {}

            # 验证泵名称
            name = self.inputs['name'].text().strip()
            if not name:
                QMessageBox.warning(self, "警告", "请输入泵名称")
                return
            data['name'] = name

            # 验证功率
            power = self.inputs['power'].text().strip()
            if not power:
                QMessageBox.warning(self, "警告", "请输入功率")
                return
            try:
                power_float = float(power)
                if power_float <= 0:
                    QMessageBox.warning(self, "警告", "功率必须大于0")
                    return
                data['power'] = power
            except ValueError:
                QMessageBox.warning(self, "警告", "功率必须是有效的数字")
                return

            # 验证循环流量
            flow = self.inputs['flow'].text().strip()
            if not flow:
                QMessageBox.warning(self, "警告", "请输入循环流量")
                return
            try:
                flow_float = float(flow)
                if flow_float <= 0:
                    QMessageBox.warning(self, "警告", "循环流量必须大于0")
                    return
                data['flow'] = flow
            except ValueError:
                QMessageBox.warning(self, "警告", "循环流量必须是有效的数字")
                return

            # 验证扬程
            head = self.inputs['head'].text().strip()
            if not head:
                QMessageBox.warning(self, "警告", "请输入扬程")
                return
            try:
                head_float = float(head)
                if head_float <= 0:
                    QMessageBox.warning(self, "警告", "扬程必须大于0")
                    return
                data['head'] = head
            except ValueError:
                QMessageBox.warning(self, "警告", "扬程必须是有效的数字")
                return

            # 验证电流范围
            current_range = self.inputs['current_range'].text().strip()
            if not current_range:
                QMessageBox.warning(self, "警告", "请输入电流范围")
                return
            # 检查电流范围格式（例如：10-20）
            if not self.is_valid_current_range(current_range):
                QMessageBox.warning(self, "警告", "电流范围格式无效，请使用如 '10-20' 的格式")
                return
            data['current_range'] = current_range

            # 发送数据
            self.pump_data.emit(data)
            self.accept()

        except Exception as e:
            QMessageBox.critical(self, "错误", f"添加泵数据失败：{str(e)}")

    def is_valid_current_range(self, current_range):
        """验证电流范围格式"""
        try:
            if '-' not in current_range:
                return False
            min_val, max_val = current_range.split('-')
            min_float = float(min_val)
            max_float = float(max_val)
            return min_float >= 0 and max_float > min_float
        except:
            return False

from PyQt5.QtWidgets import QDialog
from PyQt5 import uic

class AddPumpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        # 加载UI文件
        uic.loadUi('pump_dialog.ui', self)
        
        # 绑定按钮事件
        self.pushButton_cancel.clicked.connect(self.reject)
        self.pushButton_confirm.clicked.connect(self.accept)
    
    def get_pump_data(self):
        """获取泵的数据"""
        return {
            'name': self.lineEdit_name.text(),
            'flow': self.lineEdit_flow.text(),
            'current_min': self.spinBox_current_min.value(),
            'current_max': self.spinBox_current_max.value(),
            'power': self.lineEdit_power.text(),
            'head': self.lineEdit_head.text()
        } 
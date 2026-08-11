import traceback

from PyQt5.QtCore import QThread, pyqtSignal, QTimer


class ChartUpdateThread(QThread):
    result = pyqtSignal(bool)

    def __init__(self, interval):
        super().__init__()
        self.interval = interval

    def run(self):
        try:
            timer = QTimer()
            timer.timeout.connect(self.emit_signal)
            timer.start(self.interval * 1000)
            self.exec_()
        except Exception as e:
            traceback.print_exc()

    def emit_signal(self):
        try:
            self.result.emit(True)
        except Exception as e:
            traceback.print_exc()

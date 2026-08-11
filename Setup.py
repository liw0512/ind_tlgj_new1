import sys
import traceback

from PyQt5.QtWidgets import QApplication

from system.gui.ExtSystemInitWindow import SystemInitWindow

if __name__ == '__main__':
    try:
        app = QApplication(sys.argv)
        window = SystemInitWindow()
        window.show()
        sys.exit(app.exec_())

    except Exception as e:
            traceback.print_exc()
# ==================================================
# FFlash — Entry Point
# ==================================================

import sys
from PySide6.QtWidgets import QApplication
from gui.main_window import MainWindow
from gui.style import load_stylesheet, is_dark_mode_enabled


if __name__ == "__main__":

    app = QApplication(sys.argv)
    app.setStyleSheet(load_stylesheet(dark=is_dark_mode_enabled()))

    window = MainWindow()
    window.show()

    sys.exit(app.exec())
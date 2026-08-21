# ==================================================
# Main Window
# ==================================================
#
# Central MainWindow class that composes all tab
# functionality via mixins.
# ==================================================

from datetime import datetime

from PySide6.QtWidgets import QMainWindow

from ui_main_window import Ui_MainWindow
from gui.flash_tab import FlashTabMixin
from gui.configure_tab import ConfigureTabMixin
from config.settings import APP_NAME


class MainWindow(
    FlashTabMixin,
    ConfigureTabMixin,
    QMainWindow
):

    def __init__(self):

        super().__init__()

        # ==========================================
        # Setup UI
        # ==========================================

        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        self.setWindowTitle(APP_NAME)

        # ==========================================
        # Initialize Tabs
        # ==========================================

        self.setup_flash_tab()
        self.setup_configure_tab()

        # ==========================================
        # Logs
        # ==========================================

        self.ui.informationText.clear()
        self.ui.traceText.clear()

        self.log_information("Ready.")

    # ==================================================
    # Information log
    # ==================================================

    def log_information(self, message):

        timestamp = datetime.now().strftime(
            "%H:%M:%S"
        )

        self.ui.informationText.append(
            f"[{timestamp}] {message}"
        )

        self.ui.informationText.ensureCursorVisible()

    # ==================================================
    # Trace log
    # ==================================================

    def log_trace(self, message):

        timestamp = datetime.now().strftime(
            "%H:%M:%S.%f"
        )[:-3]

        self.ui.traceText.append(
            f"[{timestamp}] {message}"
        )

        self.ui.traceText.ensureCursorVisible()

    # ==================================================
    # Close Event
    # ==================================================

    def closeEvent(self, event):
        """Hàm này được gọi tự động khi bấm nút [X] tắt cửa sổ"""

        if (self.thread is not None
                and self.thread.isRunning()):

            self.worker.request_abort()
            self.thread.quit()
            self.thread.wait()

        event.accept()

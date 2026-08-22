# ==================================================
# Menu Bar
# ==================================================
#
# Wires the File/Tools/Help menus declared in
# gui/main_window.ui (menubar was empty Designer boilerplate
# until now) to their handlers. Kept intentionally small —
# most actions already live in tabs/buttons; this only adds
# entries for things with no other GUI entry point yet
# (Test Connection) plus conventional File/Help items.
# ==================================================

import os
import sys

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox

from config.settings import APP_NAME, APP_VERSION, APP_AUTHOR, APP_AUTHOR_NAME
from gui.test_connection_dialog import TestConnectionDialog

# Running from source: docs/user_guide.html sits two levels up
# from this file (gui/menu_bar.py -> gui/ -> project root ->
# docs/). Running as a PyInstaller --onefile .exe: bundled data
# files are extracted to sys._MEIPASS at startup instead (see
# build.bat's --add-data), so prefer that when it's set.
_PROJECT_ROOT = (
    sys._MEIPASS if hasattr(sys, "_MEIPASS")
    else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
_GUIDELINE_PATH = os.path.join(_PROJECT_ROOT, "docs", "user_guide.html")


class MenuBarMixin:
    """Mixin wiring the MainWindow menu bar (File/Tools/Help)."""

    def setup_menu_bar(self):

        if hasattr(self.ui, 'actionLoadFirmware'):
            self.ui.actionLoadFirmware.triggered.connect(
                self.action_load_firmware
            )

        if hasattr(self.ui, 'actionExit'):
            self.ui.actionExit.triggered.connect(
                self.action_exit
            )

        if hasattr(self.ui, 'actionTestConnection'):
            self.ui.actionTestConnection.triggered.connect(
                self.action_test_connection
            )

        if hasattr(self.ui, 'actionExportReport'):
            self.ui.actionExportReport.triggered.connect(
                self.export_report
            )

        if hasattr(self.ui, 'actionAbout'):
            self.ui.actionAbout.triggered.connect(
                self.action_about
            )

        if hasattr(self.ui, 'actionOpenGuideline'):
            self.ui.actionOpenGuideline.triggered.connect(
                self.action_open_guideline
            )

    # ==================================================
    # File
    # ==================================================

    def action_load_firmware(self):

        # Jump to Configure -> Data first so the newly loaded
        # row is actually visible instead of landing silently
        # on whatever tab/page the user was on.
        if hasattr(self.ui, 'tabWidget'):
            self.ui.tabWidget.setCurrentIndex(1)
        if hasattr(self.ui, 'navListWidget'):
            self.ui.navListWidget.setCurrentRow(0)

        self.add_new_datablock()

    def action_exit(self):

        self.close()

    # ==================================================
    # Tools
    # ==================================================

    def action_test_connection(self):

        use_virtual = True
        if hasattr(self.ui, 'comboBoxHardware'):
            use_virtual = (
                self.ui.comboBoxHardware.currentData() is None
            )

        # Same best-effort CAN bus conflict warning as the
        # Flash button — Test Connection talks to real hardware
        # too, so the same "is CANoe already open" risk applies.
        if (not use_virtual
                and hasattr(self, 'detect_can_conflict_warning')):
            warning = self.detect_can_conflict_warning()
            if warning:
                choice = QMessageBox.warning(
                    self,
                    "Possible CAN Bus Conflict",
                    warning + "\n\nContinue with Test Connection anyway?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if choice != QMessageBox.Yes:
                    return

        functional = False
        if hasattr(self.ui, 'comboBoxFlashSequence'):
            functional = (
                "Suzuki"
                in self.ui.comboBoxFlashSequence.currentText()
            )

        security_dll_path = getattr(
            self, '_security_dll_path', ''
        ) or None

        can_config = (
            self.get_can_config()
            if hasattr(self, 'get_can_config')
            else {}
        )

        dialog = TestConnectionDialog(
            self, use_virtual, security_dll_path,
            functional, can_config,
        )
        dialog.exec()

    # ==================================================
    # Help
    # ==================================================

    def action_about(self):

        QMessageBox.about(
            self,
            f"About {APP_NAME}",
            f"<b>{APP_NAME} v{APP_VERSION}</b>"
            f"<p>ECU firmware flashing over CAN using UDS "
            f"(ISO 14229).</p>"
            f"<p>Author: {APP_AUTHOR} ({APP_AUTHOR_NAME})</p>",
        )

    def action_open_guideline(self):

        if os.path.isfile(_GUIDELINE_PATH):
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(_GUIDELINE_PATH)
            )
        else:
            QMessageBox.warning(
                self, "Guideline Not Found",
                f"Could not find user_guide.html at:\n{_GUIDELINE_PATH}",
            )

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
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import QApplication, QMessageBox

from config.settings import APP_NAME, APP_VERSION, APP_AUTHOR, APP_AUTHOR_NAME
from gui.test_connection_dialog import TestConnectionDialog
from gui.style import load_stylesheet, is_dark_mode_enabled

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

MAX_RECENT_FILES = 8


class MenuBarMixin:
    """Mixin wiring the MainWindow menu bar (File/Tools/Help)."""

    def setup_menu_bar(self):

        if hasattr(self.ui, 'actionLoadFirmware'):
            self.ui.actionLoadFirmware.triggered.connect(
                self.action_load_firmware
            )

        if hasattr(self.ui, 'menuRecentFiles'):
            self._rebuild_recent_files_menu()

        if hasattr(self.ui, 'actionClearRecentFiles'):
            self.ui.actionClearRecentFiles.triggered.connect(
                self.action_clear_recent_files
            )

        if hasattr(self.ui, 'actionSaveProjectAs'):
            self.ui.actionSaveProjectAs.triggered.connect(
                self.save_project_as
            )

        if hasattr(self.ui, 'actionOpenProject'):
            self.ui.actionOpenProject.triggered.connect(
                self.open_project
            )

        if hasattr(self.ui, 'actionCloseWindow'):
            self.ui.actionCloseWindow.triggered.connect(
                self.action_exit
            )

        if hasattr(self.ui, 'actionExit'):
            self.ui.actionExit.triggered.connect(
                self.action_exit
            )

        if hasattr(self.ui, 'actionClearInformationLog'):
            self.ui.actionClearInformationLog.triggered.connect(
                self.action_clear_information_log
            )

        if hasattr(self.ui, 'actionClearTrace'):
            self.ui.actionClearTrace.triggered.connect(
                self.action_clear_trace
            )

        if hasattr(self.ui, 'actionFlash'):
            self.ui.actionFlash.triggered.connect(
                self.flash_button_clicked
            )

        if hasattr(self.ui, 'actionAbort'):
            self.ui.actionAbort.triggered.connect(
                self.flash_button_clicked
            )

        if hasattr(self.ui, 'menuTools'):
            self.ui.menuTools.aboutToShow.connect(
                self._sync_flash_abort_menu_state
            )
            # Also sync once up front — aboutToShow only fires
            # once the user actually opens the menu, but nothing
            # stops a test (or a screen reader) from checking
            # isEnabled() before that ever happens.
            self._sync_flash_abort_menu_state()

        if hasattr(self.ui, 'actionTestConnection'):
            self.ui.actionTestConnection.triggered.connect(
                self.action_test_connection
            )

        if hasattr(self.ui, 'actionExportReport'):
            self.ui.actionExportReport.triggered.connect(
                self.export_report
            )

        # Live theme state, read by gui/flash_tab.py's status-color
        # helpers so Steps/Segments row highlights always match
        # whichever theme is *currently* applied (not just the one
        # read at startup) — kept in sync by action_toggle_dark_mode()
        # below on every toggle. Default matches
        # is_dark_mode_enabled()'s own default.
        self._dark_mode_active = is_dark_mode_enabled()

        if hasattr(self.ui, 'actionDarkMode'):
            # Reflects the theme main.py already applied at
            # startup (read from the same QSettings key) — set
            # before connecting toggled, so this doesn't itself
            # fire action_toggle_dark_mode() and redundantly
            # re-apply/re-save what's already correct.
            self.ui.actionDarkMode.setChecked(self._dark_mode_active)
            self.ui.actionDarkMode.toggled.connect(
                self.action_toggle_dark_mode
            )

        if hasattr(self.ui, 'actionResizeDefault'):
            self.ui.actionResizeDefault.triggered.connect(
                self.action_resize_default
            )

        if hasattr(self.ui, 'actionResizeMedium'):
            self.ui.actionResizeMedium.triggered.connect(
                self.action_resize_medium
            )

        if hasattr(self.ui, 'actionResizeLarge'):
            self.ui.actionResizeLarge.triggered.connect(
                self.action_resize_large
            )

        if hasattr(self.ui, 'actionMaximizeWindow'):
            self.ui.actionMaximizeWindow.triggered.connect(
                self.action_maximize_window
            )

        if hasattr(self.ui, 'actionFullScreen'):
            self.ui.actionFullScreen.triggered.connect(
                self.action_full_screen
            )

        if hasattr(self.ui, 'actionAbout'):
            # On macOS, Qt's native menu bar auto-relocates any
            # action whose text contains "about" out of its own
            # menu and into the system application menu (shown
            # as "python" here, since running unpackaged via
            # `python main.py` has no proper .app bundle/name) —
            # so "About SFlash" would silently vanish from Help
            # and reappear somewhere a user wouldn't think to
            # look. NoRole opts out, keeping it in Help on every
            # platform.
            self.ui.actionAbout.setMenuRole(
                QAction.MenuRole.NoRole
            )
            self.ui.actionAbout.triggered.connect(
                self.action_about
            )

        if hasattr(self.ui, 'actionOpenGuideline'):
            self.ui.actionOpenGuideline.triggered.connect(
                self.action_open_guideline
            )

        if hasattr(self.ui, 'actionExportIssue'):
            self.ui.actionExportIssue.triggered.connect(
                self.export_issue
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

    def load_recent_file(self, file_path):
        """Reload a single file picked from File > Recent Files."""

        if not self._load_firmware_file(file_path):
            return

        if hasattr(self.ui, 'tabWidget'):
            self.ui.tabWidget.setCurrentIndex(1)
        if hasattr(self.ui, 'navListWidget'):
            self.ui.navListWidget.setCurrentRow(0)

        if hasattr(self, '_update_details_table'):
            self._update_details_table(self._loaded_datablocks[-1])

    def _record_recent_file(self, file_path):
        """
        Add file_path to the front of the persisted Recent Files
        list (deduping/moving an existing entry to the front
        instead of listing it twice), capped at
        MAX_RECENT_FILES, then rebuild the submenu.

        Called by gui/configure_tab.py's _load_firmware_file()
        after every successful load — both from the file-dialog
        path (add_new_datablock()) and from load_recent_file()
        above.
        """

        if not hasattr(self, '_settings'):
            return

        recent = self._settings.value(
            "recentFiles/list", [], type=list
        )
        recent = [p for p in recent if p != file_path]
        recent.insert(0, file_path)
        recent = recent[:MAX_RECENT_FILES]

        self._settings.setValue("recentFiles/list", recent)
        self._settings.sync()

        self._rebuild_recent_files_menu()

    def _rebuild_recent_files_menu(self):

        menu = self.ui.menuRecentFiles
        menu.clear()

        recent = (
            self._settings.value("recentFiles/list", [], type=list)
            if hasattr(self, '_settings') else []
        )

        if not recent:
            placeholder = menu.addAction("(No Recent Files)")
            placeholder.setEnabled(False)
            return

        for file_path in recent:
            action = QAction(os.path.basename(file_path), menu)
            action.setToolTip(file_path)
            action.triggered.connect(
                lambda checked=False, p=file_path:
                    self.load_recent_file(p)
            )
            menu.addAction(action)

        menu.addSeparator()
        if hasattr(self.ui, 'actionClearRecentFiles'):
            menu.addAction(self.ui.actionClearRecentFiles)

    def action_clear_recent_files(self):

        if not hasattr(self, '_settings'):
            return

        self._settings.setValue("recentFiles/list", [])
        self._settings.sync()
        self._rebuild_recent_files_menu()

    # ==================================================
    # Edit
    # ==================================================

    def action_clear_information_log(self):

        self.ui.informationText.clear()

    def action_clear_trace(self):

        self.ui.traceTable.setRowCount(0)

    # ==================================================
    # View
    # ==================================================

    def action_toggle_dark_mode(self, checked):

        self._dark_mode_active = checked

        QApplication.instance().setStyleSheet(
            load_stylesheet(dark=checked)
        )

        # self._settings only exists once setup_settings_profile()
        # has run — guaranteed by the time a user can actually
        # click this menu item, since MainWindow.__init__() fully
        # completes (and calls it) before show() is ever reached.
        if hasattr(self, '_settings'):
            self._settings.setValue("appearance/darkMode", checked)
            self._settings.sync()

    def _resize_window(self, width, height):
        """
        Set an exact window size. showNormal() first if the
        window is currently maximized/full screen — resize()
        while in either of those states is a no-op in Qt (the
        window manager keeps it maximized/full screen), so a
        discrete size from the menu would otherwise silently
        fail to take effect after picking Maximize/Full Screen.
        """

        if self.isMaximized() or self.isFullScreen():
            self.showNormal()

        self.resize(width, height)

    def action_resize_default(self):

        self._resize_window(1100, 850)

    def action_resize_medium(self):

        self._resize_window(1366, 789)

    def action_resize_large(self):

        self._resize_window(1920, 1080)

    def action_maximize_window(self):

        self.showMaximized()

    def action_full_screen(self):

        self.showFullScreen()

    # ==================================================
    # Tools
    # ==================================================

    def _sync_flash_abort_menu_state(self):
        """
        Enable exactly one of Tools > Flash / Abort at a time,
        matching whichever action flashButton itself currently
        represents (same button, same flash_button_clicked()
        toggle — see gui/flash_tab.py). Read-only check against
        self.thread; never touches it, so this can't interact
        with the QThread lifecycle rules documented on
        flash_button_clicked()/on_flash_finished().
        """

        running = (
            self.thread is not None and self.thread.isRunning()
        )

        if hasattr(self.ui, 'actionFlash'):
            self.ui.actionFlash.setEnabled(not running)

        if hasattr(self.ui, 'actionAbort'):
            self.ui.actionAbort.setEnabled(running)

    def action_test_connection(self):

        self.open_test_connection_dialog()

    def open_test_connection_dialog(self):
        """
        Builds CAN config from the current GUI state and runs
        the Test Connection probe in a modal dialog. Shared by
        the Tools > Test Connection... menu action above and
        the Hardware configure page's own Test Connection
        button (gui/configure_tab.py's
        test_connection_button_clicked()), so both go through
        identical setup/warning logic instead of duplicating it.

        Returns the TestConnectionDialog once it closes (its
        .passed is True/False, or None if the dialog was closed
        before the probe finished), or None if a CAN conflict
        warning was declined before the dialog ever opened.
        """

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
                    return None

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

        return dialog

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

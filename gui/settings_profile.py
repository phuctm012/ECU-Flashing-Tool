# ==================================================
# Settings Profile (persisted via QSettings)
# ==================================================
#
# Saves/restores the Hardware channel, Radar Side, Security
# Access DLL path, and Flash Sequence choice across app
# restarts, so the user doesn't have to reconfigure these
# every time the app is opened (docs/gui_todo.md item #7).
#
# A single default profile (no named profiles) is enough for
# the current single-line-at-a-time flashing workflow.
# ==================================================

import os

from PySide6.QtCore import QSettings

from config.settings import APP_AUTHOR, APP_NAME


class SettingsProfileMixin:
    """
    Mixin adding profile persistence to MainWindow. Call
    setup_settings_profile() once, after setup_configure_tab()
    has already populated comboBoxHardware/comboBoxRadarSide/
    comboBoxFlashSequence with their real items — load_profile()
    needs those items in place to select the right one.
    """

    # ==================================================
    # Setup
    # ==================================================

    def setup_settings_profile(self):

        # Explicit IniFormat (not the 2-arg QSettings(org, app)
        # convenience constructor, which resolves to whatever
        # the platform's native store is — Registry on Windows,
        # NSUserDefaults/plist on macOS — regardless of
        # QSettings.setDefaultFormat()/setPath(); those two
        # calls only take effect for a format a QSettings
        # object was actually constructed with). A plain .ini
        # file is also more transparent/portable than the
        # Registry, and lets tests redirect it via
        # QSettings.setPath(IniFormat, ...) — see
        # tests/qt_test_utils.py.
        self._settings = QSettings(
            QSettings.IniFormat, QSettings.UserScope,
            APP_AUTHOR, APP_NAME,
        )

        self.load_profile()

        # Save on every change instead of only on app close —
        # survives a crash/force-quit, and each save is a
        # cheap handful of QSettings.setValue() calls.
        if hasattr(self.ui, 'comboBoxHardware'):
            self.ui.comboBoxHardware.currentIndexChanged.connect(
                lambda _: self.save_profile()
            )

        if hasattr(self.ui, 'comboBoxRadarSide'):
            self.ui.comboBoxRadarSide.currentIndexChanged.connect(
                lambda _: self.save_profile()
            )

        if hasattr(self.ui, 'comboBoxFlashSequence'):
            self.ui.comboBoxFlashSequence.currentIndexChanged.connect(
                lambda _: self.save_profile()
            )

    # ==================================================
    # Save
    # ==================================================

    def save_profile(self):

        s = self._settings

        if hasattr(self.ui, 'comboBoxHardware'):
            # currentData() is None for the Virtual ECU
            # Simulator entry, an int channel index otherwise.
            # Stored as two separate values (rather than the
            # raw None/int) so the round-trip through
            # QSettings' native backends (Windows Registry,
            # macOS plist, .ini) can't turn None into an
            # ambiguous string on read-back.
            channel = self.ui.comboBoxHardware.currentData()
            s.setValue("hardware/isVirtual", channel is None)
            s.setValue(
                "hardware/channel",
                channel if channel is not None else -1
            )

        if hasattr(self.ui, 'comboBoxRadarSide'):
            s.setValue(
                "radarSide/index",
                self.ui.comboBoxRadarSide.currentIndex()
            )

        if hasattr(self.ui, 'comboBoxFlashSequence'):
            s.setValue(
                "flashSequence/index",
                self.ui.comboBoxFlashSequence.currentIndex()
            )

        s.setValue(
            "securityDll/path",
            getattr(self, '_security_dll_path', '') or ''
        )

        # Force an immediate flush to disk/registry rather than
        # relying on Qt's internal deferred sync — save_profile()
        # runs on every change specifically so a crash/force-quit
        # doesn't lose the profile, which only holds if writes
        # are actually durable by the time this returns.
        s.sync()

    # ==================================================
    # Load
    # ==================================================

    def load_profile(self):

        s = self._settings

        if hasattr(self.ui, 'comboBoxHardware'):
            is_virtual = s.value(
                "hardware/isVirtual", True, type=bool
            )
            channel = s.value("hardware/channel", -1, type=int)
            target = None if is_virtual else channel

            combo = self.ui.comboBoxHardware
            for i in range(combo.count()):
                if combo.itemData(i) == target:
                    combo.setCurrentIndex(i)
                    break
            # No matching entry (e.g. saved real channel not
            # plugged in this run) — combo already defaults to
            # "Virtual ECU Simulator" (index 0), so just leave
            # it there rather than erroring out.

        if hasattr(self.ui, 'comboBoxRadarSide'):
            index = s.value("radarSide/index", 0, type=int)
            combo = self.ui.comboBoxRadarSide
            if 0 <= index < combo.count():
                combo.setCurrentIndex(index)

        if hasattr(self.ui, 'comboBoxFlashSequence'):
            index = s.value("flashSequence/index", 0, type=int)
            combo = self.ui.comboBoxFlashSequence
            if 0 <= index < combo.count():
                combo.setCurrentIndex(index)

        if hasattr(self.ui, 'lineEditSecurityDll'):
            path = s.value("securityDll/path", "", type=str)
            if path and os.path.isfile(path):
                self._security_dll_path = path
                self.ui.lineEditSecurityDll.setText(path)
            # Saved path missing/moved (different machine, or
            # deleted) — silently leave the field at its
            # built-in-algorithm default rather than pointing
            # at a DLL that no longer exists.

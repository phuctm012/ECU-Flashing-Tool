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

        if hasattr(self.ui, 'lineEditCompressionMethod'):
            self.ui.lineEditCompressionMethod.textEdited.connect(
                lambda _: self.save_profile()
            )

        if hasattr(self.ui, 'lineEditEncryptionMethod'):
            self.ui.lineEditEncryptionMethod.textEdited.connect(
                lambda _: self.save_profile()
            )

        if hasattr(self.ui, 'lineEditTesterSerialNumber'):
            self.ui.lineEditTesterSerialNumber.textEdited.connect(
                lambda _: self.save_profile()
            )

        if hasattr(self.ui, 'actionModeBatchFlash'):
            self.ui.actionModeBatchFlash.toggled.connect(
                lambda _: self.save_profile()
            )

    # ==================================================
    # Save
    # ==================================================

    def save_profile(self):

        s = self._settings

        if hasattr(self.ui, 'comboBoxHardware'):
            # currentData() is None for the Virtual ECU
            # Simulator entry, otherwise the full channel dict
            # from detect_vector_channels() (keys: channel,
            # hw_channel, serial, is_on_bus, label). Only the
            # identifying fields (hw_channel + serial) are
            # persisted, as plain ints, so the round-trip
            # through QSettings' native backends (Windows
            # Registry, macOS plist, .ini) can't turn None into
            # an ambiguous string on read-back, and so a stored
            # value never depends on QSettings being able to
            # serialize a whole dict.
            data = self.ui.comboBoxHardware.currentData()
            s.setValue("hardware/isVirtual", data is None)
            s.setValue(
                "hardware/channel",
                data.get("hw_channel", data.get("channel", -1))
                if data is not None else -1
            )
            s.setValue(
                "hardware/serial",
                (data.get("serial") or -1) if data is not None else -1
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

        if hasattr(self.ui, 'lineEditCompressionMethod'):
            text = self.ui.lineEditCompressionMethod.text().strip()
            s.setValue(
                "dataFormat/compression",
                int(text, 16) if text else 0
            )

        if hasattr(self.ui, 'lineEditEncryptionMethod'):
            text = self.ui.lineEditEncryptionMethod.text().strip()
            s.setValue(
                "dataFormat/encrypting",
                int(text, 16) if text else 0
            )

        if hasattr(self.ui, 'lineEditTesterSerialNumber'):
            s.setValue(
                "fingerprint/testerSerialNumber",
                self.ui.lineEditTesterSerialNumber.text().strip()
            )

        if hasattr(self.ui, 'actionModeBatchFlash'):
            s.setValue(
                "flash/mode",
                "batch"
                if self.ui.actionModeBatchFlash.isChecked()
                else "flash",
            )

        for i, panel in enumerate(
            getattr(self, '_parallel_panels', []), start=1
        ):
            # comm_settings (Basic Communication overrides from
            # gui/parallel_channel_settings_dialog.py) is independent
            # of channel selection — a panel can be customized while
            # still "Not Selected" — so this is saved before the
            # selected/not-selected branch below, not nested inside it.
            comm = panel["comm_settings"]
            s.setValue(f"parallel/panel{i}/commCustomized", comm is not None)
            if comm is not None:
                s.setValue(f"parallel/panel{i}/commTxId", comm["tx_id"])
                s.setValue(f"parallel/panel{i}/commRxId", comm["rx_id"])
                s.setValue(
                    f"parallel/panel{i}/commFunctionalId",
                    comm["functional_id"],
                )

            data = panel["combo"].currentData()
            if data == "not-selected":
                s.setValue(f"parallel/panel{i}/selected", False)
                continue
            s.setValue(f"parallel/panel{i}/selected", True)
            s.setValue(f"parallel/panel{i}/isVirtual", data is None)
            s.setValue(
                f"parallel/panel{i}/channel",
                data.get("hw_channel", data.get("channel", -1))
                if data is not None else -1
            )
            s.setValue(
                f"parallel/panel{i}/serial",
                (data.get("serial") or -1) if data is not None else -1
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
            serial = s.value("hardware/serial", -1, type=int)
            target = None if is_virtual else (channel, serial)

            combo = self.ui.comboBoxHardware
            for i in range(combo.count()):
                data = combo.itemData(i)
                if data is None:
                    key = None
                else:
                    key = (
                        data.get("hw_channel", data.get("channel")),
                        data.get("serial") or -1,
                    )
                if key == target:
                    combo.setCurrentIndex(i)
                    break
            # No matching entry (e.g. saved real channel not
            # plugged in this run) — combo already defaults to
            # "Virtual ECU Simulator" (index 0), so just leave
            # it there rather than erroring out.

        for i, panel in enumerate(
            getattr(self, '_parallel_panels', []), start=1
        ):
            # See save_profile()'s matching comment — comm_settings
            # is independent of channel selection, so restored
            # before the selected/not-selected branch below.
            comm_customized = s.value(
                f"parallel/panel{i}/commCustomized", False, type=bool
            )
            if comm_customized:
                panel["comm_settings"] = {
                    "tx_id": s.value(
                        f"parallel/panel{i}/commTxId", 0x778, type=int
                    ),
                    "rx_id": s.value(
                        f"parallel/panel{i}/commRxId", 0x788, type=int
                    ),
                    "functional_id": s.value(
                        f"parallel/panel{i}/commFunctionalId",
                        0x700, type=int,
                    ),
                }

            selected = s.value(f"parallel/panel{i}/selected", False, type=bool)
            if not selected:
                continue
            is_virtual = s.value(
                f"parallel/panel{i}/isVirtual", True, type=bool
            )
            channel = s.value(f"parallel/panel{i}/channel", -1, type=int)
            serial = s.value(f"parallel/panel{i}/serial", -1, type=int)
            target = None if is_virtual else (channel, serial)

            combo = panel["combo"]
            for idx in range(combo.count()):
                item_data = combo.itemData(idx)
                if item_data == "not-selected":
                    continue
                key = (
                    None if item_data is None
                    else (
                        item_data.get(
                            "hw_channel", item_data.get("channel")
                        ),
                        item_data.get("serial") or -1,
                    )
                )
                if key == target:
                    combo.setCurrentIndex(idx)
                    break
            # No matching entry — combo stays on "Not Selected"
            # (index 0), same reasoning as comboBoxHardware above.

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

        if hasattr(self.ui, 'lineEditCompressionMethod'):
            value = s.value("dataFormat/compression", 0, type=int)
            if 0 <= value <= 15:
                self.ui.lineEditCompressionMethod.setText(
                    f"{value:X}"
                )

        if hasattr(self.ui, 'lineEditEncryptionMethod'):
            value = s.value("dataFormat/encrypting", 0, type=int)
            if 0 <= value <= 15:
                self.ui.lineEditEncryptionMethod.setText(
                    f"{value:X}"
                )

        if hasattr(self.ui, 'lineEditTesterSerialNumber'):
            text = s.value(
                "fingerprint/testerSerialNumber", "", type=str
            )
            # Only restore a valid, full-length hex string —
            # anything else (missing key, corrupted .ini) just
            # leaves the .ui-declared default in place rather
            # than showing a blank/broken field.
            if len(text) == 20 and all(
                c in "0123456789ABCDEFabcdef" for c in text
            ):
                self.ui.lineEditTesterSerialNumber.setText(
                    text.upper()
                )

        if hasattr(self.ui, 'actionModeBatchFlash'):
            mode = s.value("flash/mode", "flash", type=str)
            self.ui.actionModeBatchFlash.setChecked(mode == "batch")

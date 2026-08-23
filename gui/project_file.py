# ==================================================
# Save / Load Project (.ffproj)
# ==================================================
#
# A named, user-initiated snapshot of a flashing session —
# which firmware files are loaded (and ticked/unticked), plus
# the Hardware/Radar Side/Logical Link/Security DLL/Flash
# Sequence configuration — saved to a plain JSON file with a
# .ffproj extension so it can be reopened exactly as left, or
# handed to someone else (docs/gui_todo.md item #20).
#
# Distinct from gui/settings_profile.py's single auto-saved
# QSettings profile, which silently remembers only the
# last-used configuration for next launch and never touches
# loaded firmware — Project is explicit, named, and portable.
# ==================================================

import json
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox

PROJECT_FILE_FILTER = "FFlash Project (*.ffproj);;All Files (*)"
PROJECT_FORMAT_VERSION = 1


class ProjectFileMixin:
    """
    Mixin adding save_project_as()/open_project() — wired to
    File > Save Project As.../Open Project... in
    gui/menu_bar.py. Reuses gui/configure_tab.py's
    _load_firmware_file() (the same per-file loader shared with
    File > Recent Files) so a project's firmware list goes
    through identical parsing/error handling as every other
    load path.
    """

    # ==================================================
    # Save
    # ==================================================

    def save_project_as(self):

        path, _ = QFileDialog.getSaveFileName(
            self, "Save Project", "", PROJECT_FILE_FILTER
        )

        if not path:
            return

        if not path.lower().endswith(".ffproj"):
            path += ".ffproj"

        data = self._build_project_data()

        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except OSError as e:
            QMessageBox.critical(
                self, "Save Failed",
                f"Could not save project:\n{e}",
            )
            return

        self.log_information(f"Project saved: {path}")

    def _build_project_data(self):

        table = self.ui.tableWidgetDatablocks
        datablocks = getattr(self, '_loaded_datablocks', [])

        firmware_files = []
        for i, datablock in enumerate(datablocks):
            check_item = table.item(i, 0)
            checked = (
                check_item is None
                or check_item.checkState() == Qt.Checked
            )
            firmware_files.append({
                "path": datablock.file_path,
                "checked": checked,
            })

        hardware = {"is_virtual": True, "channel": -1}
        if hasattr(self.ui, 'comboBoxHardware'):
            channel = self.ui.comboBoxHardware.currentData()
            hardware = {
                "is_virtual": channel is None,
                "channel": channel if channel is not None else -1,
            }

        return {
            "format_version": PROJECT_FORMAT_VERSION,
            "firmware_files": firmware_files,
            "hardware": hardware,
            "radar_side_index": (
                self.ui.comboBoxRadarSide.currentIndex()
                if hasattr(self.ui, 'comboBoxRadarSide') else 0
            ),
            "logical_link_index": (
                self.ui.comboBoxLogicalLink.currentIndex()
                if hasattr(self.ui, 'comboBoxLogicalLink') else 0
            ),
            "security_dll_path": getattr(
                self, '_security_dll_path', ''
            ) or "",
            "flash_sequence_index": (
                self.ui.comboBoxFlashSequence.currentIndex()
                if hasattr(self.ui, 'comboBoxFlashSequence') else 0
            ),
        }

    # ==================================================
    # Open
    # ==================================================

    def open_project(self):

        path, _ = QFileDialog.getOpenFileName(
            self, "Open Project", "", PROJECT_FILE_FILTER
        )

        if not path:
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            QMessageBox.critical(
                self, "Open Failed",
                f"Could not read project file:\n{e}",
            )
            return

        self._apply_project_data(data)
        self.log_information(f"Project loaded: {path}")

    def _apply_project_data(self, data):

        # Replace whatever's currently loaded — Open Project
        # restores a full session, it doesn't merge into one.
        self._loaded_datablocks = []
        self.ui.tableWidgetDatablocks.setRowCount(0)
        self._add_placeholder_row()

        for entry in data.get("firmware_files", []):
            file_path = entry.get("path")
            if not file_path:
                continue
            if not self._load_firmware_file(file_path):
                continue
            if not entry.get("checked", True):
                row = len(self._loaded_datablocks) - 1
                check_item = self.ui.tableWidgetDatablocks.item(
                    row, 0
                )
                if check_item is not None:
                    check_item.setCheckState(Qt.Unchecked)

        if self._loaded_datablocks:
            self._update_details_table(
                self._loaded_datablocks[-1]
            )

        hardware = data.get("hardware", {})
        if hasattr(self.ui, 'comboBoxHardware'):
            is_virtual = hardware.get("is_virtual", True)
            channel = hardware.get("channel", -1)
            target = None if is_virtual else channel
            combo = self.ui.comboBoxHardware
            for i in range(combo.count()):
                if combo.itemData(i) == target:
                    combo.setCurrentIndex(i)
                    break
            # No matching entry (e.g. saved real channel not
            # plugged in this run) — combo already defaults to
            # "Virtual ECU Simulator" (index 0), same fallback
            # as gui/settings_profile.py's load_profile().

        if hasattr(self.ui, 'comboBoxRadarSide'):
            index = data.get("radar_side_index", 0)
            combo = self.ui.comboBoxRadarSide
            if 0 <= index < combo.count():
                combo.setCurrentIndex(index)

        if hasattr(self.ui, 'comboBoxLogicalLink'):
            index = data.get("logical_link_index", 0)
            combo = self.ui.comboBoxLogicalLink
            if 0 <= index < combo.count():
                combo.setCurrentIndex(index)

        if hasattr(self.ui, 'lineEditSecurityDll'):
            dll_path = data.get("security_dll_path", "")
            if dll_path and os.path.isfile(dll_path):
                self._security_dll_path = dll_path
                self.ui.lineEditSecurityDll.setText(dll_path)
            # Saved path missing/moved — silently leave the
            # field at its default, same as load_profile().

        if hasattr(self.ui, 'comboBoxFlashSequence'):
            index = data.get("flash_sequence_index", 0)
            combo = self.ui.comboBoxFlashSequence
            if 0 <= index < combo.count():
                combo.setCurrentIndex(index)

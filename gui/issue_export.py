# ==================================================
# Export Issue (plain text, for reporting a bug/debug help)
# ==================================================
#
# export_issue() — Help > Export Issue... — bundles just what's
# useful to debug a flashing problem into a single plain .txt
# file: environment, current CAN/hardware configuration, loaded
# firmware, the Information log, and the Trace (CAN frames) —
# meant to be attached when asking for help. A checkbox in the
# confirmation prompt optionally also bundles the loaded
# firmware file(s) themselves into a .zip alongside issue.txt —
# for bugs suspected to be firmware-parsing-related (wrong
# record type, checksum, segment boundaries), where the
# metadata-only .txt isn't enough to reproduce the problem
# without the actual file. Opt-in and off by default: firmware
# can be sensitive/proprietary, so it's never bundled silently.
#
# Deliberately narrower than Tools > Export Report... (HTML,
# gui/report_export.py), which is a "proof this flash went
# correctly" record for keeping. This is a debugging aid, so it
# skips content that's redundant for that purpose:
#   - stepsTable — the Information log already narrates the same
#     steps (each one is logged via information_message when it
#     runs), usually with more detail per step (e.g. exact DID/
#     byte counts) than the Steps table's short description text.
#   - tableWidgetCustomConfig — not yet wired to actual flashing
#     behavior (docs/gui_todo.md item #3), so including it would
#     misleadingly imply it affects what happened.
#
# Follows the same pure-write/dialog-wrapper split as
# gui/report_export.py and gui/main_window.py's Save Log.
# ==================================================

import os
import platform
import zipfile
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QFileDialog, QMessageBox

from config.settings import APP_NAME, APP_VERSION

SEPARATOR = "=" * 60


class IssueExportMixin:
    """Mixin adding export_issue() — wired to Help > Export
    Issue... in gui/menu_bar.py, no setup of its own needed."""

    # ==================================================
    # Export (dialog-opening wrapper)
    # ==================================================

    def export_issue(self):

        include_firmware = self._ask_include_firmware()
        if include_firmware is None:
            return  # Cancel

        if include_firmware:
            default_name = (
                "sflash_issue_"
                + datetime.now().strftime("%Y%m%d_%H%M%S")
                + ".zip"
            )
            file_filter = "Zip Files (*.zip);;All Files (*)"
        else:
            default_name = (
                "sflash_issue_"
                + datetime.now().strftime("%Y%m%d_%H%M%S")
                + ".txt"
            )
            file_filter = "Text Files (*.txt);;All Files (*)"

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export Issue", default_name, file_filter,
        )

        if not file_path:
            return

        if include_firmware:
            self._write_issue_zip(file_path)
        else:
            self._write_issue_file(file_path)

    def _ask_include_firmware(self):
        """
        Returns True/False for the "Include firmware files"
        checkbox, or None if the user cancelled. Off by default
        each time — firmware can be sensitive, so bundling it
        is always a deliberate choice, never a leftover setting
        from a previous export.
        """

        checkbox = QCheckBox("Include loaded firmware file(s) (.zip)")

        box = QMessageBox(self)
        box.setWindowTitle("Export Issue")
        box.setText(
            "Export debugging info for this session "
            "(environment, configuration, log, trace)."
        )
        box.setCheckBox(checkbox)
        box.setStandardButtons(
            QMessageBox.Ok | QMessageBox.Cancel
        )
        box.setDefaultButton(QMessageBox.Ok)

        if box.exec() != QMessageBox.Ok:
            return None

        return checkbox.isChecked()

    # ==================================================
    # Write (pure — no dialogs, easy to unit test)
    # ==================================================

    def _write_issue_file(self, file_path):

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(self._build_issue_text())

        except OSError as e:
            QMessageBox.critical(
                self, "Export Issue Failed", str(e)
            )
            return

        self.log_information(
            f"Issue exported to {file_path}"
        )

    def _write_issue_zip(self, file_path):

        if not file_path.lower().endswith(".zip"):
            file_path += ".zip"

        try:
            with zipfile.ZipFile(
                file_path, "w", zipfile.ZIP_DEFLATED
            ) as zf:

                zf.writestr("issue.txt", self._build_issue_text())

                seen_names = set()
                for datablock in getattr(
                    self, '_loaded_datablocks', []
                ):
                    src = datablock.file_path
                    if not os.path.isfile(src):
                        continue  # moved/deleted since loading

                    name = self._unique_zip_name(
                        datablock.file_name, seen_names
                    )
                    seen_names.add(name)
                    zf.write(src, arcname=name)

        except OSError as e:
            QMessageBox.critical(
                self, "Export Issue Failed", str(e)
            )
            return

        self.log_information(
            f"Issue (with firmware) exported to {file_path}"
        )

    @staticmethod
    def _unique_zip_name(name, seen_names):
        """
        Disambiguate 2 loaded datablocks that happen to share a
        file name (e.g. loaded from 2 different folders) so the
        zip doesn't end up with 2 entries silently overwriting
        each other on extract.
        """

        if name not in seen_names:
            return name

        base, ext = os.path.splitext(name)
        i = 2
        while f"{base}_{i}{ext}" in seen_names:
            i += 1
        return f"{base}_{i}{ext}"

    def _build_issue_text(self):

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        sections = [
            f"{SEPARATOR}\n"
            f"{APP_NAME} v{APP_VERSION} — Issue Export\n"
            f"Exported: {now}\n"
            f"{SEPARATOR}\n",
            self._issue_environment_section(),
            self._issue_configuration_section(),
            self._issue_can_details_section(),
            self._issue_datablocks_section(),
            self._issue_information_log_section(),
            self._issue_trace_section(),
        ]

        return "\n".join(sections)

    # --------------------------------------------------
    # Section builders
    # --------------------------------------------------

    def _issue_environment_section(self):

        lines = [
            "--- Environment ---",
            f"OS: {platform.platform()}",
            f"Python: {platform.python_version()}",
        ]
        return "\n".join(lines) + "\n"

    def _issue_configuration_section(self):

        hardware = "N/A"
        if hasattr(self.ui, 'comboBoxHardware'):
            hardware = self.ui.comboBoxHardware.currentText()

        radar_side = "N/A"
        if hasattr(self.ui, 'comboBoxRadarSide'):
            radar_side = self.ui.comboBoxRadarSide.currentText()

        logical_link = "N/A"
        if hasattr(self.ui, 'comboBoxLogicalLink'):
            logical_link = self.ui.comboBoxLogicalLink.currentText()

        sequence = "N/A"
        if hasattr(self.ui, 'comboBoxFlashSequence'):
            sequence = self.ui.comboBoxFlashSequence.currentText()

        security_dll = "(built-in algorithm)"
        if hasattr(self.ui, 'lineEditSecurityDll'):
            security_dll = (
                self.ui.lineEditSecurityDll.text()
                or "(built-in algorithm)"
            )

        result = "N/A"
        if hasattr(self.ui, 'statsLabel'):
            result = self.ui.statsLabel.text()

        lines = [
            "--- Configuration ---",
            f"Hardware: {hardware}",
            f"Radar Side: {radar_side}",
            f"Logical Link: {logical_link}",
            f"Flash Sequence: {sequence}",
            f"Security Access DLL: {security_dll}",
            f"Result: {result}",
        ]
        return "\n".join(lines) + "\n"

    def _issue_can_details_section(self):

        lines = ["--- CAN Communication Details ---"]

        if not hasattr(self.ui, 'tableWidgetCommDetails'):
            lines.append("N/A")
            return "\n".join(lines) + "\n"

        table = self.ui.tableWidgetCommDetails
        for row in range(table.rowCount()):
            prop_item = table.item(row, 0)
            if prop_item is None:
                continue
            val_item = table.item(row, 1)
            lines.append(
                f"{prop_item.text()}: "
                f"{val_item.text() if val_item else ''}"
            )

        if len(lines) == 1:
            lines.append("N/A")

        return "\n".join(lines) + "\n"

    def _issue_datablocks_section(self):

        lines = ["--- Loaded Datablocks ---"]

        if not hasattr(self.ui, 'tableWidgetDatablocks'):
            lines.append("N/A")
            return "\n".join(lines) + "\n"

        table = self.ui.tableWidgetDatablocks
        for row in range(table.rowCount()):

            type_item = table.item(row, 1)
            if type_item is None:
                continue  # trailing "add a Datablock" placeholder row

            checkbox_item = table.item(row, 0)
            checked = (
                checkbox_item is not None
                and checkbox_item.checkState() == Qt.Checked
            )

            name = (
                table.item(row, 2).text() if table.item(row, 2) else ""
            )
            checksum = (
                table.item(row, 3).text() if table.item(row, 3) else ""
            )

            status = "Included" if checked else "Excluded (unticked)"
            lines.append(
                f"[{status}] {type_item.text()}  {name}  "
                f"Checksum={checksum}"
            )

        if len(lines) == 1:
            lines.append("No firmware file loaded.")

        return "\n".join(lines) + "\n"

    def _issue_information_log_section(self):

        text = self.ui.informationText.toPlainText() or "(empty)"
        return "--- Information Log ---\n" + text + "\n"

    def _issue_trace_section(self):

        table = self.ui.traceTable
        headers = [
            table.horizontalHeaderItem(col).text()
            for col in range(table.columnCount())
        ]

        lines = [
            "--- Trace (CAN Frames) ---",
            " | ".join(headers),
        ]

        for row in range(table.rowCount()):
            cells = [
                table.item(row, col).text()
                if table.item(row, col) else ""
                for col in range(table.columnCount())
            ]
            lines.append(" | ".join(cells))

        if table.rowCount() == 0:
            lines.append("No trace recorded.")

        return "\n".join(lines) + "\n"

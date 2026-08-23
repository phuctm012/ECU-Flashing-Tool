# ==================================================
# Flash Session Report Export (HTML)
# ==================================================
#
# export_report() — reachable from the menu bar (Tools >
# Export Report..., see gui/menu_bar.py) — snapshots whatever
# is currently on screen (Datablocks, Steps, Trace, Information
# log, current Configure selections) into a single self-
# contained HTML file, for keeping as evidence a flash was done
# correctly (docs/gui_todo.md item #8). Can be exported at any
# time, not just right after a flash finishes. Used to also
# have its own "Export Report..." button on the Flash tab, but
# that was removed once the same action was reachable from the
# menu bar — no need for both.
#
# Follows the same split as the existing Save Log/Save Trace
# CSV export (gui/main_window.py): a pure _write_report_file()
# that only touches widgets/the filesystem (testable without a
# real QFileDialog) plus a thin dialog-opening wrapper.
# ==================================================

import html
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox

from config.settings import APP_NAME, APP_VERSION


class ReportExportMixin:
    """Mixin adding export_report() — wired to Tools > Export
    Report... in gui/menu_bar.py, no setup of its own needed."""

    # ==================================================
    # Export (dialog-opening wrapper)
    # ==================================================

    def export_report(self):

        default_name = (
            "flash_report_"
            + datetime.now().strftime("%Y%m%d_%H%M%S")
            + ".html"
        )

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Flash Report",
            default_name,
            "HTML Files (*.html);;All Files (*)",
        )

        if not file_path:
            return

        self._write_report_file(file_path)

    # ==================================================
    # Write (pure — no dialogs, easy to unit test)
    # ==================================================

    def _write_report_file(self, file_path):

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(self._build_report_html())

        except OSError as e:
            QMessageBox.critical(
                self, "Export Report Failed", str(e)
            )
            return

        self.log_information(
            f"Report exported to {file_path}"
        )

    def _build_report_html(self):

        e = html.escape
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{e(APP_NAME)} Flash Report — {e(now)}</title>
<style>
  body {{ font-family: Segoe UI, Arial, sans-serif; margin: 24px; color: #1a1a1a; }}
  h1 {{ font-size: 20px; margin-bottom: 0; }}
  .subtitle {{ color: #666; margin-top: 4px; margin-bottom: 24px; }}
  h2 {{ font-size: 15px; background: #E0E0E0; padding: 6px 8px; margin-top: 28px; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 8px; }}
  th, td {{ border: 1px solid #ccc; padding: 4px 8px; text-align: left; font-size: 13px; }}
  th {{ background: #f2f2f2; }}
  pre {{ background: #f7f7f7; border: 1px solid #ddd; padding: 10px; \
white-space: pre-wrap; font-size: 12px; }}
  .summary td:first-child {{ font-weight: bold; width: 220px; }}
</style>
</head>
<body>
<h1>{e(APP_NAME)} v{e(APP_VERSION)} — Flash Session Report</h1>
<div class="subtitle">Exported {e(now)}</div>

<h2>Summary</h2>
{self._report_summary_table()}

<h2>Datablocks</h2>
{self._report_datablocks_table()}

<h2>Steps</h2>
{self._report_steps_table()}

<h2>Trace</h2>
{self._report_trace_table()}

<h2>Information Log</h2>
<pre>{e(self.ui.informationText.toPlainText())}</pre>

</body>
</html>
"""

    # --------------------------------------------------
    # Section builders
    # --------------------------------------------------

    def _report_summary_table(self):

        e = html.escape

        hardware = "N/A"
        if hasattr(self.ui, 'comboBoxHardware'):
            hardware = self.ui.comboBoxHardware.currentText()

        radar_side = "N/A"
        if hasattr(self.ui, 'comboBoxRadarSide'):
            radar_side = self.ui.comboBoxRadarSide.currentText()

        sequence = "N/A"
        if hasattr(self.ui, 'comboBoxFlashSequence'):
            sequence = self.ui.comboBoxFlashSequence.currentText()

        security_dll = "Built-in algorithm"
        if hasattr(self.ui, 'lineEditSecurityDll'):
            security_dll = (
                self.ui.lineEditSecurityDll.text()
                or "Built-in algorithm"
            )

        result = "N/A"
        if hasattr(self.ui, 'statsLabel'):
            result = self.ui.statsLabel.text()

        rows = [
            ("Hardware", hardware),
            ("Radar Side", radar_side),
            ("Flash Sequence", sequence),
            ("Security Access DLL", security_dll),
            ("Result", result),
        ]

        body = "".join(
            f"<tr><td>{e(k)}</td><td>{e(v)}</td></tr>"
            for k, v in rows
        )

        return f'<table class="summary">{body}</table>'

    def _report_datablocks_table(self):

        e = html.escape

        if not hasattr(self.ui, 'tableWidgetDatablocks'):
            return "<p>N/A</p>"

        table = self.ui.tableWidgetDatablocks
        rows_html = []

        for row in range(table.rowCount()):

            type_item = table.item(row, 1)
            if type_item is None:
                continue  # the trailing "add a Datablock" placeholder row

            checkbox_item = table.item(row, 0)
            checked = (
                checkbox_item is not None
                and checkbox_item.checkState() == Qt.Checked
            )

            cells = [
                "Included" if checked else "Excluded (unticked)",
                type_item.text(),
                table.item(row, 2).text() if table.item(row, 2) else "",
                table.item(row, 3).text() if table.item(row, 3) else "",
            ]
            rows_html.append(
                "<tr>" + "".join(f"<td>{e(c)}</td>" for c in cells)
                + "</tr>"
            )

        if not rows_html:
            return "<p>No firmware file loaded.</p>"

        header = (
            "<tr><th>Status</th><th>Type</th>"
            "<th>Datablock</th><th>Checksum</th></tr>"
        )
        return f"<table>{header}{''.join(rows_html)}</table>"

    def _report_steps_table(self):

        e = html.escape
        table = self.ui.stepsTable
        rows_html = []

        for row in range(table.rowCount()):
            ts_item = table.item(row, 0)
            desc_item = table.item(row, 1)
            if desc_item is None:
                continue  # the "No steps recorded yet." placeholder row
            color = (
                desc_item.background().color().name()
                if desc_item else "#ffffff"
            )
            rows_html.append(
                f'<tr style="background:{color}">'
                f"<td>{e(ts_item.text() if ts_item else '')}</td>"
                f"<td>{e(desc_item.text() if desc_item else '')}</td>"
                "</tr>"
            )

        if not rows_html:
            return "<p>No steps recorded.</p>"

        header = "<tr><th>Timestamp</th><th>Description</th></tr>"
        return f"<table>{header}{''.join(rows_html)}</table>"

    def _report_trace_table(self):

        e = html.escape
        table = self.ui.traceTable

        headers = [
            table.horizontalHeaderItem(col).text()
            for col in range(table.columnCount())
        ]

        rows_html = []
        for row in range(table.rowCount()):
            cells = [
                table.item(row, col).text()
                if table.item(row, col) else ""
                for col in range(table.columnCount())
            ]
            rows_html.append(
                "<tr>" + "".join(f"<td>{e(c)}</td>" for c in cells)
                + "</tr>"
            )

        if not rows_html:
            return "<p>No trace recorded.</p>"

        header = (
            "<tr>"
            + "".join(f"<th>{e(h)}</th>" for h in headers)
            + "</tr>"
        )
        return f"<table>{header}{''.join(rows_html)}</table>"

# ==================================================
# Main Window
# ==================================================
#
# Central MainWindow class that composes all tab
# functionality via mixins.
# ==================================================

import csv
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QMainWindow,
    QLabel,
    QMenu,
    QFileDialog,
    QMessageBox,
    QTableWidgetItem,
    QHeaderView,
)

from ui_main_window import Ui_MainWindow
from gui.flash_tab import FlashTabMixin
from gui.configure_tab import ConfigureTabMixin
from config.settings import (
    APP_NAME,
    APP_AUTHOR,
    APP_AUTHOR_NAME,
)


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
        # Status Bar (author credit, bottom-right)
        # ==========================================

        author_label = QLabel(
            f"Author: {APP_AUTHOR} ({APP_AUTHOR_NAME})"
        )
        author_label.setStyleSheet("color: gray;")
        self.ui.statusbar.addPermanentWidget(author_label)

        # ==========================================
        # Initialize Tabs
        # ==========================================

        self.setup_flash_tab()
        self.setup_configure_tab()

        # ==========================================
        # Logs
        # ==========================================

        self.ui.informationText.clear()
        self.ui.traceTable.setRowCount(0)

        header = self.ui.traceTable.horizontalHeader()
        for col in (0, 1, 3, 4):
            header.setSectionResizeMode(
                col, QHeaderView.ResizeMode.ResizeToContents
            )
        for col in (2, 5):
            header.setSectionResizeMode(
                col, QHeaderView.ResizeMode.Stretch
            )

        self.setup_log_context_menu(
            self.ui.informationText, "information_log.txt"
        )
        self.setup_trace_table_context_menu()

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
    #
    # Two entry points feed the same traceTable:
    # - log_trace(message): narrative-only text (step
    #   execution, errors...) — shown as a "SYSTEM" row.
    # - log_trace_row(row): a structured UDS request/
    #   response pair from FlashWorker.trace_row, laid
    #   out like a real CAN trace tool export.
    # ==================================================

    def log_trace(self, message):

        timestamp = datetime.now().strftime(
            "%H:%M:%S.%f"
        )[:-3]

        self._add_trace_row(
            timestamp, "SYSTEM", message, "", "", ""
        )

    def log_trace_row(self, row):

        req_ts = (
            f"{row['req_ts']:.5f}s"
            if row.get("req_ts") is not None else ""
        )
        resp_ts = (
            f"{row['resp_ts']:.5f}s"
            if row.get("resp_ts") is not None else ""
        )

        self._add_trace_row(
            req_ts,
            row.get("req_target") or "",
            row.get("req_data") or "",
            resp_ts,
            row.get("resp_source") or "",
            row.get("resp_data") or "",
        )

    def _add_trace_row(
        self,
        req_ts, req_target, req_data,
        resp_ts, resp_source, resp_data,
    ):

        table = self.ui.traceTable
        row = table.rowCount()
        table.insertRow(row)

        for col, text in enumerate([
            req_ts, req_target, req_data,
            resp_ts, resp_source, resp_data,
        ]):
            table.setItem(row, col, QTableWidgetItem(text))

        table.scrollToBottom()

    # ==================================================
    # Information Log Context Menu (right-click -> Save Log...)
    # ==================================================

    def setup_log_context_menu(self, text_edit, default_filename):

        text_edit.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )

        text_edit.customContextMenuRequested.connect(
            lambda pos: self._show_log_context_menu(
                text_edit, pos, default_filename
            )
        )

    def _show_log_context_menu(
        self, text_edit, pos, default_filename
    ):

        menu = text_edit.createStandardContextMenu()
        menu.addSeparator()

        save_action = menu.addAction("Save Log...")
        save_action.triggered.connect(
            lambda: self._save_log_to_file(
                text_edit, default_filename
            )
        )

        menu.exec(text_edit.mapToGlobal(pos))

    def _save_log_to_file(self, text_edit, default_filename):

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Log",
            default_filename,
            "Text Files (*.txt);;All Files (*)",
        )

        if not file_path:
            return

        self._write_log_file(text_edit, file_path)

    def _write_log_file(self, text_edit, file_path):

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(text_edit.toPlainText())

        except OSError as e:
            QMessageBox.critical(
                self, "Save Log Failed", str(e)
            )
            return

        self.log_information(
            f"Log saved to {file_path}"
        )

    # ==================================================
    # Trace Table Context Menu (right-click -> Save Log (CSV)...)
    # ==================================================

    def setup_trace_table_context_menu(self):

        table = self.ui.traceTable

        table.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )

        table.customContextMenuRequested.connect(
            self._show_trace_table_context_menu
        )

    def _show_trace_table_context_menu(self, pos):

        menu = QMenu(self)

        save_action = menu.addAction("Save Log (CSV)...")
        save_action.triggered.connect(
            self._save_trace_table_to_csv
        )

        menu.exec(
            self.ui.traceTable.mapToGlobal(pos)
        )

    def _save_trace_table_to_csv(self):

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Trace Log",
            "trace_log.csv",
            "CSV Files (*.csv);;All Files (*)",
        )

        if not file_path:
            return

        self._write_trace_table_csv(file_path)

    def _write_trace_table_csv(self, file_path):

        table = self.ui.traceTable

        headers = [
            table.horizontalHeaderItem(col).text()
            for col in range(table.columnCount())
        ]

        try:
            with open(
                file_path, "w", newline="", encoding="utf-8"
            ) as f:

                writer = csv.writer(f)
                writer.writerow(headers)

                for row in range(table.rowCount()):
                    writer.writerow([
                        table.item(row, col).text()
                        if table.item(row, col) else ""
                        for col in range(table.columnCount())
                    ])

        except OSError as e:
            QMessageBox.critical(
                self, "Save Log Failed", str(e)
            )
            return

        self.log_information(
            f"Trace log saved to {file_path}"
        )

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

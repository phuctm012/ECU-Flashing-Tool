# ==================================================
# Flash Tab Logic
# ==================================================
#
# Handles all UI logic for the Flash tab:
# - Steps table
# - Segments table
# - Progress bar & stats
# - Flash/Abort button
# ==================================================

import time
from datetime import datetime

from PySide6.QtWidgets import QTableWidgetItem, QMessageBox
from PySide6.QtGui import QColor
from PySide6.QtCore import QThread, Qt, QPropertyAnimation, QEasingCurve

from core.flash_controller import FlashWorker
from core.flash_sequence import (
    build_flash_sequence,
    build_suzuki_slp1_flash_sequence,
    DEFAULT_FLASH_SEQUENCE,
)
from config.settings import (
    STATUS_COLOR_RUNNING,
    STATUS_COLOR_DONE,
    STATUS_COLOR_ERROR,
    STATUS_TEXT_COLOR,
    STATUS_COLOR_RUNNING_DARK,
    STATUS_COLOR_DONE_DARK,
    STATUS_COLOR_ERROR_DARK,
    STATUS_TEXT_COLOR_DARK,
)

STEPS_PLACEHOLDER_TEXT = "No steps recorded yet."
SEGMENTS_PLACEHOLDER_TEXT = (
    "No datablock loaded — load a firmware file to see segments here."
)

# kind -> (light background, dark background) — picked at
# coloring time by _status_colors() based on which theme is
# currently active (self._dark_mode_active), not just the theme
# at app startup, so toggling View > Dark Mode mid-run recolors
# correctly on the next status update.
_STATUS_COLOR_PAIRS = {
    "running": (STATUS_COLOR_RUNNING, STATUS_COLOR_RUNNING_DARK),
    "done": (STATUS_COLOR_DONE, STATUS_COLOR_DONE_DARK),
    "error": (STATUS_COLOR_ERROR, STATUS_COLOR_ERROR_DARK),
}


class FlashTabMixin:
    """
    Mixin class that adds Flash tab functionality
    to the MainWindow.
    """

    # ==================================================
    # Setup Flash Tab
    # ==================================================

    def setup_flash_tab(self):

        # Flash Worker & Thread
        self.thread = None
        self.worker = None

        # Progress
        self.ui.progressBar.setRange(0, 100)
        self.ui.progressBar.setValue(0)

        # Animate value changes instead of jumping instantly —
        # kept as an instance attribute (not a local) so it isn't
        # garbage-collected mid-animation and so on_progress_changed()
        # can re-target a still-running animation instead of racing it.
        self._progress_animation = QPropertyAnimation(
            self.ui.progressBar, b"value"
        )
        self._progress_animation.setDuration(200)
        self._progress_animation.setEasingCurve(QEasingCurve.OutCubic)

        # Stats Label
        from PySide6.QtWidgets import QLabel
        self.ui.statsLabel = QLabel("ETA: -- | Speed: --")
        self.ui.horizontalLayout_flashHeader.addWidget(
            self.ui.statsLabel
        )

        # Button
        self.ui.flashButton.setText("Flash")

        # Steps table
        self.ui.stepsTable.setColumnCount(2)
        self.ui.stepsTable.setHorizontalHeaderLabels(
            ["Timestamp", "Description"]
        )
        self.ui.stepsTable.horizontalHeader().setStretchLastSection(
            True
        )

        # Segments table
        self.ui.segmentsTable.setColumnCount(6)
        self.ui.segmentsTable.setHorizontalHeaderLabels(
            [
                "Status",
                "Start Address",
                "Length",
                "Type",
                "Datablock",
                "Segment"
            ]
        )
        self._set_table_placeholder(
            self.ui.stepsTable, STEPS_PLACEHOLDER_TEXT
        )
        self._steps_placeholder_active = True

        self._set_table_placeholder(
            self.ui.segmentsTable, SEGMENTS_PLACEHOLDER_TEXT
        )

        self.ui.segmentsTable.horizontalHeader().setStretchLastSection(
            True
        )

        # Connect button
        self.ui.flashButton.clicked.connect(
            self.flash_button_clicked
        )

    # ==================================================
    # Empty-state placeholder row
    # ==================================================

    def _set_table_placeholder(self, table, text):
        """
        Replace a table's contents with a single centered,
        non-editable "no data" row spanning every column —
        mirrors gui/configure_tab.py's _add_placeholder_row()
        for tableWidgetDatablocks. Only column 0 gets an item;
        callers that iterate a table's rows for real data (e.g.
        gui/report_export.py's _report_steps_table()) already
        skip rows with no item in their data column, the same
        way _report_datablocks_table() does.
        """

        table.setRowCount(0)
        table.insertRow(0)

        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignCenter)
        item.setForeground(QColor("gray"))
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        table.setItem(0, 0, item)
        table.setSpan(0, 0, 1, table.columnCount())

    # ==================================================
    # Status colors (theme-aware)
    # ==================================================

    def _status_colors(self, kind):
        """
        Return (background_hex, text_hex) for a status highlight
        — "running"/"done"/"error" — picking the light or dark
        pair based on which theme is *currently* live
        (self._dark_mode_active, kept in sync by
        gui/menu_bar.py's action_toggle_dark_mode() on every
        toggle), not just whichever theme was active at startup.
        """

        light_bg, dark_bg = _STATUS_COLOR_PAIRS[kind]

        if getattr(self, '_dark_mode_active', False):
            return dark_bg, STATUS_TEXT_COLOR_DARK

        return light_bg, STATUS_TEXT_COLOR

    # ==================================================
    # Flash button
    # ==================================================

    def flash_button_clicked(self):

        if (self.thread is not None
                and self.thread.isRunning()):

            # Abort
            self.worker.request_abort()

        else:

            # Check hardware selection — Virtual ECU Simulator
            # is stored with userData=None, real Vector
            # channels with their channel index (see
            # ConfigureTabMixin.populate_hardware_combo()).
            use_virtual = True
            if hasattr(self.ui, 'comboBoxHardware'):
                use_virtual = (
                    self.ui.comboBoxHardware.currentData() is None
                )

            # Warn (don't block) about a likely CAN bus
            # conflict — e.g. CANoe left running with a
            # measurement active — before touching real
            # hardware. Not applicable to the Virtual ECU
            # Simulator. User can still choose to proceed.
            if (not use_virtual
                    and hasattr(self, 'detect_can_conflict_warning')):
                warning = self.detect_can_conflict_warning()
                if warning:
                    choice = QMessageBox.warning(
                        self,
                        "Possible CAN Bus Conflict",
                        warning + "\n\nContinue with flashing anyway?",
                        QMessageBox.Yes | QMessageBox.No,
                        QMessageBox.No,
                    )
                    if choice != QMessageBox.Yes:
                        return

            # Build flash sequence from checked datablocks
            # only — unticking a row's checkbox excludes it
            # from both the flash sequence and the Segments
            # table (see ConfigureTabMixin.get_checked_datablocks()).
            datablocks = (
                self.get_checked_datablocks()
                if hasattr(self, 'get_checked_datablocks')
                else getattr(self, '_loaded_datablocks', [])
            )

            # Nothing to flash — block instead of running a
            # pointless session/security/reset sequence with
            # no Download step and an empty Segments table.
            if not datablocks:
                QMessageBox.warning(
                    self,
                    "No Firmware Loaded",
                    "No firmware file is loaded (or ticked) "
                    "to flash.\n\nLoad a datablock in the Data "
                    "tab first, or tick at least one row in "
                    "the Datablocks table.",
                )
                return

            # Start
            self.prepare_flash_ui(datablocks)

            use_suzuki_sequence = False
            if hasattr(self.ui, 'comboBoxFlashSequence'):
                use_suzuki_sequence = (
                    "Suzuki"
                    in self.ui.comboBoxFlashSequence.currentText()
                )

            if use_suzuki_sequence:
                tester_serial_number = (
                    self.get_tester_serial_number()
                    if hasattr(self, 'get_tester_serial_number')
                    else None
                )
                steps = build_suzuki_slp1_flash_sequence(
                    datablocks,
                    tester_serial_number=tester_serial_number,
                )
            else:
                steps = build_flash_sequence(datablocks)

            security_dll_path = getattr(
                self, '_security_dll_path', ''
            ) or None

            can_config = (
                self.get_can_config()
                if hasattr(self, 'get_can_config')
                else {}
            )

            data_format_config = (
                self.get_data_format_config()
                if hasattr(self, 'get_data_format_config')
                else {}
            )

            self.thread = QThread()
            self.worker = FlashWorker(
                steps=steps,
                datablocks=datablocks,
                use_virtual=use_virtual,
                security_dll_path=security_dll_path,
                keepalive_functional=use_suzuki_sequence,
                can_channel=can_config.get("channel", 0),
                can_serial=can_config.get("serial"),
                can_tx_id=can_config.get("tx_id", 0x778),
                can_rx_id=can_config.get("rx_id", 0x788),
                can_bitrate=can_config.get("bitrate", 500000),
                can_fd=can_config.get("fd", False),
                can_data_bitrate=can_config.get(
                    "data_bitrate", 2000000
                ),
                download_compression=data_format_config.get(
                    "compression", 0x00
                ),
                download_encrypting=data_format_config.get(
                    "encrypting", 0x00
                ),
            )

            self.worker.moveToThread(self.thread)

            self.thread.started.connect(
                self.worker.run
            )

            self.worker.flash_finished.connect(
                self.thread.quit
            )

            self.worker.flash_aborted.connect(
                self.thread.quit
            )

            self.worker.flash_finished.connect(
                self.worker.deleteLater
            )

            self.worker.flash_aborted.connect(
                self.worker.deleteLater
            )

            # NOTE: intentionally NOT connecting
            # thread.finished -> thread.deleteLater here.
            # _cleanup_thread() below is the single owner
            # of the QThread's lifetime: it wait()s for the
            # OS thread to fully stop, THEN drops the last
            # Python reference. Having both deleteLater()
            # (deferred, C++-side) and a Python callback
            # clearing self.thread (immediate, refcount-
            # triggered) racing on the same finished signal
            # is what caused "QThread: Destroyed while
            # thread is still running" crashes.

            # Connect signals
            self.worker.step_started.connect(
                self.on_step_started
            )

            self.worker.progress_changed.connect(
                self.on_progress_changed
            )

            self.worker.information_message.connect(
                self.on_information_message
            )

            self.worker.trace_message.connect(
                self.on_trace_message
            )

            self.worker.trace_row.connect(
                self.on_trace_row
            )

            self.worker.segment_progress.connect(
                self.on_segment_progress
            )

            self.worker.ecu_info_message.connect(
                self.on_ecu_info
            )

            self.worker.flash_finished.connect(
                self.on_flash_finished
            )

            self.worker.flash_aborted.connect(
                self.on_flash_aborted
            )

            self.thread.finished.connect(
                self._cleanup_thread
            )

            self.thread.start()

    def _cleanup_thread(self):

        # thread.finished only fires once the QThread has
        # genuinely stopped, so this is the single safe
        # place to drop the last references to it.
        if self.thread is not None:
            self.thread.wait()

        self.thread = None
        self.worker = None

    # ==================================================
    # Prepare UI before flash
    # ==================================================

    def prepare_flash_ui(self, datablocks=None):
        """
        datablocks: the datablocks that will actually be
        flashed (already filtered by checkbox state — see
        flash_button_clicked()). Defaults to every loaded
        datablock, unfiltered, for callers that don't care
        about the checkbox distinction.
        """

        if datablocks is None:
            datablocks = getattr(
                self, '_loaded_datablocks', []
            )

        # Button
        self.ui.flashButton.setText("Abort")

        # Reset progress (stop any animation left over from a
        # previous run so it can't re-target a stale endValue)
        self._progress_animation.stop()
        self.ui.progressBar.setValue(0)

        self.start_time = time.time()
        self._total_bytes_sent = 0
        self._total_bytes_all = 0

        # Calculate total bytes from the datablocks that
        # will actually be flashed
        for db in datablocks:
            self._total_bytes_all += db.total_size

        self.ui.statsLabel.setText(
            "ETA: calculating... | Speed: --"
        )

        # Clear old steps
        self.ui.stepsTable.setRowCount(0)
        self._steps_placeholder_active = False

        # Clear old segments
        self.ui.segmentsTable.setRowCount(0)

        # Clear logs
        self.ui.informationText.clear()
        self.ui.traceTable.setRowCount(0)

        # Add segments from the datablocks that will
        # actually be flashed
        self.add_segments_from_datablocks(datablocks)

    # ==================================================
    # Step signal
    # ==================================================

    def on_step_started(self, description):

        self.add_step(description)

    # ==================================================
    # Progress signal
    # ==================================================

    def on_progress_changed(self, value):

        self._progress_animation.stop()
        self._progress_animation.setStartValue(
            self.ui.progressBar.value()
        )
        self._progress_animation.setEndValue(value)
        self._progress_animation.start()

        if (hasattr(self, 'start_time')
                and self.start_time and value > 0):

            elapsed = time.time() - self.start_time
            total_estimated = (elapsed / value) * 100
            eta = max(0, total_estimated - elapsed)

            # Calculate speed from actual bytes
            if (hasattr(self, '_total_bytes_sent')
                    and self._total_bytes_sent > 0
                    and elapsed > 0):
                speed = (
                    self._total_bytes_sent / 1024.0
                ) / elapsed
                self.ui.statsLabel.setText(
                    f"ETA: {int(eta)}s | "
                    f"Speed: {speed:.1f} KB/s | "
                    f"Elapsed: {elapsed:.1f}s"
                )
            else:
                self.ui.statsLabel.setText(
                    f"ETA: {int(eta)}s | "
                    f"Elapsed: {elapsed:.1f}s"
                )

        self.update_segments(value)

    # ==================================================
    # Segment progress (per-segment bytes)
    # ==================================================

    def on_segment_progress(
        self,
        seg_idx,
        bytes_sent,
        total_bytes
    ):
        """Called for each chunk of data transferred."""

        # Track total bytes for speed calculation
        self._total_bytes_sent = getattr(
            self, '_total_bytes_sent', 0
        )
        self._total_bytes_sent = bytes_sent  # cumulative per segment

        # Update segment status text with percentage
        if seg_idx < self.ui.segmentsTable.rowCount():
            pct = int(
                (bytes_sent / total_bytes) * 100
            ) if total_bytes > 0 else 0

            status_item = self.ui.segmentsTable.item(
                seg_idx, 0
            )
            if status_item:
                status_item.setText(
                    f"Flashing... {pct}%"
                )

            # Color active segment
            bg, fg = self._status_colors('running')
            for col in range(
                self.ui.segmentsTable.columnCount()
            ):
                item = self.ui.segmentsTable.item(
                    seg_idx, col
                )
                if item:
                    item.setBackground(QColor(bg))
                    item.setForeground(QColor(fg))

    # ==================================================
    # Information signal
    # ==================================================

    def on_information_message(self, message):

        self.log_information(message)

    # ==================================================
    # ECU Info signal
    # ==================================================

    def on_ecu_info(self, info_dict):
        """Display ECU identification data."""

        self.log_information(
            "─── ECU Identification ───"
        )

        for key, value in info_dict.items():
            self.log_information(
                f"  {key}: {value}"
            )

        self.log_information(
            "──────────────────────────"
        )

    # ==================================================
    # Trace signal
    # ==================================================

    def on_trace_message(self, message):

        self.log_trace(message)

    def on_trace_row(self, row):

        self.log_trace_row(row)

    # ==================================================
    # Flash finished
    # ==================================================

    def on_flash_finished(self):

        self.ui.flashButton.setText("Flash")

        # Color last step
        row = self.ui.stepsTable.rowCount() - 1
        if row >= 0:
            bg, fg = self._status_colors('done')
            for col in range(2):
                item = self.ui.stepsTable.item(row, col)
                if item:
                    item.setBackground(QColor(bg))
                    item.setForeground(QColor(fg))

        # Final stats
        elapsed = time.time() - self.start_time
        total_sent = getattr(
            self, '_total_bytes_sent', 0
        )
        if elapsed > 0 and total_sent > 0:
            speed = (total_sent / 1024.0) / elapsed
            self.ui.statsLabel.setText(
                f"Done | {elapsed:.1f}s | "
                f"{speed:.1f} KB/s | "
                f"{total_sent} bytes"
            )
        else:
            self.ui.statsLabel.setText(
                f"Done | {elapsed:.1f}s"
            )

        # NOTE: do NOT touch self.thread/self.worker here.
        # flash_finished is emitted from inside FlashWorker.
        # run() itself, i.e. WHILE the worker thread is still
        # executing (run() hasn't returned yet) — dropping the
        # last Python reference to self.thread at this point
        # destroys the QThread object that is, at that exact
        # moment, still actively running, which crashes with
        # "QThread: Destroyed while thread is still running".
        # _cleanup_thread() (connected to thread.finished,
        # which only fires once the thread has genuinely
        # stopped) is the single place that clears these.

    # ==================================================
    # Flash aborted
    # ==================================================

    def on_flash_aborted(self):

        self.ui.flashButton.setText("Flash")

        self.add_step("Flash aborted")

        # Color aborted step
        row = self.ui.stepsTable.rowCount() - 1
        if row >= 0:
            bg, fg = self._status_colors('error')
            for col in range(2):
                item = self.ui.stepsTable.item(row, col)
                if item:
                    item.setBackground(QColor(bg))
                    item.setForeground(QColor(fg))

        self.ui.statsLabel.setText(
            "ETA: -- | Speed: Aborted"
        )

        # See note in on_flash_finished() — do not touch
        # self.thread/self.worker from here.

    # ==================================================
    # Add step
    # ==================================================

    def add_step(self, description):

        if getattr(self, '_steps_placeholder_active', False):
            self.ui.stepsTable.setRowCount(0)
            self._steps_placeholder_active = False

        row = self.ui.stepsTable.rowCount()
        self.ui.stepsTable.insertRow(row)

        timestamp = datetime.now().strftime(
            "%H:%M:%S.%f"
        )[:-3]

        self.ui.stepsTable.setItem(
            row, 0, QTableWidgetItem(timestamp)
        )

        self.ui.stepsTable.setItem(
            row, 1, QTableWidgetItem(description)
        )

        # Color logic: current step is "running", previous is "done"
        running_bg, running_fg = self._status_colors('running')
        for col in range(2):
            item = self.ui.stepsTable.item(row, col)
            if item:
                item.setBackground(QColor(running_bg))
                item.setForeground(QColor(running_fg))

        if row > 0:
            done_bg, done_fg = self._status_colors('done')
            for col in range(2):
                prev_item = self.ui.stepsTable.item(
                    row - 1, col
                )
                if prev_item:
                    prev_item.setBackground(QColor(done_bg))
                    prev_item.setForeground(QColor(done_fg))

        self.ui.stepsTable.scrollToBottom()

    # ==================================================
    # Segments from datablocks
    # ==================================================

    def add_segments_from_datablocks(self, datablocks=None):
        """
        Rebuild the segments table from the given datablocks
        (defaults to every loaded datablock, unfiltered).
        Always clears the table first, so it's safe to call
        directly regardless of what the caller left in it. Shows
        a single "no data" placeholder row if the list is empty
        or has no segments — never fake per-segment demo rows
        (see docs/gui_todo.md item #6).
        """

        if datablocks is None:
            datablocks = getattr(
                self, '_loaded_datablocks', []
            )

        self.ui.segmentsTable.setRowCount(0)

        for db_idx, datablock in enumerate(datablocks):
            for seg_idx, segment in enumerate(
                datablock.segments
            ):
                row = self.ui.segmentsTable.rowCount()
                self.ui.segmentsTable.insertRow(row)

                items = [
                    "Waiting",
                    f"0x{segment.start_address:X}",
                    str(segment.length),
                    "Data",
                    str(db_idx + 1),
                    str(seg_idx + 1),
                ]

                for col, value in enumerate(items):
                    self.ui.segmentsTable.setItem(
                        row, col,
                        QTableWidgetItem(value)
                    )

        if self.ui.segmentsTable.rowCount() == 0:
            self._set_table_placeholder(
                self.ui.segmentsTable, SEGMENTS_PLACEHOLDER_TEXT
            )

    # ==================================================
    # Update segments
    # ==================================================

    def update_segments(self, progress):

        num_segments = self.ui.segmentsTable.rowCount()

        if num_segments == 0:
            return

        segment_progress = 100 / num_segments
        current_segment_idx = int(
            progress / segment_progress
        )

        for i in range(num_segments):

            status = "Waiting"
            # Transparent (not a forced white) so an untouched
            # row falls back to the table's own themed background
            # (light or dark) instead of overriding it — a
            # hardcoded white here was invisible against Dark
            # Mode's near-white default text.
            color = QColor(Qt.transparent)
            text_color = None

            if i < current_segment_idx:
                status = "Flashed"
                bg, fg = self._status_colors('done')
                color = QColor(bg)
                text_color = QColor(fg)

            elif (i == current_segment_idx
                  and progress < 100):
                status = "Flashing..."
                bg, fg = self._status_colors('running')
                color = QColor(bg)
                text_color = QColor(fg)

            if progress == 100:
                status = "Flashed"
                bg, fg = self._status_colors('done')
                color = QColor(bg)
                text_color = QColor(fg)

            # Update status text
            status_item = self.ui.segmentsTable.item(
                i, 0
            )
            if status_item:
                status_item.setText(status)

            # Color row — _status_colors() already picked the
            # right (background, text) pair for whichever theme
            # is currently live (see its docstring).
            for col in range(
                self.ui.segmentsTable.columnCount()
            ):
                item = self.ui.segmentsTable.item(
                    i, col
                )
                if item:
                    item.setBackground(color)
                    if text_color is not None:
                        item.setForeground(text_color)

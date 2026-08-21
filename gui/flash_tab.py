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

from PySide6.QtWidgets import QTableWidgetItem
from PySide6.QtGui import QColor
from PySide6.QtCore import QThread, Qt

from core.flash_controller import FlashWorker
from core.flash_sequence import (
    build_flash_sequence,
    build_suzuki_slp1_flash_sequence,
    DEFAULT_FLASH_SEQUENCE,
)


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

        # Stats Label
        from PySide6.QtWidgets import QLabel
        self.ui.statsLabel = QLabel("ETA: -- | Speed: --")
        self.ui.horizontalLayout.addWidget(
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
        self.ui.segmentsTable.horizontalHeader().setStretchLastSection(
            True
        )

        # Connect button
        self.ui.flashButton.clicked.connect(
            self.flash_button_clicked
        )

    # ==================================================
    # Flash button
    # ==================================================

    def flash_button_clicked(self):

        if (self.thread is not None
                and self.thread.isRunning()):

            # Abort
            self.worker.request_abort()

        else:

            # Start
            self.prepare_flash_ui()

            # Build flash sequence from loaded datablocks
            datablocks = getattr(
                self, '_loaded_datablocks', []
            )

            use_suzuki_sequence = False
            if hasattr(self.ui, 'comboBoxFlashSequence'):
                use_suzuki_sequence = (
                    "Suzuki"
                    in self.ui.comboBoxFlashSequence.currentText()
                )

            if use_suzuki_sequence:
                steps = build_suzuki_slp1_flash_sequence(
                    datablocks
                )
            else:
                steps = build_flash_sequence(datablocks)

            # Check hardware selection
            use_virtual = True
            if hasattr(self.ui, 'comboBoxHardware'):
                hw_text = self.ui.comboBoxHardware.currentText()
                use_virtual = "Virtual" in hw_text

            security_dll_path = getattr(
                self, '_security_dll_path', ''
            ) or None

            can_config = (
                self.get_can_config()
                if hasattr(self, 'get_can_config')
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
                can_tx_id=can_config.get("tx_id", 0x778),
                can_rx_id=can_config.get("rx_id", 0x788),
                can_bitrate=can_config.get("bitrate", 500000),
                can_fd=can_config.get("fd", False),
                can_data_bitrate=can_config.get(
                    "data_bitrate", 2000000
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

            self.thread.finished.connect(
                self.thread.deleteLater
            )

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

        self.thread = None
        self.worker = None

    # ==================================================
    # Prepare UI before flash
    # ==================================================

    def prepare_flash_ui(self):

        # Button
        self.ui.flashButton.setText("Abort")

        # Reset progress
        self.ui.progressBar.setValue(0)

        self.start_time = time.time()
        self._total_bytes_sent = 0
        self._total_bytes_all = 0

        # Calculate total bytes from all datablocks
        datablocks = getattr(
            self, '_loaded_datablocks', []
        )
        for db in datablocks:
            self._total_bytes_all += db.total_size

        self.ui.statsLabel.setText(
            "ETA: calculating... | Speed: --"
        )

        # Clear old steps
        self.ui.stepsTable.setRowCount(0)

        # Clear old segments
        self.ui.segmentsTable.setRowCount(0)

        # Clear logs
        self.ui.informationText.clear()
        self.ui.traceText.clear()

        # Add segments from loaded datablocks
        self.add_segments_from_datablocks()

    # ==================================================
    # Step signal
    # ==================================================

    def on_step_started(self, description):

        self.add_step(description)

    # ==================================================
    # Progress signal
    # ==================================================

    def on_progress_changed(self, value):

        self.ui.progressBar.setValue(value)

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

            # Color active segment yellow
            for col in range(
                self.ui.segmentsTable.columnCount()
            ):
                item = self.ui.segmentsTable.item(
                    seg_idx, col
                )
                if item:
                    item.setBackground(
                        QColor("#FFFACD")
                    )

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

    # ==================================================
    # Flash finished
    # ==================================================

    def on_flash_finished(self):

        self.ui.flashButton.setText("Flash")

        # Color last step green
        row = self.ui.stepsTable.rowCount() - 1
        if row >= 0:
            for col in range(2):
                item = self.ui.stepsTable.item(row, col)
                if item:
                    item.setBackground(
                        QColor("#C8E6C9")
                    )

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

        self.thread = None
        self.worker = None

    # ==================================================
    # Flash aborted
    # ==================================================

    def on_flash_aborted(self):

        self.ui.flashButton.setText("Flash")

        self.add_step("Flash aborted")

        # Color aborted step red
        row = self.ui.stepsTable.rowCount() - 1
        if row >= 0:
            for col in range(2):
                item = self.ui.stepsTable.item(row, col)
                if item:
                    item.setBackground(
                        QColor("#FFCDD2")
                    )

        self.ui.statsLabel.setText(
            "ETA: -- | Speed: Aborted"
        )

        self.thread = None
        self.worker = None

    # ==================================================
    # Add step
    # ==================================================

    def add_step(self, description):

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

        # Color logic: Current is yellow, previous is green
        for col in range(2):
            item = self.ui.stepsTable.item(row, col)
            if item:
                item.setBackground(QColor("#FFFACD"))

        if row > 0:
            for col in range(2):
                prev_item = self.ui.stepsTable.item(
                    row - 1, col
                )
                if prev_item:
                    prev_item.setBackground(
                        QColor("#C8E6C9")
                    )

        self.ui.stepsTable.scrollToBottom()

    # ==================================================
    # Segments from datablocks
    # ==================================================

    def add_segments_from_datablocks(self):
        """
        Populate segments table from loaded datablocks.
        Falls back to demo data if no datablocks loaded.
        """

        datablocks = getattr(
            self, '_loaded_datablocks', []
        )

        if datablocks:

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

        else:

            # Demo segments (fallback)
            demo_segments = [
                ("Waiting", "0x1000", "400",
                 "Data", "1", "1"),
                ("Waiting", "0x2000", "800",
                 "Data", "1", "2"),
                ("Waiting", "0x5000", "900000",
                 "Data", "1", "3"),
                ("Waiting", "0x100", "400",
                 "Data", "2", "1"),
                ("Waiting", "0x1000", "800",
                 "Data", "2", "2"),
            ]

            for segment in demo_segments:

                row = self.ui.segmentsTable.rowCount()
                self.ui.segmentsTable.insertRow(row)

                for col, value in enumerate(segment):
                    self.ui.segmentsTable.setItem(
                        row, col,
                        QTableWidgetItem(value)
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
            color = QColor(Qt.white)

            if i < current_segment_idx:
                status = "Flashed"
                color = QColor("#C8E6C9")

            elif (i == current_segment_idx
                  and progress < 100):
                status = "Flashing..."
                color = QColor("#FFFACD")

            if progress == 100:
                status = "Flashed"
                color = QColor("#C8E6C9")

            # Update status text
            status_item = self.ui.segmentsTable.item(
                i, 0
            )
            if status_item:
                status_item.setText(status)

            # Color row
            for col in range(
                self.ui.segmentsTable.columnCount()
            ):
                item = self.ui.segmentsTable.item(
                    i, col
                )
                if item:
                    item.setBackground(color)

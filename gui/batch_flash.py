# ==================================================
# Batch Flash
# ==================================================
#
# BatchFlashMixin — "Batch Flash" mode for the existing Flash
# tab (Tools > Mode > Flash / Batch Flash). Orchestrates two
# already-hardened QThread-based workers sequentially, never
# concurrently: TestConnectionWorker (identify — reads Serial
# Number via DID 0xF18C) then FlashWorker (flash). Neither
# worker class is modified — see
# docs/superpowers/specs/2026-08-30-sequential-batch-flash-design.md.
#
# Threading follows the exact lifecycle rules documented in
# CLAUDE.md's "Threading model": a worker's own *_finished
# signal connects to thread.quit + worker.deleteLater; only a
# slot connected to thread.finished (never the worker's own
# signal) clears self._identify_thread/self._identify_worker or
# self.thread/self.worker (the latter pair is owned by
# gui/flash_tab.py's flash_button_clicked() for single-flash,
# and reused here for the batch Flash step too).
# ==================================================

from datetime import datetime

from PySide6.QtCore import QThread
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QMessageBox, QTableWidgetItem

from core.test_connection import TestConnectionWorker
from core.flash_controller import FlashWorker
from core.flash_sequence import (
    build_flash_sequence,
    build_suzuki_slp1_flash_sequence,
)


class BatchFlashMixin:
    """Mixin adding Batch Flash mode to MainWindow's Flash tab."""

    # ==================================================
    # Setup
    # ==================================================

    def setup_batch_flash(self):

        self._batch_mode_active = False
        self._identify_thread = None
        self._identify_worker = None
        self._batch_operator_abort = False
        self._batch_last_information_message = ""
        self._reset_batch_session()

        # buttonStopBatch/buttonExportBatchReport.clicked are
        # wired further down in this method, once stop_batch()/
        # export_batch_report() are defined below (they aren't
        # yet at this point in the file — added incrementally).

    def _reset_batch_session(self):

        self._batch_ecu_index = 0
        self._batch_counts = {"pass": 0, "fail": 0, "abort": 0}
        self._batch_records = []
        self._batch_session_start_time = None
        self._batch_stopping = False

        if hasattr(self.ui, 'labelEcuCounter'):
            self.ui.labelEcuCounter.setText("ECU #0")
        if hasattr(self.ui, 'labelBatchTally'):
            self._update_batch_tally_label()
        if hasattr(self.ui, 'tableWidgetBatchLog'):
            self.ui.tableWidgetBatchLog.setRowCount(0)
        if hasattr(self.ui, 'buttonExportBatchReport'):
            self.ui.buttonExportBatchReport.setEnabled(False)

    # ==================================================
    # Mode toggle
    # ==================================================

    def on_batch_mode_toggled(self, is_batch):

        self._batch_mode_active = is_batch

        if hasattr(self.ui, 'groupBoxBatchFlash'):
            self.ui.groupBoxBatchFlash.setVisible(is_batch)

        if hasattr(self.ui, 'flashButton'):
            self.ui.flashButton.setText(
                "Start Batch" if is_batch else "Flash"
            )

    def _update_batch_tally_label(self):

        c = self._batch_counts
        self.ui.labelBatchTally.setText(
            f"{c['pass']} PASS · {c['fail']} FAIL · {c['abort']} ABORTED"
        )

    # ==================================================
    # Main button (Start Batch / Abort / Next ECU)
    # ==================================================

    def _batch_main_button_clicked(self):

        if self.thread is not None and self.thread.isRunning():
            # Flashing — Abort this unit only (batch keeps
            # going, see stop_batch() for ending the session).
            self._batch_operator_abort = True
            self.worker.request_abort()
            return

        if (self._identify_thread is not None
                and self._identify_thread.isRunning()):
            # Already identifying — ignore (mirrors
            # flash_button_clicked()'s own re-entrancy
            # assumption: the button/menu are the only entry
            # points, and both are disabled while a thread is
            # alive — see Task 8).
            return

        datablocks = (
            self.get_checked_datablocks()
            if hasattr(self, 'get_checked_datablocks')
            else getattr(self, '_loaded_datablocks', [])
        )

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

        self._start_identify()

    def _start_identify(self):

        if self._batch_session_start_time is None:
            # Set once per session, on the very first Identify -
            # a "Next ECU" retry after "No ECU detected" must
            # not push this forward, and the batch report needs
            # this as a stable session-start marker, not a
            # per-unit timestamp.
            self._batch_session_start_time = datetime.now()

        self.ui.buttonStopBatch.setEnabled(True)
        self.ui.labelBatchStatus.setText(
            "Identifying ECU — reading Serial Number..."
        )
        self.ui.labelBatchStatusCaption.setText(
            "Reads DID 0xF18C via the same probe as Tools > "
            "Test Connection — independent of the flash "
            "sequence itself."
        )

        use_virtual = True
        if hasattr(self.ui, 'comboBoxHardware'):
            use_virtual = (
                self.ui.comboBoxHardware.currentData() is None
            )

        security_dll_path = getattr(
            self, '_security_dll_path', ''
        ) or None

        use_suzuki_sequence = False
        if hasattr(self.ui, 'comboBoxFlashSequence'):
            use_suzuki_sequence = (
                "Suzuki"
                in self.ui.comboBoxFlashSequence.currentText()
            )

        can_config = (
            self.get_can_config()
            if hasattr(self, 'get_can_config')
            else {}
        )

        self._batch_identify_ecu_info = {}

        self._identify_thread = QThread()
        self._identify_worker = TestConnectionWorker(
            use_virtual=use_virtual,
            security_dll_path=security_dll_path,
            functional=use_suzuki_sequence,
            can_channel=can_config.get("channel", 0),
            can_serial=can_config.get("serial"),
            can_tx_id=can_config.get("tx_id", 0x778),
            can_rx_id=can_config.get("rx_id", 0x788),
            can_bitrate=can_config.get("bitrate", 500000),
            can_fd=can_config.get("fd", False),
            can_data_bitrate=can_config.get(
                "data_bitrate", 2000000
            ),
        )
        self._identify_worker.moveToThread(self._identify_thread)

        self._identify_thread.started.connect(
            self._identify_worker.run
        )

        self._identify_worker.ecu_info_message.connect(
            self._on_identify_ecu_info
        )
        self._identify_worker.finished.connect(
            self._on_identify_finished
        )

        self._identify_worker.finished.connect(
            self._identify_thread.quit
        )
        self._identify_worker.finished.connect(
            self._identify_worker.deleteLater
        )

        # NOTE: intentionally NOT connecting thread.finished ->
        # thread.deleteLater here — see module docstring and
        # CLAUDE.md's "Threading model".
        self._identify_thread.finished.connect(
            self._cleanup_identify_thread
        )

        self._identify_thread.start()

    def _on_identify_ecu_info(self, info_dict):
        self._batch_identify_ecu_info = info_dict

    def _cleanup_identify_thread(self):

        if self._identify_thread is not None:
            self._identify_thread.wait()

        self._identify_thread = None
        self._identify_worker = None

    def _on_identify_finished(self, passed, message):

        if not passed:
            self.ui.labelBatchStatus.setText(
                "No ECU detected on the bus."
            )
            self.ui.labelBatchStatusCaption.setText(
                "Check connection and try again — not logged, "
                "does not count against the batch."
            )
            self.ui.buttonStopBatch.setEnabled(False)
            return

        serial = self._batch_identify_ecu_info.get(
            "ECU Serial Number", "UNKNOWN"
        )
        self._start_flash_for_current_ecu(serial)

    # ==================================================
    # Flash step (after a successful Identify)
    # ==================================================

    def _start_flash_for_current_ecu(self, serial):

        datablocks = (
            self.get_checked_datablocks()
            if hasattr(self, 'get_checked_datablocks')
            else getattr(self, '_loaded_datablocks', [])
        )

        if not datablocks:
            # Edge case: the operator unchecked every datablock
            # during the ~1s Identify probe. FlashWorker.run()
            # treats 0 steps as an immediate flash_finished
            # (see core/flash_controller.py) - without this
            # guard that would silently log a false PASS row
            # with no actual work done, corrupting the batch's
            # traceability report. Bail out the same way
            # _batch_main_button_clicked() already does for the
            # "Start Batch" case.
            QMessageBox.warning(
                self,
                "No Firmware Loaded",
                "No firmware file is loaded (or ticked) to "
                "flash.\n\nLoad a datablock in the Data tab "
                "first, or tick at least one row in the "
                "Datablocks table, then click Next ECU again.",
            )
            self.ui.buttonStopBatch.setEnabled(False)
            return

        self._batch_current_serial = serial
        self._batch_flash_start_time = datetime.now()
        self._batch_operator_abort = False
        self._batch_last_information_message = ""

        self.prepare_flash_ui(datablocks)
        self.ui.flashButton.setText("Abort")

        use_virtual = True
        if hasattr(self.ui, 'comboBoxHardware'):
            use_virtual = (
                self.ui.comboBoxHardware.currentData() is None
            )

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

        self.thread.started.connect(self.worker.run)

        self.worker.flash_finished.connect(self.thread.quit)
        self.worker.flash_aborted.connect(self.thread.quit)
        self.worker.flash_finished.connect(self.worker.deleteLater)
        self.worker.flash_aborted.connect(self.worker.deleteLater)

        # Same signals flash_button_clicked() connects for a
        # normal single flash, reused as-is - none of these
        # touch flashButton's text, so they're safe to share.
        self.worker.step_started.connect(self.on_step_started)
        self.worker.progress_changed.connect(self.on_progress_changed)
        self.worker.information_message.connect(
            self.on_information_message
        )
        self.worker.information_message.connect(
            self._capture_last_information_message
        )
        self.worker.trace_message.connect(self.on_trace_message)
        self.worker.trace_row.connect(self.on_trace_row)
        self.worker.segment_progress.connect(self.on_segment_progress)
        self.worker.ecu_info_message.connect(self.on_ecu_info)

        # Batch-specific finish handlers (NOT on_flash_finished/
        # on_flash_aborted - those set flashButton back to
        # "Flash", which single-flash mode needs but batch mode
        # must not).
        self.worker.flash_finished.connect(self._on_batch_flash_finished)
        self.worker.flash_aborted.connect(self._on_batch_flash_aborted)

        # NOTE: intentionally NOT connecting thread.finished ->
        # thread.deleteLater - _cleanup_thread() (gui/flash_tab.py,
        # shared with single-flash) is the single owner of this
        # QThread's lifetime, same reasoning as flash_button_clicked().
        self.thread.finished.connect(self._cleanup_thread)

        self.thread.start()

    def _capture_last_information_message(self, message):
        self._batch_last_information_message = message

    def _on_batch_flash_finished(self):

        self._color_last_step_row('done')
        duration = self._batch_elapsed_seconds()
        self._on_batch_unit_finished(
            "pass", self._batch_current_serial, duration
        )

    def _on_batch_flash_aborted(self):

        result = "abort" if self._batch_operator_abort else "fail"
        self._color_last_step_row(
            'running' if result == "abort" else 'error'
        )
        duration = self._batch_elapsed_seconds()
        reason = (
            None if result == "abort"
            else self._batch_last_information_message
        )
        self._on_batch_unit_finished(
            result, self._batch_current_serial, duration, reason
        )

    def _color_last_step_row(self, kind):

        row = self.ui.stepsTable.rowCount() - 1
        if row < 0:
            return
        bg, fg = self._status_colors(kind)
        for col in range(2):
            item = self.ui.stepsTable.item(row, col)
            if item:
                item.setBackground(QColor(bg))
                item.setForeground(QColor(fg))

    def _batch_elapsed_seconds(self):
        return int(
            (datetime.now() - self._batch_flash_start_time)
            .total_seconds()
        )

    # ==================================================
    # Batch Log / tally / ECU counter
    # ==================================================

    def _on_batch_unit_finished(self, result, serial, duration, reason=None):

        self._batch_ecu_index += 1
        self._batch_counts[result] += 1
        self._batch_records.append({
            "index": self._batch_ecu_index,
            "serial": serial,
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "result": result,
            "duration": duration,
            "reason": reason,
        })

        self._append_batch_log_row(
            self._batch_ecu_index, serial, result, duration, reason
        )
        self._update_batch_tally_label()
        self.ui.labelEcuCounter.setText(f"ECU #{self._batch_ecu_index}")
        self.ui.buttonExportBatchReport.setEnabled(True)

        result_labels = {
            "pass": "PASS", "fail": "FAIL", "abort": "ABORTED",
        }

        if self._batch_stopping:
            # stop_batch() requested this abort and is waiting
            # for it to actually land before touching button/
            # label state (see stop_batch()'s own comment) - this
            # is that landing point. Do NOT set "Next ECU" here.
            self._batch_stopping = False
            self.ui.flashButton.setText("Start Batch")
            self.ui.buttonStopBatch.setEnabled(False)
            self.ui.labelBatchStatus.setText(
                f"Batch stopped after ECU #{self._batch_ecu_index} "
                f"({result_labels[result]}). Log kept below — "
                "click Start Batch to begin a new session."
            )
            self.ui.labelBatchStatusCaption.setText("")
            return

        self.ui.labelBatchStatus.setText(
            f"ECU #{self._batch_ecu_index} — "
            f"{result_labels[result]} ({serial}, {duration}s)."
        )
        self.ui.labelBatchStatusCaption.setText(
            "Swap in the next ECU, then click Next ECU."
        )
        self.ui.flashButton.setText("Next ECU")

    def _append_batch_log_row(self, index, serial, result, duration, reason):

        table = self.ui.tableWidgetBatchLog
        row = table.rowCount()
        table.insertRow(row)

        result_labels = {
            "pass": "PASS", "fail": "FAIL", "abort": "ABORTED",
        }
        color_kind = {"pass": "done", "fail": "error", "abort": "running"}

        cells = [
            str(index), serial, datetime.now().strftime("%H:%M:%S"),
            result_labels[result], f"{duration}s",
        ]
        for col, text in enumerate(cells):
            item = QTableWidgetItem(text)
            bg, fg = self._status_colors(color_kind[result])
            item.setBackground(QColor(bg))
            item.setForeground(QColor(fg))
            table.setItem(row, col, item)

        if reason:
            table.item(row, 3).setToolTip(reason)

        table.scrollToBottom()

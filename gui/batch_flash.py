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
from PySide6.QtWidgets import QMessageBox

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
        # Task 4 replaces this stub with a call to
        # self._start_flash_for_current_ecu(serial).
        self.ui.labelBatchStatus.setText(
            f"Identified ECU (Serial {serial}) — flash not "
            "yet wired (Task 4)."
        )

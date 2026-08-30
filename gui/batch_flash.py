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

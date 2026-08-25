# ==================================================
# Stress Test (Custom Actions tab)
# ==================================================
#
# TESTING-ONLY TOOL — not a real product feature, meant to be
# deleted once it's served its purpose. Repeatedly runs the
# normal Flash button's exact flow (flash_button_clicked() in
# gui/flash_tab.py) N times in a row against whatever hardware
# is currently selected, so a real ECU can be flashed
# back-to-back to look for intermittent failures. Every other
# mechanism (Trace/Information logs, Export Report/Issue, CAN
# conflict warning) keeps working completely unchanged — this
# file only adds a loop around the same button click.
#
# Threading: reuses flash_button_clicked() as-is, so the same
# QThread lifecycle rules from CLAUDE.md's "Threading model"
# apply. This mixin never touches self.thread/self.worker
# directly from flash_finished/flash_aborted — the next cycle
# is only started from self.thread's own finished signal
# (after gui/flash_tab.py's _cleanup_thread() has already run
# and nulled them out), same as the rest of the app.
# ==================================================

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox


class StressTestMixin:

    # ==================================================
    # Setup
    # ==================================================

    def setup_stress_test(self):

        self._stress_test_running = False

        if not hasattr(self.ui, 'buttonStressTestStart'):
            return

        self.ui.buttonStressTestStart.clicked.connect(
            self.stress_test_button_clicked
        )

    # ==================================================
    # Start / Stop
    # ==================================================

    def stress_test_button_clicked(self):

        if self._stress_test_running:
            self._stress_test_stop()
        else:
            self._stress_test_start()

    def _stress_test_start(self):

        count = self.ui.spinBoxStressTestCount.value()

        choice = QMessageBox.question(
            self,
            "Stress Test",
            f"This will run Flash {count} time(s) in a row "
            f"against the currently selected hardware.\n\n"
            f"This is a testing-only tool — continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if choice != QMessageBox.Yes:
            return

        self._stress_test_total = count
        self._stress_test_current = 0
        self._stress_test_pass_count = 0
        self._stress_test_fail_count = 0
        self._stress_test_cancel_requested = False
        self._stress_test_running = True

        self.ui.buttonStressTestStart.setText("Stop Stress Test")
        self.ui.spinBoxStressTestCount.setEnabled(False)

        self._stress_test_run_next()

    def _stress_test_stop(self):

        self._stress_test_cancel_requested = True

        # Abort the in-flight cycle immediately rather than
        # waiting for it to finish on its own — reuses the same
        # request_abort() the normal Abort button calls.
        if self.thread is not None and self.thread.isRunning():
            self.worker.request_abort()

        self.ui.labelStressTestStatus.setText(
            "Stopping after the current flash finishes... "
            + self._stress_test_summary_text()
        )

    # ==================================================
    # Cycle loop
    # ==================================================

    def _stress_test_run_next(self):

        if (self._stress_test_cancel_requested
                or self._stress_test_current
                >= self._stress_test_total):
            self._stress_test_finish()
            return

        self._stress_test_current += 1
        self._stress_test_update_status()

        self.flash_button_clicked()

        # Capture references now — self.thread/self.worker are
        # reset to None by _cleanup_thread() once this cycle's
        # thread.finished fires, well before that.
        cycle_thread = self.thread
        cycle_worker = self.worker

        if cycle_thread is None or cycle_worker is None:
            # flash_button_clicked() didn't actually start a
            # flash this time (e.g. no datablock loaded, or a
            # CAN-conflict warning was declined) — stop instead
            # of looping forever.
            self._stress_test_cancel_requested = True
            self._stress_test_finish()
            return

        cycle_worker.flash_finished.connect(
            self._stress_test_on_cycle_passed
        )
        cycle_worker.flash_aborted.connect(
            self._stress_test_on_cycle_aborted
        )
        cycle_thread.finished.connect(
            self._stress_test_on_cycle_thread_finished
        )

    def _stress_test_on_cycle_passed(self):

        self._stress_test_pass_count += 1

    def _stress_test_on_cycle_aborted(self):

        self._stress_test_fail_count += 1

    def _stress_test_on_cycle_thread_finished(self):

        self._stress_test_update_status()

        # Deferred rather than called directly — lets Qt finish
        # unwinding this thread.finished emission before a new
        # QThread is created and started for the next cycle.
        QTimer.singleShot(0, self._stress_test_run_next)

    def _stress_test_finish(self):

        self._stress_test_running = False
        self.ui.buttonStressTestStart.setText("Start Stress Test")
        self.ui.spinBoxStressTestCount.setEnabled(True)
        self._stress_test_update_status(finished=True)

    # ==================================================
    # Status label
    # ==================================================

    def _stress_test_summary_text(self):

        return (
            f"({self._stress_test_current}/"
            f"{self._stress_test_total} run — "
            f"{self._stress_test_pass_count} pass, "
            f"{self._stress_test_fail_count} fail)"
        )

    def _stress_test_update_status(self, finished=False):

        prefix = (
            "Stress test finished"
            if finished else "Stress test running"
        )
        self.ui.labelStressTestStatus.setText(
            f"{prefix} {self._stress_test_summary_text()}"
        )

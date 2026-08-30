# ==================================================
# Batch Flash QThread Lifecycle Tests
# ==================================================
#
# Same discipline as tests/test_flash_threading.py: calling a
# worker's run() directly (synchronously) cannot catch QThread
# lifecycle bugs (see CLAUDE.md's "Threading model") — these
# tests always go through moveToThread() + thread.start().
# ==================================================

import os
import sys
import unittest
import unittest.mock

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from PySide6.QtCore import QTimer

from tests.qt_test_utils import get_app
from gui.main_window import MainWindow
from parsers.hex_parser import Segment, Datablock
from core.flash_controller import FlashWorker

SAMPLE_HEX = os.path.join(os.path.dirname(__file__), "sample.hex")


def _run_until(app, predicate, timeout_ms=15000, interval_ms=20):
    state = {"elapsed": 0, "satisfied": False}

    def tick():
        if predicate():
            state["satisfied"] = True
            app.quit()
        elif state["elapsed"] >= timeout_ms:
            app.quit()
        else:
            state["elapsed"] += interval_ms

    timer = QTimer()
    timer.setInterval(interval_ms)
    timer.timeout.connect(tick)
    timer.start()
    app.exec()
    timer.stop()
    if not state["satisfied"]:
        raise RuntimeError("timed out waiting for condition")
    return state["satisfied"]


class TestIdentifyRealThread(unittest.TestCase):
    """
    Covers _start_identify()/_on_identify_finished() against
    the Virtual ECU Simulator — real QThread, no mocking of
    TestConnectionWorker itself (it's reused unmodified).
    """

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()
        self.window.ui.actionModeBatchFlash.setChecked(True)
        self.window._load_firmware_file(SAMPLE_HEX)

    def test_no_firmware_loaded_blocks_before_starting_identify(self):
        self.window.ui.actionModeFlash.setChecked(True)
        self.window.ui.actionModeBatchFlash.setChecked(True)
        self.window._loaded_datablocks = []

        with unittest.mock.patch(
            "gui.batch_flash.QMessageBox.warning"
        ):
            self.window.flash_button_clicked()

        self.assertIsNone(self.window._identify_thread)

    def test_start_batch_runs_identify_against_virtual_ecu(self):
        self.window.flash_button_clicked()

        self.assertIsNotNone(self.window._identify_thread)

        _run_until(
            self.app,
            lambda: self.window._identify_thread is None,
        )

        self.assertIsNone(self.window._identify_thread)
        self.assertIsNone(self.window._identify_worker)

        # A successful Identify auto-starts a real Flash QThread
        # (see Task 4) — wait for it too, or it's left running
        # when this test method returns and MainWindow gets
        # garbage-collected, which is exactly the historic
        # "QThread: Destroyed while thread is still running"
        # crash this codebase's tests exist to catch.
        _run_until(self.app, lambda: self.window.thread is None)


class TestFullBatchCycleRealThread(unittest.TestCase):
    """
    Identify -> Flash -> PASS/FAIL/ABORTED, end to end, against
    the Virtual ECU Simulator. Real QThread throughout (both
    the Identify probe and the flash itself).
    """

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()
        self.window.ui.actionModeBatchFlash.setChecked(True)
        self.window._load_firmware_file(SAMPLE_HEX)

    def _run_full_cycle(self):
        self.window.flash_button_clicked()  # Start Batch -> Identify
        _run_until(
            self.app,
            lambda: self.window._identify_thread is None,
        )
        # Identify success auto-starts Flash - wait for that too.
        _run_until(
            self.app,
            lambda: self.window.thread is None,
        )

    def test_full_cycle_logs_a_pass_row_and_advances_to_next_ecu(self):
        self._run_full_cycle()

        table = self.window.ui.tableWidgetBatchLog
        self.assertEqual(table.rowCount(), 1)
        self.assertEqual(table.item(0, 0).text(), "1")
        self.assertTrue(len(table.item(0, 1).text()) > 0)  # Serial Number
        self.assertEqual(table.item(0, 3).text(), "PASS")

        self.assertEqual(self.window._batch_counts["pass"], 1)
        self.assertEqual(self.window.ui.flashButton.text(), "Next ECU")
        self.assertEqual(self.window.ui.labelEcuCounter.text(), "ECU #1")

    def test_operator_abort_mid_flash_logs_aborted_not_fail(self):
        # Large payload so there's still a step to abort
        # partway through (Virtual ECU flashes fast otherwise) —
        # same technique as tests/test_flash_threading.py.
        db = Datablock(file_path="synthetic_batch.bin")
        db.segments.append(
            Segment(start_address=0x1000, data=bytes([0xAA]) * 200_000)
        )
        self.window._loaded_datablocks = [db]

        self.window.flash_button_clicked()  # Start Batch -> Identify
        _run_until(
            self.app,
            lambda: self.window._identify_thread is None,
        )
        # Now flashing - click again to Abort.
        self.window.flash_button_clicked()

        _run_until(self.app, lambda: self.window.thread is None)

        table = self.window.ui.tableWidgetBatchLog
        self.assertEqual(table.item(0, 3).text(), "ABORTED")
        self.assertEqual(self.window._batch_counts["abort"], 1)
        self.assertEqual(self.window._batch_counts["fail"], 0)

    def test_step_failure_logs_fail_not_aborted_with_a_reason(self):
        # Deterministic FAIL without needing the simulator to
        # naturally reject anything: force the very first step to
        # report failure, exactly like a real NRC/UDS error would
        # (core/flash_controller.py's own "if not success:" branch
        # - see FlashWorker.run()) - patched at the class level so
        # it applies to the FlashWorker this test's own flash
        # start-up creates.
        with unittest.mock.patch.object(
            FlashWorker, '_execute_step', return_value=False
        ):
            self._run_full_cycle()

        table = self.window.ui.tableWidgetBatchLog
        self.assertEqual(table.item(0, 3).text(), "FAIL")
        self.assertEqual(self.window._batch_counts["fail"], 1)
        self.assertEqual(self.window._batch_counts["abort"], 0)
        self.assertIn(
            "Step failed",
            self.window._batch_records[0]["reason"],
        )


if __name__ == "__main__":
    unittest.main()

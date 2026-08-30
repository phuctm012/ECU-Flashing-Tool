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


if __name__ == "__main__":
    unittest.main()

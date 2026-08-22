# ==================================================
# Flash QThread Lifecycle Tests (crash regression)
# ==================================================
#
# Regression tests for a real crash: "QThread: Destroyed
# while thread is still running" (SIGABRT), hit when running
# the actual app and clicking Flash.
#
# Root cause: on_flash_finished()/on_flash_aborted() in
# gui/flash_tab.py used to set self.thread = None. Those
# slots run off FlashWorker.flash_finished/flash_aborted,
# which FlashWorker emits from INSIDE run() itself — i.e.
# while the worker thread is still actively executing (run()
# hasn't returned yet). Dropping the last Python reference to
# self.thread at that moment destroys the QThread object
# representing the very thread that is running that code,
# which Qt treats as fatal.
#
# IMPORTANT: these tests MUST exercise the real QThread path
# (flash_button_clicked() + a running Qt event loop). Calling
# FlashWorker.run() directly, synchronously (as
# test_flash_controller.py does, for testing the flash logic
# itself) bypasses the QThread/moveToThread glue code entirely
# and would never have caught this bug — that's exactly how it
# went unnoticed through the rest of this project's test
# coverage until a real user hit it.
# ==================================================

import os
import sys
import unittest

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from PySide6.QtCore import QTimer

from tests.qt_test_utils import get_app
from gui.main_window import MainWindow
from parsers.hex_parser import Segment, Datablock, parse_hex_file

SAMPLE_HEX = os.path.join(os.path.dirname(__file__), "sample.hex")


class _ThreadLifecycleTestCase(unittest.TestCase):
    """Shared helpers for driving flash_button_clicked() through a
    real (short-lived) Qt event loop and waiting for cleanup."""

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()

    def _run_event_loop_until(
        self, predicate, timeout_ms=15000, interval_ms=20
    ):
        """
        Pumps the Qt event loop (app.exec()) until `predicate()`
        is true or timeout_ms elapses. Returns True if the
        predicate was satisfied before the timeout.
        """

        state = {"elapsed": 0, "satisfied": False}

        def tick():
            if predicate():
                state["satisfied"] = True
                self.app.quit()
            elif state["elapsed"] >= timeout_ms:
                self.app.quit()
            else:
                state["elapsed"] += interval_ms

        timer = QTimer()
        timer.setInterval(interval_ms)
        timer.timeout.connect(tick)
        timer.start()

        self.app.exec()

        timer.stop()
        return state["satisfied"]

    def _wait_for_cleanup(self, timeout_ms=15000):
        return self._run_event_loop_until(
            lambda: (
                self.window.thread is None
                and self.window.worker is None
            ),
            timeout_ms=timeout_ms,
        )


class TestSingleFlashRun(_ThreadLifecycleTestCase):

    def test_flash_via_real_qthread_does_not_crash(self):
        db = parse_hex_file(SAMPLE_HEX)
        self.window._loaded_datablocks = [db]

        self.window.flash_button_clicked()

        cleaned_up = self._wait_for_cleanup()

        self.assertTrue(
            cleaned_up,
            "flash did not finish + clean up its QThread "
            "within the timeout (hang, or crashed silently)",
        )


class TestRepeatedFlashRuns(_ThreadLifecycleTestCase):
    """
    A real user clicks Flash multiple times across a session.
    Each run must fully clean up its QThread before the next
    one starts, with no crash.
    """

    def test_five_sequential_runs_do_not_crash(self):
        db = parse_hex_file(SAMPLE_HEX)
        self.window._loaded_datablocks = [db]

        RUNS = 5

        for i in range(RUNS):
            self.window.flash_button_clicked()
            cleaned_up = self._wait_for_cleanup()
            self.assertTrue(
                cleaned_up,
                f"run {i + 1}/{RUNS} did not clean up in time",
            )


class TestAbortMidFlash(_ThreadLifecycleTestCase):

    def test_abort_shortly_after_start_does_not_crash(self):
        # Large payload so there are still steps left to abort
        # partway through (Virtual ECU flashes fast otherwise).
        db = Datablock(file_path="synthetic.bin")
        db.segments.append(
            Segment(start_address=0x1000, data=bytes([0xAA]) * 200_000)
        )
        self.window._loaded_datablocks = [db]

        self.window.flash_button_clicked()  # start

        QTimer.singleShot(
            80, self.window.flash_button_clicked
        )  # click again -> Abort branch

        cleaned_up = self._wait_for_cleanup()

        self.assertTrue(
            cleaned_up, "abort did not clean up in time"
        )


class TestCloseWindowMidFlash(_ThreadLifecycleTestCase):
    """
    Closing the main window while a flash is running goes
    through MainWindow.closeEvent() — a different code path
    than clicking Abort (request_abort() + thread.quit() +
    thread.wait(), all synchronous, right before the window
    actually closes). Must not crash either.
    """

    def test_close_event_mid_flash_does_not_crash(self):
        db = Datablock(file_path="synthetic.bin")
        db.segments.append(
            Segment(start_address=0x1000, data=bytes([0xAA]) * 200_000)
        )
        self.window._loaded_datablocks = [db]

        self.window.flash_button_clicked()  # start

        def close_window():
            from PySide6.QtGui import QCloseEvent
            self.window.closeEvent(QCloseEvent())

        QTimer.singleShot(80, close_window)

        # closeEvent() itself blocks (thread.wait()) until the
        # abort has fully completed, so by the time the 80ms
        # timer callback returns, cleanup should already be
        # done — but thread.finished -> _cleanup_thread is
        # still delivered async, so poll briefly regardless.
        cleaned_up = self._wait_for_cleanup(timeout_ms=5000)

        self.assertTrue(
            cleaned_up, "close-mid-flash did not clean up in time"
        )


if __name__ == "__main__":
    unittest.main()

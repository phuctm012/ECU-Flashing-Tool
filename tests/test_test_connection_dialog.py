# ==================================================
# TestConnectionDialog QThread Lifecycle Tests
# ==================================================
#
# Same threading-crash class as tests/test_flash_threading.py
# (see that file's docstring for the full "QThread: Destroyed
# while thread is still running" backstory) — TestConnection
# Dialog follows the identical rule (never touch self._thread/
# self._worker from a slot connected to worker.finished, only
# from _cleanup_thread() via thread.finished), so it needs the
# same real-QThread-plus-running-event-loop test treatment
# rather than calling TestConnectionWorker.run() directly
# (that's tests/test_test_connection.py's job).
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
from gui.test_connection_dialog import TestConnectionDialog


class _DialogThreadLifecycleTestCase(unittest.TestCase):

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()

    def _run_event_loop_until(
        self, predicate, timeout_ms=15000, interval_ms=20
    ):
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


class TestSingleProbeRun(_DialogThreadLifecycleTestCase):

    def test_probe_via_real_qthread_does_not_crash(self):
        dialog = TestConnectionDialog(
            self.window, True, None, False, {}
        )

        cleaned_up = self._run_event_loop_until(
            lambda: dialog._thread is None
        )

        self.assertTrue(
            cleaned_up,
            "probe did not finish + clean up its QThread "
            "within the timeout (hang, or crashed silently)",
        )
        self.assertIn("PASSED", dialog.logText.toPlainText())

        dialog.close()


class TestRepeatedProbeRuns(_DialogThreadLifecycleTestCase):

    def test_five_sequential_runs_do_not_crash(self):
        RUNS = 5

        for i in range(RUNS):
            dialog = TestConnectionDialog(
                self.window, True, None, False, {}
            )
            cleaned_up = self._run_event_loop_until(
                lambda: dialog._thread is None
            )
            self.assertTrue(
                cleaned_up,
                f"run {i + 1}/{RUNS} did not clean up in time",
            )
            dialog.close()


class TestCloseDialogMidProbe(_DialogThreadLifecycleTestCase):

    def test_close_event_mid_probe_does_not_crash(self):
        dialog = TestConnectionDialog(
            self.window, True, None, True, {}
        )

        def close_dialog():
            from PySide6.QtGui import QCloseEvent
            dialog.closeEvent(QCloseEvent())

        # Fire almost immediately — the probe is fast, so this
        # mostly exercises closeEvent()'s thread.wait() path
        # rather than truly interrupting mid-request.
        QTimer.singleShot(5, close_dialog)

        cleaned_up = self._run_event_loop_until(
            lambda: dialog._thread is None
        )

        self.assertTrue(
            cleaned_up, "close-mid-probe did not clean up in time"
        )


if __name__ == "__main__":
    unittest.main()

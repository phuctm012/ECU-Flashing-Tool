import os
import sys
import unittest

from PySide6.QtCore import QTimer

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from tests.qt_test_utils import get_app
from gui.main_window import MainWindow


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


class TestPerPanelIdentify(unittest.TestCase):

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()

    def test_identify_succeeds_against_virtual_ecu_and_captures_serial(self):
        panel = self.window._parallel_panels[0]
        panel["combo"].setCurrentIndex(1)  # Virtual ECU Simulator

        self.window._start_identify_for_panel(panel)
        self.assertEqual(panel["phase"], "identifying")

        _run_until(self.app, lambda: panel["identify_thread"] is None)

        self.assertIsNotNone(panel["serial"])
        self.assertNotEqual(panel["phase"], "identifying")


if __name__ == "__main__":
    unittest.main()

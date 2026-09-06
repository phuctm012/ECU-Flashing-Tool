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


class TestPerPanelFlash(unittest.TestCase):

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()
        ok = self.window._load_firmware_file(
            os.path.join(
                os.path.dirname(__file__), "sample.hex"
            )
        )
        assert ok

    def test_full_identify_then_flash_reaches_pass(self):
        panel = self.window._parallel_panels[0]
        panel["combo"].setCurrentIndex(1)  # Virtual ECU Simulator

        self.window._start_identify_for_panel(panel)
        _run_until(self.app, lambda: panel["identify_thread"] is None)
        _run_until(self.app, lambda: panel["flash_thread"] is None)

        self.assertEqual(panel["phase"], "pass")
        self.assertEqual(panel["progress_bar"].value(), 100)

    def test_second_panel_can_flash_independently(self):
        # Only this panel gets a channel selected/started - proves
        # a single panel's flow doesn't depend on any other panel's
        # state existing.
        panel = self.window._parallel_panels[2]
        panel["combo"].setCurrentIndex(1)

        self.window._start_identify_for_panel(panel)
        _run_until(self.app, lambda: panel["identify_thread"] is None)
        _run_until(self.app, lambda: panel["flash_thread"] is None)

        self.assertEqual(panel["phase"], "pass")
        for other in self.window._parallel_panels:
            if other is not panel:
                self.assertEqual(other["phase"], "idle")


class TestAbortAndStartAll(unittest.TestCase):

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()
        ok = self.window._load_firmware_file(
            os.path.join(os.path.dirname(__file__), "sample.hex")
        )
        assert ok

    def test_abort_mid_flash_settles_panel_without_crash(self):
        from parsers.hex_parser import Segment, Datablock
        db = Datablock(file_path="synthetic_parallel.bin")
        db.segments.append(
            Segment(start_address=0x1000, data=bytes([0xAA]) * 200_000)
        )
        self.window._loaded_datablocks = [db]

        panel = self.window._parallel_panels[0]
        panel["combo"].setCurrentIndex(1)
        self.window._start_identify_for_panel(panel)
        _run_until(self.app, lambda: panel["identify_thread"] is None)
        self.assertEqual(panel["phase"], "flashing")

        self.window._abort_panel(panel)
        _run_until(self.app, lambda: panel["flash_thread"] is None)
        self.app.processEvents()

        self.assertIn(panel["phase"], ("fail", "idle"))
        self.assertEqual(panel["flash_button"].text(), "Flash")

    def test_start_all_only_starts_panels_with_a_channel_selected(self):
        panels = self.window._parallel_panels
        panels[0]["combo"].setCurrentIndex(1)  # Virtual
        panels[1]["combo"].setCurrentIndex(1)  # Virtual
        # panels[2], panels[3] left on "Not Selected"

        self.window.parallel_start_all()

        self.assertEqual(panels[0]["phase"], "identifying")
        self.assertEqual(panels[1]["phase"], "identifying")
        self.assertEqual(panels[2]["phase"], "idle")
        self.assertEqual(panels[3]["phase"], "idle")

        # flash_thread starts out None before flashing even begins,
        # so waiting on it alone could return immediately, on the
        # very first tick, before Identify has even run - wait for
        # both Identify threads to finish first (guaranteeing
        # _start_flash_for_panel() has already run, same as
        # TestPerPanelFlash's two-step wait).
        _run_until(
            self.app,
            lambda: panels[0]["identify_thread"] is None
            and panels[1]["identify_thread"] is None,
        )
        _run_until(
            self.app,
            lambda: panels[0]["flash_thread"] is None
            and panels[1]["flash_thread"] is None,
        )
        self.assertEqual(panels[0]["phase"], "pass")
        self.assertEqual(panels[1]["phase"], "pass")


if __name__ == "__main__":
    unittest.main()

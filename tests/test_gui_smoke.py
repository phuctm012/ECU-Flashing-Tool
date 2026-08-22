# ==================================================
# GUI Smoke Tests
# ==================================================
#
# MainWindow construction, key widgets, CAN config
# reading (Radar Side / hardware channel / CAN FD), and
# the Save Log mechanics (.txt for Information, .csv for
# Trace) — all without popping any real dialogs.
# ==================================================

import csv
import os
import sys
import tempfile
import unittest

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from tests.qt_test_utils import get_app
from gui.main_window import MainWindow


class TestMainWindowConstruction(unittest.TestCase):

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()

    def test_key_widgets_exist(self):
        expected = [
            "flashButton", "progressBar",
            "stepsTable", "segmentsTable",
            "informationText", "traceTable",
            "comboBoxHardware", "comboBoxRadarSide",
            "comboBoxLogicalLink", "tableWidgetCommDetails",
            "tableWidgetCustomConfig", "comboBoxFlashSequence",
            "lineEditSecurityDll", "buttonBrowseSecurityDll",
            "navListWidget", "stackedWidget",
        ]
        for name in expected:
            self.assertTrue(
                hasattr(self.window.ui, name),
                f"missing widget: {name}",
            )

    def test_trace_table_has_six_columns(self):
        self.assertEqual(
            self.window.ui.traceTable.columnCount(), 6
        )

    def test_trace_table_starts_empty(self):
        self.assertEqual(
            self.window.ui.traceTable.rowCount(), 0
        )

    def test_custom_config_table_fixed_height(self):
        table = self.window.ui.tableWidgetCustomConfig
        self.assertEqual(
            table.minimumHeight(), table.maximumHeight()
        )

    def test_flash_sequence_combo_options(self):
        combo = self.window.ui.comboBoxFlashSequence
        texts = [combo.itemText(i) for i in range(combo.count())]
        self.assertTrue(any("Generic" in t for t in texts))
        self.assertTrue(any("Suzuki" in t for t in texts))

    def test_nav_list_maps_to_stacked_widget(self):
        nav = self.window.ui.navListWidget
        stacked = self.window.ui.stackedWidget
        for row in range(nav.count()):
            nav.setCurrentRow(row)
            self.assertEqual(stacked.currentIndex(), row)


class TestCanConfig(unittest.TestCase):

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()

    def test_default_is_radar_side_left(self):
        config = self.window.get_can_config()
        self.assertEqual(config["tx_id"], 0x77B)
        self.assertEqual(config["rx_id"], 0x78B)
        self.assertEqual(config["channel"], 0)
        self.assertFalse(config["fd"])

    def test_radar_side_right(self):
        self.window.ui.comboBoxRadarSide.setCurrentIndex(1)
        config = self.window.get_can_config()
        self.assertEqual(config["tx_id"], 0x77A)
        self.assertEqual(config["rx_id"], 0x78A)

    def test_channel_parsed_from_hardware_combo(self):
        combo = self.window.ui.comboBoxHardware
        for i in range(combo.count()):
            if "Channel 2" in combo.itemText(i):
                combo.setCurrentIndex(i)
                break
        config = self.window.get_can_config()
        self.assertEqual(config["channel"], 1)

    def test_radar_side_survives_can_fd_switch(self):
        self.window.ui.comboBoxRadarSide.setCurrentIndex(1)
        self.window.ui.comboBoxLogicalLink.setCurrentIndex(1)  # CAN FD
        self.window.ui.comboBoxLogicalLink.setCurrentIndex(0)  # back to CAN
        config = self.window.get_can_config()
        self.assertEqual(config["tx_id"], 0x77A)

    def test_can_fd_reflected_in_config(self):
        self.window.ui.comboBoxLogicalLink.setCurrentIndex(1)  # CAN FD
        config = self.window.get_can_config()
        self.assertTrue(config["fd"])

    def test_editing_can_id_table_overrides_value(self):
        table = self.window.ui.tableWidgetCommDetails
        for row in range(table.rowCount()):
            if table.item(row, 0).text() == "Physical Request CAN ID":
                from PySide6.QtWidgets import QTableWidgetItem
                table.setItem(row, 1, QTableWidgetItem("0x7E0"))
        config = self.window.get_can_config()
        self.assertEqual(config["tx_id"], 0x7E0)


class TestLogSaving(unittest.TestCase):

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()

    def test_information_log_saves_as_txt(self):
        self.window.log_information("hello world")

        with tempfile.NamedTemporaryFile(
            suffix=".txt", delete=False
        ) as f:
            path = f.name

        try:
            self.window._write_log_file(
                self.window.ui.informationText, path
            )
            with open(path, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("hello world", content)
        finally:
            os.unlink(path)

    def test_trace_row_saves_as_csv_with_correct_header(self):
        self.window.log_trace_row({
            "req_ts": 0.01234, "req_target": "0x77B",
            "req_data": "10 03",
            "resp_ts": 0.02345, "resp_source": "0x78B",
            "resp_data": "50 03",
        })
        self.window.log_trace("Executing: test step")

        with tempfile.NamedTemporaryFile(
            suffix=".csv", delete=False
        ) as f:
            path = f.name

        try:
            self.window._write_trace_table_csv(path)

            with open(path, newline="", encoding="utf-8") as f:
                rows = list(csv.reader(f))

            self.assertEqual(rows[0], [
                "Request TimeStamp", "Request Target",
                "Request Data", "Response TimeStamp",
                "Response Source", "Response Data",
            ])
            self.assertIn("0x77B", rows[1])
            self.assertIn("10 03", rows[1])
            self.assertIn("0x78B", rows[1])
            self.assertEqual(rows[2][1], "SYSTEM")
        finally:
            os.unlink(path)

    def test_write_log_file_failure_does_not_raise(self):
        # Writing to a directory (not a file) must be handled
        # gracefully (an OSError caught internally), not
        # propagate an exception. QMessageBox.critical is
        # patched to a no-op so this doesn't pop a real modal
        # dialog and hang a headless test run.
        import gui.main_window as main_window_module

        original = main_window_module.QMessageBox.critical
        main_window_module.QMessageBox.critical = (
            lambda *a, **k: None
        )

        try:
            self.window.log_information("hello")
            try:
                self.window._write_log_file(
                    self.window.ui.informationText, "/"
                )
            except Exception as e:  # noqa: BLE001
                self.fail(
                    f"_write_log_file raised unexpectedly: {e}"
                )
        finally:
            main_window_module.QMessageBox.critical = original


if __name__ == "__main__":
    unittest.main()

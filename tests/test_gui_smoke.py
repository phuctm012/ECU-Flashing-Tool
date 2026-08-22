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
import unittest.mock

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from PySide6.QtWidgets import QLabel

from tests.qt_test_utils import get_app
from gui.main_window import MainWindow
from config.settings import APP_NAME, APP_VERSION


class TestMainWindowConstruction(unittest.TestCase):

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()

    def test_window_title_shows_name_and_version(self):
        self.assertEqual(
            self.window.windowTitle(), f"{APP_NAME} v{APP_VERSION}"
        )

    def test_status_bar_shows_version_bottom_left(self):
        # addWidget() (not addPermanentWidget()) puts a QStatusBar
        # child on the left/normal side — verifies the version
        # label is actually there and shows the right text.
        labels = self.window.ui.statusbar.findChildren(QLabel)
        found = [
            w for w in labels if f"v{APP_VERSION}" in w.text()
        ]
        self.assertTrue(found, "Version label not found in status bar")

    def test_key_widgets_exist(self):
        expected = [
            "flashButton", "progressBar",
            "stepsTable", "segmentsTable",
            "informationText", "traceTable",
            "comboBoxHardware", "buttonRefreshHardware",
            "comboBoxRadarSide",
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

    def test_flash_sequence_combo_defaults_to_suzuki(self):
        combo = self.window.ui.comboBoxFlashSequence
        self.assertIn("Suzuki", combo.currentText())

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

    def test_default_is_radar_side_s0(self):
        config = self.window.get_can_config()
        self.assertEqual(config["tx_id"], 0x77B)
        self.assertEqual(config["rx_id"], 0x78B)
        self.assertEqual(config["channel"], 0)
        self.assertFalse(config["fd"])

    def test_radar_side_s1(self):
        self.window.ui.comboBoxRadarSide.setCurrentIndex(1)
        config = self.window.get_can_config()
        self.assertEqual(config["tx_id"], 0x77A)
        self.assertEqual(config["rx_id"], 0x78A)

    def test_hardware_combo_has_only_virtual_when_no_real_hw(self):
        # No python-can/Vector driver in the test environment,
        # so detect_vector_channels() returns [] — the combo
        # must NOT contain any hardcoded placeholder channels.
        combo = self.window.ui.comboBoxHardware
        self.assertEqual(combo.count(), 1)
        self.assertIn("Virtual", combo.itemText(0))
        self.assertIsNone(combo.itemData(0))

    def test_channel_read_from_hardware_combo_userdata(self):
        # Simulates what populate_hardware_combo() would add
        # for a real detected channel (userData = channel
        # index), and confirms get_can_config() reads it —
        # not by parsing the display text.
        combo = self.window.ui.comboBoxHardware
        combo.addItem("VN1640A - Channel 2", userData=1)
        combo.setCurrentIndex(combo.count() - 1)
        config = self.window.get_can_config()
        self.assertEqual(config["channel"], 1)

    def test_refresh_hardware_button_repopulates_without_crash(self):
        self.window.ui.buttonRefreshHardware.click()
        combo = self.window.ui.comboBoxHardware
        self.assertEqual(combo.count(), 1)
        self.assertIn("Virtual", combo.currentText())

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


class TestCanConflictWarning(unittest.TestCase):
    """
    Covers ConfigureTabMixin.detect_can_conflict_warning() —
    the check that warns before a real-hardware flash if a
    Vector desktop tool (CANoe/CANalyzer/CANape) looks like
    it's already running, since users can forget it's open
    (see docs/walkthrough.md Phase 4.23). Mocks the two
    underlying signals (no real Vector tools/hardware in this
    dev/test environment).
    """

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()

    def test_no_warning_when_nothing_detected(self):
        with unittest.mock.patch(
            "communication.vector_can.detect_running_vector_tools",
            return_value=[],
        ), unittest.mock.patch(
            "communication.vector_can.detect_vector_channels",
            return_value=[],
        ):
            self.assertIsNone(
                self.window.detect_can_conflict_warning()
            )

    def test_warns_when_vector_tool_running(self):
        with unittest.mock.patch(
            "communication.vector_can.detect_running_vector_tools",
            return_value=["canoe"],
        ), unittest.mock.patch(
            "communication.vector_can.detect_vector_channels",
            return_value=[],
        ):
            warning = self.window.detect_can_conflict_warning()
        self.assertIsNotNone(warning)
        self.assertIn("CANOE", warning)

    def test_warns_when_selected_channel_is_on_bus(self):
        combo = self.window.ui.comboBoxHardware
        combo.addItem("VN1640A - Channel 1", userData=0)
        combo.setCurrentIndex(combo.count() - 1)

        with unittest.mock.patch(
            "communication.vector_can.detect_running_vector_tools",
            return_value=[],
        ), unittest.mock.patch(
            "communication.vector_can.detect_vector_channels",
            return_value=[
                {"label": "VN1640A - Channel 1",
                 "channel": 0, "is_on_bus": True},
            ],
        ):
            warning = self.window.detect_can_conflict_warning()
        self.assertIsNotNone(warning)
        self.assertIn("VN1640A - Channel 1", warning)

    def test_helper_does_not_filter_by_selected_hardware(self):
        # detect_can_conflict_warning() itself always reports a
        # running Vector tool if one is found — it's
        # flash_button_clicked() that skips calling it at all
        # when Virtual ECU Simulator is selected, so a running
        # tool still surfaces here even with the combo left on
        # its Virtual default.
        with unittest.mock.patch(
            "communication.vector_can.detect_running_vector_tools",
            return_value=["canoe"],
        ), unittest.mock.patch(
            "communication.vector_can.detect_vector_channels",
            return_value=[],
        ):
            warning = self.window.detect_can_conflict_warning()
        self.assertIsNotNone(warning)


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

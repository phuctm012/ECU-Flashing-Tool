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
import json
import os
import sys
import tempfile
import unittest
import unittest.mock
import zipfile

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from PySide6.QtWidgets import QLabel, QTableWidgetItem, QMessageBox
from PySide6.QtCore import Qt, QPoint

from tests.qt_test_utils import get_app
from gui.main_window import MainWindow
from gui.flash_tab import (
    STEPS_PLACEHOLDER_TEXT,
    SEGMENTS_PLACEHOLDER_TEXT,
)
from config.settings import (
    APP_NAME,
    APP_VERSION,
    STATUS_COLOR_DONE,
    STATUS_COLOR_DONE_DARK,
    STATUS_TEXT_COLOR,
    STATUS_TEXT_COLOR_DARK,
)
from parsers.auto_parser import parse_firmware_file

SAMPLE_HEX = os.path.join(os.path.dirname(__file__), "sample.hex")


class TestMainWindowConstruction(unittest.TestCase):

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()

    def test_window_title_shows_name_and_version(self):
        self.assertEqual(
            self.window.windowTitle(), f"{APP_NAME} v{APP_VERSION}"
        )

    def test_window_icon_is_set(self):
        # docs/gui_todo.md item #12 — the app used to run with no
        # window/taskbar icon at all (windowIcon() null).
        self.assertFalse(self.window.windowIcon().isNull())

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

    def test_steps_and_segments_tables_start_with_placeholder(self):
        # docs/gui_todo.md item #16 — previously these tables
        # were just blank boxes before a flash ever ran.
        steps = self.window.ui.stepsTable
        segments = self.window.ui.segmentsTable
        self.assertEqual(steps.rowCount(), 1)
        self.assertEqual(steps.item(0, 0).text(), STEPS_PLACEHOLDER_TEXT)
        self.assertEqual(segments.rowCount(), 1)
        self.assertEqual(
            segments.item(0, 0).text(), SEGMENTS_PLACEHOLDER_TEXT
        )

    def test_add_step_clears_placeholder_on_first_real_step(self):
        self.window.add_step("Session Control")
        table = self.window.ui.stepsTable
        self.assertEqual(table.rowCount(), 1)
        self.assertEqual(table.item(0, 1).text(), "Session Control")

    def test_section_header_labels_tagged_for_theme_qss(self):
        # docs/gui_todo.md item #15 — these used to hardcode a
        # light-only "background-color: #E0E0E0" inline
        # styleSheet, unreadable once Dark Mode existed.
        for name in (
            "labelDatablocks", "labelDetails", "labelHardware",
            "labelRadarSide", "labelLogicalLink",
            "labelFlashSequence", "labelSecurityDll",
            "labelCustomConfig",
        ):
            label = getattr(self.window.ui, name)
            self.assertTrue(
                label.property("sectionHeader"),
                f"{name} missing sectionHeader property",
            )

    def test_progress_change_targets_animation_not_instant_jump(self):
        # docs/gui_todo.md item #13 — progressBar.setValue() is no
        # longer called directly from on_progress_changed(); the
        # animation is retargeted instead and settles asynchronously.
        self.window.on_progress_changed(42)
        self.assertEqual(
            self.window._progress_animation.endValue(), 42
        )

    def test_custom_config_table_fixed_height(self):
        table = self.window.ui.tableWidgetCustomConfig
        self.assertEqual(
            table.minimumHeight(), table.maximumHeight()
        )

    def test_colored_step_rows_have_explicit_readable_text_color(self):
        # In Light theme, the default text color already matches
        # STATUS_TEXT_COLOR — but every cell that gets a status
        # background must still get an explicit foreground, since
        # Dark Mode's default text would otherwise be unreadable
        # against these light pastel backgrounds (#FCE9B5/#D3E9D6/
        # #F3D0D3). Forcing light mode here isolates that pairing;
        # see TestStatusColorsFollowLiveTheme for the dark pairing.
        self.window._dark_mode_active = False
        self.window.add_step("Session Control")
        self.window.add_step("Security Access")
        current = self.window.ui.stepsTable.item(1, 1)
        previous = self.window.ui.stepsTable.item(0, 1)
        self.assertEqual(
            current.foreground().color().name(), STATUS_TEXT_COLOR
        )
        self.assertEqual(
            previous.foreground().color().name(), STATUS_TEXT_COLOR
        )

    def test_flash_finished_and_aborted_rows_have_readable_text_color(self):
        self.window._dark_mode_active = False
        self.window.prepare_flash_ui([])  # sets self.start_time
        self.window.add_step("Session Control")

        self.window.on_flash_finished()
        item = self.window.ui.stepsTable.item(0, 1)
        self.assertEqual(
            item.foreground().color().name(), STATUS_TEXT_COLOR
        )

        self.window.add_step("Retry")
        self.window.on_flash_aborted()
        aborted_item = self.window.ui.stepsTable.item(1, 1)
        self.assertEqual(
            aborted_item.foreground().color().name(), STATUS_TEXT_COLOR
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
        # for a real detected channel (userData = the full
        # channel dict from detect_vector_channels()), and
        # confirms get_can_config() reads it — not by parsing
        # the display text.
        combo = self.window.ui.comboBoxHardware
        combo.addItem(
            "VN1640A - Channel 2",
            userData={
                "label": "VN1640A - Channel 2",
                "channel": 1, "hw_channel": 1,
                "serial": None, "is_on_bus": False,
            },
        )
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


class TestDataFormatConfig(unittest.TestCase):
    """
    Covers the Compression Method/Encryption Method fields
    (ConfigureTabMixin.get_data_format_config() and
    lineEditCompressionMethod/lineEditEncryptionMethod) — these
    only change RequestDownload's dataFormatIdentifier byte, not
    any real data transform (see
    core/flash_controller.py/communication/uds_client.py for
    where the value actually reaches the wire). Single-hex-digit
    text fields (not a dropdown), embedded directly into
    tableWidgetDetails' rows 2/3 (Value column) via
    setCellWidget(), built in Python since Designer's .ui format
    can't express a widget embedded in a specific table cell —
    see ConfigureTabMixin._setup_data_format_inputs().
    """

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()

    def test_defaults_to_none_none(self):
        config = self.window.get_data_format_config()
        self.assertEqual(
            config, {"compression": 0, "encrypting": 0}
        )

    def test_field_text_is_the_nibble_value(self):
        self.window.ui.lineEditCompressionMethod.setText("3")
        self.window.ui.lineEditEncryptionMethod.setText("A")

        config = self.window.get_data_format_config()

        self.assertEqual(
            config, {"compression": 3, "encrypting": 10}
        )

    def test_fields_are_embedded_in_details_table_rows(self):
        details = self.window.ui.tableWidgetDetails

        self.assertIs(
            details.cellWidget(2, 1),
            self.window.ui.lineEditCompressionMethod,
        )
        self.assertIs(
            details.cellWidget(3, 1),
            self.window.ui.lineEditEncryptionMethod,
        )
        self.assertEqual(
            details.item(2, 0).text(), "Compression Method"
        )
        self.assertEqual(
            details.item(3, 0).text(), "Encryption Method"
        )

    def test_details_table_shows_zero_at_default(self):
        details = self.window.ui.tableWidgetDetails
        self.assertEqual(details.cellWidget(2, 1).text(), "0")
        self.assertEqual(details.cellWidget(3, 1).text(), "0")

    def test_lowercase_input_is_auto_uppercased(self):
        field = self.window.ui.lineEditEncryptionMethod
        field.setText("")
        field.textEdited.emit("b")

        self.assertEqual(field.text(), "B")
        self.assertEqual(
            self.window.get_data_format_config()["encrypting"], 11
        )

    def test_validator_rejects_out_of_range_characters(self):
        field = self.window.ui.lineEditCompressionMethod
        validator = field.validator()

        self.assertIsNotNone(validator)
        for char in ("G", "g", "Z", "-", " "):
            state, _, _ = validator.validate(char, 0)
            self.assertNotEqual(
                state, validator.State.Acceptable, char
            )
        for char in "0123456789ABCDEFabcdef":
            state, _, _ = validator.validate(char, 0)
            self.assertEqual(
                state, validator.State.Acceptable, char
            )

    def test_field_max_length_is_one(self):
        self.assertEqual(
            self.window.ui.lineEditCompressionMethod.maxLength(), 1
        )
        self.assertEqual(
            self.window.ui.lineEditEncryptionMethod.maxLength(), 1
        )

    def test_loading_firmware_does_not_disturb_data_format_selection(self):
        # Compression/Encryption are a persistent global setting,
        # not a per-datablock property — loading a file must not
        # reset whatever the user already picked.
        self.window.ui.lineEditCompressionMethod.setText("3")

        self.window._load_firmware_file(SAMPLE_HEX)
        self.window._update_details_table(
            self.window._loaded_datablocks[-1]
        )

        self.assertEqual(
            self.window.ui.lineEditCompressionMethod.text(), "3"
        )
        self.assertEqual(
            self.window.ui.tableWidgetDetails.cellWidget(
                2, 1
            ).text(),
            "3",
        )

    def test_removing_last_datablock_does_not_clear_data_format_selection(self):
        self.window.ui.lineEditEncryptionMethod.setText("7")
        self.window._load_firmware_file(SAMPLE_HEX)

        self.window._remove_datablock_row(0)

        self.assertEqual(
            self.window.ui.lineEditEncryptionMethod.text(), "7"
        )


class TestFingerprintConfig(unittest.TestCase):
    """
    Covers the Tester Serial Number field (Configure ->
    Miscellaneous -> Fingerprint, lineEditTesterSerialNumber /
    ConfigureTabMixin.get_tester_serial_number()) — the DID
    0xF198 WriteDataByIdentifier payload sent by the Suzuki SLP1
    sequence's "Write Tester Info" step (core/flash_sequence.py).
    Declared directly in main_window.ui (unlike Compression/
    Encryption Method, this isn't embedded in a table cell, so
    there's no Designer limitation forcing Python construction).
    """

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()

    def test_defaults_to_documented_value(self):
        self.assertEqual(
            self.window.ui.lineEditTesterSerialNumber.text(),
            "00112233445566778899",
        )
        self.assertEqual(
            self.window.get_tester_serial_number(),
            bytes.fromhex("00112233445566778899"),
        )

    def test_field_text_is_the_payload(self):
        self.window.ui.lineEditTesterSerialNumber.setText(
            "AABBCCDDEE0011223344"
        )
        self.assertEqual(
            self.window.get_tester_serial_number(),
            bytes.fromhex("AABBCCDDEE0011223344"),
        )

    def test_lowercase_input_is_auto_uppercased(self):
        field = self.window.ui.lineEditTesterSerialNumber
        field.setText("")
        field.textEdited.emit("ab")

        self.assertEqual(field.text(), "AB")

    def test_validator_rejects_non_hex_characters(self):
        field = self.window.ui.lineEditTesterSerialNumber
        validator = field.validator()

        self.assertIsNotNone(validator)
        for char in ("G", "Z", "-", " "):
            state, _, _ = validator.validate(char, 0)
            self.assertNotEqual(
                state, validator.State.Acceptable, char
            )
        for char in "0123456789ABCDEFabcdef":
            state, _, _ = validator.validate(char, 0)
            self.assertEqual(
                state, validator.State.Acceptable, char
            )

    def test_field_max_length_is_twenty(self):
        self.assertEqual(
            self.window.ui.lineEditTesterSerialNumber.maxLength(),
            20,
        )

    def test_odd_length_falls_back_to_default(self):
        # Mid-edit state (e.g. user just deleted the last
        # character) — must never raise or send a malformed
        # payload; falls back to the documented default instead.
        self.window.ui.lineEditTesterSerialNumber.setText("00112")
        self.assertEqual(
            self.window.get_tester_serial_number(),
            bytes.fromhex("00112233445566778899"),
        )

    def test_empty_field_falls_back_to_default(self):
        self.window.ui.lineEditTesterSerialNumber.setText("")
        self.assertEqual(
            self.window.get_tester_serial_number(),
            bytes.fromhex("00112233445566778899"),
        )


class TestHardwareComboDetectionError(unittest.TestCase):
    """
    Covers populate_hardware_combo() logging the real reason to
    the Information tab when Vector hardware detection fails for
    an actual environment reason (not just "nothing plugged
    in") — added after a real deployment where the same .exe
    worked on one bench PC and silently found nothing on
    another, with no console available (--windowed build) to see
    why.
    """

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()

    def test_detection_error_is_logged(self):
        with unittest.mock.patch(
            "communication.vector_can.detect_vector_channels_with_error",
            return_value=([], "Vector XL Driver Library error: boom"),
        ):
            self.window.populate_hardware_combo()

        self.assertIn(
            "Vector XL Driver Library error: boom",
            self.window.ui.informationText.toPlainText(),
        )

    def test_no_hardware_plugged_in_logs_nothing(self):
        self.window.ui.informationText.clear()
        with unittest.mock.patch(
            "communication.vector_can.detect_vector_channels_with_error",
            return_value=([], None),
        ):
            self.window.populate_hardware_combo()

        self.assertEqual(
            self.window.ui.informationText.toPlainText(), ""
        )

    def test_successful_detection_logs_nothing(self):
        self.window.ui.informationText.clear()
        channel = {
            "label": "VN1640A - Channel 1", "channel": 0,
            "hw_channel": 0, "serial": None, "is_on_bus": False,
        }
        with unittest.mock.patch(
            "communication.vector_can.detect_vector_channels_with_error",
            return_value=([channel], None),
        ):
            self.window.populate_hardware_combo()

        self.assertEqual(
            self.window.ui.informationText.toPlainText(), ""
        )
        self.assertEqual(self.window.ui.comboBoxHardware.count(), 2)


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
        combo.addItem(
            "VN1640A - Channel 1",
            userData={
                "label": "VN1640A - Channel 1",
                "channel": 0, "hw_channel": 0,
                "serial": None, "is_on_bus": True,
            },
        )
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


class TestUpdateSegmentsColors(unittest.TestCase):
    """
    Covers update_segments()'s background/foreground coloring.
    The "Waiting" state must not force a plain white background
    (unreadable against Dark Mode's near-white default text —
    docs/gui_todo.md item #15), and Flashed/Flashing rows must
    pair their light pastel background with an explicit dark
    foreground for the same reason.
    """

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()
        db = parse_firmware_file(SAMPLE_HEX)  # 2 segments
        self.window.add_segments_from_datablocks([db])

    def test_flashed_and_flashing_rows_get_explicit_text_color(self):
        self.window._dark_mode_active = False
        self.window.update_segments(50)
        table = self.window.ui.segmentsTable
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            self.assertEqual(
                item.foreground().color().name(), STATUS_TEXT_COLOR
            )

    def test_waiting_row_background_is_transparent_not_forced_white(self):
        self.window.update_segments(0)
        table = self.window.ui.segmentsTable
        # Only the first segment is "Flashing..." at progress 0 —
        # the second is still "Waiting".
        waiting_item = table.item(1, 0)
        self.assertEqual(waiting_item.text(), "Waiting")
        self.assertEqual(waiting_item.background().color().alpha(), 0)


class TestStatusColorsFollowLiveTheme(unittest.TestCase):
    """
    Covers _status_colors() (gui/flash_tab.py) — Dark Mode uses
    dark-tinted status backgrounds with bright text instead of
    the light theme's light pastel blocks, which looked like
    bright stickers against Dark Mode's navy background (user
    feedback after trying a real flash run in Dark Mode). Also
    covers that this follows self._dark_mode_active *live*
    (toggled by gui/menu_bar.py's action_toggle_dark_mode() on
    every View > Dark Mode click), not just whichever theme was
    active when MainWindow was constructed.
    """

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()

    def test_light_mode_active_by_default_uses_light_status_colors(self):
        # Fresh MainWindow, never toggled — Light Mode is the app
        # default. add_step() colors the new row "running" and the
        # previous row "done" — 2 calls to get a "done" row.
        self.window.add_step("Session Control")
        self.window.add_step("Security Access")
        done_item = self.window.ui.stepsTable.item(0, 1)
        self.assertEqual(
            done_item.background().color().name(), STATUS_COLOR_DONE.lower()
        )
        self.assertEqual(
            done_item.foreground().color().name(), STATUS_TEXT_COLOR
        )

    def test_dark_mode_uses_dark_status_colors(self):
        self.window._dark_mode_active = True
        self.window.add_step("Session Control")
        self.window.add_step("Security Access")
        done_item = self.window.ui.stepsTable.item(0, 1)
        self.assertEqual(
            done_item.background().color().name(), STATUS_COLOR_DONE_DARK
        )
        self.assertEqual(
            done_item.foreground().color().name(), STATUS_TEXT_COLOR_DARK
        )

    def test_toggling_theme_mid_session_recolors_new_rows(self):
        # Same window, no re-construction — proves the color
        # choice is read live on each call, not cached once at
        # startup/construction time.
        self.window._dark_mode_active = False
        self.window.add_step("Step A")
        self.window.add_step("Step B")
        light_item = self.window.ui.stepsTable.item(0, 1)
        self.assertEqual(
            light_item.background().color().name(), STATUS_COLOR_DONE.lower()
        )

        self.window._dark_mode_active = True
        self.window.add_step("Step C")
        dark_item = self.window.ui.stepsTable.item(1, 1)
        self.assertEqual(
            dark_item.background().color().name(), STATUS_COLOR_DONE_DARK
        )


class TestCheckedDatablocksFilter(unittest.TestCase):
    """
    Covers ConfigureTabMixin.get_checked_datablocks() and its
    use in flash_tab.py's prepare_flash_ui()/
    add_segments_from_datablocks() — unticking a Datablocks
    row's checkbox must exclude it from both the Segments
    table and the flash sequence built from
    flash_button_clicked(), instead of always flashing
    everything loaded (see docs/gui_todo.md item #2).
    """

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()

    def _load_two_datablocks(self):
        db1 = parse_firmware_file(SAMPLE_HEX)
        db2 = parse_firmware_file(SAMPLE_HEX)
        self.window._loaded_datablocks = [db1, db2]

        table = self.window.ui.tableWidgetDatablocks
        for row in range(2):
            table.insertRow(row)
            item = QTableWidgetItem("")
            item.setCheckState(Qt.Checked)
            table.setItem(row, 0, item)

        return db1, db2

    def test_all_checked_by_default_returns_everything(self):
        db1, db2 = self._load_two_datablocks()
        self.assertEqual(
            self.window.get_checked_datablocks(), [db1, db2]
        )

    def test_unchecked_row_excluded(self):
        db1, db2 = self._load_two_datablocks()
        self.window.ui.tableWidgetDatablocks.item(
            1, 0
        ).setCheckState(Qt.Unchecked)
        self.assertEqual(
            self.window.get_checked_datablocks(), [db1]
        )

    def test_missing_row_defaults_to_included(self):
        # No row at all for a loaded datablock (edge case
        # that shouldn't happen in practice, but must not
        # silently drop the datablock if it does).
        db1 = parse_firmware_file(SAMPLE_HEX)
        self.window._loaded_datablocks = [db1]
        self.window.ui.tableWidgetDatablocks.setRowCount(0)
        self.assertEqual(
            self.window.get_checked_datablocks(), [db1]
        )

    def test_add_segments_uses_only_given_datablocks(self):
        db1, db2 = self._load_two_datablocks()
        self.window.add_segments_from_datablocks([db1])
        self.assertEqual(
            self.window.ui.segmentsTable.rowCount(),
            len(db1.segments),
        )

    def test_prepare_flash_ui_totals_only_given_datablocks(self):
        db1, db2 = self._load_two_datablocks()
        self.window.prepare_flash_ui([db1])
        self.assertEqual(
            self.window._total_bytes_all, db1.total_size
        )
        self.assertEqual(
            self.window.ui.segmentsTable.rowCount(),
            len(db1.segments),
        )


class TestDatablocksContextMenu(unittest.TestCase):
    """
    Covers the Datablocks table's right-click context menu
    (ConfigureTabMixin._show_datablocks_context_menu() and its
    Disable/Remove handlers) — Add Datablock/Disable
    Datablock/Remove Datablock. Uses real _load_firmware_file()
    calls (not manual row injection) so row/list indices come
    from the actual code path, not test bookkeeping.
    """

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()

    def _pos_for_row(self, row):
        table = self.window.ui.tableWidgetDatablocks
        y = table.rowViewportPosition(row) + table.rowHeight(row) // 2
        return QPoint(5, y)

    def test_menu_on_datablock_row_has_disable_and_remove(self):
        self.window._load_firmware_file(SAMPLE_HEX)

        menu = self.window._build_datablocks_context_menu(
            self._pos_for_row(0)
        )
        labels = [a.text() for a in menu.actions() if not a.isSeparator()]

        self.assertIn("Add Datablock", labels)
        self.assertIn("Disable Datablock", labels)
        self.assertIn("Remove Datablock", labels)

    def test_menu_on_placeholder_row_only_has_add(self):
        # No datablocks loaded — the only row is the
        # "Please click here to add a Datablock" placeholder.
        menu = self.window._build_datablocks_context_menu(
            self._pos_for_row(0)
        )
        labels = [a.text() for a in menu.actions() if not a.isSeparator()]

        self.assertEqual(labels, ["Add Datablock"])

    def test_menu_below_last_row_only_has_add(self):
        self.window._load_firmware_file(SAMPLE_HEX)

        table = self.window.ui.tableWidgetDatablocks
        below_last_row_y = (
            table.rowViewportPosition(table.rowCount() - 1)
            + table.rowHeight(table.rowCount() - 1)
            + 50
        )
        menu = self.window._build_datablocks_context_menu(
            QPoint(5, below_last_row_y)
        )
        labels = [a.text() for a in menu.actions() if not a.isSeparator()]

        self.assertEqual(labels, ["Add Datablock"])

    def test_disable_datablock_row_unchecks_without_removing(self):
        self.window._load_firmware_file(SAMPLE_HEX)

        self.window._disable_datablock_row(0)

        self.assertEqual(len(self.window._loaded_datablocks), 1)
        self.assertEqual(
            self.window.ui.tableWidgetDatablocks.item(0, 0).checkState(),
            Qt.Unchecked,
        )
        self.assertEqual(self.window.get_checked_datablocks(), [])

    def test_remove_datablock_row_removes_table_row_and_list_entry(self):
        self.window._load_firmware_file(SAMPLE_HEX)
        table = self.window.ui.tableWidgetDatablocks
        self.assertEqual(table.rowCount(), 2)  # 1 datablock + placeholder

        self.window._remove_datablock_row(0)

        self.assertEqual(len(self.window._loaded_datablocks), 0)
        # Only the placeholder row remains.
        self.assertEqual(table.rowCount(), 1)

    def test_remove_middle_row_keeps_others_in_lockstep(self):
        self.window._load_firmware_file(SAMPLE_HEX)
        self.window._load_firmware_file(SAMPLE_HEX)
        self.window._load_firmware_file(SAMPLE_HEX)
        db_middle = self.window._loaded_datablocks[1]
        db_last = self.window._loaded_datablocks[2]

        self.window._remove_datablock_row(0)

        self.assertEqual(
            self.window._loaded_datablocks, [db_middle, db_last]
        )
        table = self.window.ui.tableWidgetDatablocks
        self.assertEqual(table.rowCount(), 3)  # 2 datablocks + placeholder
        # Row 0 in the table must now correspond to db_middle —
        # get_checked_datablocks() relies on exactly this.
        table.item(0, 0).setCheckState(Qt.Unchecked)
        self.assertEqual(
            self.window.get_checked_datablocks(), [db_last]
        )

    def test_remove_last_datablock_clears_details_table(self):
        self.window._load_firmware_file(SAMPLE_HEX)
        # _load_firmware_file() alone doesn't populate Details
        # (only add_new_datablock() does, after the loop) —
        # mirror that step here to get a non-empty starting
        # value to verify gets cleared.
        self.window._update_details_table(
            self.window._loaded_datablocks[-1]
        )
        details = self.window.ui.tableWidgetDetails
        self.assertNotEqual(details.item(0, 1).text(), "")

        self.window._remove_datablock_row(0)

        for row in range(details.rowCount()):
            item = details.item(row, 1)
            self.assertTrue(item is None or item.text() == "")

    def test_remove_out_of_range_row_does_not_crash(self):
        self.window._load_firmware_file(SAMPLE_HEX)
        self.window._remove_datablock_row(5)  # must not raise
        self.assertEqual(len(self.window._loaded_datablocks), 1)


class TestEmptyDatablocksGuard(unittest.TestCase):
    """
    Covers flash_button_clicked() blocking (instead of running
    a no-op flash) when no datablock is loaded/ticked, and
    add_segments_from_datablocks() showing only the "no data"
    placeholder row (see docs/gui_todo.md item #16) instead of
    falling back to fake per-segment demo rows (see item #6).
    """

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()

    def test_add_segments_shows_placeholder_when_no_datablocks(self):
        self.window.add_segments_from_datablocks([])
        table = self.window.ui.segmentsTable
        self.assertEqual(table.rowCount(), 1)
        self.assertEqual(
            table.item(0, 0).text(), SEGMENTS_PLACEHOLDER_TEXT
        )
        self.assertIsNone(table.item(0, 1))

    def test_flash_button_blocks_when_no_datablocks_loaded(self):
        self.window._loaded_datablocks = []

        with unittest.mock.patch(
            "gui.flash_tab.QMessageBox.warning"
        ) as mock_warning:
            self.window.flash_button_clicked()

        mock_warning.assert_called_once()
        self.assertIsNone(self.window.thread)
        self.assertIsNone(self.window.worker)
        # Still just the placeholder row — the blocked click
        # never touched segmentsTable.
        self.assertEqual(
            self.window.ui.segmentsTable.rowCount(), 1
        )

    def test_flash_button_blocks_when_nothing_ticked(self):
        db = parse_firmware_file(SAMPLE_HEX)
        self.window._loaded_datablocks = [db]

        table = self.window.ui.tableWidgetDatablocks
        table.insertRow(0)
        item = QTableWidgetItem("")
        item.setCheckState(Qt.Unchecked)
        table.setItem(0, 0, item)

        with unittest.mock.patch(
            "gui.flash_tab.QMessageBox.warning"
        ) as mock_warning:
            self.window.flash_button_clicked()

        mock_warning.assert_called_once()
        self.assertIsNone(self.window.thread)


class TestSettingsProfile(unittest.TestCase):
    """
    Covers SettingsProfileMixin (gui/settings_profile.py) —
    Hardware/Radar Side/Security DLL/Flash Sequence should
    survive an app restart via QSettings (docs/gui_todo.md
    item #7). get_app() redirects QSettings to a fresh
    throwaway .ini per test method (see tests/qt_test_utils.py)
    so these tests never touch a developer's real saved
    profile, and don't leak into each other.
    """

    def setUp(self):
        self.app = get_app()

    def test_fresh_profile_defaults_to_s0_and_suzuki(self):
        # No saved settings yet (first-ever run) — must fall
        # back to the same .ui-declared defaults as before
        # this feature existed, not error out or leave blank.
        window = MainWindow()
        self.assertIn("S0", window.ui.comboBoxRadarSide.currentText())
        self.assertIn(
            "Suzuki", window.ui.comboBoxFlashSequence.currentText()
        )
        self.assertEqual(window.ui.comboBoxHardware.currentIndex(), 0)

    def test_radar_side_and_flash_sequence_persist_across_restart(self):
        window1 = MainWindow()
        window1.ui.comboBoxRadarSide.setCurrentIndex(1)  # S1
        window1.ui.comboBoxFlashSequence.setCurrentIndex(1)  # Generic

        # Simulate reopening the app: same QSettings store
        # (get_app() not called again), fresh MainWindow.
        window2 = MainWindow()
        self.assertEqual(window2.ui.comboBoxRadarSide.currentIndex(), 1)
        self.assertEqual(
            window2.ui.comboBoxFlashSequence.currentIndex(), 1
        )

    def test_data_format_selection_persists_across_restart(self):
        window1 = MainWindow()
        window1.ui.lineEditCompressionMethod.setText("3")
        window1.ui.lineEditEncryptionMethod.setText("A")
        # save_profile() is wired to textEdited (real user input),
        # which setText() alone doesn't fire — save explicitly,
        # same effect as the user actually typing the digit.
        window1.save_profile()

        window2 = MainWindow()
        self.assertEqual(
            window2.get_data_format_config(),
            {"compression": 3, "encrypting": 10},
        )

    def test_tester_serial_number_persists_across_restart(self):
        window1 = MainWindow()
        window1.ui.lineEditTesterSerialNumber.setText(
            "AABBCCDDEE0011223344"
        )
        window1.save_profile()

        window2 = MainWindow()
        self.assertEqual(
            window2.get_tester_serial_number(),
            bytes.fromhex("AABBCCDDEE0011223344"),
        )

    def test_security_dll_path_persists_if_file_still_exists(self):
        with tempfile.NamedTemporaryFile(
            suffix=".dll", delete=False
        ) as f:
            dll_path = f.name

        try:
            window1 = MainWindow()
            window1._security_dll_path = dll_path
            window1.ui.lineEditSecurityDll.setText(dll_path)
            window1.save_profile()

            window2 = MainWindow()
            self.assertEqual(
                window2._security_dll_path, dll_path
            )
            self.assertEqual(
                window2.ui.lineEditSecurityDll.text(), dll_path
            )
        finally:
            os.unlink(dll_path)

    def test_security_dll_path_ignored_if_file_no_longer_exists(self):
        window1 = MainWindow()
        window1._security_dll_path = "/no/such/security.dll"
        window1.save_profile()

        window2 = MainWindow()
        self.assertEqual(
            getattr(window2, '_security_dll_path', ''), ''
        )
        self.assertEqual(window2.ui.lineEditSecurityDll.text(), "")

    def test_saved_real_hardware_channel_not_present_falls_back_to_virtual(self):
        # Simulates a saved profile from a run where a real
        # Vector channel was selected, on a machine/session
        # where that channel (or any real hardware) isn't
        # currently detected — must not crash or get stuck on
        # a nonexistent combo entry.
        window1 = MainWindow()
        window1._settings.setValue("hardware/isVirtual", False)
        window1._settings.setValue("hardware/channel", 3)
        window1._settings.sync()

        window2 = MainWindow()
        self.assertIsNone(window2.ui.comboBoxHardware.currentData())

    def test_real_hardware_channel_persists_across_restart(self):
        # comboBoxHardware's userData is the full channel dict
        # from detect_vector_channels() (not a bare int), so
        # the profile must persist/match on the identifying
        # fields (hw_channel + serial) rather than comparing
        # the dict itself against a stored QSettings value.
        channel_entry = {
            "label": "VN1640A - Channel 2",
            "channel": 1, "hw_channel": 1,
            "serial": 5551234, "is_on_bus": False,
        }
        with unittest.mock.patch(
            "communication.vector_can.detect_vector_channels_with_error",
            return_value=([channel_entry], None),
        ):
            window1 = MainWindow()
            window1.ui.comboBoxHardware.setCurrentIndex(1)

            window2 = MainWindow()

        self.assertEqual(
            window2.ui.comboBoxHardware.currentData(), channel_entry
        )


class TestReportExport(unittest.TestCase):
    """
    Covers ReportExportMixin (gui/report_export.py) — the
    manual "Export Report..." button on the Flash tab
    (docs/gui_todo.md item #8). _write_report_file() is a pure
    snapshot of whatever's currently on screen, so these tests
    populate the relevant widgets directly instead of running
    a real flash.
    """

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()

    def _load_one_checked_datablock(self):
        db = parse_firmware_file(SAMPLE_HEX)
        table = self.window.ui.tableWidgetDatablocks
        table.insertRow(0)
        check_item = QTableWidgetItem("")
        check_item.setCheckState(Qt.Checked)
        table.setItem(0, 0, check_item)
        table.setItem(0, 1, QTableWidgetItem(db.file_type))
        table.setItem(0, 2, QTableWidgetItem(db.file_name))
        table.setItem(
            0, 3, QTableWidgetItem(f"0x{db.checksum:08X}")
        )
        return db

    def test_report_contains_summary_datablocks_steps_and_trace(self):
        db = self._load_one_checked_datablock()
        self.window.add_step("Start Programming Session")
        self.window.log_trace_row({
            "req_ts": 0.01, "req_target": "0x77B",
            "req_data": "10 02",
            "resp_ts": 0.02, "resp_source": "0x78B",
            "resp_data": "50 02",
        })
        self.window.log_information("ECU unlocked")

        with tempfile.NamedTemporaryFile(
            suffix=".html", delete=False
        ) as f:
            path = f.name

        try:
            self.window._write_report_file(path)
            with open(path, encoding="utf-8") as f:
                content = f.read()

            self.assertIn(APP_NAME, content)
            self.assertIn("Virtual ECU Simulator", content)
            self.assertIn(db.file_name, content)
            self.assertIn(f"0x{db.checksum:08X}", content)
            self.assertIn("Start Programming Session", content)
            self.assertIn("0x77B", content)
            self.assertIn("10 02", content)
            self.assertIn("ECU unlocked", content)
        finally:
            os.unlink(path)

    def test_unchecked_datablock_marked_excluded_in_report(self):
        self._load_one_checked_datablock()
        self.window.ui.tableWidgetDatablocks.item(
            0, 0
        ).setCheckState(Qt.Unchecked)

        with tempfile.NamedTemporaryFile(
            suffix=".html", delete=False
        ) as f:
            path = f.name

        try:
            self.window._write_report_file(path)
            with open(path, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("Excluded", content)
        finally:
            os.unlink(path)

    def test_report_html_escapes_untrusted_text(self):
        # add_step() writes to stepsTable (a QTableWidgetItem,
        # plain text — unlike QTextEdit.append(), which
        # auto-detects and interprets "<...>"-looking text as
        # rich text itself, before this code ever sees it).
        self.window.add_step("<b>bold</b> description")

        with tempfile.NamedTemporaryFile(
            suffix=".html", delete=False
        ) as f:
            path = f.name

        try:
            self.window._write_report_file(path)
            with open(path, encoding="utf-8") as f:
                content = f.read()
            self.assertNotIn("<b>bold</b>", content)
            self.assertIn("&lt;b&gt;bold&lt;/b&gt;", content)
        finally:
            os.unlink(path)

    def test_report_skips_steps_placeholder_row_when_no_steps_run(self):
        # stepsTable still holds its item #16 "No steps recorded
        # yet." placeholder row here (no add_step() call) — the
        # report must fall back to its own empty-state text, not
        # leak the on-screen placeholder row into the report.
        with tempfile.NamedTemporaryFile(
            suffix=".html", delete=False
        ) as f:
            path = f.name

        try:
            self.window._write_report_file(path)
            with open(path, encoding="utf-8") as f:
                content = f.read()
            self.assertNotIn(STEPS_PLACEHOLDER_TEXT, content)
            self.assertIn("No steps recorded.", content)
        finally:
            os.unlink(path)

    def test_write_report_file_failure_does_not_raise(self):
        # Writing to a directory (not a file) — OSError must be
        # caught internally, not propagate. QMessageBox.critical
        # patched to a no-op to avoid a real modal dialog.
        with unittest.mock.patch(
            "gui.report_export.QMessageBox.critical"
        ) as mock_critical:
            self.window._write_report_file(tempfile.gettempdir())
        mock_critical.assert_called_once()


class TestIssueExport(unittest.TestCase):
    """
    Covers IssueExportMixin (gui/issue_export.py) — Help >
    Export Issue..., a plain-.txt debugging bundle distinct
    from Tools > Export Report... (HTML). _write_issue_file()
    is a pure snapshot of whatever's currently on screen, same
    split as TestReportExport.
    """

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()

    def _load_one_checked_datablock(self):
        db = parse_firmware_file(SAMPLE_HEX)
        table = self.window.ui.tableWidgetDatablocks
        table.insertRow(0)
        check_item = QTableWidgetItem("")
        check_item.setCheckState(Qt.Checked)
        table.setItem(0, 0, check_item)
        table.setItem(0, 1, QTableWidgetItem(db.file_type))
        table.setItem(0, 2, QTableWidgetItem(db.file_name))
        table.setItem(
            0, 3, QTableWidgetItem(f"0x{db.checksum:08X}")
        )
        return db

    def test_issue_contains_environment_config_datablocks_log_and_trace(
        self,
    ):
        db = self._load_one_checked_datablock()
        self.window.log_trace_row({
            "req_ts": 0.01, "req_target": "0x77B",
            "req_data": "10 02",
            "resp_ts": 0.02, "resp_source": "0x78B",
            "resp_data": "50 02",
        })
        self.window.log_information("ECU unlocked")

        text = self.window._build_issue_text()

        self.assertIn(APP_NAME, text)
        self.assertIn("--- Environment ---", text)
        self.assertIn("Virtual ECU Simulator", text)
        self.assertIn("--- CAN Communication Details ---", text)
        self.assertIn("0x77B", text)
        self.assertIn(db.file_name, text)
        self.assertIn(f"0x{db.checksum:08X}", text)
        self.assertIn("ECU unlocked", text)
        self.assertIn("10 02", text)

    def test_issue_omits_steps_table_content(self):
        # Deliberately narrower than Export Report — the
        # Information log already narrates the same steps.
        self.window.add_step("Start Programming Session")

        text = self.window._build_issue_text()

        self.assertNotIn("Steps", text)
        self.assertNotIn("Start Programming Session", text)

    def test_unchecked_datablock_marked_excluded_in_issue(self):
        self._load_one_checked_datablock()
        self.window.ui.tableWidgetDatablocks.item(
            0, 0
        ).setCheckState(Qt.Unchecked)

        text = self.window._build_issue_text()

        self.assertIn("Excluded", text)

    def test_write_issue_file_creates_file(self):
        with tempfile.NamedTemporaryFile(
            suffix=".txt", delete=False
        ) as f:
            path = f.name

        try:
            self.window._write_issue_file(path)
            with open(path, encoding="utf-8") as f:
                content = f.read()
            self.assertIn(APP_NAME, content)
        finally:
            os.unlink(path)

    def test_write_issue_file_failure_does_not_raise(self):
        with unittest.mock.patch(
            "gui.issue_export.QMessageBox.critical"
        ) as mock_critical:
            self.window._write_issue_file(tempfile.gettempdir())
        mock_critical.assert_called_once()

    def test_ask_include_firmware_true_when_checked_and_ok(self):
        def fake_exec(box_self):
            box_self.checkBox().setChecked(True)
            return QMessageBox.Ok

        with unittest.mock.patch.object(
            QMessageBox, 'exec', fake_exec
        ):
            result = self.window._ask_include_firmware()

        self.assertTrue(result)

    def test_ask_include_firmware_false_by_default_when_ok(self):
        # Checkbox left untouched — off by default each time.
        with unittest.mock.patch.object(
            QMessageBox, 'exec', lambda box_self: QMessageBox.Ok
        ):
            result = self.window._ask_include_firmware()

        self.assertFalse(result)

    def test_ask_include_firmware_none_when_cancelled(self):
        with unittest.mock.patch.object(
            QMessageBox, 'exec',
            lambda box_self: QMessageBox.Cancel,
        ):
            result = self.window._ask_include_firmware()

        self.assertIsNone(result)

    def test_write_issue_zip_contains_txt_and_firmware(self):
        db = self._load_one_checked_datablock()
        self.window._loaded_datablocks = [db]

        with tempfile.NamedTemporaryFile(
            suffix=".zip", delete=False
        ) as f:
            path = f.name

        try:
            self.window._write_issue_zip(path)
            with zipfile.ZipFile(path) as zf:
                names = zf.namelist()
                self.assertIn("issue.txt", names)
                self.assertIn(db.file_name, names)
                self.assertIn(APP_NAME, zf.read("issue.txt").decode())
        finally:
            os.unlink(path)

    def test_write_issue_zip_appends_extension(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "no_extension")
            self.window._write_issue_zip(path)
            self.assertTrue(os.path.isfile(path + ".zip"))

    def test_write_issue_zip_skips_missing_firmware_file(self):
        db = parse_firmware_file(SAMPLE_HEX)
        db.file_path = "/no/such/file.hex"
        self.window._loaded_datablocks = [db]

        with tempfile.NamedTemporaryFile(
            suffix=".zip", delete=False
        ) as f:
            path = f.name

        try:
            self.window._write_issue_zip(path)
            with zipfile.ZipFile(path) as zf:
                self.assertEqual(zf.namelist(), ["issue.txt"])
        finally:
            os.unlink(path)

    def test_write_issue_zip_disambiguates_duplicate_names(self):
        db1 = parse_firmware_file(SAMPLE_HEX)
        db2 = parse_firmware_file(SAMPLE_HEX)  # same file_name
        self.window._loaded_datablocks = [db1, db2]

        with tempfile.NamedTemporaryFile(
            suffix=".zip", delete=False
        ) as f:
            path = f.name

        try:
            self.window._write_issue_zip(path)
            with zipfile.ZipFile(path) as zf:
                names = zf.namelist()
                self.assertEqual(len(names), 3)  # issue.txt + 2 firmware
                self.assertEqual(len(set(names)), 3)  # all unique
        finally:
            os.unlink(path)

    def test_write_issue_zip_failure_does_not_raise(self):
        # A path ending in ".zip" that's itself a directory —
        # the ".zip" suffix means _write_issue_zip() won't
        # "fix" the path by appending another extension, so it
        # stays a genuine write failure (IsADirectoryError, an
        # OSError subclass).
        dir_path = tempfile.mkdtemp(suffix=".zip")
        with unittest.mock.patch(
            "gui.issue_export.QMessageBox.critical"
        ) as mock_critical:
            self.window._write_issue_zip(dir_path)
        mock_critical.assert_called_once()

    def test_export_issue_cancelled_at_prompt_does_nothing(self):
        with unittest.mock.patch.object(
            self.window, '_ask_include_firmware', return_value=None
        ), unittest.mock.patch(
            "gui.issue_export.QFileDialog.getSaveFileName"
        ) as mock_dialog:
            self.window.export_issue()
        mock_dialog.assert_not_called()

    def test_export_issue_writes_txt_when_firmware_not_included(self):
        with tempfile.NamedTemporaryFile(
            suffix=".txt", delete=False
        ) as f:
            path = f.name
        os.unlink(path)  # just want the path, not an open handle

        try:
            with unittest.mock.patch.object(
                self.window, '_ask_include_firmware',
                return_value=False,
            ), unittest.mock.patch(
                "gui.issue_export.QFileDialog.getSaveFileName",
                return_value=(path, ""),
            ):
                self.window.export_issue()

            self.assertTrue(os.path.isfile(path))
            self.assertFalse(zipfile.is_zipfile(path))
            with open(path, encoding="utf-8") as f:
                self.assertIn(APP_NAME, f.read())
        finally:
            if os.path.isfile(path):
                os.unlink(path)

    def test_export_issue_writes_zip_when_firmware_included(self):
        db = self._load_one_checked_datablock()
        self.window._loaded_datablocks = [db]

        with tempfile.NamedTemporaryFile(
            suffix=".zip", delete=False
        ) as f:
            path = f.name

        try:
            with unittest.mock.patch.object(
                self.window, '_ask_include_firmware',
                return_value=True,
            ), unittest.mock.patch(
                "gui.issue_export.QFileDialog.getSaveFileName",
                return_value=(path, ""),
            ):
                self.window.export_issue()

            with zipfile.ZipFile(path) as zf:
                self.assertIn(db.file_name, zf.namelist())
        finally:
            os.unlink(path)


class TestMenuBar(unittest.TestCase):
    """
    Covers MenuBarMixin (gui/menu_bar.py) — File/Tools/Help
    menu actions declared in gui/main_window.ui, wired in
    setup_menu_bar(). Each test triggers the QAction and
    checks the right handler ran, mocking anything that would
    otherwise pop a real dialog/open a real URL.
    """

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()

    def test_load_firmware_switches_to_data_tab_and_opens_dialog(self):
        self.window.ui.tabWidget.setCurrentIndex(0)  # start on Flash

        with unittest.mock.patch.object(
            self.window, 'add_new_datablock'
        ) as mock_add:
            self.window.ui.actionLoadFirmware.trigger()

        self.assertEqual(self.window.ui.tabWidget.currentIndex(), 1)
        self.assertEqual(self.window.ui.navListWidget.currentRow(), 0)
        mock_add.assert_called_once()

    def test_exit_action_calls_close(self):
        with unittest.mock.patch.object(
            self.window, 'close'
        ) as mock_close:
            self.window.ui.actionExit.trigger()
        mock_close.assert_called_once()

    def test_close_window_action_calls_close(self):
        with unittest.mock.patch.object(
            self.window, 'close'
        ) as mock_close:
            self.window.ui.actionCloseWindow.trigger()
        mock_close.assert_called_once()

    def test_dark_mode_action_starts_unchecked_by_default(self):
        # Fresh profile, never toggled — defaults to Light Mode.
        self.assertFalse(self.window.ui.actionDarkMode.isChecked())

    def test_dark_mode_toggle_applies_dark_stylesheet_and_persists(self):
        # self.app is the one shared QApplication reused by every
        # GUI test in this process — always restore its
        # styleSheet() so a failed assertion here can't leak a
        # dark theme into unrelated tests that run afterward.
        try:
            self.window.ui.actionDarkMode.setChecked(True)

            self.assertIn(
                "#5b8fd9",  # dark-theme accent, not in the light QSS
                self.app.styleSheet(),
            )
            self.assertTrue(
                self.window._settings.value(
                    "appearance/darkMode", False, type=bool
                )
            )

            self.window.ui.actionDarkMode.setChecked(False)
            self.assertNotIn("#5b8fd9", self.app.styleSheet())
        finally:
            self.window.ui.actionDarkMode.setChecked(False)

    def test_resize_default_sets_exact_size(self):
        self.window.ui.actionResizeMedium.trigger()  # move away first
        self.window.ui.actionResizeDefault.trigger()
        self.assertEqual(
            self.window.size().toTuple(), (1100, 850)
        )

    def test_resize_medium_sets_exact_size(self):
        self.window.ui.actionResizeMedium.trigger()
        # 789, not 768 -- buttonLoadFromGitLab added one more row to pageData's minimum height
        self.assertEqual(
            self.window.size().toTuple(), (1366, 789)
        )

    def test_resize_large_sets_exact_size(self):
        self.window.ui.actionResizeLarge.trigger()
        self.assertEqual(
            self.window.size().toTuple(), (1920, 1080)
        )

    def test_maximize_window_action(self):
        self.window.ui.actionMaximizeWindow.trigger()
        self.assertTrue(self.window.isMaximized())

    def test_full_screen_action(self):
        self.window.ui.actionFullScreen.trigger()
        self.assertTrue(self.window.isFullScreen())

    def test_resize_after_maximize_un_maximizes_first(self):
        self.window.ui.actionMaximizeWindow.trigger()
        self.assertTrue(self.window.isMaximized())

        self.window.ui.actionResizeDefault.trigger()

        self.assertFalse(self.window.isMaximized())
        self.assertEqual(
            self.window.size().toTuple(), (1100, 850)
        )

    def test_resize_after_full_screen_exits_full_screen_first(self):
        self.window.ui.actionFullScreen.trigger()
        self.assertTrue(self.window.isFullScreen())

        self.window.ui.actionResizeMedium.trigger()

        self.assertFalse(self.window.isFullScreen())
        self.assertEqual(
            self.window.size().toTuple(), (1366, 789)
        )

    def test_export_report_action_calls_export_report(self):
        with unittest.mock.patch.object(
            self.window, 'export_report'
        ) as mock_export:
            self.window.ui.actionExportReport.trigger()
        mock_export.assert_called_once()

    def test_about_action_shows_message_box(self):
        with unittest.mock.patch(
            "gui.menu_bar.QMessageBox.about"
        ) as mock_about:
            self.window.ui.actionAbout.trigger()
        mock_about.assert_called_once()
        self.assertIn(APP_NAME, mock_about.call_args[0][1])

    def test_about_action_opts_out_of_macos_auto_relocation(self):
        # On macOS, Qt's native menu bar silently moves any
        # action whose text contains "about" out of Help and
        # into the system application menu (TextHeuristicRole,
        # the default) — invisible in this offscreen test env,
        # but very much not in Help for a real macOS user. Must
        # stay NoRole so "About SFlash" stays in Help everywhere.
        from PySide6.QtGui import QAction
        self.assertEqual(
            self.window.ui.actionAbout.menuRole(),
            QAction.MenuRole.NoRole,
        )

    def test_open_guideline_opens_existing_file(self):
        with unittest.mock.patch(
            "gui.menu_bar.QDesktopServices.openUrl"
        ) as mock_open:
            self.window.ui.actionOpenGuideline.trigger()
        mock_open.assert_called_once()

    def test_export_issue_action_calls_export_issue(self):
        with unittest.mock.patch.object(
            self.window, 'export_issue'
        ) as mock_export:
            self.window.ui.actionExportIssue.trigger()
        mock_export.assert_called_once()

    def test_flash_and_abort_actions_start_disabled_correctly(self):
        # Fresh window, nothing running — Flash is the only
        # sensible action, matching flashButton's own initial
        # "Flash" (not "Abort") label.
        self.assertTrue(self.window.ui.actionFlash.isEnabled())
        self.assertFalse(self.window.ui.actionAbort.isEnabled())

    def test_sync_flash_abort_menu_state_reflects_running_thread(self):
        # Lightweight state-sync check — a Mock stands in for a
        # running QThread so this doesn't need a real flash run
        # (see tests/test_flash_threading.py for that).
        self.window.thread = unittest.mock.Mock()
        self.window.thread.isRunning.return_value = True

        self.window._sync_flash_abort_menu_state()

        self.assertFalse(self.window.ui.actionFlash.isEnabled())
        self.assertTrue(self.window.ui.actionAbort.isEnabled())

        self.window.thread.isRunning.return_value = False
        self.window._sync_flash_abort_menu_state()

        self.assertTrue(self.window.ui.actionFlash.isEnabled())
        self.assertFalse(self.window.ui.actionAbort.isEnabled())

    def test_flash_action_calls_flash_button_clicked(self):
        with unittest.mock.patch.object(
            self.window, 'flash_button_clicked'
        ) as mock_clicked:
            self.window.ui.actionFlash.trigger()
        mock_clicked.assert_called_once()

    def test_abort_action_calls_flash_button_clicked(self):
        # QAction.trigger() doesn't emit triggered while
        # disabled — Abort starts disabled (nothing running), so
        # enable it first, matching the only state a real click
        # could ever reach it in.
        self.window.ui.actionAbort.setEnabled(True)
        with unittest.mock.patch.object(
            self.window, 'flash_button_clicked'
        ) as mock_clicked:
            self.window.ui.actionAbort.trigger()
        mock_clicked.assert_called_once()

    def test_test_connection_action_opens_dialog(self):
        with unittest.mock.patch(
            "gui.menu_bar.TestConnectionDialog"
        ) as MockDialog:
            self.window.ui.actionTestConnection.trigger()
        MockDialog.assert_called_once()
        MockDialog.return_value.exec.assert_called_once()

    def test_test_connection_respects_can_conflict_warning(self):
        # Same guard as Flash: on real hardware, a detected
        # conflict shows a Yes/No warning; answering No must
        # skip opening the dialog entirely.
        combo = self.window.ui.comboBoxHardware
        combo.addItem(
            "VN1640A - Channel 1",
            userData={
                "label": "VN1640A - Channel 1",
                "channel": 0, "hw_channel": 0,
                "serial": None, "is_on_bus": False,
            },
        )
        combo.setCurrentIndex(combo.count() - 1)

        with unittest.mock.patch.object(
            self.window, 'detect_can_conflict_warning',
            return_value="Something is on the bus",
        ), unittest.mock.patch(
            "gui.menu_bar.QMessageBox.warning",
            return_value=QMessageBox.No,
        ), unittest.mock.patch(
            "gui.menu_bar.TestConnectionDialog"
        ) as MockDialog:
            self.window.ui.actionTestConnection.trigger()

        MockDialog.assert_not_called()

    def test_load_from_gitlab_menu_action_opens_dialog(self):
        with unittest.mock.patch(
            "gui.menu_bar.GitLabFetchDialog"
        ) as mock_dialog_cls:
            mock_dialog_cls.return_value.exec.return_value = None
            self.window.ui.actionLoadFromGitLab.trigger()
        mock_dialog_cls.assert_called_once_with(self.window)
        mock_dialog_cls.return_value.exec.assert_called_once()


class TestConnectionButton(unittest.TestCase):
    """
    Covers the Communication page's own Test Connection button
    (gui/configure_tab.py's test_connection_button_clicked()) —
    reuses gui/menu_bar.py's open_test_connection_dialog() (the
    same dialog as Tools > Test Connection...) and colors the
    button green/red based on the outcome.
    """

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()

    def _add_real_channel(self):
        combo = self.window.ui.comboBoxHardware
        combo.addItem(
            "VN1640A - Channel 1",
            userData={
                "label": "VN1640A - Channel 1",
                "channel": 0, "hw_channel": 0,
                "serial": None, "is_on_bus": False,
            },
        )
        combo.setCurrentIndex(combo.count() - 1)

    def test_click_opens_same_dialog_as_menu_action(self):
        with unittest.mock.patch(
            "gui.menu_bar.TestConnectionDialog"
        ) as MockDialog:
            MockDialog.return_value.passed = True
            self.window.ui.buttonTestConnectionHardware.click()

        MockDialog.assert_called_once()
        MockDialog.return_value.exec.assert_called_once()

    def test_passed_colors_button_with_done_status_colors(self):
        with unittest.mock.patch(
            "gui.menu_bar.TestConnectionDialog"
        ) as MockDialog:
            MockDialog.return_value.passed = True
            self.window.ui.buttonTestConnectionHardware.click()

        bg, fg = self.window._status_colors('done')
        style = self.window.ui.buttonTestConnectionHardware.styleSheet()
        self.assertIn(bg, style)
        self.assertIn(fg, style)

    def test_failed_colors_button_with_error_status_colors(self):
        with unittest.mock.patch(
            "gui.menu_bar.TestConnectionDialog"
        ) as MockDialog:
            MockDialog.return_value.passed = False
            self.window.ui.buttonTestConnectionHardware.click()

        bg, fg = self.window._status_colors('error')
        style = self.window.ui.buttonTestConnectionHardware.styleSheet()
        self.assertIn(bg, style)
        self.assertIn(fg, style)

    def test_closed_before_finished_leaves_button_uncolored(self):
        with unittest.mock.patch(
            "gui.menu_bar.TestConnectionDialog"
        ) as MockDialog:
            MockDialog.return_value.passed = None
            self.window.ui.buttonTestConnectionHardware.click()

        self.assertEqual(
            self.window.ui.buttonTestConnectionHardware.styleSheet(), ""
        )

    def test_declined_can_conflict_warning_leaves_button_uncolored(self):
        self._add_real_channel()

        with unittest.mock.patch.object(
            self.window, 'detect_can_conflict_warning',
            return_value="Something is on the bus",
        ), unittest.mock.patch(
            "gui.menu_bar.QMessageBox.warning",
            return_value=QMessageBox.No,
        ), unittest.mock.patch(
            "gui.menu_bar.TestConnectionDialog"
        ) as MockDialog:
            self.window.ui.buttonTestConnectionHardware.click()

        MockDialog.assert_not_called()
        self.assertEqual(
            self.window.ui.buttonTestConnectionHardware.styleSheet(), ""
        )

    def test_changing_hardware_selection_resets_button_color(self):
        with unittest.mock.patch(
            "gui.menu_bar.TestConnectionDialog"
        ) as MockDialog:
            MockDialog.return_value.passed = True
            self.window.ui.buttonTestConnectionHardware.click()
        self.assertNotEqual(
            self.window.ui.buttonTestConnectionHardware.styleSheet(), ""
        )

        self._add_real_channel()

        self.assertEqual(
            self.window.ui.buttonTestConnectionHardware.styleSheet(), ""
        )

    def test_refresh_hardware_resets_button_color(self):
        with unittest.mock.patch(
            "gui.menu_bar.TestConnectionDialog"
        ) as MockDialog:
            MockDialog.return_value.passed = False
            self.window.ui.buttonTestConnectionHardware.click()
        self.assertNotEqual(
            self.window.ui.buttonTestConnectionHardware.styleSheet(), ""
        )

        self.window.ui.buttonRefreshHardware.click()

        self.assertEqual(
            self.window.ui.buttonTestConnectionHardware.styleSheet(), ""
        )

    def test_button_is_left_of_refresh_in_layout(self):
        layout = self.window.ui.horizontalLayout_hardware
        order = [
            layout.itemAt(i).widget().objectName()
            for i in range(layout.count())
            if layout.itemAt(i).widget() is not None
        ]
        self.assertLess(
            order.index("buttonTestConnectionHardware"),
            order.index("buttonRefreshHardware"),
        )


class TestGitLabButtonOnDataPage(unittest.TestCase):

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()

    def test_button_opens_same_dialog_as_menu_action(self):
        with unittest.mock.patch(
            "gui.menu_bar.GitLabFetchDialog"
        ) as mock_dialog_cls:
            mock_dialog_cls.return_value.exec.return_value = None
            self.window.ui.buttonLoadFromGitLab.click()
        mock_dialog_cls.assert_called_once_with(self.window)


class TestRecentFiles(unittest.TestCase):
    """
    Covers File > Recent Files (gui/menu_bar.py's
    _record_recent_file()/_rebuild_recent_files_menu()/
    load_recent_file()) — added on top of the shared
    _load_firmware_file() helper extracted from
    gui/configure_tab.py's add_new_datablock() so both the
    file-dialog path and Recent Files go through identical
    parsing/error handling.
    """

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()

    def test_starts_with_no_recent_files_placeholder(self):
        actions = self.window.ui.menuRecentFiles.actions()
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].text(), "(No Recent Files)")
        self.assertFalse(actions[0].isEnabled())

    def test_loading_file_adds_entry_with_tooltip(self):
        self.window._load_firmware_file(SAMPLE_HEX)

        actions = self.window.ui.menuRecentFiles.actions()
        self.assertEqual(actions[0].text(), os.path.basename(SAMPLE_HEX))
        self.assertEqual(actions[0].toolTip(), SAMPLE_HEX)
        # separator + "Clear Recent Files" after the 1 entry
        self.assertEqual(actions[-1].text(), "Clear Recent Files")

    def test_loading_same_file_again_dedupes_instead_of_duplicating(self):
        self.window._load_firmware_file(SAMPLE_HEX)
        self.window._load_firmware_file(SAMPLE_HEX)

        paths = self.window._settings.value(
            "recentFiles/list", [], type=list
        )
        self.assertEqual(paths, [SAMPLE_HEX])

    def test_recent_files_list_capped_at_max(self):
        from gui.menu_bar import MAX_RECENT_FILES

        # _record_recent_file() is pure list bookkeeping — no
        # need for MAX_RECENT_FILES+2 real firmware files.
        for i in range(MAX_RECENT_FILES + 2):
            self.window._record_recent_file(f"/fake/path/{i}.hex")

        paths = self.window._settings.value(
            "recentFiles/list", [], type=list
        )
        self.assertEqual(len(paths), MAX_RECENT_FILES)
        # Most recently recorded stays first.
        self.assertEqual(
            paths[0], f"/fake/path/{MAX_RECENT_FILES + 1}.hex"
        )

    def test_clear_recent_files_action_empties_menu_and_settings(self):
        self.window._load_firmware_file(SAMPLE_HEX)

        self.window.ui.actionClearRecentFiles.trigger()

        self.assertEqual(
            self.window._settings.value("recentFiles/list", [], type=list),
            [],
        )
        actions = self.window.ui.menuRecentFiles.actions()
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].text(), "(No Recent Files)")

    def test_load_recent_file_switches_tab_and_loads_datablock(self):
        self.window.ui.tabWidget.setCurrentIndex(0)  # start on Flash

        self.window.load_recent_file(SAMPLE_HEX)

        self.assertEqual(self.window.ui.tabWidget.currentIndex(), 1)
        self.assertEqual(self.window.ui.navListWidget.currentRow(), 0)
        self.assertEqual(len(self.window._loaded_datablocks), 1)

    def test_load_recent_file_missing_file_shows_warning_not_crash(self):
        with unittest.mock.patch(
            "gui.configure_tab.QMessageBox.warning"
        ) as mock_warning:
            self.window.load_recent_file("/no/such/file.hex")

        mock_warning.assert_called_once()
        self.assertEqual(len(self.window._loaded_datablocks), 0)
        # A failed load must not get recorded as if it succeeded.
        actions = self.window.ui.menuRecentFiles.actions()
        self.assertEqual(actions[0].text(), "(No Recent Files)")


class TestEditMenu(unittest.TestCase):
    """Covers the new Edit menu (gui/menu_bar.py)."""

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()

    def test_clear_information_log_action_clears_text(self):
        self.window.log_information("hello")
        self.assertNotEqual(
            self.window.ui.informationText.toPlainText(), ""
        )

        self.window.ui.actionClearInformationLog.trigger()

        self.assertEqual(
            self.window.ui.informationText.toPlainText(), ""
        )

    def test_clear_trace_action_clears_table(self):
        self.window.log_trace("Executing: Something")
        self.assertGreater(self.window.ui.traceTable.rowCount(), 0)

        self.window.ui.actionClearTrace.trigger()

        self.assertEqual(self.window.ui.traceTable.rowCount(), 0)


class TestProjectFile(unittest.TestCase):
    """
    Covers File > Save Project As.../Open Project... (module
    gui/project_file.py, docs/gui_todo.md item #20) — a named
    .sfproj JSON snapshot of the loaded firmware (+ ticked
    state) and CAN/hardware configuration, distinct from
    gui/settings_profile.py's single auto-saved profile.
    """

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()
        self.tmpdir = tempfile.mkdtemp()

    def _project_path(self, name="test"):
        return os.path.join(self.tmpdir, name)

    def test_build_project_data_reflects_loaded_state(self):
        self.window._load_firmware_file(SAMPLE_HEX)
        self.window.ui.comboBoxRadarSide.setCurrentIndex(1)  # S1

        data = self.window._build_project_data()

        self.assertEqual(data["format_version"], 1)
        self.assertEqual(len(data["firmware_files"]), 1)
        self.assertEqual(
            data["firmware_files"][0]["path"], SAMPLE_HEX
        )
        self.assertTrue(data["firmware_files"][0]["checked"])
        self.assertEqual(data["hardware"], {
            "is_virtual": True, "channel": -1, "serial": -1
        })
        self.assertEqual(data["radar_side_index"], 1)

    def test_build_project_data_captures_unchecked_datablock(self):
        self.window._load_firmware_file(SAMPLE_HEX)
        self.window.ui.tableWidgetDatablocks.item(
            0, 0
        ).setCheckState(Qt.Unchecked)

        data = self.window._build_project_data()

        self.assertFalse(data["firmware_files"][0]["checked"])

    def test_save_and_open_project_round_trip(self):
        self.window._load_firmware_file(SAMPLE_HEX)
        self.window.ui.comboBoxRadarSide.setCurrentIndex(1)
        self.window.ui.lineEditCompressionMethod.setText("3")
        self.window.ui.lineEditEncryptionMethod.setText("A")
        self.window.ui.lineEditTesterSerialNumber.setText(
            "AABBCCDDEE0011223344"
        )

        path = self._project_path("roundtrip.sfproj")
        with unittest.mock.patch(
            "gui.project_file.QFileDialog.getSaveFileName",
            return_value=(path, ""),
        ):
            self.window.save_project_as()

        self.assertTrue(os.path.isfile(path))

        window2 = MainWindow()
        with unittest.mock.patch(
            "gui.project_file.QFileDialog.getOpenFileName",
            return_value=(path, ""),
        ):
            window2.open_project()

        self.assertEqual(len(window2._loaded_datablocks), 1)
        self.assertEqual(
            window2._loaded_datablocks[0].file_path, SAMPLE_HEX
        )
        self.assertEqual(
            window2.ui.comboBoxRadarSide.currentIndex(), 1
        )
        self.assertEqual(
            window2.get_data_format_config(),
            {"compression": 3, "encrypting": 10},
        )
        self.assertEqual(
            window2.get_tester_serial_number(),
            bytes.fromhex("AABBCCDDEE0011223344"),
        )

    def test_save_and_open_project_round_trip_real_hardware(self):
        # comboBoxHardware's userData is the full channel dict
        # from detect_vector_channels(), not a bare int — the
        # saved/reopened hardware selection must match on the
        # identifying fields (hw_channel + serial).
        channel_entry = {
            "label": "VN1640A - Channel 2",
            "channel": 1, "hw_channel": 1,
            "serial": 5551234, "is_on_bus": False,
        }
        path = self._project_path("real_hw.sfproj")
        with unittest.mock.patch(
            "communication.vector_can.detect_vector_channels_with_error",
            return_value=([channel_entry], None),
        ):
            # Fresh window, built while the mock is active, so
            # its combo actually has the real-channel entry —
            # self.window from setUp() was already populated
            # (Virtual-only) before this patch took effect.
            window1 = MainWindow()
            window1.ui.comboBoxHardware.setCurrentIndex(1)

            with unittest.mock.patch(
                "gui.project_file.QFileDialog.getSaveFileName",
                return_value=(path, ""),
            ):
                window1.save_project_as()

            window2 = MainWindow()
            with unittest.mock.patch(
                "gui.project_file.QFileDialog.getOpenFileName",
                return_value=(path, ""),
            ):
                window2.open_project()

        self.assertEqual(
            window2.ui.comboBoxHardware.currentData(), channel_entry
        )

    def test_save_project_appends_sfproj_extension(self):
        path = self._project_path("no_extension")
        with unittest.mock.patch(
            "gui.project_file.QFileDialog.getSaveFileName",
            return_value=(path, ""),
        ):
            self.window.save_project_as()

        self.assertTrue(os.path.isfile(path + ".sfproj"))

    def test_save_project_cancelled_dialog_does_nothing(self):
        with unittest.mock.patch(
            "gui.project_file.QFileDialog.getSaveFileName",
            return_value=("", ""),
        ):
            self.window.save_project_as()  # must not raise

    def test_open_project_replaces_not_merges_existing_datablocks(self):
        self.window._load_firmware_file(SAMPLE_HEX)
        self.assertEqual(len(self.window._loaded_datablocks), 1)

        path = self._project_path("empty.sfproj")
        with open(path, "w") as f:
            json.dump({"format_version": 1, "firmware_files": []}, f)

        with unittest.mock.patch(
            "gui.project_file.QFileDialog.getOpenFileName",
            return_value=(path, ""),
        ):
            self.window.open_project()

        self.assertEqual(len(self.window._loaded_datablocks), 0)
        self.assertEqual(
            self.window.ui.tableWidgetDatablocks.rowCount(), 1
        )  # just the "add a Datablock" placeholder row

    def test_open_project_missing_firmware_file_warns_not_crash(self):
        path = self._project_path("missing_fw.sfproj")
        with open(path, "w") as f:
            json.dump({
                "format_version": 1,
                "firmware_files": [{"path": "/no/such.hex", "checked": True}],
            }, f)

        with unittest.mock.patch(
            "gui.project_file.QFileDialog.getOpenFileName",
            return_value=(path, ""),
        ), unittest.mock.patch(
            "gui.configure_tab.QMessageBox.warning"
        ) as mock_warning:
            self.window.open_project()

        mock_warning.assert_called_once()
        self.assertEqual(len(self.window._loaded_datablocks), 0)

    def test_open_project_invalid_json_shows_error_not_crash(self):
        path = self._project_path("corrupt.sfproj")
        with open(path, "w") as f:
            f.write("{not valid json")

        with unittest.mock.patch(
            "gui.project_file.QFileDialog.getOpenFileName",
            return_value=(path, ""),
        ), unittest.mock.patch(
            "gui.project_file.QMessageBox.critical"
        ) as mock_critical:
            self.window.open_project()  # must not raise

        mock_critical.assert_called_once()

    def test_menu_actions_call_save_and_open(self):
        with unittest.mock.patch.object(
            self.window, 'save_project_as'
        ) as mock_save:
            self.window.ui.actionSaveProjectAs.trigger()
        mock_save.assert_called_once()

        with unittest.mock.patch.object(
            self.window, 'open_project'
        ) as mock_open:
            self.window.ui.actionOpenProject.trigger()
        mock_open.assert_called_once()


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


class TestGitLabFetchDialogConnectionCard(unittest.TestCase):
    """
    Covers GitLabFetchDialog's Connection card (URL/project/token
    fields) and its own QSettings persistence — separate from
    gui/settings_profile.py, since this dialog only exists while
    open (see docs/superpowers/specs/2026-08-27-gitlab-firmware-
    fetch-design.md, section 4).
    """

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()

    def test_defaults_when_nothing_saved(self):
        from gui.gitlab_dialog import GitLabFetchDialog
        dialog = GitLabFetchDialog(self.window)
        self.assertEqual(dialog.urlEdit.text(), "https://gitlab.com")
        self.assertEqual(dialog.projectEdit.text(), "")
        self.assertEqual(dialog.tokenEdit.text(), "")
        self.assertEqual(
            dialog.tokenEdit.echoMode(), dialog.tokenEdit.EchoMode.Password
        )

    def test_fields_persist_across_dialog_instances(self):
        from gui.gitlab_dialog import GitLabFetchDialog

        dialog1 = GitLabFetchDialog(self.window)
        dialog1.urlEdit.setText("https://gitlab.example.com")
        dialog1.projectEdit.setText("radar-team/suzuki-slp1-firmware")
        dialog1.tokenEdit.setText("glpat-abc123")
        dialog1.urlEdit.textEdited.emit(dialog1.urlEdit.text())

        dialog2 = GitLabFetchDialog(self.window)
        self.assertEqual(dialog2.urlEdit.text(), "https://gitlab.example.com")
        self.assertEqual(dialog2.projectEdit.text(), "radar-team/suzuki-slp1-firmware")
        self.assertEqual(dialog2.tokenEdit.text(), "glpat-abc123")

    def test_ci_tab_is_selected_by_default(self):
        from gui.gitlab_dialog import GitLabFetchDialog
        dialog = GitLabFetchDialog(self.window)
        self.assertEqual(dialog.tabs.currentIndex(), 0)
        self.assertEqual(dialog.tabs.tabText(0), "CI Artifact")
        self.assertEqual(dialog.tabs.tabText(1), "Package Registry")

    def test_ci_browse_table_starts_hidden(self):
        from gui.gitlab_dialog import GitLabFetchDialog
        dialog = GitLabFetchDialog(self.window)
        dialog.show()
        self.app.processEvents()
        self.assertFalse(dialog.ciBrowseTable.isVisible())
        # _run_action() is mocked out here — expanding Browse also
        # starts a real fetch (a real QThread), which this test has
        # no way to wait for/clean up; this test only cares about
        # the visibility toggle. The real fetch-and-populate
        # behavior (including full QThread lifecycle) is covered by
        # tests/test_gitlab_dialog_threading.py, which does wait.
        with unittest.mock.patch.object(dialog, '_run_action'):
            dialog.ciBrowseToggle.click()
        self.assertTrue(dialog.ciBrowseTable.isVisible())

    def test_ci_row_download_button_disabled_when_job_has_no_artifacts(self):
        # Regression test for final-review Fix 5's per-row Download
        # button on ciBrowseTable — must stay disabled for a
        # has_artifacts: False row, same as _on_ci_row_activated()'s
        # existing guard for the double-click path.
        from gui.gitlab_dialog import GitLabFetchDialog
        dialog = GitLabFetchDialog(self.window)
        dialog._populate_ci_browse_table([
            {
                "pipeline_id": 101, "job_id": 4822, "job_name": "lint",
                "ref": "main", "status": "failed",
                "created_at": "2026-08-27T09:15:00Z", "has_artifacts": False,
            },
        ])

        button = dialog.ciBrowseTable.cellWidget(0, 5)
        self.assertIsNotNone(button)
        self.assertFalse(button.isEnabled())

    def test_ci_row_download_button_enabled_when_job_has_artifacts(self):
        from gui.gitlab_dialog import GitLabFetchDialog
        dialog = GitLabFetchDialog(self.window)
        dialog._populate_ci_browse_table([
            {
                "pipeline_id": 100, "job_id": 4821, "job_name": "build_firmware",
                "ref": "main", "status": "success",
                "created_at": "2026-08-27T09:14:00Z", "has_artifacts": True,
            },
        ])

        button = dialog.ciBrowseTable.cellWidget(0, 5)
        self.assertIsNotNone(button)
        self.assertTrue(button.isEnabled())

    def test_job_name_combo_populates_from_browse_results(self):
        from gui.gitlab_dialog import GitLabFetchDialog
        dialog = GitLabFetchDialog(self.window)
        dialog._populate_ci_browse_table([
            {
                "pipeline_id": 100, "job_id": 4821, "job_name": "build_firmware",
                "ref": "main", "status": "success",
                "created_at": "2026-08-27T09:14:00Z", "has_artifacts": True,
            },
            {
                "pipeline_id": 99, "job_id": 4810, "job_name": "lint",
                "ref": "main", "status": "success",
                "created_at": "2026-08-26T09:14:00Z", "has_artifacts": False,
            },
            # Same job name as the first row, from an older pipeline —
            # must not appear twice in the dropdown.
            {
                "pipeline_id": 98, "job_id": 4790, "job_name": "build_firmware",
                "ref": "main", "status": "success",
                "created_at": "2026-08-25T09:14:00Z", "has_artifacts": True,
            },
        ])

        items = [dialog.ciJobEdit.itemText(i) for i in range(dialog.ciJobEdit.count())]
        self.assertEqual(items, ["build_firmware", "lint"])

    def test_job_name_combo_keeps_typed_text_after_browse_populates(self):
        from gui.gitlab_dialog import GitLabFetchDialog
        dialog = GitLabFetchDialog(self.window)
        dialog.ciJobEdit.setEditText("not_in_the_list_yet")

        dialog._populate_ci_browse_table([
            {
                "pipeline_id": 100, "job_id": 4821, "job_name": "build_firmware",
                "ref": "main", "status": "success",
                "created_at": "2026-08-27T09:14:00Z", "has_artifacts": True,
            },
        ])

        self.assertEqual(dialog.ciJobEdit.currentText(), "not_in_the_list_yet")

    def test_job_name_combo_value_persists_across_dialog_instances(self):
        from gui.gitlab_dialog import GitLabFetchDialog

        dialog1 = GitLabFetchDialog(self.window)
        dialog1.ciJobEdit.setEditText("build_firmware")
        dialog1.ciJobEdit.currentTextChanged.emit(dialog1.ciJobEdit.currentText())

        dialog2 = GitLabFetchDialog(self.window)
        self.assertEqual(dialog2.ciJobEdit.currentText(), "build_firmware")


class TestGitLabEntryPointWidgets(unittest.TestCase):
    """
    Covers the two static entry-point widgets for the "Load from
    GitLab" feature — declared in main_window.ui (unlike the
    dialog's own internal widgets, which are Python-built in
    gui/gitlab_dialog.py, same precedent as TestConnectionDialog).
    """

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()

    def test_action_load_from_gitlab_exists_in_file_menu(self):
        self.assertTrue(hasattr(self.window.ui, 'actionLoadFromGitLab'))
        self.assertIn(
            self.window.ui.actionLoadFromGitLab,
            self.window.ui.menuFile.actions(),
        )

    def test_button_load_from_gitlab_exists_on_data_page(self):
        self.assertTrue(hasattr(self.window.ui, 'buttonLoadFromGitLab'))
        self.assertTrue(self.window.ui.buttonLoadFromGitLab.isEnabled())


class TestGitLabFetchDialogPackageTab(unittest.TestCase):

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()

    def test_package_name_persists_across_dialog_instances(self):
        from gui.gitlab_dialog import GitLabFetchDialog

        dialog1 = GitLabFetchDialog(self.window)
        dialog1.packageNameEdit.setText("suzuki-slp1-radar-firmware")
        dialog1.packageNameEdit.textEdited.emit(dialog1.packageNameEdit.text())

        dialog2 = GitLabFetchDialog(self.window)
        self.assertEqual(dialog2.packageNameEdit.text(), "suzuki-slp1-radar-firmware")

    def test_package_browse_table_starts_hidden(self):
        from gui.gitlab_dialog import GitLabFetchDialog
        dialog = GitLabFetchDialog(self.window)
        # dialog.show() + processEvents() are required here: Qt's
        # isVisible() on a child widget is always False while the
        # top-level ancestor has never been shown, on any platform
        # (not offscreen-specific) — Task 4's identical
        # test_ci_browse_table_starts_hidden shipped without this
        # and had to go through a fix round for exactly this reason.
        dialog.show()
        self.app.processEvents()
        # Package table is on Tab 1, so switch to it to test visibility
        dialog.tabs.setCurrentIndex(1)
        self.app.processEvents()
        self.assertFalse(dialog.pkgBrowseTable.isVisible())
        # Same reasoning as test_ci_browse_table_starts_hidden above
        # — _run_action() starts a real QThread this test can't wait
        # for, so it's mocked out; the real fetch path is covered by
        # tests/test_gitlab_dialog_threading.py.
        with unittest.mock.patch.object(dialog, '_run_action'):
            dialog.pkgBrowseToggle.click()
        self.assertTrue(dialog.pkgBrowseTable.isVisible())

    def test_pkg_row_activation_uses_browsed_name_not_live_edit_text(self):
        # Regression test for the final-review Fix 9 finding:
        # _on_pkg_row_activated() used to re-read the LIVE
        # packageNameEdit.text() at click time instead of the
        # package name the row's version list was actually fetched
        # for. If the user browses "A", then edits the field to "B"
        # before clicking a row that still shows one of "A"'s
        # versions, the download must still go out for "A".
        from gui.gitlab_dialog import GitLabFetchDialog
        dialog = GitLabFetchDialog(self.window)
        dialog.packageNameEdit.setText("A")

        with unittest.mock.patch.object(dialog, '_run_action'):
            dialog.pkgBrowseToggle.click()

        dialog._populate_pkg_browse_table([
            {"package_id": 1, "version": "1.0.0", "created_at": "2026-08-01T00:00:00Z"},
        ])

        # User edits the field after browsing, without re-browsing.
        dialog.packageNameEdit.setText("B")

        with unittest.mock.patch.object(dialog, '_run_action') as mock_run:
            dialog._on_pkg_row_activated(0, 0)

        action, params = mock_run.call_args[0]
        self.assertEqual(action, "download_package_version")
        self.assertEqual(params["package_name"], "A")
        self.assertEqual(params["version"], "1.0.0")


class TestGitLabFetchDialogZipPicker(unittest.TestCase):

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()
        from gui.gitlab_dialog import GitLabFetchDialog
        self.dialog = GitLabFetchDialog(self.window)

    def _make_zip_bytes(self, names_and_contents):
        import io
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name, content in names_and_contents:
                zf.writestr(name, content)
        return buf.getvalue()

    def test_download_ready_switches_to_picker_and_lists_files(self):
        self.dialog.show()
        self.app.processEvents()
        data = self._make_zip_bytes([
            ("firmware/RAD_SUZ05_FFI_ForCanFlashing.s3", "S1..."),
            ("firmware/checksum.sha256", "abc123"),
        ])
        self.dialog._on_download_ready(data, "build_firmware-4821.zip")
        self.app.processEvents()

        self.assertEqual(self.dialog.tabs.isVisible(), False)
        self.assertEqual(self.dialog.pickerList.count(), 2)

    def test_recognized_firmware_file_is_preselected(self):
        self.dialog.show()
        self.app.processEvents()
        data = self._make_zip_bytes([
            ("build/manifest.json", "{}"),
            ("firmware/RAD_SUZ05_FFI_ForCanFlashing.s3", "S1..."),
        ])
        self.dialog._on_download_ready(data, "build_firmware-4821.zip")
        self.app.processEvents()

        selected = self.dialog.pickerList.selectedItems()
        self.assertEqual(len(selected), 1)
        self.assertTrue(
            selected[0].text().endswith("RAD_SUZ05_FFI_ForCanFlashing.s3")
        )

    def test_first_of_multiple_recognized_files_is_preselected(self):
        # Regression test for the final-review "last match wins"
        # finding: _show_picker()'s loop used to keep overwriting
        # preselect_row on every match instead of stopping at the
        # first one, contradicting the design spec's "the first
        # entry... is pre-selected". A fixture with only one
        # recognized file (test_recognized_firmware_file_is_preselected
        # above) can't catch a first-vs-last regression — this one
        # has two.
        self.dialog.show()
        self.app.processEvents()
        data = self._make_zip_bytes([
            ("firmware/a.hex", "a"),
            ("firmware/b.hex", "b"),
        ])
        self.dialog._on_download_ready(data, "build_firmware-4821.zip")
        self.app.processEvents()

        selected = self.dialog.pickerList.selectedItems()
        self.assertEqual(len(selected), 1)
        self.assertTrue(selected[0].text().endswith("firmware/a.hex"))

    def test_directory_entries_are_not_listed_as_selectable(self):
        # Regression test for the final-review "empty zip / directory
        # entries listed as selectable" finding: zipfile.namelist()
        # includes directory entries (names ending in "/"), which
        # aren't real files and shouldn't appear as pickable rows.
        self.dialog.show()
        self.app.processEvents()
        # writestr with a trailing "/" name creates a directory entry
        # in the zip's namelist(), same as a real directory added via
        # zf.write() on a folder.
        import io
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("firmware/", "")
            zf.writestr("firmware/RAD_SUZ05_FFI_ForCanFlashing.s3", "S1...")
        data = buf.getvalue()

        self.dialog._on_download_ready(data, "build_firmware-4821.zip")
        self.app.processEvents()

        self.assertEqual(self.dialog.pickerList.count(), 1)
        self.assertTrue(
            self.dialog.pickerList.item(0).text().endswith(
                "RAD_SUZ05_FFI_ForCanFlashing.s3"
            )
        )

    def test_download_temp_dir_removed_on_close(self):
        # Regression test for the final-review "temp dir never
        # cleaned up" finding.
        self.dialog.show()
        self.app.processEvents()
        data = self._make_zip_bytes([
            ("firmware/RAD_SUZ05_FFI_ForCanFlashing.s3", "S1..."),
        ])
        self.dialog._on_download_ready(data, "build_firmware-4821.zip")
        self.app.processEvents()

        download_dir = self.dialog._download_dir
        self.assertTrue(os.path.isdir(download_dir))

        self.dialog.close()

        self.assertFalse(os.path.isdir(download_dir))

    def test_load_selected_file_calls_existing_load_pipeline(self):
        self.dialog.show()
        self.app.processEvents()
        data = self._make_zip_bytes([
            ("firmware/RAD_SUZ05_FFI_ForCanFlashing.s3", "S1130000100055555555555555555555555\n"),
        ])
        self.dialog._on_download_ready(data, "build_firmware-4821.zip")
        self.app.processEvents()

        with unittest.mock.patch.object(
            self.window, '_load_firmware_file', return_value=True
        ) as mock_load:
            self.dialog.pickerLoadButton.click()

        mock_load.assert_called_once()
        loaded_path = mock_load.call_args[0][0]
        self.assertTrue(loaded_path.endswith("RAD_SUZ05_FFI_ForCanFlashing.s3"))

    def test_load_selected_file_closes_dialog(self):
        self.dialog.show()
        self.app.processEvents()
        data = self._make_zip_bytes([
            ("firmware/RAD_SUZ05_FFI_ForCanFlashing.s3", "S1..."),
        ])
        self.dialog._on_download_ready(data, "build_firmware-4821.zip")
        self.app.processEvents()

        with unittest.mock.patch.object(
            self.window, '_load_firmware_file', return_value=True
        ):
            self.dialog.pickerLoadButton.click()

        self.assertFalse(self.dialog.isVisible())

    def test_non_zip_download_loads_directly_without_picker(self):
        with unittest.mock.patch.object(
            self.window, '_load_firmware_file', return_value=True
        ) as mock_load:
            self.dialog._on_download_ready(b"not a zip file at all", "firmware.hex")

        mock_load.assert_called_once()
        self.assertTrue(mock_load.call_args[0][0].endswith("firmware.hex"))

    def test_back_button_returns_to_tabs(self):
        self.dialog.show()
        self.app.processEvents()
        data = self._make_zip_bytes([
            ("firmware/RAD_SUZ05_FFI_ForCanFlashing.s3", "S1..."),
        ])
        self.dialog._on_download_ready(data, "build_firmware-4821.zip")
        self.app.processEvents()
        self.dialog.pickerBackButton.click()
        self.app.processEvents()

        self.assertTrue(self.dialog.tabs.isVisible())


if __name__ == "__main__":
    unittest.main()

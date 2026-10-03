# ==================================================
# Project File (.sfproj) Headless Reader Tests
# ==================================================
#
# core/project_config.py reads the same files
# gui/project_file.py writes, into CLI-shaped values instead of
# widgets (cli.py's --project). Two things make this worth
# pinning:
#
#  1. The file stores combo *indices*, not text. The index ->
#     meaning mapping lives in core/project_config.py and is
#     checked here against gui/main_window.ui's actual item
#     order — so reordering a combo in Designer fails a test
#     instead of silently flashing the wrong Radar Side from a
#     saved project.
#
#  2. The GUI tolerates a project whose Security DLL has moved
#     (falls back to the built-in algorithm) and an unticked
#     "Active Security Access" (no DLL at all). The CLI reader
#     has to make exactly the same two calls, or a project
#     would mean different things in the two entry points.
# ==================================================

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from core.project_config import (
    CAN_FD_BY_INDEX,
    PROJECT_FORMAT_VERSION,
    RADAR_SIDE_BY_INDEX,
    SEQUENCE_BY_INDEX,
    ProjectFileError,
    load_project,
)

SAMPLE_HEX = os.path.join(os.path.dirname(__file__), "sample.hex")
UI_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "gui", "main_window.ui",
)


def _write_project(tmp_dir, **overrides):

    data = {
        "format_version": PROJECT_FORMAT_VERSION,
        "firmware_files": [{"path": SAMPLE_HEX, "checked": True}],
        "hardware": {
            "is_virtual": True, "channel": -1, "serial": -1,
        },
        "radar_side_index": 0,
        "logical_link_index": 0,
        "security_dll_path": "",
        "security_access_enabled": False,
        "flash_sequence_index": 0,
        "compression_method": 0,
        "encryption_method": 0,
        "tester_serial_number": "",
    }
    data.update(overrides)

    path = os.path.join(tmp_dir, "demo.sfproj")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)

    return path


def _combo_items(name):
    """
    The <item><property name="text"> strings of one combo in
    gui/main_window.ui, in Designer's order — read as raw XML
    (same way every other .ui assertion in this suite does)
    rather than instantiating the whole MainWindow.
    """

    import re

    with open(UI_FILE, encoding="utf-8") as f:
        xml = f.read()

    start = xml.index(f'name="{name}"')
    # Up to the widget's closing tag: items belong to this combo
    # only.
    end = xml.index("</widget>", start)
    block = xml[start:end]

    return re.findall(r"<string>(.*?)</string>", block)


class TestComboIndexMappingMatchesTheUi(unittest.TestCase):

    def test_radar_side_indices(self):
        items = _combo_items("comboBoxRadarSide")
        self.assertEqual(len(items), len(RADAR_SIDE_BY_INDEX))
        for index, side in RADAR_SIDE_BY_INDEX.items():
            self.assertTrue(
                items[index].lower().startswith(side),
                f"comboBoxRadarSide item {index} is "
                f"{items[index]!r}, not {side!r}",
            )

    def test_flash_sequence_indices(self):
        items = _combo_items("comboBoxFlashSequence")
        self.assertEqual(len(items), len(SEQUENCE_BY_INDEX))
        self.assertIn("Suzuki", items[0])
        self.assertIn("Generic", items[1])
        self.assertEqual(SEQUENCE_BY_INDEX[0], "suzuki")
        self.assertEqual(SEQUENCE_BY_INDEX[1], "generic")

    def test_logical_link_indices(self):
        items = _combo_items("comboBoxLogicalLink")
        self.assertEqual(items[0], "CAN")
        self.assertEqual(items[1], "CAN FD")
        self.assertIs(CAN_FD_BY_INDEX[0], False)
        self.assertIs(CAN_FD_BY_INDEX[1], True)


class TestLoadProject(unittest.TestCase):

    def test_reads_a_default_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load_project(_write_project(tmp))

        self.assertEqual(config["firmware_files"], [SAMPLE_HEX])
        self.assertEqual(config["hardware"], "virtual")
        self.assertEqual(config["radar_side"], "s0")
        self.assertEqual(config["sequence"], "suzuki")
        self.assertIs(config["can_fd"], False)
        self.assertIsNone(config["security_dll"])

    def test_decodes_every_combo_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load_project(_write_project(
                tmp,
                radar_side_index=1,
                flash_sequence_index=1,
                logical_link_index=1,
            ))

        self.assertEqual(config["radar_side"], "s1")
        self.assertEqual(config["sequence"], "generic")
        self.assertIs(config["can_fd"], True)

    def test_unticked_files_are_excluded_not_flashed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_project(tmp, firmware_files=[
                {"path": SAMPLE_HEX, "checked": False},
            ])
            config = load_project(path)

        self.assertEqual(config["firmware_files"], [])
        self.assertEqual(config["excluded_files"], [SAMPLE_HEX])

    def test_missing_firmware_is_listed_not_raised(self):
        # Every missing file is reported at once so the operator
        # fixes them in one pass; cli.py turns this into exit 2.
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_project(tmp, firmware_files=[
                {"path": "/nope/a.hex", "checked": True},
                {"path": "/nope/b.hex", "checked": True},
            ])
            config = load_project(path)

        self.assertEqual(len(config["missing_files"]), 2)
        self.assertEqual(config["firmware_files"], [])

    def test_real_hardware_channel_and_serial(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load_project(_write_project(tmp, hardware={
                "is_virtual": False, "channel": 2, "serial": 123456,
            }))

        self.assertEqual(config["hardware"], "vector")
        self.assertEqual(config["channel"], 2)
        self.assertEqual(config["serial"], 123456)

    def test_serial_minus_one_means_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load_project(_write_project(tmp, hardware={
                "is_virtual": False, "channel": 0, "serial": -1,
            }))

        self.assertIsNone(config["serial"])


class TestSecurityAccessGate(unittest.TestCase):

    def test_dll_ignored_when_security_access_is_unticked(self):
        # Mirrors ConfigureTabMixin.get_security_dll_path():
        # unticked really does mean "dummy algorithm".
        with tempfile.TemporaryDirectory() as tmp:
            dll = os.path.join(tmp, "sec.dll")
            open(dll, "wb").close()
            config = load_project(_write_project(
                tmp,
                security_dll_path=dll,
                security_access_enabled=False,
            ))

        self.assertIsNone(config["security_dll"])

    def test_dll_used_when_ticked_and_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            dll = os.path.join(tmp, "sec.dll")
            open(dll, "wb").close()
            config = load_project(_write_project(
                tmp,
                security_dll_path=dll,
                security_access_enabled=True,
            ))

        self.assertEqual(config["security_dll"], dll)

    def test_moved_dll_falls_back_to_the_builtin_algorithm(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load_project(_write_project(
                tmp,
                security_dll_path="/gone/sec.dll",
                security_access_enabled=True,
            ))

        self.assertIsNone(config["security_dll"])
        self.assertTrue(config["security_access_enabled"])


class TestTesterSerialAndNibbles(unittest.TestCase):

    def test_hex_text_becomes_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load_project(_write_project(
                tmp, tester_serial_number="AABBCCDD"
            ))
        self.assertEqual(config["tester_serial"], bytes.fromhex("AABBCCDD"))

    def test_empty_falls_back_to_the_documented_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load_project(_write_project(tmp))
        self.assertEqual(
            config["tester_serial"],
            bytes.fromhex("00112233445566778899"),
        )

    def test_hand_edited_garbage_falls_back_instead_of_raising(self):
        # A .sfproj can be edited outside the GUI, bypassing the
        # interactive hex validator — same tolerance as
        # get_tester_serial_number().
        with tempfile.TemporaryDirectory() as tmp:
            config = load_project(_write_project(
                tmp, tester_serial_number="ZZ"
            ))
        self.assertEqual(
            config["tester_serial"],
            bytes.fromhex("00112233445566778899"),
        )

    def test_out_of_range_nibbles_clamp_to_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load_project(_write_project(
                tmp, compression_method=99, encryption_method=-3
            ))
        self.assertEqual(config["compression"], 0)
        self.assertEqual(config["encryption"], 0)


class TestProjectErrors(unittest.TestCase):

    def test_unreadable_file(self):
        with self.assertRaises(ProjectFileError):
            load_project("/nonexistent/demo.sfproj")

    def test_malformed_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.sfproj")
            with open(path, "w", encoding="utf-8") as f:
                f.write("{not json")
            with self.assertRaises(ProjectFileError):
                load_project(path)

    def test_newer_format_version_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_project(
                tmp, format_version=PROJECT_FORMAT_VERSION + 1
            )
            with self.assertRaises(ProjectFileError) as ctx:
                load_project(path)
        self.assertIn("newer", str(ctx.exception))

    def test_json_that_is_not_an_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "list.sfproj")
            with open(path, "w", encoding="utf-8") as f:
                json.dump([1, 2, 3], f)
            with self.assertRaises(ProjectFileError):
                load_project(path)


class TestFormatVersionIsSharedWithTheGui(unittest.TestCase):

    def test_gui_imports_the_same_constant(self):
        # gui/project_file.py writes format_version; this reader
        # validates it. One constant, imported — not two
        # literals that can drift.
        from gui import project_file
        self.assertIs(
            project_file.PROJECT_FORMAT_VERSION,
            PROJECT_FORMAT_VERSION,
        )


if __name__ == "__main__":
    unittest.main()

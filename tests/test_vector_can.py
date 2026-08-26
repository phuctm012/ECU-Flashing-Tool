# ==================================================
# Vector CAN Detection Tests
# ==================================================
#
# Covers detect_vector_channels()'s "is_on_bus" field and
# detect_running_vector_tools() — the two signals behind the
# CANoe/CANalyzer/CANape bus-conflict warning (see
# gui/configure_tab.py: detect_can_conflict_warning(), and
# cli.py: _warn_can_conflict()). Real Vector hardware/tools
# aren't available in this dev/test environment, so both are
# exercised via mocking.
# ==================================================

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from communication.vector_can import (
    detect_vector_channels,
    detect_vector_channels_with_error,
    detect_running_vector_tools,
)


def _patched_canlib(fake_canlib):
    """
    detect_vector_channels() does `from can.interfaces.vector
    import canlib` — python-can isn't installed in this dev/
    test env, so every level of that dotted path needs a
    sys.modules stand-in for the import to resolve at all, with
    "can.interfaces.vector"'s .canlib attribute set to our fake.
    """

    return patch.dict(sys.modules, {
        "can": MagicMock(),
        "can.interfaces": MagicMock(),
        "can.interfaces.vector": MagicMock(canlib=fake_canlib),
        "can.interfaces.vector.canlib": fake_canlib,
    })


class TestDetectVectorChannelsIsOnBus(unittest.TestCase):

    def _make_config(self, channel_index, is_on_bus):
        cfg = MagicMock()
        cfg.channel_index = channel_index
        cfg.hw_name = "VN1640A"
        cfg.hw_channel = 0
        cfg.is_on_bus = is_on_bus
        return cfg

    def test_is_on_bus_true_is_passed_through(self):
        fake_canlib = MagicMock()
        fake_canlib.get_channel_configs.return_value = [
            self._make_config(0, True),
        ]
        with _patched_canlib(fake_canlib):
            channels = detect_vector_channels()
        self.assertEqual(len(channels), 1)
        self.assertTrue(channels[0]["is_on_bus"])

    def test_is_on_bus_false_by_default(self):
        fake_canlib = MagicMock()
        fake_canlib.get_channel_configs.return_value = [
            self._make_config(0, False),
        ]
        with _patched_canlib(fake_canlib):
            channels = detect_vector_channels()
        self.assertEqual(len(channels), 1)
        self.assertFalse(channels[0]["is_on_bus"])

    def test_missing_attribute_defaults_to_false(self):
        cfg = MagicMock(spec=["channel_index", "hw_name", "hw_channel"])
        cfg.channel_index = 0
        cfg.hw_name = "VN1640A"
        cfg.hw_channel = 0
        fake_canlib = MagicMock()
        fake_canlib.get_channel_configs.return_value = [cfg]
        with _patched_canlib(fake_canlib):
            channels = detect_vector_channels()
        self.assertEqual(channels[0]["is_on_bus"], False)

    def test_no_driver_returns_empty_list(self):
        # python-can / Vector driver not installed in this
        # dev/test env — must not raise, and must return [].
        self.assertEqual(detect_vector_channels(), [])


class TestDetectVectorChannelsWithError(unittest.TestCase):
    """
    Covers detect_vector_channels_with_error() — added after a
    real deployment where the same .exe detected hardware on one
    bench PC but not another, with no way to tell why (a
    --windowed PyInstaller build has no console). Distinguishes
    "nothing plugged in" (error=None) from "something is
    actually broken on this machine" (error=<reason>), unlike
    detect_vector_channels() which collapses both to [].
    """

    def test_success_with_channels_has_no_error(self):
        fake_canlib = MagicMock()
        cfg = MagicMock()
        cfg.channel_index = 0
        cfg.hw_name = "VN1640A"
        cfg.hw_channel = 0
        cfg.is_on_bus = False
        fake_canlib.get_channel_configs.return_value = [cfg]
        with _patched_canlib(fake_canlib):
            channels, error = detect_vector_channels_with_error()
        self.assertEqual(len(channels), 1)
        self.assertIsNone(error)

    def test_import_failure_reports_python_can_missing(self):
        # No python-can / vector extra installed at all — the
        # `from can.interfaces.vector import canlib` import
        # itself fails. Simulated here the same way it happens
        # in this dev/test env: sys.modules has no "can" entry.
        with patch.dict(sys.modules):
            for mod in list(sys.modules):
                if mod == "can" or mod.startswith("can."):
                    del sys.modules[mod]
            channels, error = detect_vector_channels_with_error()
        self.assertEqual(channels, [])
        self.assertIsNotNone(error)
        self.assertIn("python-can", error)

    def test_driver_exception_reports_underlying_message(self):
        # python-can is installed, but the XL Driver Library
        # call itself fails (e.g. driver not installed on this
        # machine) — the real exception text must reach the
        # caller, not just an empty list.
        fake_canlib = MagicMock()
        fake_canlib.get_channel_configs.side_effect = OSError(
            "XL Driver Library not found"
        )
        with _patched_canlib(fake_canlib):
            channels, error = detect_vector_channels_with_error()
        self.assertEqual(channels, [])
        self.assertIn("XL Driver Library not found", error)

    def test_no_hardware_plugged_in_has_no_error(self):
        # Driver present and working, just nothing connected —
        # an empty channel list here is not a failure.
        fake_canlib = MagicMock()
        fake_canlib.get_channel_configs.return_value = []
        with _patched_canlib(fake_canlib):
            channels, error = detect_vector_channels_with_error()
        self.assertEqual(channels, [])
        self.assertIsNone(error)


class TestDetectRunningVectorTools(unittest.TestCase):

    def test_non_windows_returns_empty_without_calling_tasklist(self):
        with patch("communication.vector_can.sys.platform", "darwin"):
            with patch(
                "communication.vector_can.subprocess.run"
            ) as mock_run:
                result = detect_running_vector_tools()
        self.assertEqual(result, [])
        mock_run.assert_not_called()

    def test_windows_detects_canoe_running(self):
        fake_result = MagicMock()
        fake_result.stdout = "CANoe64.exe   1234  Console  1  200,000 K\n"
        with patch("communication.vector_can.sys.platform", "win32"):
            with patch(
                "communication.vector_can.subprocess.run",
                return_value=fake_result,
            ):
                result = detect_running_vector_tools()
        self.assertIn("canoe", result)

    def test_windows_no_known_tool_running(self):
        fake_result = MagicMock()
        fake_result.stdout = "notepad.exe   1234  Console  1  10,000 K\n"
        with patch("communication.vector_can.sys.platform", "win32"):
            with patch(
                "communication.vector_can.subprocess.run",
                return_value=fake_result,
            ):
                result = detect_running_vector_tools()
        self.assertEqual(result, [])

    def test_subprocess_failure_returns_empty_never_raises(self):
        with patch("communication.vector_can.sys.platform", "win32"):
            with patch(
                "communication.vector_can.subprocess.run",
                side_effect=OSError("tasklist not found"),
            ):
                result = detect_running_vector_tools()
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()

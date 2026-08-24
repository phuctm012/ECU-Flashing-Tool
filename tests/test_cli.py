# ==================================================
# CLI Tests
# ==================================================
#
# Exercises cli.py's commands via its main(argv) entry
# point (returns an exit code, doesn't call sys.exit()
# directly), capturing stdout instead of spawning a real
# subprocess — faster and works the same cross-platform.
# ==================================================

import io
import os
import re
import sys
import contextlib
import tempfile
import unittest

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

import cli
from parsers.auto_parser import parse_firmware_file
from parsers.hex_parser import HexParseError

SAMPLE_HEX = os.path.join(os.path.dirname(__file__), "sample.hex")


def _run_cli(argv):
    """
    Run cli.main(argv), returning (exit_code, combined_output).
    Combines stdout + stderr (cli.py intentionally sends
    errors/failure summaries to stderr, like any well-behaved
    CLI) so tests can assert on either without caring which
    stream a given line landed on.
    """

    out_buf = io.StringIO()
    err_buf = io.StringIO()
    with contextlib.redirect_stdout(out_buf), \
            contextlib.redirect_stderr(err_buf):
        code = cli.main(argv)
    return code, out_buf.getvalue() + err_buf.getvalue()


class TestAutoParser(unittest.TestCase):

    def test_routes_hex_by_extension(self):
        db = parse_firmware_file(SAMPLE_HEX)
        self.assertGreater(db.total_size, 0)

    def test_routes_bin_with_base_address(self):
        with tempfile.NamedTemporaryFile(
            suffix=".bin", delete=False
        ) as f:
            f.write(bytes([0xAA] * 10))
            path = f.name
        try:
            db = parse_firmware_file(path, base_address=0x8000)
            self.assertEqual(db.segments[0].start_address, 0x8000)
        finally:
            os.unlink(path)

    def test_unknown_extension_falls_back_to_hex(self):
        with self.assertRaises(HexParseError):
            parse_firmware_file("/nonexistent/file.xyz")


class TestCliInfo(unittest.TestCase):

    def test_info_prints_segment_details(self):
        code, out = _run_cli(["info", SAMPLE_HEX])
        self.assertEqual(code, 0)
        self.assertIn("2 segment(s)", out)
        self.assertIn("64 bytes total", out)

    def test_info_missing_file_returns_error_code(self):
        code, out = _run_cli(["info", "/nonexistent/file.hex"])
        self.assertEqual(code, 2)


class TestCliListHardware(unittest.TestCase):

    def test_runs_without_error(self):
        code, out = _run_cli(["list-hardware"])
        self.assertEqual(code, 0)
        self.assertIn("virtual", out)
        self.assertIn("vector", out)
        self.assertIn("0x77B", out)  # Radar Side S0


class TestCliFlashDryRun(unittest.TestCase):

    def test_generic_sequence_dry_run(self):
        code, out = _run_cli(
            ["flash", SAMPLE_HEX, "--sequence", "generic", "--dry-run"]
        )
        self.assertEqual(code, 0)
        self.assertIn("nothing was sent to the ECU", out)
        self.assertIn("Reset ECU", out)

    def test_suzuki_sequence_dry_run(self):
        code, out = _run_cli(
            ["flash", SAMPLE_HEX, "--sequence", "suzuki", "--dry-run"]
        )
        self.assertEqual(code, 0)
        self.assertIn("Confirm Default Session (Network)", out)

    def test_default_sequence_is_suzuki(self):
        # No --sequence given: must default to suzuki (the real
        # trace-validated sequence), not generic.
        code, out = _run_cli(["flash", SAMPLE_HEX, "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("Confirm Default Session (Network)", out)


class TestCliFlashVirtual(unittest.TestCase):

    def test_generic_flash_completes(self):
        code, out = _run_cli(
            ["flash", SAMPLE_HEX, "--sequence", "generic"]
        )
        self.assertEqual(code, 0)
        self.assertIn("Flash completed successfully", out)

    def test_suzuki_flash_with_radar_side_s1(self):
        code, out = _run_cli([
            "flash", SAMPLE_HEX,
            "--sequence", "suzuki",
            "--radar-side", "s1",
            "--verbose",
        ])
        self.assertEqual(code, 0)
        self.assertIn("0x77A", out)  # physical Tx for S1
        self.assertIn("FuncGroup-0x700", out)

    def test_quiet_flash_suppresses_step_output(self):
        code, out = _run_cli(["flash", SAMPLE_HEX, "-q"])
        self.assertEqual(code, 0)
        self.assertNotIn("Read ECU Identification", out)
        self.assertIn("Flash completed successfully", out)

    def test_explicit_tx_rx_override_radar_side(self):
        code, out = _run_cli([
            "flash", SAMPLE_HEX,
            "--tx-id", "0x123", "--rx-id", "0x456",
            "--verbose", "-q",
        ])
        self.assertEqual(code, 0)
        self.assertIn("0x123", out)


class TestCliTestConnection(unittest.TestCase):

    def test_generic_passes_and_restores_default_session(self):
        code, out = _run_cli(
            ["test-connection", "--sequence", "generic"]
        )
        self.assertEqual(code, 0)
        self.assertIn("Read DID 0xF189", out)
        self.assertIn("Restored Default session", out)
        self.assertIn("PASSED", out)

    def test_default_sequence_is_suzuki(self):
        # No --sequence given: must default to suzuki, i.e. the
        # pre-security steps use functional addressing.
        code, out = _run_cli(["test-connection", "--verbose"])
        self.assertEqual(code, 0)
        self.assertIn("Disable DTC Settings (Network)", out)
        self.assertIn("PASSED", out)

    def test_never_touches_erase_or_download(self):
        code, out = _run_cli(["test-connection", "--verbose"])
        self.assertEqual(code, 0)
        # No RequestDownload (0x34) / TransferData (0x36)
        # request ever sent — this command must stay
        # read-only + session/security only. Match the SID
        # as the token right after the target (not anywhere
        # in the output), since "34"/"36" can legitimately
        # appear inside DID response *data* (e.g. ASCII '4'/
        # '6' bytes in "PN-12345-678").
        self.assertIsNone(
            re.search(r"TRACE:\s+\S+\s+34\s", out),
            "RequestDownload (0x34) must never be sent",
        )
        self.assertIsNone(
            re.search(r"TRACE:\s+\S+\s+36\s", out),
            "TransferData (0x36) must never be sent",
        )

    def test_suzuki_radar_side_s1_restores_dtc_and_comm(self):
        code, out = _run_cli([
            "test-connection", "--sequence", "suzuki",
            "--radar-side", "s1", "--verbose",
        ])
        self.assertEqual(code, 0)
        self.assertIn("0x77A", out)  # physical Tx for S1
        self.assertIn("FuncGroup-0x700", out)
        self.assertIn("Restored Default session", out)
        # cleanup must re-enable what it disabled
        self.assertIn("28 00 01", out)  # CommunicationControl enable
        self.assertIn("85 01", out)     # ControlDTCSetting ON

    def test_reads_ecu_identification(self):
        code, out = _run_cli(["test-connection"])
        self.assertEqual(code, 0)
        self.assertIn("SW Version", out)

    def test_vector_without_python_can_fails_gracefully(self):
        code, out = _run_cli([
            "test-connection", "--hardware", "vector",
        ])
        self.assertEqual(code, 1)
        self.assertIn("connection failed", out.lower())


class TestCliFlashErrors(unittest.TestCase):

    def test_vector_without_python_can_fails_gracefully(self):
        # python-can isn't installed in the dev/test env — this
        # must fail cleanly (exit 1, flash_aborted) not crash.
        code, out = _run_cli([
            "flash", SAMPLE_HEX, "--hardware", "vector",
        ])
        self.assertEqual(code, 1)
        self.assertIn("aborted", out.lower())

    def test_bad_file_returns_error_code(self):
        code, out = _run_cli(["flash", "/nonexistent/file.hex"])
        self.assertEqual(code, 2)

    def test_invalid_sequence_choice_raises_systemexit(self):
        with self.assertRaises(SystemExit) as ctx:
            cli.main(["flash", SAMPLE_HEX, "--sequence", "bogus"])
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()

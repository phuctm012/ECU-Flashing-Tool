# ==================================================
# CLI Tests — the commands/options added for parity with the GUI
# ==================================================
#
# tests/test_cli.py covers the original four commands. This file
# covers what was added so the CLI can do everything the GUI can:
#
#   * multiple firmware files in one flash (the GUI's Datablocks
#     table has always allowed several; the CLI took exactly one)
#   * --project, reading a .sfproj saved by File > Save Project As
#   * --report / --trace-csv / --json-summary (Export Report and
#     the Trace tab's Save Log)
#   * batch and parallel (Batch Flash mode, Parallel Flash tab)
#
# Same approach as test_cli.py: drive cli.main(argv) in-process
# and capture both streams, rather than spawning subprocesses.
# ==================================================

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

import cli
from core.project_config import PROJECT_FORMAT_VERSION

SAMPLE_HEX = os.path.join(os.path.dirname(__file__), "sample.hex")


def _run_cli(argv):

    out_buf = io.StringIO()
    err_buf = io.StringIO()
    with contextlib.redirect_stdout(out_buf), \
            contextlib.redirect_stderr(err_buf):
        code = cli.main(argv)
    return code, out_buf.getvalue() + err_buf.getvalue()


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


# ==================================================
# Multiple firmware files
# ==================================================

class TestMultipleFirmwareFiles(unittest.TestCase):

    def test_info_accepts_several_files_and_totals_them(self):
        code, out = _run_cli(["info", SAMPLE_HEX, SAMPLE_HEX])
        self.assertEqual(code, 0)
        self.assertIn("Total: 2 datablock(s)", out)

    def test_flash_builds_download_steps_for_every_file(self):
        # sample.hex has 2 segments, so two files means four
        # Download steps — one per segment per datablock, exactly
        # as build_suzuki_slp1_flash_sequence() splices them in
        # for the GUI's Datablocks table.
        code, out = _run_cli([
            "flash", SAMPLE_HEX, SAMPLE_HEX, "--dry-run",
        ])
        self.assertEqual(code, 0)
        self.assertEqual(out.count("Download Datablock 1"), 2)
        self.assertEqual(out.count("Download Datablock 2"), 2)

    def test_flash_two_files_completes_against_the_virtual_ecu(self):
        code, out = _run_cli([
            "flash", SAMPLE_HEX, SAMPLE_HEX, "--quiet",
        ])
        self.assertEqual(code, 0)
        self.assertIn("completed successfully", out)

    def test_a_parse_error_names_the_offending_file(self):
        code, out = _run_cli([
            "flash", SAMPLE_HEX, "/nonexistent/second.hex",
        ])
        self.assertEqual(code, 2)
        self.assertIn("second.hex", out)

    def test_no_file_and_no_project_is_an_error_not_a_crash(self):
        code, out = _run_cli(["flash"])
        self.assertEqual(code, 2)
        self.assertIn("No firmware file given", out)


# ==================================================
# --project
# ==================================================

class TestProjectOption(unittest.TestCase):

    def test_project_supplies_the_firmware_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = _run_cli([
                "flash", "--project", _write_project(tmp),
                "--dry-run",
            ])
        self.assertEqual(code, 0)
        self.assertIn("sample.hex", out)

    def test_project_supplies_the_sequence_and_radar_side(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_project(
                tmp, flash_sequence_index=1, radar_side_index=1
            )
            code, out = _run_cli(["flash", "--project", path,
                                  "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("(generic)", out)

    def test_an_explicit_flag_beats_the_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_project(tmp, flash_sequence_index=1)
            code, out = _run_cli([
                "flash", "--project", path,
                "--sequence", "suzuki", "--dry-run",
            ])
        self.assertEqual(code, 0)
        self.assertIn("(suzuki)", out)

    def test_explicit_files_beat_the_projects_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_project(tmp, firmware_files=[
                {"path": "/nonexistent/from_project.hex",
                 "checked": True},
            ])
            # The project's file is missing, which is normally
            # fatal — but it's reported before anything else, so
            # this also pins that a broken project is never
            # silently ignored.
            code, out = _run_cli([
                "flash", SAMPLE_HEX, "--project", path, "--dry-run",
            ])
        self.assertEqual(code, 2)
        self.assertIn("missing", out.lower())

    def test_a_missing_project_file_is_fatal(self):
        code, out = _run_cli([
            "flash", "--project", "/nonexistent/x.sfproj",
        ])
        self.assertEqual(code, 2)
        self.assertIn("Project error", out)

    def test_project_works_for_test_connection_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = _run_cli([
                "test-connection", "--project", _write_project(tmp),
                "--quiet",
            ])
        self.assertEqual(code, 0)


# ==================================================
# --report / --trace-csv / --json-summary
# ==================================================

class TestReportArtefacts(unittest.TestCase):

    def test_flash_writes_all_three_artefacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = os.path.join(tmp, "r.html")
            trace = os.path.join(tmp, "r.csv")
            summary = os.path.join(tmp, "r.json")

            code, out = _run_cli([
                "flash", SAMPLE_HEX, "--quiet",
                "--report", report, "--trace-csv", trace,
                "--json-summary", summary,
            ])

            self.assertEqual(code, 0)
            self.assertTrue(os.path.isfile(report))
            self.assertTrue(os.path.isfile(trace))

            with open(summary, encoding="utf-8") as f:
                data = json.load(f)

        self.assertEqual(data["command"], "flash")
        self.assertEqual(data["result"], "PASS")
        self.assertGreater(len(data["steps"]), 10)

    def test_trace_is_captured_without_verbose(self):
        # --verbose only controls *printing*; a report must carry
        # the full trace either way, like the GUI's Trace tab,
        # which fills whether or not anyone is looking at it.
        with tempfile.TemporaryDirectory() as tmp:
            summary = os.path.join(tmp, "r.json")
            _run_cli([
                "flash", SAMPLE_HEX, "--quiet",
                "--json-summary", summary,
            ])
            with open(summary, encoding="utf-8") as f:
                data = json.load(f)

        self.assertGreater(data["trace_row_count"], 10)

    def test_a_failed_flash_still_writes_its_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = os.path.join(tmp, "r.json")
            code, out = _run_cli([
                "flash", SAMPLE_HEX, "--hardware", "vector",
                "--quiet", "--json-summary", summary,
            ])
            # 3, not 1: an unreachable ECU is an infrastructure
            # problem a pipeline may retry — see TestExitCodes.
            self.assertEqual(code, cli.EXIT_NO_ECU)
            with open(summary, encoding="utf-8") as f:
                data = json.load(f)

        self.assertEqual(data["result"], "FAIL")

    def test_test_connection_writes_a_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = os.path.join(tmp, "tc.html")
            code, _ = _run_cli([
                "test-connection", "--quiet", "--report", report,
            ])
            self.assertEqual(code, 0)
            with open(report, encoding="utf-8") as f:
                html = f.read()

        self.assertIn("test-connection report", html)

    def test_an_unwritable_report_path_warns_but_keeps_exit_zero(self):
        # The flash itself succeeded; failing the run because a
        # report file couldn't be written would be worse than the
        # missing file.
        code, out = _run_cli([
            "flash", SAMPLE_HEX, "--quiet",
            "--report", os.path.join(
                "/nonexistent_dir_xyz", "r.html"
            ),
        ])
        self.assertEqual(code, 0)
        self.assertIn("could not write", out.lower())


# ==================================================
# Pipeline support: exit codes, --timeout, ECU info
# ==================================================

class TestExitCodes(unittest.TestCase):
    """
    A pipeline retries an infrastructure problem but not a
    genuine failure, so the two must not share an exit code.
    """

    def test_success_is_zero(self):
        code, _ = _run_cli(["flash", SAMPLE_HEX, "--quiet"])
        self.assertEqual(code, cli.EXIT_OK)

    def test_unreachable_ecu_is_three_not_one(self):
        # python-can is not installed in the test env, so
        # --hardware vector cannot connect — the same shape as a
        # cable pulled out or a channel already taken.
        code, out = _run_cli([
            "flash", SAMPLE_HEX, "--hardware", "vector", "--quiet",
        ])
        self.assertEqual(code, cli.EXIT_NO_ECU)
        self.assertIn("not reachable", out.lower())

    def test_test_connection_unreachable_is_three(self):
        code, _ = _run_cli([
            "test-connection", "--hardware", "vector", "--quiet",
        ])
        self.assertEqual(code, cli.EXIT_NO_ECU)

    def test_bad_arguments_stay_two(self):
        code, _ = _run_cli(["flash", "/nonexistent/x.hex"])
        self.assertEqual(code, cli.EXIT_USAGE)

    def test_the_codes_are_distinct(self):
        codes = [
            cli.EXIT_OK, cli.EXIT_FLASH_FAILED, cli.EXIT_USAGE,
            cli.EXIT_NO_ECU, cli.EXIT_TIMEOUT,
            cli.EXIT_INTERRUPTED,
        ]
        self.assertEqual(len(codes), len(set(codes)))


class TestRunTimeout(unittest.TestCase):

    def test_an_expired_timeout_exits_four(self):
        # Enforced inside UdsClient, not by a watchdog thread: a
        # watchdog can only set the abort flag, which is read
        # between steps, while one request can hold the bus for
        # max_pending * p2_star_timeout.
        code, out = _run_cli([
            "flash", SAMPLE_HEX, "--quiet", "--timeout", "0.001",
        ])
        self.assertEqual(code, cli.EXIT_TIMEOUT)
        self.assertIn("timed out", out.lower())

    def test_a_timeout_still_writes_the_report(self):
        # The whole point: a job killed by the CI runner leaves
        # no evidence, so SFlash must produce its own first.
        with tempfile.TemporaryDirectory() as tmp:
            summary = os.path.join(tmp, "t.json")
            code, _ = _run_cli([
                "flash", SAMPLE_HEX, "--quiet",
                "--timeout", "0.001", "--json-summary", summary,
            ])
            self.assertEqual(code, cli.EXIT_TIMEOUT)
            with open(summary, encoding="utf-8") as f:
                data = json.load(f)

        self.assertEqual(data["result"], "FAIL")

    def test_a_generous_timeout_does_not_interfere(self):
        code, _ = _run_cli([
            "flash", SAMPLE_HEX, "--quiet", "--timeout", "600",
        ])
        self.assertEqual(code, cli.EXIT_OK)

    def test_test_connection_honours_the_timeout(self):
        code, _ = _run_cli([
            "test-connection", "--quiet", "--timeout", "0.001",
        ])
        self.assertEqual(code, cli.EXIT_TIMEOUT)

    def test_batch_applies_the_timeout_per_unit(self):
        code, out = _run_cli([
            "batch", SAMPLE_HEX, "--count", "2", "--quiet",
            "--timeout", "0.001",
        ])
        self.assertNotEqual(code, cli.EXIT_OK)
        # Both units still get a row — the series is reported,
        # not abandoned.
        self.assertIn("FAIL 2", out)


class TestEcuInfoInTheSummary(unittest.TestCase):

    def test_test_connection_records_what_it_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = os.path.join(tmp, "tc.json")
            code, _ = _run_cli([
                "test-connection", "--quiet",
                "--json-summary", summary,
            ])
            self.assertEqual(code, cli.EXIT_OK)
            with open(summary, encoding="utf-8") as f:
                data = json.load(f)

        self.assertIn("ECU Serial Number", data["ecu_info"])
        self.assertTrue(data["ecu_info"]["ECU Serial Number"])

    def test_the_generic_flash_sequence_records_its_did_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = os.path.join(tmp, "g.json")
            _run_cli([
                "flash", SAMPLE_HEX, "--sequence", "generic",
                "--quiet", "--json-summary", summary,
            ])
            with open(summary, encoding="utf-8") as f:
                data = json.load(f)

        self.assertIn("Serial Number", data["ecu_info"])

    def test_the_suzuki_sequence_has_no_did_reads_to_record(self):
        # Not a bug: SUZUKI_SLP1_FLASH_SEQUENCE deliberately has
        # no ReadDataByIdentifier steps (it mirrors a real trace).
        # A pipeline that wants the ECU's identity alongside a
        # Suzuki flash runs test-connection first, or uses batch.
        with tempfile.TemporaryDirectory() as tmp:
            summary = os.path.join(tmp, "s.json")
            _run_cli([
                "flash", SAMPLE_HEX, "--sequence", "suzuki",
                "--quiet", "--json-summary", summary,
            ])
            with open(summary, encoding="utf-8") as f:
                data = json.load(f)

        self.assertEqual(data["ecu_info"], {})

    def test_batch_records_identification_per_unit(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = os.path.join(tmp, "b.json")
            _run_cli([
                "batch", SAMPLE_HEX, "--count", "2", "--quiet",
                "--json-summary", summary,
            ])
            with open(summary, encoding="utf-8") as f:
                data = json.load(f)

        for unit in data["units"]:
            self.assertIn("ECU Serial Number", unit["ecu_info"])
            self.assertTrue(unit["serial"])


# ==================================================
# batch
# ==================================================

class TestBatchCommand(unittest.TestCase):

    def test_dry_run_lists_the_units_and_the_sequence(self):
        code, out = _run_cli([
            "batch", SAMPLE_HEX, "--count", "3", "--dry-run",
        ])
        self.assertEqual(code, 0)
        self.assertIn("3 unit(s)", out)
        self.assertIn("Flash sequence per unit", out)

    def test_two_units_pass_and_exit_zero(self):
        code, out = _run_cli([
            "batch", SAMPLE_HEX, "--count", "2", "--quiet",
        ])
        self.assertEqual(code, 0)
        self.assertIn("PASS 2 | FAIL 0 | ABORTED 0", out)

    def test_report_contains_a_row_per_unit(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = os.path.join(tmp, "b.json")
            code, _ = _run_cli([
                "batch", SAMPLE_HEX, "--count", "2", "--quiet",
                "--json-summary", summary,
            ])
            self.assertEqual(code, 0)
            with open(summary, encoding="utf-8") as f:
                data = json.load(f)

        self.assertEqual(data["command"], "batch")
        self.assertEqual(len(data["units"]), 2)
        self.assertEqual(data["unit_counts"]["PASS"], 2)
        # Identify captured each ECU's serial, which is the whole
        # point of running it before the flash.
        self.assertTrue(data["units"][0]["serial"])

    def test_unit_specs_define_separate_targets(self):
        code, out = _run_cli([
            "batch", SAMPLE_HEX, "--dry-run",
            "--unit", "name=Left,side=s0",
            "--unit", "name=Right,side=s1",
        ])
        self.assertEqual(code, 0)
        self.assertIn("Left", out)
        self.assertIn("Right", out)
        self.assertIn("0x77A", out)

    def test_units_file_is_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            units = os.path.join(tmp, "units.json")
            with open(units, "w", encoding="utf-8") as f:
                json.dump({"units": [
                    {"name": "A", "side": "s0"},
                    {"name": "B", "side": "s1"},
                ]}, f)

            code, out = _run_cli([
                "batch", SAMPLE_HEX, "--units-file", units,
                "--dry-run",
            ])

        self.assertEqual(code, 0)
        self.assertIn("2 unit(s)", out)

    def test_a_bad_unit_spec_exits_two(self):
        code, out = _run_cli([
            "batch", SAMPLE_HEX, "--unit", "channl=0", "--dry-run",
        ])
        self.assertEqual(code, 2)
        self.assertIn("unknown unit field", out)

    def test_no_identify_skips_the_probe(self):
        code, out = _run_cli([
            "batch", SAMPLE_HEX, "--no-identify",
        ])
        self.assertEqual(code, 0)
        self.assertNotIn("identify", out.lower())


# ==================================================
# parallel
# ==================================================

class TestParallelCommand(unittest.TestCase):

    def test_dry_run_lists_the_channels(self):
        code, out = _run_cli([
            "parallel", SAMPLE_HEX, "--dry-run",
            "--unit", "name=Left,side=s0",
            "--unit", "name=Right,side=s1",
        ])
        self.assertEqual(code, 0)
        self.assertIn("2 unit(s)", out)
        self.assertIn("Flash sequence per channel", out)

    def test_four_virtual_channels_all_pass(self):
        code, out = _run_cli([
            "parallel", SAMPLE_HEX, "--count", "4", "--quiet",
        ])
        self.assertEqual(code, 0)
        self.assertIn("PASS 4 | FAIL 0 | ABORTED 0", out)

    def test_two_units_on_the_same_real_channel_are_refused(self):
        # One physical ECU driven by two concurrent UDS sessions
        # would fail in a way that looks like a hardware fault.
        # The GUI makes this impossible by construction (one
        # panel per channel); the CLI has to check.
        code, out = _run_cli([
            "parallel", SAMPLE_HEX, "--hardware", "vector",
            "--unit", "channel=0", "--unit", "channel=0",
            "--dry-run",
        ])
        self.assertEqual(code, 2)
        self.assertIn("same target", out)

    def test_identical_virtual_units_are_allowed(self):
        # Each virtual unit gets its own in-memory bus and
        # simulator, so N identical ones are a legitimate way to
        # smoke-test concurrency.
        code, out = _run_cli([
            "parallel", SAMPLE_HEX, "--count", "2", "--dry-run",
        ])
        self.assertEqual(code, 0)

    def test_report_lists_every_channel(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = os.path.join(tmp, "p.json")
            code, _ = _run_cli([
                "parallel", SAMPLE_HEX, "--count", "3", "--quiet",
                "--json-summary", summary,
            ])
            self.assertEqual(code, 0)
            with open(summary, encoding="utf-8") as f:
                data = json.load(f)

        self.assertEqual(data["command"], "parallel")
        self.assertEqual(len(data["units"]), 3)


if __name__ == "__main__":
    unittest.main()

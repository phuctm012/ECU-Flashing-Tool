# ==================================================
# `--config file.json` Tests
# ==================================================
#
# A run config holds every setting a command takes, so a
# pipeline step or a bench setup is a file under version control
# instead of a twelve-flag command line.
#
# The two properties worth pinning hardest:
#
#   * The accepted keys come from the command's own argparse
#     actions, not from a hand-written list — so the format
#     cannot drift from the flags. TestEverySettingIsConfigurable
#     is the guarantee behind calling it "every setting"; add a
#     flag and it must stay true with no change to
#     core/run_config.py.
#
#   * An unknown key is an error, never ignored. A dropped
#     "timeout_s" means the run quietly has no watchdog at all —
#     exactly the failure --timeout exists to prevent.
#
# Precedence (command line > config > .sfproj > default) is left
# to argparse's own set_defaults rather than hand-written
# tie-breaking, so the tests check the observable result.
# ==================================================

import argparse
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
from core.run_config import (
    RunConfigError,
    check_keys,
    load_run_config,
    normalise_key,
)

SAMPLE_HEX = os.path.join(os.path.dirname(__file__), "sample.hex")


def _run_cli(argv):

    out_buf = io.StringIO()
    err_buf = io.StringIO()
    with contextlib.redirect_stdout(out_buf), \
            contextlib.redirect_stderr(err_buf):
        code = cli.main(argv)
    return code, out_buf.getvalue() + err_buf.getvalue()


def _write_json(directory, name, payload):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    return path


def _parse_with_config(config_path, argv):
    """
    Applies a config the way main() does, then parses — lets a
    test inspect the resolved namespace without running a flash.
    """

    parser = cli.build_arg_parser()
    code = cli._apply_run_config(
        parser, ["flash", "--config", config_path] + list(argv)
    )
    assert code is None, f"config was rejected: {code}"
    return parser.parse_args(["flash"] + list(argv))


# ==================================================
# core/run_config.py
# ==================================================

class TestKeyNormalisation(unittest.TestCase):

    def test_all_three_spellings_mean_the_same_option(self):
        # The operator reads the key off --help, where it is
        # spelled with dashes.
        for spelling in ("--json-summary", "json-summary",
                         "json_summary"):
            self.assertEqual(normalise_key(spelling), "json_summary")

    def test_whitespace_is_tolerated(self):
        self.assertEqual(normalise_key("  --timeout "), "timeout")


class TestLoadRunConfig(unittest.TestCase):

    def test_reads_an_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_json(tmp, "c.json",
                               {"timeout": 900, "quiet": True})
            self.assertEqual(
                load_run_config(path),
                {"timeout": 900, "quiet": True},
            )

    def test_comment_fields_are_dropped(self):
        # JSON has no comment syntax and a config file always
        # wants one.
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_json(tmp, "c.json", {
                "comment": "line 1 bench", "timeout": 900,
            })
            self.assertEqual(load_run_config(path), {"timeout": 900})

    def test_two_spellings_of_one_key_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_json(tmp, "c.json", {
                "json-summary": "a.json", "json_summary": "b.json",
            })
            with self.assertRaises(RunConfigError):
                load_run_config(path)

    def test_a_missing_file_raises(self):
        with self.assertRaises(RunConfigError):
            load_run_config("/nonexistent/config.json")

    def test_malformed_json_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "c.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write("{not json")
            with self.assertRaises(RunConfigError):
                load_run_config(path)

    def test_a_json_list_is_not_a_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_json(tmp, "c.json", [1, 2, 3])
            with self.assertRaises(RunConfigError):
                load_run_config(path)


class TestCheckKeys(unittest.TestCase):

    def test_known_keys_pass(self):
        check_keys({"timeout": 1}, {"timeout", "quiet"}, "flash")

    def test_an_unknown_key_raises_and_suggests(self):
        with self.assertRaises(RunConfigError) as ctx:
            check_keys({"timeout_s": 1}, {"timeout"}, "flash")

        message = str(ctx.exception)
        self.assertIn("timeout_s", message)
        self.assertIn("did you mean 'timeout'", message)

    def test_every_unknown_key_is_listed_at_once(self):
        with self.assertRaises(RunConfigError) as ctx:
            check_keys(
                {"aaa": 1, "bbb": 2}, {"timeout"}, "flash"
            )
        message = str(ctx.exception)
        self.assertIn("aaa", message)
        self.assertIn("bbb", message)
        self.assertIn("2 setting(s)", message)


# ==================================================
# The format follows the flags
# ==================================================

class TestEverySettingIsConfigurable(unittest.TestCase):
    """
    The claim is that a config file can hold *every* setting a
    command takes. That only stays true if the accepted keys are
    read off the parser, so this test asserts exactly that, for
    every command that takes --config.
    """

    COMMANDS = ("flash", "batch", "parallel", "test-connection")

    def _subparser(self, command):
        parser = cli.build_arg_parser()
        return cli._subparser_for(parser, command)

    def test_each_command_accepts_all_of_its_own_options(self):
        for command in self.COMMANDS:
            with self.subTest(command=command):
                subparser = self._subparser(command)
                actions = cli._config_actions(subparser)

                expected = {
                    action.dest
                    for action in subparser._actions
                    if action.dest not in ("help", "func", "config")
                }
                self.assertEqual(set(actions), expected)
                # Not a token few — flash alone has >20.
                self.assertGreater(len(actions), 10)

    def test_the_pipeline_critical_settings_are_all_there(self):
        # The ones that could only be typed by hand before, and
        # the reason this feature exists.
        actions = cli._config_actions(self._subparser("flash"))
        for key in ("timeout", "bitrate", "data_bitrate", "tx_id",
                    "rx_id", "report", "trace_csv", "json_summary",
                    "security_dll", "security_dll_signature",
                    "security_dll_variant", "base_address"):
            self.assertIn(key, actions)

    def test_config_itself_cannot_be_set_from_a_config(self):
        actions = cli._config_actions(self._subparser("flash"))
        self.assertNotIn("config", actions)


# ==================================================
# Applying it
# ==================================================

class TestConfigDrivesTheRun(unittest.TestCase):

    def test_a_run_defined_entirely_by_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = os.path.join(tmp, "r.json")
            path = _write_json(tmp, "job.json", {
                "comment": "bench 2",
                "file": [SAMPLE_HEX],
                "sequence": "generic",
                "radar-side": "s1",
                "timeout": 900,
                "quiet": True,
                "json-summary": summary,
            })

            code, _ = _run_cli(["flash", "--config", path])
            self.assertEqual(code, cli.EXIT_OK)

            with open(summary, encoding="utf-8") as f:
                data = json.load(f)

        self.assertEqual(data["result"], "PASS")
        self.assertEqual(
            data["configuration"]["Flash Sequence"], "generic"
        )
        self.assertEqual(data["configuration"]["Radar Side"], "s1")

    def test_a_command_line_flag_beats_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_json(tmp, "job.json", {
                "file": [SAMPLE_HEX], "sequence": "generic",
            })
            code, out = _run_cli([
                "flash", "--config", path,
                "--sequence", "suzuki", "--dry-run",
            ])

        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("(suzuki)", out)

    def test_the_file_beats_a_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _write_json(tmp, "p.sfproj", {
                "format_version": PROJECT_FORMAT_VERSION,
                "firmware_files": [
                    {"path": SAMPLE_HEX, "checked": True},
                ],
                "hardware": {
                    "is_virtual": True, "channel": -1, "serial": -1,
                },
                "radar_side_index": 0,
                "flash_sequence_index": 0,     # suzuki
                "logical_link_index": 0,
                "security_dll_path": "",
                "security_access_enabled": False,
                "compression_method": 0,
                "encryption_method": 0,
                "tester_serial_number": "",
            })
            config = _write_json(tmp, "job.json", {
                "sequence": "generic",
            })

            code, out = _run_cli([
                "flash", "--config", config,
                "--project", project, "--dry-run",
            ])

        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("(generic)", out)

    def test_values_go_through_the_options_own_type(self):
        # "0x77B" must become an int and a hex string must become
        # bytes, exactly as on the command line — otherwise the
        # sequence builder gets a str and fails much later.
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_json(tmp, "job.json", {
                "file": [SAMPLE_HEX],
                "tx_id": "0x77A",
                "rx_id": 1930,
                "tester_serial": "AABBCCDD",
            })
            args = _parse_with_config(path, [])

        self.assertEqual(args.tx_id, 0x77A)
        self.assertEqual(args.rx_id, 1930)
        self.assertEqual(args.tester_serial, bytes.fromhex("AABBCCDD"))

    def test_a_boolean_flag_can_be_set_from_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_json(tmp, "job.json", {
                "file": [SAMPLE_HEX], "quiet": True,
            })
            args = _parse_with_config(path, [])

        self.assertTrue(args.quiet)

    def test_batch_units_can_live_in_the_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_json(tmp, "job.json", {
                "file": [SAMPLE_HEX],
                "unit": ["name=Left,side=s0", "name=Right,side=s1"],
                "dry-run": True,
            })
            code, out = _run_cli(["batch", "--config", path])

        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("2 unit(s)", out)
        self.assertIn("Left", out)
        self.assertIn("Right", out)


class TestConfigErrors(unittest.TestCase):

    def test_an_unknown_key_exits_two_and_names_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_json(tmp, "job.json", {
                "file": [SAMPLE_HEX], "timeout_s": 900,
            })
            code, out = _run_cli(["flash", "--config", path])

        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertIn("timeout_s", out)
        self.assertIn("did you mean", out)

    def test_an_invalid_choice_exits_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_json(tmp, "job.json", {
                "file": [SAMPLE_HEX], "sequence": "bogus",
            })
            code, out = _run_cli(["flash", "--config", path])

        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertIn("not one of", out)

    def test_an_uninterpretable_value_exits_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_json(tmp, "job.json", {
                "file": [SAMPLE_HEX], "tx_id": "not-a-number",
            })
            code, out = _run_cli(["flash", "--config", path])

        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertIn("tx_id", out)

    def test_a_missing_config_file_exits_two(self):
        code, out = _run_cli([
            "flash", "--config", "/nonexistent/job.json",
        ])
        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertIn("Config error", out)

    def test_config_on_a_command_without_it_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_json(tmp, "job.json", {"timeout": 900})
            code, out = _run_cli([
                "info", SAMPLE_HEX, "--config", path,
            ])

        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertIn("--config needs a command", out)

    def test_no_config_leaves_everything_untouched(self):
        # The whole feature must be invisible when unused.
        code, out = _run_cli(["flash", SAMPLE_HEX, "--dry-run"])
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("(suzuki)", out)


if __name__ == "__main__":
    unittest.main()

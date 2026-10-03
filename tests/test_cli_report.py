# ==================================================
# CLI Report Artefact Tests
# ==================================================
#
# core/report.py builds the HTML/CSV/JSON artefacts cli.py's
# --report / --trace-csv / --json-summary write. It's pure data
# in, text out — no Qt, no widgets — so these tests construct a
# RunRecord directly instead of running a flash.
#
# The one non-obvious thing being pinned: the trace CSV's
# columns and cell formatting must match what the GUI's Trace
# tab writes, so a CLI-produced and a GUI-produced trace of the
# same flash are comparable line for line.
# ==================================================

import csv
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from core import report
from core.report import (
    RESULT_FAIL,
    RESULT_PASS,
    RunRecord,
    build_json_summary,
    build_report_html,
    format_trace_row_cells,
    write_json_summary,
    write_report_html,
    write_trace_csv,
)
from parsers.auto_parser import parse_firmware_file

SAMPLE_HEX = os.path.join(os.path.dirname(__file__), "sample.hex")

TRACE_ROW = {
    "req_ts": 0.006638,
    "req_target": "FuncGroup-0x700",
    "req_data": "10 03",
    "resp_ts": 0.019204,
    "resp_source": "0x78B",
    "resp_data": "50 03 00 32 01 F4",
}


def _record_with_everything():

    record = RunRecord(
        "flash", [("Hardware", "Virtual ECU Simulator")]
    )
    record.datablocks = [parse_firmware_file(SAMPLE_HEX)]
    record.add_step("Start Extended Session (Network)")
    record.add_step("Erase Memory")
    record.add_trace_row(TRACE_ROW)
    record.finish(RESULT_PASS)
    return record


class TestTraceCellFormatting(unittest.TestCase):

    def test_matches_the_guis_six_cells(self):
        # Same formatting as gui/main_window.py's
        # _format_trace_row_cells(): 5 decimal places + "s".
        self.assertEqual(
            format_trace_row_cells(TRACE_ROW),
            (
                "0.00664s", "FuncGroup-0x700", "10 03",
                "0.01920s", "0x78B", "50 03 00 32 01 F4",
            ),
        )

    def test_missing_timestamps_become_empty_strings(self):
        cells = format_trace_row_cells(
            {"req_target": "0x77B", "req_data": "3E 00"}
        )
        self.assertEqual(cells[0], "")
        self.assertEqual(cells[3], "")
        self.assertEqual(cells[4], "")


class TestTraceCsv(unittest.TestCase):

    def test_header_matches_the_gui_trace_table_columns(self):
        # The GUI reads these straight off traceTable's header
        # items in gui/main_window.ui — if that table is ever
        # re-ordered this test is the thing that catches the
        # CSVs drifting apart.
        self.assertEqual(
            report.TRACE_CSV_HEADERS,
            [
                "Request TimeStamp",
                "Request Target",
                "Request Data",
                "Response TimeStamp",
                "Response Source",
                "Response Data",
            ],
        )

    def test_writes_header_plus_one_row_per_trace_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "trace.csv")
            write_trace_csv(path, [TRACE_ROW, TRACE_ROW])

            with open(path, newline="", encoding="utf-8") as f:
                rows = list(csv.reader(f))

        self.assertEqual(rows[0], report.TRACE_CSV_HEADERS)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1][2], "10 03")

    def test_empty_trace_still_writes_the_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "trace.csv")
            write_trace_csv(path, [])
            with open(path, newline="", encoding="utf-8") as f:
                rows = list(csv.reader(f))
        self.assertEqual(len(rows), 1)


class TestHtmlReport(unittest.TestCase):

    def test_contains_every_section(self):
        html = build_report_html(_record_with_everything())
        for heading in ("Summary", "Firmware", "Steps", "Trace"):
            self.assertIn(f"<h2>{heading}</h2>", html)

    def test_units_section_only_appears_for_multi_ecu_runs(self):
        single = build_report_html(_record_with_everything())
        self.assertNotIn("<h2>Units</h2>", single)

        record = _record_with_everything()
        record.add_unit("Unit 1", RESULT_PASS, duration=12.3,
                        serial="SN-1")
        self.assertIn("<h2>Units</h2>", build_report_html(record))

    def test_unit_rows_carry_a_result_class_for_colouring(self):
        record = RunRecord("batch")
        record.datablocks = [parse_firmware_file(SAMPLE_HEX)]
        record.add_unit("Unit 1", RESULT_PASS, duration=1.0)
        record.add_unit("Unit 2", RESULT_FAIL, duration=2.0,
                        reason="NRC 0x33")
        html = build_report_html(record)

        self.assertIn('class="pass"', html)
        self.assertIn('class="fail"', html)
        self.assertIn("NRC 0x33", html)

    def test_escapes_html_in_values(self):
        record = RunRecord("flash", [("Hardware", "<script>x</script>")])
        html = build_report_html(record)
        self.assertNotIn("<script>x</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_no_trace_rows_says_so_instead_of_an_empty_table(self):
        record = RunRecord("flash")
        html = build_report_html(record)
        self.assertIn("No trace rows recorded", html)

    def test_write_report_html_creates_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "report.html")
            write_report_html(path, _record_with_everything())
            with open(path, encoding="utf-8") as f:
                content = f.read()
        self.assertTrue(content.startswith("<!doctype html>"))


class TestJsonSummary(unittest.TestCase):

    def test_carries_the_fields_a_ci_job_asserts_on(self):
        data = build_json_summary(_record_with_everything())

        self.assertEqual(data["command"], "flash")
        self.assertEqual(data["result"], RESULT_PASS)
        self.assertEqual(data["trace_row_count"], 1)
        self.assertEqual(len(data["steps"]), 2)
        self.assertEqual(data["firmware"][0]["segment_count"], 2)
        self.assertEqual(
            data["firmware"][0]["checksum"],
            f"0x{parse_firmware_file(SAMPLE_HEX).checksum:08X}",
        )

    def test_unit_counts_tally_per_result(self):
        record = RunRecord("batch")
        record.add_unit("A", RESULT_PASS, duration=1)
        record.add_unit("B", RESULT_FAIL, duration=1)
        record.add_unit("C", RESULT_PASS, duration=1)

        counts = build_json_summary(record)["unit_counts"]
        self.assertEqual(counts[RESULT_PASS], 2)
        self.assertEqual(counts[RESULT_FAIL], 1)

    def test_written_file_is_valid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "summary.json")
            write_json_summary(path, _record_with_everything())
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        self.assertEqual(data["app"], "SFlash")


if __name__ == "__main__":
    unittest.main()

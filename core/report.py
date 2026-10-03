# ==================================================
# Headless Report Builder
# ==================================================
#
# Builds the HTML/CSV/JSON artefacts cli.py's --report /
# --trace-csv / --json-summary options write, from plain
# Python data only — no Qt, no widgets, no GUI import.
#
# This is deliberately NOT a refactor of gui/report_export.py
# or gui/batch_flash.py's _build_batch_report_html(): those
# build their HTML by reading the live widgets (traceTable,
# stepsTable, tableWidgetDatablocks, the combo boxes), which
# is the only data source they have and the reason they can't
# be reused headlessly. This module takes the same information
# as dicts/lists instead, so the CLI can produce an equivalent
# report without constructing a single widget. The two render
# the same sections (Summary / Firmware / Steps / Trace, plus
# a per-unit table for batch and parallel runs) so a report
# handed over by a colleague reads the same whichever entry
# point produced it.
#
# A run's data is collected into a RunRecord (see below), which
# every cli.py command fills the same way, so one builder
# covers flash, batch, parallel and test-connection.
# ==================================================

import csv
import html
import json
from datetime import datetime

from config.settings import APP_NAME, APP_VERSION

TRACE_CSV_HEADERS = [
    "Request TimeStamp",
    "Request Target",
    "Request Data",
    "Response TimeStamp",
    "Response Source",
    "Response Data",
]

RESULT_PASS = "PASS"
RESULT_FAIL = "FAIL"
RESULT_ABORTED = "ABORTED"


# ==================================================
# Run record
# ==================================================

class RunRecord:
    """
    Everything a CLI run wants to report on, accumulated as it
    happens. Plain attributes, no Qt signals — cli.py appends
    to it from the same callbacks it prints from.

    Attributes:
        command: "flash" / "batch" / "parallel" / "test-connection"
        config_rows: list of (label, value) shown in Summary
        datablocks: list of parsers Datablock objects
        steps: list of (timestamp_str, description) in run order
        trace_rows: list of FlashWorker.trace_row dicts
        units: list of per-unit dicts (batch units / parallel
            channels), each with name, serial, result, duration,
            reason
        result: RESULT_PASS / RESULT_FAIL / RESULT_ABORTED
    """

    def __init__(self, command, config_rows=None):

        self.command = command
        self.config_rows = list(config_rows or [])
        self.datablocks = []
        self.steps = []
        self.trace_rows = []
        self.units = []
        # ECU identification read during the run (serial number,
        # SW/HW versions). In the JSON summary so a pipeline can
        # record which ECU got which build without scraping
        # stdout.
        self.ecu_info = {}
        self.result = None
        self.started_at = datetime.now()
        self.finished_at = None

    # ==========================================
    # Accumulation
    # ==========================================

    def add_step(self, description):
        self.steps.append(
            (datetime.now().strftime("%H:%M:%S.%f")[:-3], description)
        )

    def add_trace_row(self, row):
        self.trace_rows.append(dict(row))

    def add_ecu_info(self, info):
        if info:
            self.ecu_info.update(info)

    def add_unit(self, name, result, duration=None, serial=None,
                 reason=None, ecu_info=None):
        self.units.append({
            "index": len(self.units) + 1,
            "name": name,
            "serial": serial or "",
            "result": result,
            "duration": duration,
            "reason": reason or "",
            "ecu_info": dict(ecu_info or {}),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })

    def finish(self, result):
        self.result = result
        self.finished_at = datetime.now()
        return result

    # ==========================================
    # Derived values
    # ==========================================

    @property
    def duration_seconds(self):
        end = self.finished_at or datetime.now()
        return round((end - self.started_at).total_seconds(), 1)

    def unit_counts(self):
        counts = {RESULT_PASS: 0, RESULT_FAIL: 0, RESULT_ABORTED: 0}
        for unit in self.units:
            key = (unit["result"] or "").upper()
            if key in counts:
                counts[key] += 1
        return counts


# ==================================================
# Trace formatting (shared with the GUI's columns)
# ==================================================

def format_trace_row_cells(row):
    """
    Turns a FlashWorker.trace_row dict into the same 6 cell
    strings gui/main_window.py's _format_trace_row_cells()
    produces, so the CLI's CSV/HTML trace is byte-for-byte
    comparable with the GUI's "Save Log"/Export Report output.
    """

    req_ts = (
        f"{row['req_ts']:.5f}s"
        if row.get("req_ts") is not None else ""
    )
    resp_ts = (
        f"{row['resp_ts']:.5f}s"
        if row.get("resp_ts") is not None else ""
    )

    return (
        req_ts,
        row.get("req_target") or "",
        row.get("req_data") or "",
        resp_ts,
        row.get("resp_source") or "",
        row.get("resp_data") or "",
    )


def write_trace_csv(file_path, trace_rows):
    """
    Writes the Trace table as CSV with the same header row the
    GUI's Trace tab right-click "Save Log" writes. Raises OSError
    on failure — the caller decides whether that's fatal.
    """

    with open(file_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(TRACE_CSV_HEADERS)
        for row in trace_rows:
            writer.writerow(format_trace_row_cells(row))

    return file_path


# ==================================================
# JSON summary (for CI pipelines)
# ==================================================

def build_json_summary(record):
    """
    A machine-readable summary of the run — the shape a CI job
    wants to assert on, without parsing HTML or scraping stdout.
    """

    return {
        "app": APP_NAME,
        "version": APP_VERSION,
        "command": record.command,
        "result": record.result,
        "started_at": record.started_at.isoformat(timespec="seconds"),
        "finished_at": (
            record.finished_at.isoformat(timespec="seconds")
            if record.finished_at else None
        ),
        "duration_seconds": record.duration_seconds,
        "configuration": {k: v for k, v in record.config_rows},
        "firmware": [
            {
                "file_name": db.file_name,
                "file_path": getattr(db, "file_path", ""),
                "segment_count": db.segment_count,
                "total_size": db.total_size,
                "checksum": f"0x{db.checksum:08X}",
            }
            for db in record.datablocks
        ],
        "steps": [
            {"timestamp": ts, "description": desc}
            for ts, desc in record.steps
        ],
        "ecu_info": record.ecu_info,
        "units": record.units,
        "unit_counts": record.unit_counts(),
        "trace_row_count": len(record.trace_rows),
    }


def write_json_summary(file_path, record):

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(build_json_summary(record), f, indent=2)

    return file_path


# ==================================================
# HTML report
# ==================================================

_REPORT_STYLE = """
  body { font-family: Segoe UI, Arial, sans-serif; margin: 24px; color: #1a1a1a; }
  h1 { font-size: 20px; margin-bottom: 0; }
  .subtitle { color: #666; margin-top: 4px; margin-bottom: 24px; }
  h2 { font-size: 15px; background: #E0E0E0; padding: 6px 8px; margin-top: 28px; }
  table { border-collapse: collapse; width: 100%; margin-top: 8px; }
  th, td { border: 1px solid #ccc; padding: 4px 8px; text-align: left; font-size: 13px; }
  th { background: #f2f2f2; }
  .summary td:first-child { font-weight: bold; width: 220px; }
  .pass { background: #DFF5DF; }
  .fail { background: #FADBD8; }
  .aborted { background: #FDEBD0; }
  td.mono { font-family: Consolas, monospace; font-size: 12px; }
"""


def _result_class(result):
    key = (result or "").upper()
    if key == RESULT_PASS:
        return "pass"
    if key == RESULT_FAIL:
        return "fail"
    if key == RESULT_ABORTED:
        return "aborted"
    return ""


def _summary_table(record):

    e = html.escape

    rows = list(record.config_rows)
    rows.append(("Result", record.result or "N/A"))
    rows.append(("Duration", f"{record.duration_seconds}s"))
    rows.append((
        "Started",
        record.started_at.strftime("%Y-%m-%d %H:%M:%S"),
    ))

    if record.units:
        counts = record.unit_counts()
        rows.append(("Total PASS", str(counts[RESULT_PASS])))
        rows.append(("Total FAIL", str(counts[RESULT_FAIL])))
        rows.append(("Total ABORTED", str(counts[RESULT_ABORTED])))

    body = "".join(
        f"<tr><td>{e(str(k))}</td><td>{e(str(v))}</td></tr>"
        for k, v in rows
    )
    return f'<table class="summary">{body}</table>'


def _datablocks_table(record):

    e = html.escape

    if not record.datablocks:
        return "<p>No firmware file loaded.</p>"

    rows = "".join(
        "<tr>"
        f"<td>{e(db.file_name)}</td>"
        f"<td>{db.segment_count}</td>"
        f"<td>{db.total_size}</td>"
        f"<td>0x{db.checksum:08X}</td>"
        f"<td>{e(getattr(db, 'file_path', '') or '')}</td>"
        "</tr>"
        for db in record.datablocks
    )

    header = (
        "<tr><th>Datablock</th><th>Segments</th><th>Size (bytes)</th>"
        "<th>Checksum</th><th>Path</th></tr>"
    )
    return f"<table>{header}{rows}</table>"


def _steps_table(record):

    e = html.escape

    if not record.steps:
        return "<p>No steps recorded.</p>"

    rows = "".join(
        f"<tr><td>{e(ts)}</td><td>{e(desc)}</td></tr>"
        for ts, desc in record.steps
    )
    header = "<tr><th>Timestamp</th><th>Description</th></tr>"
    return f"<table>{header}{rows}</table>"


def _units_table(record):

    e = html.escape

    rows = "".join(
        f'<tr class="{_result_class(u["result"])}">'
        f'<td>{u["index"]}</td>'
        f'<td>{e(str(u["name"]))}</td>'
        f'<td>{e(str(u["serial"]))}</td>'
        f'<td>{e(str(u["timestamp"]))}</td>'
        f'<td>{e(str(u["result"]).upper())}</td>'
        f'<td>{"" if u["duration"] is None else str(u["duration"]) + "s"}</td>'
        f'<td>{e(str(u["reason"]))}</td>'
        "</tr>"
        for u in record.units
    )

    header = (
        "<tr><th>#</th><th>Unit</th><th>Serial Number</th>"
        "<th>Timestamp</th><th>Result</th><th>Duration</th>"
        "<th>Reason</th></tr>"
    )
    return f"<table>{header}{rows}</table>"


def _ecu_table(record):

    e = html.escape

    rows = "".join(
        f"<tr><td>{e(str(k))}</td><td>{e(str(v))}</td></tr>"
        for k, v in record.ecu_info.items()
    )
    return f'<table class="summary">{rows}</table>'


def _trace_table(record):

    e = html.escape

    if not record.trace_rows:
        return (
            "<p>No trace rows recorded (run with --verbose to "
            "capture CAN/UDS frames).</p>"
        )

    rows = "".join(
        "<tr>" + "".join(
            f'<td class="mono">{e(cell)}</td>'
            for cell in format_trace_row_cells(row)
        ) + "</tr>"
        for row in record.trace_rows
    )

    header = "<tr>" + "".join(
        f"<th>{e(h)}</th>" for h in TRACE_CSV_HEADERS
    ) + "</tr>"
    return f"<table>{header}{rows}</table>"


def build_report_html(record):
    """
    Renders a RunRecord as a standalone HTML report — the
    headless equivalent of the GUI's Tools > Export Report...
    (Summary / Firmware / Steps / Trace), plus a per-unit table
    for the multi-ECU commands.
    """

    e = html.escape
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    title = f"{APP_NAME} v{APP_VERSION} — {record.command} report"

    units_section = (
        f"<h2>Units</h2>\n{_units_table(record)}\n"
        if record.units else ""
    )

    ecu_section = (
        f"<h2>ECU Identification</h2>\n{_ecu_table(record)}\n"
        if record.ecu_info else ""
    )

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{e(title)} — {e(now)}</title>
<style>{_REPORT_STYLE}</style>
</head>
<body>
<h1>{e(title)}</h1>
<div class="subtitle">Exported {e(now)} (command line)</div>

<h2>Summary</h2>
{_summary_table(record)}

<h2>Firmware</h2>
{_datablocks_table(record)}

{ecu_section}{units_section}<h2>Steps</h2>
{_steps_table(record)}

<h2>Trace</h2>
{_trace_table(record)}

</body>
</html>"""


def write_report_html(file_path, record):

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(build_report_html(record))

    return file_path

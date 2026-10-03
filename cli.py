#!/usr/bin/env python3
# ==================================================
# SFlash — Command Line Interface
# ==================================================
#
# Run the app's flashing functions without the GUI:
# parse/inspect a firmware file, or run a full flash
# sequence against the Virtual ECU Simulator or real
# Vector hardware.
#
# Cross-platform (Windows/macOS/Linux) — uses only the
# stdlib + PySide6.
#
# Deliberately creates NO QCoreApplication/QApplication. The
# workers it drives are QObjects that report progress through
# Signals (core/flash_controller.py), but every CLI command
# runs its worker on the thread that owns it — the sequential
# commands on the main thread, each parallel channel on the
# thread that constructed its own worker — so every emit is a
# direct call. No event loop is involved, and nothing here
# needs an application object; verified by
# tests/test_cli_no_qt_gui.py, which also pins that
# PySide6.QtWidgets/QtGui never get imported.
#
# That matters twice over. It keeps a frozen CLI down to
# QtCore instead of dragging in QtGui + QtWidgets for one
# unused class (roughly a third of the Qt payload instead of
# all of it — see build_cli.bat), and it removes the
# single-instance-per-process hazard entirely: the test suite
# runs cli.py and real widgets in one process, and a CLI that
# creates nothing can never create the wrong kind first.
#
# Usage:
#   python cli.py info tests/sample.hex
#   python cli.py flash tests/sample.hex
#   python cli.py flash app.s3 calib.s3 --hardware vector --channel 1 \
#       --sequence suzuki --radar-side s1
#   python cli.py list-hardware
#   python cli.py test-connection --hardware vector --channel 0 \
#       --sequence suzuki --verbose
#   python cli.py batch firmware.s3 --count 5 --pause \
#       --report batch.html
#   python cli.py parallel firmware.s3 \
#       --unit "name=Left,channel=0,side=s0" \
#       --unit "name=Right,channel=1,side=s1"
#   python cli.py gitlab jobs --ref main --success-only
#   python cli.py flash --project line1.sfproj
#
# Note: real Vector hardware (VN1640A/VN1630) requires the
# Vector XL Driver Library, which is Windows-only — the
# --hardware vector option is only usable there. The
# Virtual ECU Simulator (--hardware virtual, the default)
# works identically on every platform.
# ==================================================

import argparse
import os
import sys

from config.settings import (
    APP_NAME,
    APP_VERSION,
    SUZUKI_RADAR_CAN_IDS,
)
from parsers.auto_parser import parse_firmware_file
from parsers.hex_parser import HexParseError
from communication.security_dll import bitness_mismatch
from communication.vector_can import (
    detect_vector_channels,
    detect_vector_channels_with_error,
    detect_running_vector_tools,
)
from core.flash_sequence import (
    build_flash_sequence,
    build_suzuki_slp1_flash_sequence,
)
from core.flash_controller import FlashWorker
from core.batch_runner import BatchRunner
from core.flash_unit import (
    UnitSpecError,
    build_units,
    load_units_file,
    parse_unit_spec,
)
from core.parallel_runner import ParallelRunner
from core.project_config import ProjectFileError, load_project
from core.run_config import (
    RunConfigError,
    check_keys,
    load_run_config,
)
from core.report import (
    RESULT_ABORTED,
    RESULT_FAIL,
    RESULT_PASS,
    RunRecord,
    write_json_summary,
    write_report_html,
    write_trace_csv,
)
from cli_gitlab import add_gitlab_subparser


# ==================================================
# Exit codes
# ==================================================
#
# Split by *kind* of failure so a pipeline can retry an
# infrastructure problem (the ECU was not reachable, the run ran
# out of time) without retrying a genuine one (the ECU answered
# and the sequence failed). Anything that is not one of these is
# a bug, not a result.
# ==================================================

EXIT_OK = 0
EXIT_FLASH_FAILED = 1      # the ECU answered; the sequence failed
EXIT_USAGE = 2             # bad arguments, unreadable file/project
EXIT_NO_ECU = 3            # could not reach the ECU at all
EXIT_TIMEOUT = 4           # --timeout ran out
EXIT_INTERRUPTED = 130     # Ctrl+C

FAILURE_EXIT_CODES = {
    "connection": EXIT_NO_ECU,
    "timeout": EXIT_TIMEOUT,
}


def _exit_code_for(failure_kind, default=EXIT_FLASH_FAILED):
    return FAILURE_EXIT_CODES.get(failure_kind, default)


# ==================================================
# Helpers
# ==================================================

def _parse_hex_int(value):
    """argparse type= helper: accepts '0x77B' or '1915'."""

    try:
        return int(value, 0)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"invalid integer (hex or decimal): {value!r}"
        )


def _parse_hex_bytes(value):
    """argparse type= helper: accepts a hex string like
    '00112233445566778899' (even number of hex digits)."""

    text = value.strip()

    if len(text) % 2 != 0:
        raise argparse.ArgumentTypeError(
            f"hex string must have an even number of digits: "
            f"{value!r}"
        )

    try:
        return bytes.fromhex(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"invalid hex string: {value!r}"
        )


def _make_trace_handlers(verbose, record=None):
    """
    Returns (on_trace_message, on_trace_row) callbacks for
    FlashWorker.trace_message/trace_row — shared by any command
    that wants --verbose CAN/UDS trace output.

    Printing is gated on --verbose, but recording into `record`
    is not: --report/--trace-csv must contain the full trace
    whether or not the operator also wanted it on screen (the
    GUI's Trace tab fills unconditionally too).
    """

    def on_trace_message(message):
        if verbose:
            print(f"      TRACE: {message}")

    def on_trace_row(row):
        if record is not None:
            record.add_trace_row(row)
        if not verbose:
            return
        req = f"{row.get('req_target') or '':<16} {row.get('req_data') or ''}"
        if row.get("resp_data"):
            resp = f" -> {row.get('resp_source')}: {row['resp_data']}"
        else:
            resp = ""
        print(f"      TRACE: {req}{resp}")

    return on_trace_message, on_trace_row


# ==================================================
# --project / option resolution
# ==================================================
#
# Every option a .sfproj can supply is declared with
# default=None, so "not given on the command line" is
# distinguishable from "given, and happens to equal the
# default". _resolve_config() then fills each one in priority
# order: explicit flag > project file > built-in default.
# Without --project the project layer is simply empty, which is
# why plain `cli.py flash file.hex` behaves exactly as before.
# ==================================================

PROJECT_OVERRIDABLE_DEFAULTS = {
    "hardware": "virtual",
    "channel": 0,
    "serial": None,
    "sequence": "suzuki",
    "radar_side": "s0",
    "can_fd": False,
    "security_dll": None,
    "compression": 0x00,
    "encryption": 0x00,
    "tester_serial": None,
}

# .sfproj key -> CLI option name, for the subset above that a
# project actually stores.
_PROJECT_KEY_TO_OPTION = {
    "hardware": "hardware",
    "channel": "channel",
    "serial": "serial",
    "sequence": "sequence",
    "radar_side": "radar_side",
    "can_fd": "can_fd",
    "security_dll": "security_dll",
    "compression": "compression",
    "encryption": "encryption",
    "tester_serial": "tester_serial",
}


def _resolve_config(args):
    """
    Mutates `args` in place, resolving every
    PROJECT_OVERRIDABLE_DEFAULTS option and attaching the loaded
    project (if any) as args.project_data.

    Returns None on success, or an exit code if --project could
    not be read — an unreadable project is fatal: silently
    falling back to the built-in defaults could flash the wrong
    ECU with the wrong firmware.
    """

    project = None

    if getattr(args, "project", None):
        try:
            project = load_project(args.project)
        except ProjectFileError as e:
            print(f"Project error: {e}", file=sys.stderr)
            return 2

        if project["missing_files"]:
            print("Project error: firmware file(s) missing:",
                  file=sys.stderr)
            for path in project["missing_files"]:
                print(f"  {path}", file=sys.stderr)
            return 2

    args.project_data = project

    for key, default in PROJECT_OVERRIDABLE_DEFAULTS.items():
        if getattr(args, key, None) is not None:
            continue
        option = _PROJECT_KEY_TO_OPTION.get(key)
        if project is not None and option is not None:
            value = project.get(key)
            if value is not None:
                setattr(args, key, value)
                continue
        setattr(args, key, default)

    return _check_security_dll(args)


def _check_security_dll(args):
    """
    Refuses to start when --security-dll points at something that
    cannot possibly load, before any CAN traffic happens — the
    headless twin of ConfigureTabMixin.security_access_start_error().

    Only real hardware is checked: FlashWorker never loads the DLL
    for the Virtual ECU Simulator (it always uses the built-in
    algorithm), so a stale --security-dll is harmless there.

    The case worth failing fast on is a 32-bit Seed&Key DLL in a
    64-bit SFlash, which no amount of retrying fixes — and which,
    in a PyInstaller build, otherwise surfaces as PyInstaller's
    misleading "not found when the application was frozen".
    """

    path = getattr(args, "security_dll", None)

    if not path or getattr(args, "hardware", "virtual") == "virtual":
        return None

    if not os.path.isfile(path):
        print(
            f"Security DLL not found: {path}", file=sys.stderr,
        )
        return 2

    mismatch = bitness_mismatch(path)
    if mismatch:
        print(
            f"Cannot load Security DLL "
            f"{os.path.basename(path)}: {mismatch}",
            file=sys.stderr,
        )
        return 2

    return None


def _project_firmware_files(args):
    """
    The firmware paths a command should use: the positional
    files when given, else the project's ticked files. Explicit
    paths win so a project can be reused for a one-off file
    without editing it.
    """

    files = list(getattr(args, "file", None) or [])

    if files:
        return files

    project = getattr(args, "project_data", None)
    if project is not None:
        return list(project["firmware_files"])

    return []


def _load_datablocks(args, quiet=False):
    """
    Parses every firmware file for this run, in the order given.
    Returns (datablocks, exit_code) — exit_code is None on
    success, 2 on a parse error or when there's nothing to flash
    (both of which the GUI surfaces as a dialog before any CAN
    traffic happens).
    """

    paths = _project_firmware_files(args)

    if not paths:
        print(
            "No firmware file given. Pass one or more file paths, "
            "or --project <file>.sfproj.",
            file=sys.stderr,
        )
        return [], 2

    datablocks = []

    for path in paths:
        try:
            datablocks.append(
                parse_firmware_file(
                    path, base_address=args.base_address
                )
            )
        except HexParseError as e:
            print(f"Parse error ({path}): {e}", file=sys.stderr)
            return [], 2

    if not quiet:
        for datablock in datablocks:
            _print_datablock_info(datablock)

    return datablocks, None


# ==================================================
# Report artefacts (--report / --trace-csv / --json-summary)
# ==================================================

def _config_rows(args, units=None):
    """The Summary table's rows, mirroring the GUI report's."""

    if units:
        target = ", ".join(unit.describe() for unit in units)
    elif args.hardware == "virtual":
        target = "Virtual ECU Simulator"
    else:
        target = f"Vector channel {args.channel}"
        if args.serial:
            target += f" (serial {args.serial})"

    rows = [
        ("Hardware", target),
        ("Radar Side", args.radar_side),
        ("Flash Sequence", args.sequence),
        (
            "Security Access DLL",
            (
                f"{args.security_dll} "
                f"({args.security_dll_signature} contract)"
                if args.security_dll else "Built-in algorithm"
            ),
        ),
        ("Bitrate", f"{args.bitrate} bps"),
        ("CAN FD", "Yes" if args.can_fd else "No"),
    ]

    if getattr(args, "project", None):
        rows.append(("Project", args.project))

    return rows


def _write_report_artefacts(args, record):
    """
    Writes whichever of --report / --trace-csv / --json-summary
    were asked for. A write failure is reported but never
    changes the run's exit code — the flash already happened,
    and reporting its result as a failure because a report file
    couldn't be written would be worse than the missing file.
    """

    for option, writer, label in (
        ("report", write_report_html, "HTML report"),
        ("trace_csv", None, "Trace CSV"),
        ("json_summary", write_json_summary, "JSON summary"),
    ):
        path = getattr(args, option, None)
        if not path:
            continue

        try:
            if option == "trace_csv":
                write_trace_csv(path, record.trace_rows)
            else:
                writer(path, record)
        except OSError as e:
            print(
                f"Warning: could not write {label} to {path}: {e}",
                file=sys.stderr,
            )
            continue

        print(f"{label} written to {path}")


def _resolve_can_ids(args):
    """--tx-id/--rx-id win; otherwise --radar-side picks a default."""

    if args.tx_id is not None and args.rx_id is not None:
        return args.tx_id, args.rx_id

    ids = SUZUKI_RADAR_CAN_IDS[args.radar_side.upper()]
    tx_id = args.tx_id if args.tx_id is not None else int(ids["tx_id"], 16)
    rx_id = args.rx_id if args.rx_id is not None else int(ids["rx_id"], 16)
    return tx_id, rx_id


def _warn_can_conflict(args):
    """
    Best-effort, non-blocking check for a likely CAN bus
    conflict (e.g. CANoe/CANalyzer/CANape left running with a
    measurement active) before touching real hardware. Prints
    a warning to stderr and returns — never prompts or aborts,
    since the CLI is meant to stay scriptable/automatable; the
    GUI's equivalent (ConfigureTabMixin.detect_can_conflict_warning)
    asks interactively instead.
    """

    if args.hardware == "virtual":
        return

    running_tools = detect_running_vector_tools()

    busy_channel_label = None
    for ch in detect_vector_channels():
        if args.serial:
            match = (
                ch.get("hw_channel") == args.channel
                and ch.get("serial") == args.serial
            )
        else:
            match = ch["channel"] == args.channel
        if match and ch.get("is_on_bus"):
            busy_channel_label = ch["label"]
            break

    if not running_tools and not busy_channel_label:
        return

    print("WARNING: possible CAN bus conflict detected:", file=sys.stderr)
    if running_tools:
        print(
            f"  - Running Vector tool(s): "
            f"{', '.join(name.upper() for name in running_tools)}",
            file=sys.stderr,
        )
    if busy_channel_label:
        print(
            f"  - Channel already active on the bus: "
            f"{busy_channel_label}",
            file=sys.stderr,
        )
    print(
        "  If another tool is running a measurement on the same "
        "channel, its TesterPresent/diagnostic activity can "
        "collide with this session. Close it first unless you "
        "know you're intentionally sharing the bus.",
        file=sys.stderr,
    )
    print(file=sys.stderr)


def _print_datablock_info(datablock, indent=""):
    print(
        f"{indent}{datablock.file_name}: "
        f"{datablock.segment_count} segment(s), "
        f"{datablock.total_size} bytes total, "
        f"checksum 0x{datablock.checksum:08X}"
    )
    for i, seg in enumerate(datablock.segments):
        print(
            f"{indent}  Segment {i + 1}: "
            f"0x{seg.start_address:08X} - 0x{seg.end_address:08X} "
            f"({seg.length} bytes)"
        )


# ==================================================
# Command: info
# ==================================================

def cmd_info(args):

    datablocks = []

    for path in args.file:
        try:
            datablocks.append(
                parse_firmware_file(
                    path, base_address=args.base_address
                )
            )
        except HexParseError as e:
            print(f"Parse error ({path}): {e}", file=sys.stderr)
            return 2

    for datablock in datablocks:
        _print_datablock_info(datablock)

    if len(datablocks) > 1:
        total = sum(db.total_size for db in datablocks)
        segments = sum(db.segment_count for db in datablocks)
        print(
            f"\nTotal: {len(datablocks)} datablock(s), "
            f"{segments} segment(s), {total} bytes"
        )

    return 0


# ==================================================
# Command: list-hardware
# ==================================================

def cmd_list_hardware(args):

    print("Available --hardware values:")
    print("  virtual   Virtual ECU Simulator (no hardware needed)")
    print("  vector    Vector VN1640A/VN1630 (real hardware, Windows only)")
    print()

    channels, error = detect_vector_channels_with_error()
    if channels:
        print("Real Vector channels detected on this machine:")
        for ch in channels:
            serial = ch.get("serial")
            hw_ch = ch.get("hw_channel", ch["channel"])
            if serial:
                print(
                    f"  - {ch['label']}"
                    f" (--channel {hw_ch} --serial {serial})"
                )
            else:
                print(
                    f"  - {ch['label']}"
                    f" (--channel {ch['channel']})"
                )
    elif error:
        print(
            "No real Vector hardware detected — --hardware vector "
            "is unavailable until this is fixed:\n"
            f"  {error}"
        )
    else:
        print(
            "No real Vector hardware detected right now "
            "(nothing plugged in) — --hardware vector is "
            "unavailable until some is."
        )
    print()
    print("Radar sides (--radar-side), Suzuki Radar ECU physical CAN IDs:")
    for side, ids in SUZUKI_RADAR_CAN_IDS.items():
        print(f"  {side.lower():<6} Tx {ids['tx_id']} / Rx {ids['rx_id']}")
    return 0


# ==================================================
# Command: flash
# ==================================================

def _build_steps(args, datablocks):

    if args.sequence == "suzuki":
        return build_suzuki_slp1_flash_sequence(
            datablocks, tester_serial_number=args.tester_serial,
        )

    return build_flash_sequence(datablocks)


def cmd_flash(args):

    exit_code = _resolve_config(args)
    if exit_code is not None:
        return exit_code

    datablocks, exit_code = _load_datablocks(args, quiet=args.quiet)
    if exit_code is not None:
        return exit_code

    steps = _build_steps(args, datablocks)

    if not args.quiet:
        print(f"\nFlash sequence: {len(steps)} step(s)"
              f" ({args.sequence})")

    if args.dry_run:
        for i, step in enumerate(steps, 1):
            print(f"  [{i}/{len(steps)}] {step.description}")
        print("\n--dry-run: nothing was sent to the ECU.")
        return 0

    tx_id, rx_id = _resolve_can_ids(args)
    use_virtual = args.hardware == "virtual"

    _warn_can_conflict(args)

    if not args.quiet:
        print(
            f"Target: "
            f"{'Virtual ECU Simulator' if use_virtual else f'Vector channel {args.channel}'}"
            f" | Tx=0x{tx_id:X} Rx=0x{rx_id:X}"
            f" | {args.bitrate} bps"
            f"{' (CAN FD, data ' + str(args.data_bitrate) + ' bps)' if args.can_fd else ''}"
        )

    record = RunRecord("flash", _config_rows(args))
    record.datablocks = datablocks

    worker = FlashWorker(
        steps=steps,
        datablocks=datablocks,
        use_virtual=use_virtual,
        security_dll_path=args.security_dll,
        security_dll_signature=args.security_dll_signature,
        security_dll_variant=args.security_dll_variant,
        run_timeout=args.timeout,
        keepalive_functional=(args.sequence == "suzuki"),
        can_channel=args.channel,
        can_serial=args.serial,
        can_tx_id=tx_id,
        can_rx_id=rx_id,
        can_bitrate=args.bitrate,
        can_fd=args.can_fd,
        can_data_bitrate=args.data_bitrate,
        download_compression=args.compression,
        download_encrypting=args.encryption,
    )

    result = {"finished": False, "aborted": False}
    total_steps = len(steps)
    step_counter = {"n": 0}
    segment_last_pct = {}

    def on_step_started(description):
        step_counter["n"] += 1
        record.add_step(description)
        if not args.quiet:
            print(f"  [{step_counter['n']}/{total_steps}] {description}")

    def on_information_message(message):
        if not args.quiet:
            print(f"    {message}")

    on_trace_message, on_trace_row = _make_trace_handlers(
        args.verbose, record
    )

    def on_segment_progress(seg_idx, sent, total):
        if args.quiet or total <= 0:
            return
        pct = int((sent / total) * 100)
        if segment_last_pct.get(seg_idx, -10) >= pct - 10 and pct < 100:
            return
        segment_last_pct[seg_idx] = pct
        print(f"      segment {seg_idx + 1}: {pct}% ({sent}/{total} bytes)")

    def on_ecu_info(info):
        record.add_ecu_info(info)
        if args.quiet:
            return
        print("    --- ECU Identification ---")
        for key, value in info.items():
            print(f"    {key}: {value}")
        print("    ---------------------------")

    def on_finished():
        result["finished"] = True

    def on_aborted():
        result["aborted"] = True

    worker.step_started.connect(on_step_started)
    worker.information_message.connect(on_information_message)
    worker.trace_message.connect(on_trace_message)
    worker.trace_row.connect(on_trace_row)
    worker.segment_progress.connect(on_segment_progress)
    worker.ecu_info_message.connect(on_ecu_info)
    worker.flash_finished.connect(on_finished)
    worker.flash_aborted.connect(on_aborted)

    print()

    try:
        worker.run()
    except KeyboardInterrupt:
        print("\nInterrupted by user.", file=sys.stderr)
        record.finish(RESULT_ABORTED)
        _write_report_artefacts(args, record)
        return EXIT_INTERRUPTED

    if result["finished"]:
        record.finish(RESULT_PASS)
        print("\nFlash completed successfully.")
        _write_report_artefacts(args, record)
        return EXIT_OK

    record.finish(RESULT_FAIL)
    exit_code = _exit_code_for(worker.failure_kind)

    if exit_code == EXIT_TIMEOUT:
        print(
            f"\nTimed out after {args.timeout}s.", file=sys.stderr
        )
    elif exit_code == EXIT_NO_ECU:
        print("\nECU not reachable.", file=sys.stderr)
    else:
        print("\nFlash aborted / failed.", file=sys.stderr)

    _write_report_artefacts(args, record)
    return exit_code


# ==================================================
# Command: test-connection
# ==================================================
#
# A safe, non-destructive probe: connects, opens an Extended
# session (+ functional pre-steps for the Suzuki sequence),
# then reads ECU Identification — enough to verify CAN wiring,
# IDs, and ECU reachability. Never touches Programming session,
# Security Access, Erase Memory, or TransferData. Always tries
# to leave the ECU back in Default session (re-enabling DTC/
# Communication if they were disabled) before exiting — meant
# to be run repeatedly against real hardware to verify
# connectivity before trusting a real flash to it.
# ==================================================

def cmd_test_connection(args):

    exit_code = _resolve_config(args)
    if exit_code is not None:
        return exit_code

    tx_id, rx_id = _resolve_can_ids(args)
    use_virtual = args.hardware == "virtual"
    functional = (args.sequence == "suzuki")

    _warn_can_conflict(args)

    if not args.quiet:
        print(
            f"Target: "
            f"{'Virtual ECU Simulator' if use_virtual else f'Vector channel {args.channel}'}"
            f" | Tx=0x{tx_id:X} Rx=0x{rx_id:X}"
            f" | {args.bitrate} bps"
            f"{' (CAN FD, data ' + str(args.data_bitrate) + ' bps)' if args.can_fd else ''}"
        )
        print()

    record = RunRecord("test-connection", _config_rows(args))

    on_trace_message, on_trace_row = _make_trace_handlers(
        args.verbose, record
    )

    # Reuses FlashWorker only for its CAN/UDS connection setup
    # (virtual vs. Vector, Security DLL loading, trace
    # wiring) — steps=[] because we drive the UDS calls
    # directly below instead of going through the linear,
    # abort-on-first-failure FlashStep sequence, so we can
    # guarantee cleanup runs via try/finally no matter where
    # this stops.
    worker = FlashWorker(
        steps=[],
        datablocks=[],
        use_virtual=use_virtual,
        security_dll_path=args.security_dll,
        security_dll_signature=args.security_dll_signature,
        security_dll_variant=args.security_dll_variant,
        run_timeout=args.timeout,
        can_channel=args.channel,
        can_serial=args.serial,
        can_tx_id=tx_id,
        can_rx_id=rx_id,
        can_bitrate=args.bitrate,
        can_fd=args.can_fd,
        can_data_bitrate=args.data_bitrate,
    )
    worker.trace_message.connect(on_trace_message)
    worker.trace_row.connect(on_trace_row)

    # The probe drives _setup_uds_client() directly instead of
    # FlashWorker.run(), so --timeout has to be armed by hand.
    worker.arm_deadline()

    try:
        worker._setup_uds_client()
    except Exception as e:
        print(f"Connection failed: {e}", file=sys.stderr)
        record.add_step(f"Connection failed: {e}")
        record.finish(RESULT_FAIL)
        _write_report_artefacts(args, record)
        return EXIT_NO_ECU

    uds = worker._uds_client
    ok = True
    failure_kind = None

    def step(label):
        record.add_step(label)
        if not args.quiet:
            print(f"  [OK] {label}")

    try:
        if functional:
            uds.diagnostic_session_control(0x03, functional=True)
            step("Extended Session (Network)")
            uds.control_dtc_setting(
                setting_type=0x02, option_record=bytes([0x00]),
                functional=True,
            )
            step("Disable DTC Settings (Network)")
            uds.communication_control(
                control_type=0x03, communication_type=0x01,
                functional=True,
            )
            step("Disable Normal Communication (Network)")
        else:
            uds.diagnostic_session_control(0x03)
            step("Extended Session")

        from communication.uds_client import UdsDeadlineError
        from core.test_connection import TEST_CONNECTION_DIDS
        for did, name in TEST_CONNECTION_DIDS:
            try:
                data = uds.read_data_by_identifier(did)
                try:
                    value = data.decode("ascii").strip('\x00')
                except (UnicodeDecodeError, ValueError):
                    value = data.hex().upper()
                record.add_ecu_info({name: value})
                step(f"Read DID 0x{did:04X}: {name} = {value}")
            except UdsDeadlineError:
                # A per-DID failure is tolerated; the run's
                # deadline is not about this DID.
                raise
            except Exception as e:
                step(f"Read DID 0x{did:04X}: {name} = N/A")
                if not args.quiet:
                    print(f"         ({e})")

    except KeyboardInterrupt:
        print("\nInterrupted by user.", file=sys.stderr)
        ok = False
        failure_kind = "interrupted"
    except Exception as e:
        from communication.uds_client import UdsDeadlineError

        print(f"\nConnection test FAILED: {e}", file=sys.stderr)
        ok = False
        failure_kind = (
            "timeout" if isinstance(e, UdsDeadlineError) else "probe"
        )

    finally:
        # Best-effort cleanup: restore Default session (and
        # re-enable DTC/Communication if we disabled them),
        # regardless of where the test above stopped. Never
        # lets a cleanup failure hide the real result.
        try:
            if functional:
                uds.communication_control(
                    control_type=0x00, communication_type=0x01,
                    functional=True,
                )
                uds.control_dtc_setting(
                    setting_type=0x01, functional=True
                )
            uds.diagnostic_session_control(
                0x01, functional=functional
            )
            if not args.quiet:
                print("  Restored Default session.")
        except Exception:
            pass

        worker._cleanup()

    if ok:
        record.finish(RESULT_PASS)
        print("\nConnection test PASSED — ECU reachable.")
        _write_report_artefacts(args, record)
        return EXIT_OK

    record.finish(RESULT_FAIL)
    _write_report_artefacts(args, record)

    if failure_kind == "interrupted":
        return EXIT_INTERRUPTED
    return _exit_code_for(failure_kind)


# ==================================================
# Commands: batch / parallel (multi-ECU)
# ==================================================
#
# Both resolve their ECU list the same way (--unit specs, a
# --units-file, or --count copies of the global flags), load the
# same firmware, and report the same way — they differ only in
# whether the units run one after another (batch, with an
# Identify step per unit like the GUI's Batch Flash) or all at
# once (parallel, one CAN channel each like Parallel Flash).
# ==================================================

def _resolve_units(args, name_prefix):
    """
    Returns (units, exit_code). --units-file and --unit may be
    combined (file first, then the inline ones), so a standing
    line definition can be extended for one run without editing
    the file.
    """

    field_dicts = []

    try:
        if getattr(args, "units_file", None):
            field_dicts.extend(load_units_file(args.units_file))
        for spec in getattr(args, "unit", None) or []:
            field_dicts.append(parse_unit_spec(spec))
    except UnitSpecError as e:
        print(f"Unit error: {e}", file=sys.stderr)
        return [], 2

    tx_id, rx_id = _resolve_can_ids(args)

    defaults = {
        "hardware": args.hardware,
        "channel": args.channel,
        "serial": args.serial,
        "side": args.radar_side,
        "functional_id": 0x700,
    }

    # Only pass the resolved IDs along as defaults when the
    # operator actually pinned them; otherwise each unit derives
    # its own from its own side.
    if args.tx_id is not None:
        defaults["tx_id"] = tx_id
    if args.rx_id is not None:
        defaults["rx_id"] = rx_id

    try:
        units = build_units(
            field_dicts, defaults,
            count=getattr(args, "count", 1) or 1,
            name_prefix=name_prefix,
        )
    except (UnitSpecError, TypeError) as e:
        print(f"Unit error: {e}", file=sys.stderr)
        return [], 2

    return units, None


def _print_unit_table(units):

    print(f"{len(units)} unit(s):")
    for i, unit in enumerate(units, 1):
        print(f"  [{i}] {unit.label}: {unit.describe()}")
    print()


def _unit_summary(results):
    """Prints the per-unit tally and returns the exit code."""

    counts = {RESULT_PASS: 0, RESULT_FAIL: 0, RESULT_ABORTED: 0}
    for record in results:
        counts[record["result"]] = counts.get(record["result"], 0) + 1

    print()
    print("=" * 52)
    for record in results:
        reason = f" — {record['reason']}" if record["reason"] else ""
        serial = f" [{record['serial']}]" if record["serial"] else ""
        print(
            f"  {record['result']:<8} {record['name']}{serial} "
            f"({record['duration']}s){reason}"
        )
    print("=" * 52)
    print(
        f"  PASS {counts[RESULT_PASS]} | "
        f"FAIL {counts[RESULT_FAIL]} | "
        f"ABORTED {counts[RESULT_ABORTED]}"
    )

    if not results:
        return 1

    return 0 if counts[RESULT_FAIL] == 0 and counts[RESULT_ABORTED] == 0 else 1


class _CliRunListener:
    """
    Prints a multi-ECU run as it happens. Every hook is called
    with the unit it belongs to so parallel output stays
    attributable — ParallelRunner holds its output lock across
    each call, so a line never interleaves with another
    channel's.
    """

    def __init__(self, args, record, show_progress=True):
        self.args = args
        self.record = record
        self.show_progress = show_progress
        self._on_trace_message, self._on_trace_row = (
            _make_trace_handlers(args.verbose, record)
        )
        self._segment_pct = {}

    def _prefix(self, unit):
        return f"[{unit.label}]"

    def on_unit_started(self, index, total, unit):
        if self.args.quiet:
            return
        print(
            f"\n=== Unit {index}/{total}: {unit.label} "
            f"({unit.describe()}) ==="
        )

    def on_identify_step(self, unit, message):
        if not self.args.quiet:
            print(f"  {self._prefix(unit)} identify: {message}")

    def on_identify_result(self, unit, passed, message, info):
        self.record.add_step(
            f"{unit.label}: identify "
            f"{'PASSED' if passed else 'FAILED'}"
        )
        if not self.args.quiet:
            print(f"  {self._prefix(unit)} {message}")

    def on_step_started(self, unit, index, total, description):
        self.record.add_step(f"{unit.label}: {description}")
        if not self.args.quiet:
            print(
                f"  {self._prefix(unit)} [{index}/{total}] "
                f"{description}"
            )

    def on_information(self, unit, message):
        if not self.args.quiet:
            print(f"    {self._prefix(unit)} {message}")

    def on_trace_message(self, unit, message):
        self._on_trace_message(message)

    def on_trace_row(self, unit, row):
        self._on_trace_row(row)

    def on_segment_progress(self, unit, seg_index, sent, total):
        if self.args.quiet or not self.show_progress or total <= 0:
            return
        pct = int((sent / total) * 100)
        key = (unit.label, seg_index)
        if self._segment_pct.get(key, -10) >= pct - 10 and pct < 100:
            return
        self._segment_pct[key] = pct
        print(
            f"      {self._prefix(unit)} segment {seg_index + 1}: "
            f"{pct}% ({sent}/{total} bytes)"
        )

    def on_unit_finished(self, unit, result, duration, reason):
        suffix = f" — {reason}" if reason else ""
        print(
            f"  {self._prefix(unit)} {result} in {duration}s{suffix}"
        )


def _make_pause_hook(args):
    """
    --pause waits for the operator to swap in the next ECU, the
    way Batch Flash's "Next" button does. Returns None when not
    asked for, so a scripted run never blocks on stdin.
    """

    if not args.pause:
        return None

    def pause(index, total, unit):
        try:
            input(
                f"\nSwap in unit {index}/{total} "
                f"({unit.label}), then press Enter "
                f"(Ctrl+C to stop here)... "
            )
        except (EOFError, KeyboardInterrupt):
            print("\nStopping the batch here.", file=sys.stderr)
            return False
        return True

    return pause


def cmd_batch(args):

    exit_code = _resolve_config(args)
    if exit_code is not None:
        return exit_code

    units, exit_code = _resolve_units(args, "Unit")
    if exit_code is not None:
        return exit_code

    datablocks, exit_code = _load_datablocks(args, quiet=args.quiet)
    if exit_code is not None:
        return exit_code

    if args.dry_run:
        steps = _build_steps(args, datablocks)
        _print_unit_table(units)
        print(f"Flash sequence per unit: {len(steps)} step(s)"
              f" ({args.sequence})")
        for i, step in enumerate(steps, 1):
            print(f"  [{i}/{len(steps)}] {step.description}")
        print("\n--dry-run: nothing was sent to any ECU.")
        return 0

    _warn_can_conflict(args)

    if not args.quiet:
        _print_unit_table(units)

    record = RunRecord("batch", _config_rows(args, units))
    record.datablocks = datablocks
    listener = _CliRunListener(args, record)

    runner = BatchRunner(
        units,
        datablocks,
        sequence=args.sequence,
        tester_serial=args.tester_serial,
        security_dll_path=args.security_dll,
        security_dll_signature=args.security_dll_signature,
        security_dll_variant=args.security_dll_variant,
        run_timeout=args.timeout,
        bitrate=args.bitrate,
        can_fd=args.can_fd,
        data_bitrate=args.data_bitrate,
        compression=args.compression,
        encryption=args.encryption,
        identify=not args.no_identify,
        stop_on_fail=args.stop_on_fail,
        listener=listener,
        pause_hook=_make_pause_hook(args),
    )

    try:
        results = runner.run()
    except KeyboardInterrupt:
        results = runner.results
        print("\nInterrupted by user.", file=sys.stderr)

    for entry in results:
        record.add_unit(
            entry["name"], entry["result"],
            duration=entry["duration"], serial=entry["serial"],
            reason=entry["reason"],
            ecu_info=entry.get("ecu_info"),
        )
        record.add_ecu_info(entry.get("ecu_info"))

    exit_code = _unit_summary(results)
    record.finish(RESULT_PASS if exit_code == 0 else RESULT_FAIL)
    _write_report_artefacts(args, record)

    return exit_code


def cmd_parallel(args):

    exit_code = _resolve_config(args)
    if exit_code is not None:
        return exit_code

    units, exit_code = _resolve_units(args, "Channel")
    if exit_code is not None:
        return exit_code

    duplicate = _duplicate_target(units)
    if duplicate is not None:
        print(
            f"Unit error: two units share the same target "
            f"({duplicate}). Flashing one ECU from two threads "
            f"at once would corrupt both sessions — give each "
            f"unit its own channel or CAN IDs.",
            file=sys.stderr,
        )
        return 2

    datablocks, exit_code = _load_datablocks(args, quiet=args.quiet)
    if exit_code is not None:
        return exit_code

    if args.dry_run:
        steps = _build_steps(args, datablocks)
        _print_unit_table(units)
        print(f"Flash sequence per channel: {len(steps)} step(s)"
              f" ({args.sequence})")
        for i, step in enumerate(steps, 1):
            print(f"  [{i}/{len(steps)}] {step.description}")
        print("\n--dry-run: nothing was sent to any ECU.")
        return 0

    _warn_can_conflict(args)

    if not args.quiet:
        _print_unit_table(units)

    record = RunRecord("parallel", _config_rows(args, units))
    record.datablocks = datablocks
    listener = _CliRunListener(args, record)

    runner = ParallelRunner(
        units,
        datablocks,
        sequence=args.sequence,
        tester_serial=args.tester_serial,
        security_dll_path=args.security_dll,
        security_dll_signature=args.security_dll_signature,
        security_dll_variant=args.security_dll_variant,
        run_timeout=args.timeout,
        bitrate=args.bitrate,
        can_fd=args.can_fd,
        data_bitrate=args.data_bitrate,
        compression=args.compression,
        encryption=args.encryption,
        listener=listener,
    )

    results = runner.run()

    for entry in results:
        record.add_unit(
            entry["name"], entry["result"],
            duration=entry["duration"], serial=entry["serial"],
            reason=entry["reason"],
            ecu_info=entry.get("ecu_info"),
        )
        record.add_ecu_info(entry.get("ecu_info"))

    exit_code = _unit_summary(results)
    record.finish(RESULT_PASS if exit_code == 0 else RESULT_FAIL)
    _write_report_artefacts(args, record)

    return exit_code


def _duplicate_target(units):
    """
    Returns a description of the first duplicated (hardware,
    channel, serial, tx_id) target among `units`, or None.

    Parallel Flash's GUI makes this impossible by construction —
    one panel per physical channel, each with its own combo. On
    the command line nothing stops `--unit channel=0 --unit
    channel=0`, and two FlashWorkers sharing one physical ECU
    would interleave their UDS sessions and fail in a way that
    looks like a hardware fault, so it's rejected up front.
    Virtual units are exempt: each gets its own in-memory bus
    and simulator, so N identical virtual units are a legitimate
    (and useful) way to smoke-test concurrency.
    """

    seen = {}

    for unit in units:
        if unit.use_virtual:
            continue
        key = (unit.channel, unit.serial, unit.tx_id)
        if key in seen:
            return (
                f"{seen[key]} and {unit.label}: channel "
                f"{unit.channel}, Tx=0x{unit.tx_id:X}"
            )
        seen[key] = unit.label

    return None


# ==================================================
# Command: check-security-dll
# ==================================================
#
# A read-only diagnosis of a Seed&Key DLL: architecture, this
# build's architecture, exported entry point, and an actual load
# attempt. Exists because the failure it diagnoses is invisible
# otherwise — in a PyInstaller build every load error is rewritten
# by PyInstaller's ctypes hook into "not found when the
# application was frozen", which sends the operator looking for a
# packaging problem instead of a 32-bit DLL. Nothing here touches
# CAN or an ECU.
# ==================================================

def cmd_check_security_dll(args):

    from communication.security_dll import (
        SecurityDllError,
        describe_pe_machine,
        host_bitness_name,
        load_security_dll,
        read_pe_exports,
        read_pe_imports,
    )
    from communication.uds_client import UdsClient

    path = args.file

    print(f"File:              {path}")
    print(f"Exists:            {os.path.isfile(path)}")
    if os.path.isfile(path):
        print(f"Size:              {os.path.getsize(path)} bytes")

    architecture = describe_pe_machine(path)
    print(
        f"DLL architecture:  "
        f"{architecture or 'unknown (not a Windows PE file?)'}"
    )
    print(f"SFlash running as: {host_bitness_name()}")

    exports = read_pe_exports(path)
    imports = read_pe_imports(path)

    print(f"\nExports ({len(exports)}):")
    for name in exports or ["  (none found)"]:
        print(f"  {name}" if exports else name)

    print(f"\nDepends on ({len(imports)}):")
    for name in imports or ["  (none found)"]:
        print(f"  {name}" if imports else name)

    # Which contract load_security_dll(signature="auto") would
    # pick, worked out from the same export names — printed
    # before the load attempt so it is still useful when the DLL
    # can't be loaded on this machine at all (e.g. inspecting a
    # Windows DLL from macOS/Linux).
    if "GenerateKeyExOpt" in exports:
        contract = "vector_opt (8 args, byte arrays + iOptions)"
    elif exports:
        contract = "vector (7 args, byte arrays)"
    else:
        contract = "unknown"
    print(f"\nContract 'auto' would use: {contract}")
    if exports and "GenerateKeyExOpt" not in exports:
        print(
            "  If this DLL is really the older "
            "uint32 -> uint32 kind, pass "
            "--security-dll-signature uint32 — calling one "
            "contract as the other crashes the process."
        )

    print()

    try:
        dll = load_security_dll(path)
    except SecurityDllError as e:
        print(f"RESULT: CANNOT LOAD\n\n{e}", file=sys.stderr)
        return 1

    print("RESULT: LOADED OK")

    for name in ("GenerateKeyExOpt", args.function_name):
        if name and getattr(dll, name, None) is not None:
            print(f"Entry point found:  {name}")
            break
    else:
        print(
            f"WARNING: neither 'GenerateKeyExOpt' nor "
            f"'{args.function_name}' could be resolved — the DLL "
            f"loads but SFlash would not find a key function in "
            f"it. Exported names are listed above; pass the right "
            f"one with --function-name.",
            file=sys.stderr,
        )
        return 1

    assert UdsClient.SECURITY_DLL_AUTO == "auto"
    return 0


# ==================================================
# Argument Parser
# ==================================================

def _add_can_args(parser):
    """Shared CAN/UDS connection options for flash + test-connection."""

    parser.add_argument(
        "--hardware", choices=["virtual", "vector"], default=None,
        help="Target: Virtual ECU Simulator (default) or real Vector hardware",
    )
    parser.add_argument(
        "--channel", type=int, default=None,
        help="Vector hardware channel number, 0-based (default 0). "
             "With --serial, this is the hardware channel on that "
             "device; without --serial, it is the application "
             "channel index in Vector Hardware Config",
    )
    parser.add_argument(
        "--serial", type=int, default=None,
        help="Vector device serial number — directly selects "
             "the physical hardware, bypassing application "
             "channel mapping in Vector Hardware Config",
    )
    parser.add_argument(
        "--sequence", choices=["generic", "suzuki"], default=None,
        help="Protocol variant: suzuki (default — Suzuki Radar, "
             "functional addressing for the pre-security steps, "
             "reverse-engineered from a real trace log) or generic",
    )
    parser.add_argument(
        "--radar-side", choices=["s0", "s1"], default=None,
        help="Suzuki Radar physical CAN ID preset (default s0) — "
             "ignored if --tx-id/--rx-id are given",
    )
    parser.add_argument(
        "--tx-id", type=_parse_hex_int, default=None,
        help="Override physical request CAN ID (hex, e.g. 0x77B)",
    )
    parser.add_argument(
        "--rx-id", type=_parse_hex_int, default=None,
        help="Override physical response CAN ID (hex, e.g. 0x78B)",
    )
    parser.add_argument(
        "--bitrate", type=int, default=500000,
        help="CAN bitrate in bit/s (default 500000)",
    )
    parser.add_argument(
        "--can-fd", action="store_true", default=None,
        help="Use CAN FD instead of classic CAN",
    )
    parser.add_argument(
        "--data-bitrate", type=int, default=2000000,
        help="CAN FD data bitrate in bit/s (default 2000000)",
    )
    parser.add_argument(
        "--security-dll", default=None,
        help="Path to an external Security Access DLL (ctypes). "
             "If not given, uses the built-in dummy seed/key algorithm.",
    )
    parser.add_argument(
        "--timeout", type=float, default=None, metavar="SECONDS",
        help="Give up after this many seconds and exit 4, still "
             "writing any report asked for. Without it a run has "
             "no upper bound: one request can legitimately take "
             "up to 50 x the 10s ResponsePending timeout while "
             "the ECU keeps answering 0x78, so an unattended job "
             "can sit until the CI runner kills it — with no "
             "report and the ECU left mid-session. For batch and "
             "parallel this is the budget per unit/channel",
    )
    parser.add_argument(
        "--security-dll-signature",
        choices=["auto", "vector", "vector_opt", "uint32"],
        default="auto",
        help="Calling contract of the DLL's key function: "
             "vector (Vector/ASAM GenerateKeyEx, 7 args, byte "
             "arrays), vector_opt (the ODX variant, 8 args), or "
             "uint32 (this project's older uint32->uint32 "
             "wrapper). Default auto resolves by export name and "
             "is correct for both Vector forms — pass uint32 "
             "explicitly for a 1-argument DLL. Getting this "
             "wrong crashes the process, so it is never guessed "
             "beyond the published name mapping; run "
             "`check-security-dll` to see what a DLL exports",
    )
    parser.add_argument(
        "--security-dll-variant", default="",
        help="iVariant string passed to the Vector contracts — "
             "an OEM/ODX variant name a multi-ECU Security DLL "
             "uses to pick which algorithm to apply. Empty by "
             "default, which is what a single-purpose DLL "
             "expects",
    )
    parser.add_argument(
        "--compression", type=_parse_hex_int, default=None,
        help="RequestDownload dataFormatIdentifier compressionMethod "
             "nibble, 0-15 (default 0 = none). Only changes what the "
             "ECU is told the data format is — does not actually "
             "compress the firmware file; the loaded file must "
             "already be in that format.",
    )
    parser.add_argument(
        "--encryption", type=_parse_hex_int, default=None,
        help="RequestDownload dataFormatIdentifier encryptingMethod "
             "nibble, 0-15 (default 0 = none). Same caveat as "
             "--compression — does not actually encrypt the file.",
    )
    parser.add_argument(
        "--tester-serial", type=_parse_hex_bytes, default=None,
        help="WriteDataByIdentifier DID 0xF198 (Tester Serial "
             "Number) payload as a hex string, e.g. "
             "00112233445566778899 (default). Only used by the "
             "Suzuki sequence's 'Write Tester Info' step.",
    )
    parser.add_argument(
        "--config", default=None, metavar="PATH",
        help="JSON file holding any of this command's settings, "
             "so a pipeline step or a bench setup is a file "
             "instead of a long command line. Keys are the long "
             "option names without the dashes (\"json-summary\" "
             "or \"json_summary\"). A flag given on the command "
             "line still wins over the file, and the file wins "
             "over --project",
    )
    parser.add_argument(
        "--project", default=None,
        help="Load firmware list + configuration from a .sfproj "
             "saved by the GUI's File > Save Project As.... Any "
             "flag given explicitly still wins over the project",
    )
    parser.add_argument(
        "-q", "--quiet", action="store_true",
        help="Only print the final result and errors",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Also print CAN/UDS trace (TX/RX frames)",
    )
    _add_report_args(parser)


def _add_report_args(parser):
    """
    The headless equivalents of the GUI's Tools > Export
    Report... and the Trace tab's right-click Save Log. Written
    after the run finishes, whatever its result — a failed flash
    is exactly when the report matters.
    """

    parser.add_argument(
        "--report", default=None,
        help="Write an HTML report (Summary/Firmware/Steps/Trace, "
             "plus per-unit results for batch/parallel) to this path",
    )
    parser.add_argument(
        "--trace-csv", default=None,
        help="Write the CAN/UDS trace table to this path as CSV, "
             "same columns as the GUI's Trace tab Save Log",
    )
    parser.add_argument(
        "--json-summary", default=None,
        help="Write a machine-readable JSON summary of the run "
             "to this path (for CI assertions)",
    )


def _add_base_address_arg(parser):

    parser.add_argument(
        "--base-address", type=_parse_hex_int, default=0x0000,
        help="Start address for .bin files (hex or decimal, "
             "default 0x0000). Applies to every .bin given",
    )


def _add_unit_args(parser, noun):
    """
    The --unit/--units-file pair shared by batch and parallel.
    `noun` only changes the help text ("unit" vs "channel") —
    the option names stay the same so one units file works for
    both commands.
    """

    parser.add_argument(
        "--unit", action="append", default=None, metavar="SPEC",
        help=f"Describe one {noun} as key=value pairs, repeatable: "
             f'--unit "name=Left,channel=0,serial=123456,side=s0". '
             f"Fields: name, hardware, channel, serial, side, "
             f"tx_id, rx_id, functional_id. Anything omitted "
             f"falls back to this command's own flags",
    )
    parser.add_argument(
        "--units-file", default=None, metavar="PATH",
        help=f"JSON file describing the {noun}s — a list of the "
             f"same objects --unit takes, or an object with a "
             f'"units" list. Combined with any --unit specs',
    )


def build_arg_parser():

    parser = argparse.ArgumentParser(
        prog="cli.py",
        description=f"{APP_NAME} {APP_VERSION} — command-line interface",
    )
    parser.add_argument(
        "--version", action="version",
        version=f"{APP_NAME} {APP_VERSION}",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- info ---
    p_info = subparsers.add_parser(
        "info", help="Parse a firmware file and print segment info"
    )
    p_info.add_argument(
        "file", nargs="+",
        help="One or more paths to .hex/.s19/.s3/.../.bin files",
    )
    p_info.add_argument(
        "--base-address", type=_parse_hex_int, default=0x0000,
        help="Start address for .bin files (hex or decimal, default 0x0000)",
    )
    p_info.set_defaults(func=cmd_info)

    # --- list-hardware ---
    p_list = subparsers.add_parser(
        "list-hardware", help="List available hardware/CAN options"
    )
    p_list.set_defaults(func=cmd_list_hardware)

    # --- flash ---
    p_flash = subparsers.add_parser(
        "flash", help="Flash a firmware file to an ECU"
    )
    p_flash.add_argument(
        "file", nargs="*",
        help="One or more paths to .hex/.s19/.s3/.../.bin files, "
             "flashed as separate datablocks in the order given "
             "(the GUI's Datablocks table). May be omitted when "
             "--project supplies the firmware list",
    )
    _add_base_address_arg(p_flash)
    _add_can_args(p_flash)
    p_flash.add_argument(
        "--dry-run", action="store_true",
        help="Print the flash sequence steps and exit, without "
             "connecting to any ECU",
    )
    p_flash.set_defaults(func=cmd_flash)

    # --- batch ---
    p_batch = subparsers.add_parser(
        "batch",
        help="Flash several ECUs one after another on the same "
             "tester, Identifying each first — the CLI side of "
             "the GUI's Batch Flash mode",
    )
    p_batch.add_argument(
        "file", nargs="*",
        help="Firmware file(s) flashed into every unit (or use "
             "--project)",
    )
    _add_base_address_arg(p_batch)
    _add_can_args(p_batch)
    _add_unit_args(p_batch, "unit")
    p_batch.add_argument(
        "--count", type=int, default=1,
        help="Number of units to flash with the same settings, "
             "when no --unit/--units-file is given (default 1)",
    )
    p_batch.add_argument(
        "--pause", action="store_true",
        help="Wait for Enter between units so the operator can "
             "swap the ECU (the GUI's Next button). Off by "
             "default so scripted runs never block on stdin",
    )
    p_batch.add_argument(
        "--no-identify", action="store_true",
        help="Skip the per-unit Identify probe (which is what "
             "captures each ECU's serial number for the report)",
    )
    p_batch.add_argument(
        "--stop-on-fail", action="store_true",
        help="Stop the series at the first unit that fails "
             "(default: carry on and report every unit)",
    )
    p_batch.add_argument(
        "--dry-run", action="store_true",
        help="Print the units and the flash sequence, then exit "
             "without connecting to any ECU",
    )
    p_batch.set_defaults(func=cmd_batch)

    # --- parallel ---
    p_parallel = subparsers.add_parser(
        "parallel",
        help="Flash several ECUs simultaneously, one per CAN "
             "channel — the CLI side of the Parallel Flash tab",
    )
    p_parallel.add_argument(
        "file", nargs="*",
        help="Firmware file(s) flashed into every channel (or "
             "use --project)",
    )
    _add_base_address_arg(p_parallel)
    _add_can_args(p_parallel)
    _add_unit_args(p_parallel, "channel")
    p_parallel.add_argument(
        "--count", type=int, default=1,
        help="Number of identical channels to run when no "
             "--unit/--units-file is given (default 1). Only "
             "useful with --hardware virtual, where each unit "
             "gets its own simulator",
    )
    p_parallel.add_argument(
        "--dry-run", action="store_true",
        help="Print the channels and the flash sequence, then "
             "exit without connecting to any ECU",
    )
    p_parallel.set_defaults(func=cmd_parallel)

    # --- test-connection ---
    p_test = subparsers.add_parser(
        "test-connection",
        help="Safely test session + Security Access on an ECU "
             "(no Erase/Download/writes) before a real flash",
    )
    _add_can_args(p_test)
    p_test.set_defaults(func=cmd_test_connection)

    # --- check-security-dll ---
    p_dll = subparsers.add_parser(
        "check-security-dll",
        help="Diagnose a Security Access DLL (architecture, "
             "dependencies, entry point) without touching an ECU",
    )
    p_dll.add_argument("file", help="Path to the Seed&Key DLL")
    p_dll.add_argument(
        "--function-name", default="GenerateKeyEx",
        help="Legacy uint32->uint32 entry point to look for when "
             "'GenerateKeyExOpt' isn't exported (default "
             "GenerateKeyEx)",
    )
    p_dll.set_defaults(func=cmd_check_security_dll)

    # --- gitlab ---
    add_gitlab_subparser(subparsers)

    return parser


# ==================================================
# --config
# ==================================================
#
# The file is applied as the chosen subcommand's *defaults*
# before argv is parsed, which is what makes the precedence
# exact without any hand-written tie-breaking: an explicit flag
# overrides a default, so command line > config file, and
# _resolve_config()'s "was this left unset?" test then puts a
# --project below both.
#
# The accepted keys are read off the subparser's own actions,
# so the format can never drift from the flags — add an option
# and the config supports it. Values are passed through the
# option's own `type` converter when they arrive as strings, so
# "tx_id": "0x77B" and "tester_serial": "00112233" mean exactly
# what they mean on the command line.
# ==================================================

CONFIG_EXCLUDED_KEYS = ("help", "func", "config")


def _subparser_for(parser, command):

    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices.get(command)
    return None


def _config_actions(subparser):
    """{dest: action} for every setting the command accepts."""

    return {
        action.dest: action
        for action in subparser._actions
        if action.dest not in CONFIG_EXCLUDED_KEYS
    }


def _coerce_config_value(action, key, value):
    """
    Applies the option's own type converter to a string from
    JSON, and checks its choices — so a config file is validated
    the same way the command line is, instead of quietly
    handing a str to code expecting bytes or an int.
    """

    converted = value

    if action.type is not None and isinstance(value, str):
        try:
            converted = action.type(value)
        except (argparse.ArgumentTypeError, ValueError, TypeError) as e:
            raise RunConfigError(
                f"Config setting {key!r}: {e}"
            )

    if action.choices is not None:
        values = (
            converted if isinstance(converted, list) else [converted]
        )
        for item in values:
            if item not in action.choices:
                raise RunConfigError(
                    f"Config setting {key!r}: {item!r} is not one "
                    f"of {', '.join(str(c) for c in action.choices)}"
                )

    return converted


def _apply_run_config(parser, argv):
    """
    Finds --config in argv, loads it, and installs it as the
    chosen subcommand's defaults. Returns None, or an exit code
    if the file is unusable.
    """

    pre = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    pre.add_argument("--config")

    try:
        known, rest = pre.parse_known_args(argv)
    except SystemExit:
        # Malformed argv — let the real parser report it.
        return None

    if not known.config:
        return None

    command = next(
        (token for token in rest if not token.startswith("-")), None
    )
    subparser = _subparser_for(parser, command)

    # Named a command that has no --config of its own (info,
    # list-hardware, check-security-dll, gitlab). Say that,
    # rather than validating the file against that command's
    # flags and reporting a confusing key error — the file is
    # not the problem.
    takes_config = subparser is not None and any(
        action.dest == "config" for action in subparser._actions
    )

    if not takes_config:
        print(
            f"--config needs a command that accepts it "
            f"(flash, batch, parallel, test-connection), "
            f"got {command!r}.",
            file=sys.stderr,
        )
        return EXIT_USAGE

    actions = _config_actions(subparser)

    try:
        config = load_run_config(known.config)
        check_keys(config, set(actions), command)
        config = {
            key: _coerce_config_value(actions[key], key, value)
            for key, value in config.items()
        }
    except RunConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return EXIT_USAGE

    subparser.set_defaults(**config)
    return None


def main(argv=None):

    parser = build_arg_parser()

    exit_code = _apply_run_config(
        parser, list(sys.argv[1:] if argv is None else argv)
    )
    if exit_code is not None:
        return exit_code

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

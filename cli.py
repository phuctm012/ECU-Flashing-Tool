#!/usr/bin/env python3
# ==================================================
# FFlash — Command Line Interface
# ==================================================
#
# Run the app's flashing functions without the GUI:
# parse/inspect a firmware file, or run a full flash
# sequence against the Virtual ECU Simulator or real
# Vector hardware.
#
# Cross-platform (Windows/macOS/Linux) — uses only the
# stdlib + PySide6. Uses QApplication (same as the GUI)
# rather than QCoreApplication so the two never fight over
# Qt's single-instance-per-process rule when both run in
# the same process (e.g. the test suite). On a genuinely
# headless Linux box (no X server / Wayland), set
# QT_QPA_PLATFORM=offscreen before running this — standard
# Qt practice, no GUI is actually shown either way.
#
# Usage:
#   python cli.py info tests/sample.hex
#   python cli.py flash tests/sample.hex
#   python cli.py flash firmware.s3 --hardware vector --channel 1 \
#       --sequence suzuki --radar-side s1
#   python cli.py list-hardware
#   python cli.py test-connection --hardware vector --channel 0 \
#       --sequence suzuki --verbose
#
# Note: real Vector hardware (VN1640A/VN1630) requires the
# Vector XL Driver Library, which is Windows-only — the
# --hardware vector option is only usable there. The
# Virtual ECU Simulator (--hardware virtual, the default)
# works identically on every platform.
# ==================================================

import argparse
import sys

from PySide6.QtWidgets import QApplication

from config.settings import (
    APP_NAME,
    APP_VERSION,
    SUZUKI_RADAR_CAN_IDS,
)
from parsers.auto_parser import parse_firmware_file
from parsers.hex_parser import HexParseError
from communication.vector_can import (
    detect_vector_channels,
    detect_running_vector_tools,
)
from communication.ecu_simulator import EcuSimulator
from core.flash_sequence import (
    build_flash_sequence,
    build_suzuki_slp1_flash_sequence,
)
from core.flash_controller import FlashWorker


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


def _make_trace_handlers(verbose):
    """
    Returns (on_trace_message, on_trace_row) print callbacks
    for FlashWorker.trace_message/trace_row — shared by any
    command that wants --verbose CAN/UDS trace output.
    """

    def on_trace_message(message):
        if verbose:
            print(f"      TRACE: {message}")

    def on_trace_row(row):
        if not verbose:
            return
        req = f"{row.get('req_target') or '':<16} {row.get('req_data') or ''}"
        if row.get("resp_data"):
            resp = f" -> {row.get('resp_source')}: {row['resp_data']}"
        else:
            resp = ""
        print(f"      TRACE: {req}{resp}")

    return on_trace_message, on_trace_row


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

    try:
        datablock = parse_firmware_file(
            args.file, base_address=args.base_address
        )
    except HexParseError as e:
        print(f"Parse error: {e}", file=sys.stderr)
        return 2

    _print_datablock_info(datablock)
    return 0


# ==================================================
# Command: list-hardware
# ==================================================

def cmd_list_hardware(args):

    print("Available --hardware values:")
    print("  virtual   Virtual ECU Simulator (no hardware needed)")
    print("  vector    Vector VN1640A/VN1630 (real hardware, Windows only)")
    print()

    channels = detect_vector_channels()
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
    else:
        print(
            "No real Vector hardware detected right now "
            "(python-can/driver not installed, or nothing "
            "plugged in) — --hardware vector is unavailable "
            "until some is."
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
        return build_suzuki_slp1_flash_sequence(datablocks)

    return build_flash_sequence(datablocks)


def cmd_flash(args):

    try:
        datablock = parse_firmware_file(
            args.file, base_address=args.base_address
        )
    except HexParseError as e:
        print(f"Parse error: {e}", file=sys.stderr)
        return 2

    if not args.quiet:
        _print_datablock_info(datablock)
    datablocks = [datablock]

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

    app = QApplication.instance() or QApplication(sys.argv)

    worker = FlashWorker(
        steps=steps,
        datablocks=datablocks,
        use_virtual=use_virtual,
        security_dll_path=args.security_dll,
        keepalive_functional=(args.sequence == "suzuki"),
        can_channel=args.channel,
        can_serial=args.serial,
        can_tx_id=tx_id,
        can_rx_id=rx_id,
        can_bitrate=args.bitrate,
        can_fd=args.can_fd,
        can_data_bitrate=args.data_bitrate,
    )

    result = {"finished": False, "aborted": False}
    total_steps = len(steps)
    step_counter = {"n": 0}
    segment_last_pct = {}

    def on_step_started(description):
        step_counter["n"] += 1
        if not args.quiet:
            print(f"  [{step_counter['n']}/{total_steps}] {description}")

    def on_information_message(message):
        if not args.quiet:
            print(f"    {message}")

    on_trace_message, on_trace_row = _make_trace_handlers(args.verbose)

    def on_segment_progress(seg_idx, sent, total):
        if args.quiet or total <= 0:
            return
        pct = int((sent / total) * 100)
        if segment_last_pct.get(seg_idx, -10) >= pct - 10 and pct < 100:
            return
        segment_last_pct[seg_idx] = pct
        print(f"      segment {seg_idx + 1}: {pct}% ({sent}/{total} bytes)")

    def on_ecu_info(info):
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
        return 130

    if result["finished"]:
        print("\nFlash completed successfully.")
        return 0

    print("\nFlash aborted / failed.", file=sys.stderr)
    return 1


# ==================================================
# Command: test-connection
# ==================================================
#
# A safe, non-destructive probe: connects, opens an Extended
# then Programming session, and unlocks Security Access —
# the same steps a real flash starts with, but stops there.
# Never touches Erase Memory / TransferData / any write.
# Always tries to leave the ECU back in Default session
# (re-enabling DTC/Communication if they were disabled)
# before exiting, whether the test passed or failed partway
# through — meant to be run repeatedly against real hardware
# to verify wiring/CAN IDs/security key before trusting a
# real flash to it.
# ==================================================

def cmd_test_connection(args):

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

    app = QApplication.instance() or QApplication(sys.argv)

    on_trace_message, on_trace_row = _make_trace_handlers(args.verbose)

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

    try:
        worker._setup_uds_client()
    except Exception as e:
        print(f"Connection failed: {e}", file=sys.stderr)
        return 1

    uds = worker._uds_client
    ok = True

    def step(label):
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

        uds.diagnostic_session_control(0x02)
        step("Programming Session")

        key_func = EcuSimulator.compute_key if use_virtual else None
        uds.security_access(level=1, key_function=key_func)
        step("Security Access (ECU unlocked)")

        if not args.quiet:
            print("  Reading ECU identification...")
        info = uds.read_ecu_identification()
        for key, value in info.items():
            print(f"    {key}: {value}")

    except KeyboardInterrupt:
        print("\nInterrupted by user.", file=sys.stderr)
        ok = False
    except Exception as e:
        print(f"\nConnection test FAILED: {e}", file=sys.stderr)
        ok = False

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
        print("\nConnection test PASSED — session + security access OK.")
        return 0

    return 1


# ==================================================
# Argument Parser
# ==================================================

def _add_can_args(parser):
    """Shared CAN/UDS connection options for flash + test-connection."""

    parser.add_argument(
        "--hardware", choices=["virtual", "vector"], default="virtual",
        help="Target: Virtual ECU Simulator (default) or real Vector hardware",
    )
    parser.add_argument(
        "--channel", type=int, default=0,
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
        "--sequence", choices=["generic", "suzuki"], default="suzuki",
        help="Protocol variant: suzuki (default — Suzuki Radar, "
             "functional addressing for the pre-security steps, "
             "reverse-engineered from a real trace log) or generic",
    )
    parser.add_argument(
        "--radar-side", choices=["s0", "s1"], default="s0",
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
        "--can-fd", action="store_true",
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
        "-q", "--quiet", action="store_true",
        help="Only print the final result and errors",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Also print CAN/UDS trace (TX/RX frames)",
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
    p_info.add_argument("file", help="Path to .hex/.s19/.s3/.../.bin file")
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
    p_flash.add_argument("file", help="Path to .hex/.s19/.s3/.../.bin file")
    p_flash.add_argument(
        "--base-address", type=_parse_hex_int, default=0x0000,
        help="Start address for .bin files (hex or decimal, default 0x0000)",
    )
    _add_can_args(p_flash)
    p_flash.add_argument(
        "--dry-run", action="store_true",
        help="Print the flash sequence steps and exit, without "
             "connecting to any ECU",
    )
    p_flash.set_defaults(func=cmd_flash)

    # --- test-connection ---
    p_test = subparsers.add_parser(
        "test-connection",
        help="Safely test session + Security Access on an ECU "
             "(no Erase/Download/writes) before a real flash",
    )
    _add_can_args(p_test)
    p_test.set_defaults(func=cmd_test_connection)

    return parser


def main(argv=None):

    parser = build_arg_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

# ==================================================
# Auto-Detect Firmware Parser
# ==================================================
#
# Picks the right parser (Intel HEX / S-Record / Binary)
# based on file extension. Shared between the GUI
# (gui/configure_tab.py) and the CLI (cli.py) so the two
# never diverge on which files are routed where.
# ==================================================

import os

from parsers.hex_parser import parse_hex_file
from parsers.srec_parser import parse_srec_file
from parsers.binary_parser import parse_binary_file

SREC_EXTENSIONS = (
    ".s19", ".s28", ".s37",
    ".s1", ".s2", ".s3",
    ".srec", ".mot",
)

# Every extension parse_firmware_file() recognizes as an actual
# firmware format (vs. "anything else, tried as Intel HEX"). Single
# source of truth for callers that need to recognize firmware files
# without parsing them (e.g. gui/gitlab_dialog.py's zip-entry
# picker) — don't re-hardcode ".hex"/".bin" in a caller.
FIRMWARE_EXTENSIONS = (".hex", ".bin") + SREC_EXTENSIONS


def parse_firmware_file(file_path, base_address=0x0000):
    """
    Parse a firmware file, auto-detecting the format from its
    extension:
        .hex                -> Intel HEX
        .s19/.s3/.srec/...  -> Motorola S-Record (any type)
        .bin                -> raw binary, placed at base_address
        anything else       -> tried as Intel HEX

    Args:
        file_path: Path to the firmware file.
        base_address: Start address to use for .bin files
                      (ignored for HEX/S-Record, which carry
                      their own addresses).

    Returns:
        Datablock object.

    Raises:
        HexParseError / SrecParseError / BinaryParseError
        (all subclass HexParseError) if the file can't be
        parsed.
    """

    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".hex":
        return parse_hex_file(file_path)

    if ext in SREC_EXTENSIONS:
        return parse_srec_file(file_path)

    if ext == ".bin":
        return parse_binary_file(file_path, start_address=base_address)

    return parse_hex_file(file_path)

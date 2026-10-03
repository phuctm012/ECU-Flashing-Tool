# ==================================================
# Project File (.sfproj) — headless reader
# ==================================================
#
# gui/project_file.py's ProjectFileMixin writes a .sfproj and
# reads it back into widgets (combo indices, line edits, the
# Datablocks table's check states). That mapping is unusable
# from cli.py, which has no widgets — so this module reads the
# same JSON into plain CLI-shaped values instead: a list of
# firmware paths, "s0"/"s1", "suzuki"/"generic", a bool for
# CAN FD, a Security DLL path (or None), and so on.
#
# The file format itself stays owned by the GUI side that
# writes it; PROJECT_FORMAT_VERSION lives here so both readers
# agree on one number (gui/project_file.py imports it from
# here) instead of drifting apart.
#
# Deliberately decodes the combo *indices* the GUI persists
# rather than asking the GUI for text: the index is what's in
# the file, and the index -> meaning mapping is pinned by
# tests/test_project_config.py against gui/main_window.ui's
# item order, so a reordered combo fails loudly instead of
# silently flashing the wrong Radar Side.
# ==================================================

import json
import os

PROJECT_FORMAT_VERSION = 1

# comboBoxRadarSide item order in gui/main_window.ui
RADAR_SIDE_BY_INDEX = {0: "s0", 1: "s1"}

# comboBoxFlashSequence item order — Suzuki SLP1 is the default
# (index 0), Generic second
SEQUENCE_BY_INDEX = {0: "suzuki", 1: "generic"}

# comboBoxLogicalLink item order — CAN, then CAN FD
CAN_FD_BY_INDEX = {0: False, 1: True}

DEFAULT_TESTER_SERIAL_HEX = "00112233445566778899"


class ProjectFileError(Exception):
    """Raised when a .sfproj can't be read or isn't usable."""
    pass


def _tester_serial_bytes(text):
    """
    Mirrors ConfigureTabMixin.get_tester_serial_number(): fall
    back to the documented default rather than sending a
    malformed WriteDataByIdentifier, for an empty field, an odd
    digit count, or text that isn't hex at all (a .sfproj can be
    hand-edited, bypassing the GUI's interactive validator).
    """

    default = bytes.fromhex(DEFAULT_TESTER_SERIAL_HEX)
    text = (text or "").strip()

    if not text or len(text) % 2 != 0:
        return default

    try:
        return bytes.fromhex(text)
    except ValueError:
        return default


def load_project(path):
    """
    Reads a .sfproj and returns a dict of CLI-ready values:

        firmware_files      list of paths that were ticked
        excluded_files      list of paths that were unticked
        hardware            "virtual" or "vector"
        channel             int (-1 when the project is virtual)
        serial              int or None
        radar_side          "s0" / "s1"
        sequence            "suzuki" / "generic"
        can_fd              bool
        security_dll        path str, or None when Security
                            Access was unticked or the DLL is gone
        compression         int 0-15
        encryption          int 0-15
        tester_serial       bytes

    Raises ProjectFileError on an unreadable file, malformed
    JSON, or a format_version this build doesn't understand.
    A missing firmware file is NOT an error here — it's listed
    in `missing_files` so the caller can report every one of
    them at once instead of dying on the first.
    """

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except OSError as e:
        raise ProjectFileError(f"Could not read project file: {e}")
    except ValueError as e:
        raise ProjectFileError(f"Project file is not valid JSON: {e}")

    if not isinstance(data, dict):
        raise ProjectFileError(
            "Project file must contain a JSON object"
        )

    version = data.get("format_version", PROJECT_FORMAT_VERSION)
    if version > PROJECT_FORMAT_VERSION:
        raise ProjectFileError(
            f"Project file format version {version} is newer than "
            f"this build supports ({PROJECT_FORMAT_VERSION}) — "
            f"update SFlash to open it"
        )

    firmware_files = []
    excluded_files = []
    missing_files = []

    for entry in data.get("firmware_files", []):
        if not isinstance(entry, dict):
            continue
        file_path = entry.get("path")
        if not file_path:
            continue
        if not os.path.isfile(file_path):
            missing_files.append(file_path)
            continue
        if entry.get("checked", True):
            firmware_files.append(file_path)
        else:
            excluded_files.append(file_path)

    hardware_data = data.get("hardware") or {}
    is_virtual = hardware_data.get("is_virtual", True)
    serial = hardware_data.get("serial", -1)

    security_dll = None
    if data.get("security_access_enabled", False):
        dll_path = data.get("security_dll_path", "") or ""
        # Same tolerance as _apply_project_data(): a saved DLL
        # that has since moved falls back to the built-in
        # algorithm instead of failing the whole project load.
        if dll_path and os.path.isfile(dll_path):
            security_dll = dll_path

    def _clamped_nibble(key):
        value = data.get(key, 0)
        if not isinstance(value, int) or not 0 <= value <= 15:
            return 0
        return value

    return {
        "firmware_files": firmware_files,
        "excluded_files": excluded_files,
        "missing_files": missing_files,
        "hardware": "virtual" if is_virtual else "vector",
        "channel": hardware_data.get("channel", -1),
        "serial": None if serial in (None, -1) else serial,
        "radar_side": RADAR_SIDE_BY_INDEX.get(
            data.get("radar_side_index", 0), "s0"
        ),
        "sequence": SEQUENCE_BY_INDEX.get(
            data.get("flash_sequence_index", 0), "suzuki"
        ),
        "can_fd": CAN_FD_BY_INDEX.get(
            data.get("logical_link_index", 0), False
        ),
        "security_dll": security_dll,
        "security_access_enabled": bool(
            data.get("security_access_enabled", False)
        ),
        "compression": _clamped_nibble("compression_method"),
        "encryption": _clamped_nibble("encryption_method"),
        "tester_serial": _tester_serial_bytes(
            data.get("tester_serial_number", "")
        ),
    }

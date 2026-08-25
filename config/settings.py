# ==================================================
# Application Settings & Defaults
# ==================================================

APP_NAME = "FFlash"
APP_VERSION = "1.1"
APP_AUTHOR = "tranph9"
APP_AUTHOR_NAME = "TRAN Phuc"

# --------------------------------------------------
# Default Communication Settings
# --------------------------------------------------

CAN_CONFIGS = {

    "CAN": [
        ("Baudrate", "500,000 Baud"),
        ("Addressing Scheme", "Normal"),
        ("Physical CAN ID Type", "11-Bit"),
        ("Functional CAN ID Type", "11-Bit"),
        ("Physical Request CAN ID", "0x77B"),
        ("Response CAN ID", "0x78B"),
        ("Functional Request CAN ID", "0x700"),
    ],

    "CAN FD": [
        ("Baudrate", "500,000 Baud"),
        ("Data Baudrate", "4,000,000 Baud"),
        ("Maximum Frame Length", "64 Byte"),
        ("Addressing Scheme", "Normal"),
        ("Physical CAN ID Type", "11-Bit"),
        ("Functional CAN ID Type", "11-Bit"),
        ("Physical Request CAN ID", "0x0"),
        ("Response CAN ID", "0x0"),
        ("Functional Request CAN ID", "0x0"),
    ],
}

# --------------------------------------------------
# Suzuki Radar ECU — Physical CAN IDs
# --------------------------------------------------
#
# The vehicle has two Radar ECUs (S0/S1), each with its own
# physical Tx/Rx CAN ID. S0 is the default; the user switches
# to S1 from Configure -> Communication.

SUZUKI_RADAR_CAN_IDS = {
    "S0": {"tx_id": "0x77B", "rx_id": "0x78B"},
    "S1": {"tx_id": "0x77A", "rx_id": "0x78A"},
}

CUSTOM_CONFIG_DEFAULTS = [
    ("Erase Timeout", "120 sec"),
    ("Programming delay", "2 sec"),
    ("Post reset delay", "1 sec"),
    ("STmin override", "50 msec"),
]

# --------------------------------------------------
# Hardware
# --------------------------------------------------
#
# No static channel list here — comboBoxHardware always
# starts with just "Virtual ECU Simulator" and is filled
# out with whatever real Vector channels are actually
# detected right now, via
# communication.vector_can.detect_vector_channels().

LOGICAL_LINK_OPTIONS = [
    "CAN",
    "CAN FD",
]

# --------------------------------------------------
# File filters
# --------------------------------------------------

FILE_FILTER = (
    "S-Record Files "
    "(*.s19 *.s28 *.s37 *.s1 *.s2 *.s3 *.srec *.mot);;"
    "Hex Files (*.hex);;"
    "Binary Files (*.bin);;"
    "All Files (*)"
)

# --------------------------------------------------
# Flash status colors (Steps/Segments tables)
# --------------------------------------------------
#
# Light theme: soft, desaturated pastel tints matching the
# Engineering Blue theme's pastel accent-bg (#eef3fa) instead of
# the old saturated "Material Design" candy colors
# (#FFFACD/#C8E6C9/#FFCDD2), which clashed with the rest of
# resources/style.qss (see docs/gui_todo.md item #14). Paired
# with STATUS_TEXT_COLOR wherever a cell's background is set to
# one of these — the pastels are light regardless of surrounding
# theme, so an explicit dark foreground is required for
# readability (default text is near-white in Dark Mode).
#
# Dark theme: light pastel blocks looked like bright stickers
# against Dark Mode's navy background (user feedback after
# trying it for real). These use dark-tinted backgrounds instead
# — closer in lightness to the app's own dark surfaces
# (resources/style_dark.qss's #1e2228/#262b33) — paired with
# STATUS_TEXT_COLOR_DARK for bright, legible text, the same
# convention dark-themed dev tools (VS Code, GitHub) use for
# diff/status highlights.
#
# Shared by gui/flash_tab.py — which picks light vs. dark via
# self._dark_mode_active (kept live by gui/menu_bar.py's
# action_toggle_dark_mode(), not just read once at startup) — and,
# implicitly via each item's already-applied background(), the
# exported HTML report in gui/report_export.py (always renders
# with the light pair, since the report is a static file with its
# own fixed white background regardless of the app's live theme).

STATUS_COLOR_RUNNING = "#FCE9B5"  # soft amber — step/segment in progress
STATUS_COLOR_DONE = "#D3E9D6"     # soft green — step/segment finished
STATUS_COLOR_ERROR = "#F3D0D3"    # soft red — step/segment aborted/failed
STATUS_TEXT_COLOR = "#1a1a1a"     # dark text paired with the 3 colors above

STATUS_COLOR_RUNNING_DARK = "#4a3d1f"  # dark amber — step/segment in progress
STATUS_COLOR_DONE_DARK = "#1f3a2c"     # dark green — step/segment finished
STATUS_COLOR_ERROR_DARK = "#3d2226"    # dark red — step/segment aborted/failed
STATUS_TEXT_COLOR_DARK = "#f0f3f7"     # bright text paired with the 3 colors above

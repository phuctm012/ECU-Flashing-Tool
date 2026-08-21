# ==================================================
# Application Settings & Defaults
# ==================================================

APP_NAME = "VectorFlash Tool"
APP_VERSION = "1.0.0"

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
# The vehicle has two Radar ECUs (Left/Right), each with
# its own physical Tx/Rx CAN ID. Left is the default;
# the user switches to Right from Configure -> Communication.

SUZUKI_RADAR_CAN_IDS = {
    "Left": {"tx_id": "0x77B", "rx_id": "0x78B"},
    "Right": {"tx_id": "0x77A", "rx_id": "0x78A"},
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

HARDWARE_OPTIONS = [
    "Virtual ECU Simulator (No Hardware)",
    "VN1640A - Channel 1",
    "VN1640A - Channel 2",
    "VN1640A - Channel 3",
    "VN1640A - Channel 4",
    "VN1630 - Channel 1",
    "VN1630 - Channel 2",
]

LOGICAL_LINK_OPTIONS = [
    "CAN",
    "CAN FD",
]

# --------------------------------------------------
# File filters
# --------------------------------------------------

FILE_FILTER = (
    "Hex Files (*.hex);;"
    "S19 Files (*.s19);;"
    "Binary Files (*.bin);;"
    "All Files (*)"
)

# --------------------------------------------------
# Checksum methods
# --------------------------------------------------

CHECKSUM_METHODS = [
    "Pre-Calculation: via file selection",
    "Calculate during flash",
]

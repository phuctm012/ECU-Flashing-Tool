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
    "Hex Files (*.hex);;"
    "S-Record Files "
    "(*.s19 *.s28 *.s37 *.s1 *.s2 *.s3 *.srec *.mot);;"
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

# ==================================================
# Flash Sequence Definition
# ==================================================
#
# Defines the steps that make up a flash procedure.
# Each step has a name, a type, and optional parameters.
# This replaces the hardcoded list in FlashWorker.
# ==================================================


class FlashStep:
    """Represents a single step in the flash sequence."""

    # Step types
    TYPE_SESSION = "session"
    TYPE_SECURITY = "security"
    TYPE_COMMUNICATION = "communication"
    TYPE_DTC = "dtc"
    TYPE_ROUTINE = "routine"
    TYPE_DOWNLOAD = "download"
    TYPE_RESET = "reset"
    TYPE_CUSTOM = "custom"
    TYPE_READ_DID = "read_did"
    TYPE_WRITE_DID = "write_did"

    def __init__(
        self,
        name,
        step_type,
        description="",
        params=None,
        enabled=True,
    ):
        self.name = name
        self.step_type = step_type
        self.description = description or name
        self.params = params or {}
        self.enabled = enabled


# --------------------------------------------------
# Default Flash Sequence (UDS-based)
# --------------------------------------------------

DEFAULT_FLASH_SEQUENCE = [

    FlashStep(
        name="Read ECU Identification",
        step_type=FlashStep.TYPE_READ_DID,
        description="Read ECU Identification (Before Flash)",
        params={
            "dids": [
                0xF189,  # SW Version
                0xF191,  # HW Version
                0xF187,  # Part Number
                0xF18C,  # Serial Number
            ],
            "phase": "before",
        }
    ),

    FlashStep(
        name="Start Communication",
        step_type=FlashStep.TYPE_SESSION,
        description="Start Communication",
        params={"session": "default"}
    ),

    FlashStep(
        name="Start Extended Session (Network)",
        step_type=FlashStep.TYPE_SESSION,
        description="Start Extended Session (Network)",
        params={"session": "extended", "scope": "network"}
    ),

    FlashStep(
        name="Check Programming Preconditions",
        step_type=FlashStep.TYPE_ROUTINE,
        description="Check Programming Preconditions",
        params={"routine_id": 0xFF00}
    ),

    FlashStep(
        name="Disable DTC Settings (Network)",
        step_type=FlashStep.TYPE_DTC,
        description="Disable DTC Settings (Network)",
        params={"action": "disable", "scope": "network"}
    ),

    FlashStep(
        name="Disable Normal Communication (Network)",
        step_type=FlashStep.TYPE_COMMUNICATION,
        description="Disable Normal Communication (Network)",
        params={"action": "disable", "scope": "network"}
    ),

    FlashStep(
        name="Start Programming Session",
        step_type=FlashStep.TYPE_SESSION,
        description="Start Programming Session",
        params={"session": "programming"}
    ),

    FlashStep(
        name="Unlock ECU (Security Access)",
        step_type=FlashStep.TYPE_SECURITY,
        description="Unlock ECU (Security Access)",
        params={"level": 1}
    ),

    FlashStep(
        name="Write Fingerprint",
        step_type=FlashStep.TYPE_CUSTOM,
        description="Write Fingerprint",
        params={"did": 0xF15A}
    ),

    FlashStep(
        name="Erase Memory",
        step_type=FlashStep.TYPE_ROUTINE,
        description="Erase Memory",
        params={"routine_id": 0xFF00, "action": "erase"}
    ),

    # Download steps will be generated dynamically
    # based on the number of datablocks/segments

    FlashStep(
        name="Verify Memory",
        step_type=FlashStep.TYPE_ROUTINE,
        description="Verify Memory",
        params={"routine_id": 0xFF01, "action": "verify"}
    ),

    FlashStep(
        name="Read ECU Identification (After Flash)",
        step_type=FlashStep.TYPE_READ_DID,
        description="Read ECU Identification (After Flash)",
        params={
            "dids": [0xF189],  # Read new SW Version
            "phase": "after",
        }
    ),

    FlashStep(
        name="Reset ECU",
        step_type=FlashStep.TYPE_RESET,
        description="Reset ECU",
        params={"reset_type": "hard"}
    ),
]


def build_flash_sequence(
    datablocks=None,
    sequence=None,
    addr_length=4,
    size_length=4,
):
    """
    Build a complete flash sequence with download steps
    inserted based on the actual datablocks/segments.

    Args:
        datablocks: List of datablock objects with segments.
                    If None, returns the sequence without downloads.
        sequence: Template list of FlashStep to build from.
                 Defaults to DEFAULT_FLASH_SEQUENCE. The step
                 named "Erase Memory" marks where Download
                 steps get inserted.
        addr_length: memoryAddress byte length to use for the
                    generated RequestDownload (0x34) calls.
                    ECU/OEM-specific — default 4 bytes.
        size_length: memorySize byte length to use for the
                    generated RequestDownload (0x34) calls.
                    ECU/OEM-specific — default 4 bytes.

    Returns:
        List of FlashStep objects.
    """

    template = sequence if sequence is not None else DEFAULT_FLASH_SEQUENCE

    result = []

    for step in template:

        # Skip disabled steps
        if not step.enabled:
            continue

        if step.name == "Erase Memory" and datablocks:

            # Add erase step
            result.append(step)

            # Insert download steps for each datablock
            for db_idx, datablock in enumerate(datablocks):

                for seg_idx, segment in enumerate(datablock.segments):

                    result.append(
                        FlashStep(
                            name="Download",
                            step_type=FlashStep.TYPE_DOWNLOAD,
                            description=(
                                f"Download Datablock {db_idx + 1} "
                                f"Segment {seg_idx + 1} "
                                f"(0x{segment.start_address:X}, "
                                f"{len(segment.data)} bytes)"
                            ),
                            params={
                                "datablock_index": db_idx,
                                "segment_index": seg_idx,
                                "start_address": segment.start_address,
                                "data": segment.data,
                                "addr_length": addr_length,
                                "size_length": size_length,
                            }
                        )
                    )

        else:
            result.append(step)

    return result


# --------------------------------------------------
# Suzuki SLP1 Flash Sequence
# --------------------------------------------------
#
# Reverse-engineered from a real flashing trace log
# (20260816_102921_Report_Trace.csv) captured against
# an actual Suzuki ECU ("Suzuki SLP1") over CAN.
#
# Differences vs. DEFAULT_FLASH_SEQUENCE, all confirmed
# from the trace:
#   - No separate "Check Preconditions" call — routine
#     0xFF00 is only invoked once, in Programming session
#     (= Erase Memory). No ReadDataByIdentifier calls at
#     all (this OEM tool doesn't read ECU ID before/after).
#   - Session control, DTC setting and Communication
#     Control are sent FUNCTIONALLY (broadcast to 0x700)
#     before the Programming session starts; TesterPresent
#     keepalive during the long Erase wait is functional
#     too. Everything from the Programming session onward
#     (security, WriteDID, erase, download, verify, reset)
#     is sent to the ECU's physical address.
#   - ControlDTCSetting carries an extra manufacturer
#     option byte (0x00). CommunicationControl disables
#     only "Normal" messages (communication_type=0x01),
#     not Normal+NetworkManagement.
#   - WriteDataByIdentifier targets DID 0xF198 (10-byte
#     tester/fingerprint payload) and DID 0xF199 (4-byte
#     packed-BCD programming date), not DID 0xF15A.
#   - RoutineControl (Erase 0xFF00, Verify 0xFF01) both
#     carry a trailing optionRecord byte (0x00).
#   - RequestDownload uses a 5-byte memoryAddress field
#     (addressAndLengthFormatIdentifier = 0x45) instead
#     of the usual 4 bytes.
#   - After ECUReset, the tool sends one more
#     DiagnosticSessionControl(Default) functionally to
#     confirm the ECU came back online.
# --------------------------------------------------

def _bcd_date_today():
    """
    Packed-BCD programming date: [century, year, month, day].
    Matches DID 0xF199 in the real trace, e.g. bytes
    20 26 08 16 == 2026-08-16.
    """

    from datetime import date

    today = date.today()
    century, year = divmod(today.year, 100)

    def bcd(n):
        return ((n // 10) << 4) | (n % 10)

    return bytes([
        bcd(century), bcd(year), bcd(today.month), bcd(today.day)
    ])


SUZUKI_SLP1_FLASH_SEQUENCE = [

    FlashStep(
        name="Start Extended Session (Network)",
        step_type=FlashStep.TYPE_SESSION,
        description="Start Extended Session (Network)",
        params={
            "session": "extended",
            "functional": True,
        }
    ),

    FlashStep(
        name="Disable DTC Settings (Network)",
        step_type=FlashStep.TYPE_DTC,
        description="Disable DTC Settings (Network)",
        params={
            "action": "disable",
            "functional": True,
            "option_record": bytes([0x00]),
        }
    ),

    FlashStep(
        name="Disable Normal Communication (Network)",
        step_type=FlashStep.TYPE_COMMUNICATION,
        description="Disable Normal Communication (Network)",
        params={
            "action": "disable",
            "functional": True,
            "comm_type": 0x01,
        }
    ),

    FlashStep(
        name="Start Programming Session",
        step_type=FlashStep.TYPE_SESSION,
        description="Start Programming Session",
        params={"session": "programming"}
    ),

    FlashStep(
        name="Unlock ECU (Security Access)",
        step_type=FlashStep.TYPE_SECURITY,
        description="Unlock ECU (Security Access)",
        params={"level": 1}
    ),

    FlashStep(
        name="Write Tester Info",
        step_type=FlashStep.TYPE_WRITE_DID,
        description="Write Tester Info (DID 0xF198)",
        params={
            "did": 0xF198,
            "data": bytes.fromhex(
                "00112233445566778899"
            ),
        }
    ),

    FlashStep(
        name="Write Programming Date",
        step_type=FlashStep.TYPE_WRITE_DID,
        description="Write Programming Date (DID 0xF199)",
        params={
            "did": 0xF199,
            "data": _bcd_date_today(),
        }
    ),

    FlashStep(
        name="Erase Memory",
        step_type=FlashStep.TYPE_ROUTINE,
        description="Erase Memory",
        params={
            "routine_id": 0xFF00,
            "action": "erase",
            "option_record": bytes([0x00]),
        }
    ),

    # Download steps inserted here dynamically,
    # using addr_length=5 (see build_flash_sequence call).

    FlashStep(
        name="Verify Memory",
        step_type=FlashStep.TYPE_ROUTINE,
        description="Verify Memory",
        params={
            "routine_id": 0xFF01,
            "option_record": bytes([0x00]),
            "action": "verify",
        }
    ),

    FlashStep(
        name="Reset ECU",
        step_type=FlashStep.TYPE_RESET,
        description="Reset ECU",
        params={"reset_type": "hard"}
    ),

    FlashStep(
        name="Confirm Default Session (Network)",
        step_type=FlashStep.TYPE_SESSION,
        description="Confirm Default Session (Network)",
        params={
            "session": "default",
            "functional": True,
        }
    ),
]


def build_suzuki_slp1_flash_sequence(datablocks=None):
    """
    Build the Suzuki SLP1 flash sequence (see
    SUZUKI_SLP1_FLASH_SEQUENCE), with Download steps
    inserted using the 5-byte memoryAddress field this
    ECU expects.
    """

    return build_flash_sequence(
        datablocks,
        sequence=SUZUKI_SLP1_FLASH_SEQUENCE,
        addr_length=5,
        size_length=4,
    )

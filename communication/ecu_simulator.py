# ==================================================
# ECU Simulator
# ==================================================
#
# Simulates a real ECU responding to UDS requests.
# Implements the standard UDS flash sequence:
#   - Session management (0x10)
#   - Security Access (0x27)
#   - Communication Control (0x28)
#   - DTC Setting (0x85)
#   - Routine Control (0x31)
#   - Request Download (0x34)
#   - Transfer Data (0x36)
#   - Request Transfer Exit (0x37)
#   - Write Data (0x2E)
#   - ECU Reset (0x11)
#
# Supports configurable behavior:
#   - Random NRC errors
#   - Response delays
#   - Security key algorithm
# ==================================================

import time
import random
import struct
import hashlib
from typing import Optional

from communication.can_interface import CanMessage


# --------------------------------------------------
# UDS Constants
# --------------------------------------------------

# Positive response = SID + 0x40
POSITIVE_RESPONSE_OFFSET = 0x40

# Negative Response Code (NRC)
NRC_GENERAL_REJECT = 0x10
NRC_SERVICE_NOT_SUPPORTED = 0x11
NRC_SUB_FUNCTION_NOT_SUPPORTED = 0x12
NRC_INCORRECT_MESSAGE_LENGTH = 0x13
NRC_CONDITIONS_NOT_CORRECT = 0x22
NRC_REQUEST_SEQUENCE_ERROR = 0x24
NRC_REQUEST_OUT_OF_RANGE = 0x31
NRC_SECURITY_ACCESS_DENIED = 0x33
NRC_INVALID_KEY = 0x35
NRC_EXCEEDED_ATTEMPTS = 0x36
NRC_REQUIRED_TIME_DELAY = 0x37
NRC_UPLOAD_DOWNLOAD_NOT_ACCEPTED = 0x70
NRC_TRANSFER_DATA_SUSPENDED = 0x71
NRC_GENERAL_PROGRAMMING_FAILURE = 0x72
NRC_WRONG_BLOCK_SEQUENCE = 0x73
NRC_RESPONSE_PENDING = 0x78
NRC_SERVICE_NOT_SUPPORTED_IN_SESSION = 0x7F

# UDS Service IDs
SID_DIAGNOSTIC_SESSION = 0x10
SID_ECU_RESET = 0x11
SID_SECURITY_ACCESS = 0x27
SID_COMMUNICATION_CONTROL = 0x28
SID_WRITE_DATA = 0x2E
SID_ROUTINE_CONTROL = 0x31
SID_REQUEST_DOWNLOAD = 0x34
SID_REQUEST_UPLOAD = 0x35
SID_TRANSFER_DATA = 0x36
SID_TRANSFER_EXIT = 0x37
SID_CONTROL_DTC = 0x85
SID_TESTER_PRESENT = 0x3E
SID_READ_DATA = 0x22

# Sessions
SESSION_DEFAULT = 0x01
SESSION_PROGRAMMING = 0x02
SESSION_EXTENDED = 0x03

# Security levels
SECURITY_LEVEL_1 = 0x01
SECURITY_LEVEL_2 = 0x03


class EcuSimulator:
    """
    Simulates an ECU responding to UDS requests.

    Behaves like a real ECU with:
    - Session management
    - Security access (seed & key)
    - Flash download acceptance
    - Configurable response delays
    - Optional random NRC errors
    """

    def __init__(
        self,
        response_delay_ms=5,
        error_rate=0.0,
        max_block_length=4096,
    ):
        """
        Args:
            response_delay_ms: Simulated response delay.
            error_rate: Probability of random NRC (0.0-1.0).
            max_block_length: Max download block size.
        """

        self.response_delay_ms = response_delay_ms
        self.error_rate = error_rate
        self.max_block_length = max_block_length

        # ECU State
        self._current_session = SESSION_DEFAULT
        self._security_unlocked = False
        self._security_seed = None
        self._security_attempts = 0
        self._download_active = False
        self._expected_block_seq = 0
        self._download_total_bytes = 0
        self._download_received_bytes = 0
        self._erase_done = False
        self._fingerprint_written = False

        # ECU Info
        self.ecu_name = "SimulatedECU"
        self.sw_version = "V1.0.0"
        self.hw_version = "HW_1.0"

    # ==========================================
    # Process Request
    # ==========================================

    def process_request(
        self,
        request_data: bytes
    ) -> bytes:
        """
        Process a UDS request and return response.

        Args:
            request_data: Raw UDS request bytes.

        Returns:
            Raw UDS response bytes.
        """

        if len(request_data) == 0:
            return self._negative_response(
                0x00, NRC_INCORRECT_MESSAGE_LENGTH
            )

        # Simulate response delay
        if self.response_delay_ms > 0:
            time.sleep(
                self.response_delay_ms / 1000.0
            )

        # Random error injection
        if (self.error_rate > 0 and
                random.random() < self.error_rate):
            return self._negative_response(
                request_data[0],
                NRC_CONDITIONS_NOT_CORRECT
            )

        sid = request_data[0]

        # Dispatch to handler
        handlers = {
            SID_DIAGNOSTIC_SESSION: self._handle_session,
            SID_ECU_RESET: self._handle_reset,
            SID_READ_DATA: self._handle_read_data,
            SID_SECURITY_ACCESS: self._handle_security,
            SID_COMMUNICATION_CONTROL: self._handle_comm_control,
            SID_WRITE_DATA: self._handle_write_data,
            SID_ROUTINE_CONTROL: self._handle_routine,
            SID_REQUEST_DOWNLOAD: self._handle_download,
            SID_TRANSFER_DATA: self._handle_transfer,
            SID_TRANSFER_EXIT: self._handle_transfer_exit,
            SID_CONTROL_DTC: self._handle_dtc_control,
            SID_TESTER_PRESENT: self._handle_tester_present,
        }

        handler = handlers.get(sid)

        if handler is None:
            return self._negative_response(
                sid, NRC_SERVICE_NOT_SUPPORTED
            )

        return handler(request_data)

    # ==========================================
    # Diagnostic Session Control (0x10)
    # ==========================================

    def _handle_session(self, data):

        if len(data) < 2:
            return self._negative_response(
                SID_DIAGNOSTIC_SESSION,
                NRC_INCORRECT_MESSAGE_LENGTH
            )

        sub_function = data[1]

        if sub_function not in (
            SESSION_DEFAULT,
            SESSION_PROGRAMMING,
            SESSION_EXTENDED
        ):
            return self._negative_response(
                SID_DIAGNOSTIC_SESSION,
                NRC_SUB_FUNCTION_NOT_SUPPORTED
            )

        # Programming session requires extended first
        if (sub_function == SESSION_PROGRAMMING and
                self._current_session != SESSION_EXTENDED):
            return self._negative_response(
                SID_DIAGNOSTIC_SESSION,
                NRC_CONDITIONS_NOT_CORRECT
            )

        self._current_session = sub_function

        # Reset security on session change
        if sub_function == SESSION_DEFAULT:
            self._security_unlocked = False
            self._download_active = False

        # Response: SID+0x40, subFunction, P2server(2), P2*server(2)
        return bytes([
            SID_DIAGNOSTIC_SESSION + POSITIVE_RESPONSE_OFFSET,
            sub_function,
            0x00, 0x32,  # P2 = 50ms
            0x01, 0xF4,  # P2* = 5000ms
        ])

    # ==========================================
    # ReadDataByIdentifier (0x22)
    # ==========================================

    def _handle_read_data(self, data):

        if len(data) < 3:
            return self._negative_response(
                SID_READ_DATA,
                NRC_INCORRECT_MESSAGE_LENGTH
            )

        did = (data[1] << 8) | data[2]

        # Simulated DID data
        did_data = {
            0xF189: self.sw_version.encode('ascii'),
            0xF191: self.hw_version.encode('ascii'),
            0xF180: b'BOOT_1.0.0',
            0xF18C: b'SN-SIM-001-2026',
            0xF187: b'PN-12345-678',
            0xF188: b'SW-12345-678',
            0xF18A: b'SUPPLIER_01',
            0xF18B: bytes([0x20, 0x26, 0x08, 0x01]),
            0xF195: b'SUP_SW_1.0.0',
            0xF15A: self._get_fingerprint_data(),
            0xF186: bytes([self._current_session]),
        }

        response_data = did_data.get(did)

        if response_data is None:
            return self._negative_response(
                SID_READ_DATA,
                NRC_REQUEST_OUT_OF_RANGE
            )

        return bytes([
            SID_READ_DATA + POSITIVE_RESPONSE_OFFSET,
            data[1], data[2],
        ]) + response_data

    def _get_fingerprint_data(self):
        """Return fingerprint data if written."""

        if self._fingerprint_written:
            return bytes([
                0x20, 0x26, 0x08, 0x20,
                0x01, 0x02, 0x03,
            ])
        return bytes(7)  # Empty

    # ==========================================
    # ECU Reset (0x11)
    # ==========================================

    def _handle_reset(self, data):

        if len(data) < 2:
            return self._negative_response(
                SID_ECU_RESET,
                NRC_INCORRECT_MESSAGE_LENGTH
            )

        reset_type = data[1]

        # Simulate reset delay
        time.sleep(0.1)

        # Reset ECU state
        self._current_session = SESSION_DEFAULT
        self._security_unlocked = False
        self._download_active = False
        self._erase_done = False
        self._fingerprint_written = False

        return bytes([
            SID_ECU_RESET + POSITIVE_RESPONSE_OFFSET,
            reset_type,
        ])

    # ==========================================
    # Security Access (0x27)
    # ==========================================

    def _handle_security(self, data):

        if len(data) < 2:
            return self._negative_response(
                SID_SECURITY_ACCESS,
                NRC_INCORRECT_MESSAGE_LENGTH
            )

        # Must be in programming session
        if self._current_session != SESSION_PROGRAMMING:
            return self._negative_response(
                SID_SECURITY_ACCESS,
                NRC_SERVICE_NOT_SUPPORTED_IN_SESSION
            )

        access_type = data[1]

        # Odd = request seed, Even = send key
        if access_type % 2 == 1:
            # Request Seed
            return self._handle_request_seed(
                access_type
            )
        else:
            # Send Key
            return self._handle_send_key(
                access_type, data
            )

    def _handle_request_seed(self, level):

        if self._security_unlocked:
            # Already unlocked → seed = 0
            return bytes([
                SID_SECURITY_ACCESS + POSITIVE_RESPONSE_OFFSET,
                level,
                0x00, 0x00, 0x00, 0x00,
            ])

        if self._security_attempts >= 3:
            return self._negative_response(
                SID_SECURITY_ACCESS,
                NRC_EXCEEDED_ATTEMPTS
            )

        # Generate random seed
        self._security_seed = random.randint(
            0x10000000, 0xFFFFFFFF
        )

        seed_bytes = struct.pack(
            ">I", self._security_seed
        )

        return bytes([
            SID_SECURITY_ACCESS + POSITIVE_RESPONSE_OFFSET,
            level,
        ]) + seed_bytes

    def _handle_send_key(self, level, data):

        if len(data) < 6:
            return self._negative_response(
                SID_SECURITY_ACCESS,
                NRC_INCORRECT_MESSAGE_LENGTH
            )

        if self._security_seed is None:
            return self._negative_response(
                SID_SECURITY_ACCESS,
                NRC_REQUEST_SEQUENCE_ERROR
            )

        # Extract key from request
        key_received = struct.unpack(
            ">I", data[2:6]
        )[0]

        # Calculate expected key
        expected_key = self._calculate_key(
            self._security_seed
        )

        if key_received == expected_key:

            self._security_unlocked = True
            self._security_attempts = 0
            self._security_seed = None

            return bytes([
                SID_SECURITY_ACCESS + POSITIVE_RESPONSE_OFFSET,
                level,
            ])

        else:

            self._security_attempts += 1
            self._security_seed = None

            return self._negative_response(
                SID_SECURITY_ACCESS,
                NRC_INVALID_KEY
            )

    def _calculate_key(self, seed):
        """
        Default key calculation algorithm.
        Simple XOR-based algorithm for simulation.

        In real-world: This would call the OEM's
        Security DLL or use their specific algorithm.
        """

        key = seed ^ 0xDEADBEEF
        key = ((key << 3) | (key >> 29)) & 0xFFFFFFFF
        key = key ^ 0xCAFEBABE

        return key & 0xFFFFFFFF

    # ==========================================
    # Communication Control (0x28)
    # ==========================================

    def _handle_comm_control(self, data):

        if len(data) < 3:
            return self._negative_response(
                SID_COMMUNICATION_CONTROL,
                NRC_INCORRECT_MESSAGE_LENGTH
            )

        control_type = data[1]
        comm_type = data[2]

        return bytes([
            SID_COMMUNICATION_CONTROL + POSITIVE_RESPONSE_OFFSET,
            control_type,
        ])

    # ==========================================
    # Control DTC Setting (0x85)
    # ==========================================

    def _handle_dtc_control(self, data):

        if len(data) < 2:
            return self._negative_response(
                SID_CONTROL_DTC,
                NRC_INCORRECT_MESSAGE_LENGTH
            )

        setting_type = data[1]

        return bytes([
            SID_CONTROL_DTC + POSITIVE_RESPONSE_OFFSET,
            setting_type,
        ])

    # ==========================================
    # Write Data By Identifier (0x2E)
    # ==========================================

    def _handle_write_data(self, data):

        if len(data) < 4:
            return self._negative_response(
                SID_WRITE_DATA,
                NRC_INCORRECT_MESSAGE_LENGTH
            )

        # Must be unlocked
        if not self._security_unlocked:
            return self._negative_response(
                SID_WRITE_DATA,
                NRC_SECURITY_ACCESS_DENIED
            )

        did = (data[1] << 8) | data[2]

        # DID 0xF15A = Fingerprint
        if did == 0xF15A:
            self._fingerprint_written = True

        return bytes([
            SID_WRITE_DATA + POSITIVE_RESPONSE_OFFSET,
            data[1], data[2],
        ])

    # ==========================================
    # Routine Control (0x31)
    # ==========================================

    def _handle_routine(self, data):

        if len(data) < 4:
            return self._negative_response(
                SID_ROUTINE_CONTROL,
                NRC_INCORRECT_MESSAGE_LENGTH
            )

        sub_function = data[1]
        routine_id = (data[2] << 8) | data[3]

        # sub 0x01 = startRoutine
        # sub 0x02 = stopRoutine
        # sub 0x03 = requestResults

        # Routines that DON'T require security unlock:
        # - Check Programming Preconditions (0xFF00 in extended session)
        # All other routines require security
        precondition_check = (
            routine_id == 0xFF00
            and self._current_session == SESSION_EXTENDED
            and not self._security_unlocked
        )

        if not precondition_check and not self._security_unlocked:
            return self._negative_response(
                SID_ROUTINE_CONTROL,
                NRC_SECURITY_ACCESS_DENIED
            )

        if routine_id == 0xFF00:
            if self._current_session == SESSION_EXTENDED:
                # Check Programming Preconditions
                time.sleep(0.1)
            else:
                # Erase Memory (in programming session)
                if sub_function == 0x01:
                    time.sleep(0.5)
                    self._erase_done = True

        elif routine_id == 0xFF01:
            # Check Programming Dependencies / Verify
            time.sleep(0.2)

        elif routine_id == 0x0202:
            # Check Memory (verify)
            time.sleep(0.3)

        return bytes([
            SID_ROUTINE_CONTROL + POSITIVE_RESPONSE_OFFSET,
            sub_function,
            data[2], data[3],
            0x00,  # routineStatusRecord (success)
        ])

    # ==========================================
    # Request Download (0x34)
    # ==========================================

    def _handle_download(self, data):

        if len(data) < 4:
            return self._negative_response(
                SID_REQUEST_DOWNLOAD,
                NRC_INCORRECT_MESSAGE_LENGTH
            )

        # Must be unlocked and erased
        if not self._security_unlocked:
            return self._negative_response(
                SID_REQUEST_DOWNLOAD,
                NRC_SECURITY_ACCESS_DENIED
            )

        if not self._erase_done:
            return self._negative_response(
                SID_REQUEST_DOWNLOAD,
                NRC_UPLOAD_DOWNLOAD_NOT_ACCEPTED
            )

        # ISO 14229-1 order: SID, dataFormatIdentifier,
        # addressAndLengthFormatIdentifier, then address/size.
        data_format = data[1]
        addr_len_format = data[2]

        mem_size_len = (addr_len_format >> 4) & 0x0F
        mem_addr_len = addr_len_format & 0x0F

        # Extract memory address and size
        offset = 3
        mem_addr = 0
        for i in range(mem_addr_len):
            mem_addr = (mem_addr << 8) | data[offset + i]
        offset += mem_addr_len

        mem_size = 0
        for i in range(mem_size_len):
            mem_size = (mem_size << 8) | data[offset + i]

        self._download_active = True
        self._expected_block_seq = 1
        self._download_total_bytes = mem_size
        self._download_received_bytes = 0

        # Response: maxNumberOfBlockLength
        max_block = self.max_block_length
        max_block_bytes = max_block.to_bytes(2, 'big')

        return bytes([
            SID_REQUEST_DOWNLOAD + POSITIVE_RESPONSE_OFFSET,
            0x20,  # lengthFormatIdentifier (2 bytes)
        ]) + max_block_bytes

    # ==========================================
    # Transfer Data (0x36)
    # ==========================================

    def _handle_transfer(self, data):

        if len(data) < 2:
            return self._negative_response(
                SID_TRANSFER_DATA,
                NRC_INCORRECT_MESSAGE_LENGTH
            )

        if not self._download_active:
            return self._negative_response(
                SID_TRANSFER_DATA,
                NRC_REQUEST_SEQUENCE_ERROR
            )

        block_seq = data[1]

        # Check block sequence counter
        if block_seq != (
            self._expected_block_seq & 0xFF
        ):
            return self._negative_response(
                SID_TRANSFER_DATA,
                NRC_WRONG_BLOCK_SEQUENCE
            )

        # Accept data
        transfer_data = data[2:]
        self._download_received_bytes += len(
            transfer_data
        )
        self._expected_block_seq += 1

        # Small delay to simulate flash write
        time.sleep(0.002)

        return bytes([
            SID_TRANSFER_DATA + POSITIVE_RESPONSE_OFFSET,
            block_seq,
        ])

    # ==========================================
    # Request Transfer Exit (0x37)
    # ==========================================

    def _handle_transfer_exit(self, data):

        if not self._download_active:
            return self._negative_response(
                SID_TRANSFER_EXIT,
                NRC_REQUEST_SEQUENCE_ERROR
            )

        self._download_active = False
        self._expected_block_seq = 0

        return bytes([
            SID_TRANSFER_EXIT + POSITIVE_RESPONSE_OFFSET,
        ])

    # ==========================================
    # Tester Present (0x3E)
    # ==========================================

    def _handle_tester_present(self, data):

        sub_function = data[1] if len(data) > 1 else 0x00

        # suppressPositiveResponse bit
        if sub_function & 0x80:
            return None  # No response

        return bytes([
            SID_TESTER_PRESENT + POSITIVE_RESPONSE_OFFSET,
            0x00,
        ])

    # ==========================================
    # Negative Response
    # ==========================================

    def _negative_response(self, sid, nrc):

        return bytes([0x7F, sid, nrc])

    # ==========================================
    # Get Key (Public Method for UDS Client)
    # ==========================================

    @staticmethod
    def compute_key(seed):
        """
        Public method so UDS client can compute
        the correct key for this simulator.
        """

        key = seed ^ 0xDEADBEEF
        key = ((key << 3) | (key >> 29)) & 0xFFFFFFFF
        key = key ^ 0xCAFEBABE

        return key & 0xFFFFFFFF

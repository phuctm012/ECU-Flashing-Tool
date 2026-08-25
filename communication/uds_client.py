# ==================================================
# UDS Client (ISO 14229)
# ==================================================
#
# High-level UDS protocol client that uses a
# CAN interface (Virtual or Real) to communicate
# with an ECU.
#
# Implements all services needed for flash:
#   - DiagnosticSessionControl (0x10)
#   - ECUReset (0x11)
#   - ReadDataByIdentifier (0x22)
#   - SecurityAccess (0x27)
#   - CommunicationControl (0x28)
#   - WriteDataByIdentifier (0x2E)
#   - RoutineControl (0x31)
#   - RequestDownload (0x34)
#   - TransferData (0x36)
#   - RequestTransferExit (0x37)
#   - TesterPresent (0x3E)
#   - ControlDTCSetting (0x85)
#
# Features:
#   - NRC retry logic (configurable)
#   - TesterPresent keepalive integration
#   - Security DLL loader support
#   - ResponsePending (0x78) handling
# ==

import struct
import time
from typing import Optional, Callable

from communication.can_interface import (
    CanInterface,
    CanMessage,
    CanError,
    CanTimeoutError,
)

from communication.ecu_simulator import (
    POSITIVE_RESPONSE_OFFSET,
    NRC_RESPONSE_PENDING,
    SID_DIAGNOSTIC_SESSION,
    SID_ECU_RESET,
    SID_SECURITY_ACCESS,
    SID_COMMUNICATION_CONTROL,
    SID_WRITE_DATA,
    SID_ROUTINE_CONTROL,
    SID_REQUEST_DOWNLOAD,
    SID_TRANSFER_DATA,
    SID_TRANSFER_EXIT,
    SID_CONTROL_DTC,
    SID_TESTER_PRESENT,
    SESSION_DEFAULT,
    SESSION_PROGRAMMING,
    SESSION_EXTENDED,
)

# SID for ReadDataByIdentifier
SID_READ_DATA = 0x22

# NRC codes that are retryable
RETRYABLE_NRC = {
    0x21,  # Busy - Repeat Request
    0x22,  # Conditions Not Correct
    0x78,  # Response Pending (handled separately)
}


# --------------------------------------------------
# NRC Names (for readable error messages)
# --------------------------------------------------

NRC_NAMES = {
    0x10: "General Reject",
    0x11: "Service Not Supported",
    0x12: "Sub-Function Not Supported",
    0x13: "Incorrect Message Length",
    0x14: "Response Too Long",
    0x22: "Conditions Not Correct",
    0x24: "Request Sequence Error",
    0x25: "No Response From Sub-Net",
    0x31: "Request Out Of Range",
    0x33: "Security Access Denied",
    0x35: "Invalid Key",
    0x36: "Exceeded Number Of Attempts",
    0x37: "Required Time Delay Not Expired",
    0x70: "Upload/Download Not Accepted",
    0x71: "Transfer Data Suspended",
    0x72: "General Programming Failure",
    0x73: "Wrong Block Sequence Counter",
    0x78: "Response Pending",
    0x7E: "Sub-Function Not Supported In Active Session",
    0x7F: "Service Not Supported In Active Session",
}


class UdsError(Exception):
    """Base UDS error."""
    pass


class UdsNegativeResponse(UdsError):
    """Raised when ECU returns a negative response."""

    def __init__(self, sid, nrc):
        self.sid = sid
        self.nrc = nrc
        nrc_name = NRC_NAMES.get(nrc, "Unknown")
        super().__init__(
            f"Negative Response: SID=0x{sid:02X}, "
            f"NRC=0x{nrc:02X} ({nrc_name})"
        )


class UdsTimeoutError(UdsError):
    """Raised when no response is received."""
    pass


class UdsClient:
    """
    High-level UDS client for ECU communication.

    Usage:
        can = VirtualCanInterface()
        can.connect()

        uds = UdsClient(can)
        uds.diagnostic_session_control(0x03)
        uds.security_access(level=1, key_func=my_key)
        uds.request_download(addr, size)
        uds.transfer_data(data)
        uds.request_transfer_exit()
        uds.ecu_reset(0x01)
    """

    def __init__(
        self,
        can_interface,
        p2_timeout=2.0,
        p2_star_timeout=10.0,
        trace_callback=None,
        max_retries=3,
        retry_delay=0.5,
        functional_id=0x700,
    ):
        """
        Args:
            can_interface: CanInterface implementation.
            p2_timeout: Response timeout (seconds).
            p2_star_timeout: Extended timeout for
                             ResponsePending (seconds).
            trace_callback: Optional callback(direction,
                            data_bytes) for logging.
            max_retries: Max number of retries on
                        retryable NRC codes.
            retry_delay: Delay between retries (seconds).
            functional_id: Arbitration ID used for
                           functional (broadcast) requests
                           — e.g. network-wide session
                           control, DTC setting, comm
                           control before addressing a
                           specific ECU physically.
        """

        self._can = can_interface
        self._p2_timeout = p2_timeout
        self._p2_star_timeout = p2_star_timeout
        self._trace_callback = trace_callback
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._functional_id = functional_id

        # TesterPresent keepalive reference
        self._tp_keepalive = None

        # Security DLL function reference
        self._security_dll_func = None
        self._security_dll_is_bytes = False

    # ==========================================
    # Core: Send & Receive
    # ==========================================

    def _send_request(
        self,
        data: bytes,
        retry=True,
        functional=False,
    ) -> bytes:
        """
        Send a UDS request and wait for response.
        Handles NRC 0x78 (ResponsePending) automatically.
        Retries on retryable NRC codes.

        Args:
            data: Raw UDS request payload.
            retry: If True, retry on retryable NRC.
            functional: If True, send to the functional
                       (broadcast) arbitration ID instead
                       of the physical request ID.

        Returns:
            Raw UDS response payload.

        Raises:
            UdsNegativeResponse: If ECU returns NRC.
            UdsTimeoutError: If no response received.
        """

        retries_left = self._max_retries if retry else 0

        while True:

            try:
                return self._send_single_request(
                    data, functional=functional
                )

            except UdsNegativeResponse as e:

                if (e.nrc in RETRYABLE_NRC
                        and retries_left > 0):

                    retries_left -= 1

                    if self._trace_callback:
                        self._trace_callback(
                            "RETRY",
                            f"NRC 0x{e.nrc:02X}, "
                            f"{retries_left} retries left"
                            .encode()
                        )

                    time.sleep(self._retry_delay)
                    continue

                raise

    def _send_single_request(
        self,
        data: bytes,
        functional=False,
    ) -> bytes:
        """
        Send a single UDS request (no retry).
        Handles NRC 0x78 (ResponsePending).
        """

        # Pause TesterPresent keepalive during request
        if self._tp_keepalive:
            self._tp_keepalive.pause()

        try:

            # Trace TX
            if self._trace_callback:
                self._trace_callback(
                    "TX(FUNC)" if functional else "TX",
                    data
                )

            # Send via ISO-TP
            target_id = (
                self._functional_id if functional else None
            )
            self._can.send_isotp(data, target_id=target_id)

            # Wait for response
            timeout = self._p2_timeout
            max_pending = 50

            for _ in range(max_pending):

                response = self._can.receive_isotp(
                    timeout=timeout
                )

                if response is None:
                    raise UdsTimeoutError(
                        f"No response for SID "
                        f"0x{data[0]:02X} "
                        f"(timeout={timeout}s)"
                    )

                # Trace RX
                if self._trace_callback:
                    self._trace_callback(
                        "RX", response
                    )

                # Check for negative response
                if (len(response) >= 3
                        and response[0] == 0x7F):

                    nrc = response[2]

                    if nrc == NRC_RESPONSE_PENDING:
                        timeout = self._p2_star_timeout
                        continue
                    else:
                        raise UdsNegativeResponse(
                            response[1], nrc
                        )

                # Positive response
                expected_sid = (
                    data[0] + POSITIVE_RESPONSE_OFFSET
                )

                if response[0] == expected_sid:
                    return response

                # Unexpected response
                raise UdsError(
                    f"Unexpected response: "
                    f"expected 0x{expected_sid:02X}, "
                    f"got 0x{response[0]:02X}"
                )

            raise UdsTimeoutError(
                "Too many ResponsePending (NRC 0x78)"
            )

        finally:
            # Resume TesterPresent keepalive
            if self._tp_keepalive:
                self._tp_keepalive.resume()

    # ==========================================
    # TesterPresent Keepalive
    # ==========================================

    def start_keepalive(self, interval=2.0, functional=False):
        """
        Start TesterPresent keepalive thread.

        Args:
            interval: Seconds between TesterPresent.
            functional: If True, send keepalive to the
                       functional (broadcast) address.
        """

        from communication.tester_present import (
            TesterPresentThread,
        )

        if self._tp_keepalive is not None:
            self._tp_keepalive.stop()

        self._tp_keepalive = TesterPresentThread(
            uds_client=self,
            interval=interval,
            suppress_response=True,
            functional=functional,
            on_error=lambda e: (
                self._trace_callback(
                    "TP_ERR", e.encode()
                ) if self._trace_callback else None
            ),
        )

        self._tp_keepalive.start()

    def stop_keepalive(self):
        """Stop TesterPresent keepalive thread."""

        if self._tp_keepalive is not None:
            self._tp_keepalive.stop()
            self._tp_keepalive = None

    # ==========================================
    # Security DLL Loader
    # ==========================================

    def load_security_dll(
        self,
        dll_path,
        function_name="GenerateKeyExOpt",
    ):
        """
        Load an external DLL for security key
        calculation.

        Tries ``function_name`` first with the
        standard ODX/ASAM signature (byte-buffer
        seed of any length):
            GenerateKeyExOpt(
                iSeedArray, iSeedLen,
                iSecurityLevel, iVariant,
                oKeyArray,  iMaxKeyLen,
                oKeyLen) -> int

        Falls back to "GenerateKeyEx" with the
        same signature, then to a legacy
        UINT32 -> UINT32 wrapper.

        Args:
            dll_path: Path to the DLL file.
            function_name: Entry point to try first.
        """

        import ctypes

        try:
            dll = ctypes.CDLL(dll_path)
        except Exception as e:
            raise UdsError(
                f"Failed to load security DLL: {e}"
            )

        buf_func = None
        for name in dict.fromkeys(
            [function_name, "GenerateKeyExOpt",
             "GenerateKeyEx"]
        ):
            fn = getattr(dll, name, None)
            if fn is None:
                continue
            try:
                fn.argtypes = [
                    ctypes.POINTER(ctypes.c_uint8),
                    ctypes.c_uint32,
                    ctypes.c_uint32,
                    ctypes.c_char_p,
                    ctypes.POINTER(ctypes.c_uint8),
                    ctypes.c_uint32,
                    ctypes.POINTER(ctypes.c_uint32),
                ]
                fn.restype = ctypes.c_int32
                buf_func = fn
                break
            except Exception:
                continue

        if buf_func is not None:
            def _dll_key_func(seed_bytes, level=1):
                n = len(seed_bytes)
                seed_arr = (ctypes.c_uint8 * n)(
                    *seed_bytes
                )
                max_key = max(n, 128)
                key_arr = (ctypes.c_uint8 * max_key)()
                key_len = ctypes.c_uint32(0)
                rc = buf_func(
                    seed_arr, n, level,
                    b"", key_arr, max_key,
                    ctypes.byref(key_len),
                )
                if rc != 0:
                    raise UdsError(
                        f"Security DLL returned "
                        f"error {rc}"
                    )
                return bytes(
                    key_arr[: key_len.value]
                )

            self._security_dll_func = _dll_key_func
            self._security_dll_is_bytes = True
        else:
            fn = getattr(dll, function_name, None)
            if fn is None:
                raise UdsError(
                    f"Security DLL has no "
                    f"'{function_name}' export"
                )
            fn.argtypes = [ctypes.c_uint32]
            fn.restype = ctypes.c_uint32
            self._security_dll_func = fn
            self._security_dll_is_bytes = False

        if self._trace_callback:
            self._trace_callback(
                "INFO",
                f"Security DLL loaded: "
                f"{dll_path}".encode()
            )

    # ==========================================
    # DiagnosticSessionControl (0x10)
    # ==========================================

    def diagnostic_session_control(
        self,
        session_type,
        functional=False,
    ) -> bytes:
        """
        Change diagnostic session.

        Args:
            session_type: 0x01=Default, 0x02=Programming,
                         0x03=Extended.
            functional: If True, send functionally
                       (network-wide) instead of to the
                       physical ECU address.

        Returns:
            Response payload.
        """

        return self._send_request(
            bytes([SID_DIAGNOSTIC_SESSION, session_type]),
            functional=functional,
        )

    # ==========================================
    # ECU Reset (0x11)
    # ==========================================

    def ecu_reset(
        self,
        reset_type=0x01
    ) -> bytes:
        """
        Reset the ECU.

        Args:
            reset_type: 0x01=Hard, 0x02=KeyOffOnReset,
                       0x03=Soft.
        """

        return self._send_request(
            bytes([SID_ECU_RESET, reset_type])
        )

    # ==========================================
    # ReadDataByIdentifier (0x22)
    # ==========================================

    # Common DIDs
    DID_SW_VERSION = 0xF189         # Application SW Version
    DID_HW_VERSION = 0xF191         # ECU Hardware Version
    DID_BOOT_SW_VERSION = 0xF180    # Boot SW Version
    DID_SERIAL_NUMBER = 0xF18C      # ECU Serial Number
    DID_PART_NUMBER = 0xF187        # Part Number
    DID_SUPPLIER_ID = 0xF18A        # Supplier Identifier
    DID_ECU_MANUFACTURING_DATE = 0xF18B
    DID_FINGERPRINT = 0xF15A        # Programming Fingerprint
    DID_ACTIVE_DIAGNOSTIC_SESSION = 0xF186
    DID_SUPPLIER_SW_VERSION = 0xF195
    DID_ECU_SW_NUMBER = 0xF188

    def read_data_by_identifier(
        self,
        did,
    ) -> bytes:
        """
        Read data from ECU by Data Identifier.

        Args:
            did: Data Identifier (16-bit).

        Returns:
            Data bytes (without SID and DID).
        """

        did_bytes = struct.pack(">H", did)

        response = self._send_request(
            bytes([SID_READ_DATA]) + did_bytes
        )

        # Response: [0x62, DID_HI, DID_LO, data...]
        return response[3:]

    def read_multiple_dids(
        self,
        dids,
    ) -> dict:
        """
        Read multiple DIDs and return as dict.

        Args:
            dids: List of DID values.

        Returns:
            Dict of {did: data_bytes}.
        """

        result = {}

        for did in dids:
            try:
                data = self.read_data_by_identifier(did)
                result[did] = data
            except Exception:
                result[did] = None

        return result

    def read_ecu_identification(self) -> dict:
        """
        Read common ECU identification DIDs.

        Returns:
            Dict with ECU info strings.
        """

        info = {}

        # Map of DID → human name
        did_names = {
            self.DID_SW_VERSION: "SW Version",
            self.DID_HW_VERSION: "HW Version",
            self.DID_BOOT_SW_VERSION: "Boot SW Version",
            self.DID_SERIAL_NUMBER: "Serial Number",
            self.DID_PART_NUMBER: "Part Number",
        }

        for did, name in did_names.items():
            try:
                data = self.read_data_by_identifier(did)
                # Try to decode as ASCII, fallback to hex
                try:
                    info[name] = data.decode(
                        "ascii"
                    ).strip('\x00')
                except (UnicodeDecodeError, ValueError):
                    info[name] = data.hex().upper()
            except Exception:
                info[name] = "N/A"

        return info

    # ==========================================
    # Security Access (0x27)
    # ==========================================

    def security_access(
        self,
        level=1,
        key_function=None,
    ) -> bytes:
        """
        Perform Security Access (Seed & Key).

        Key calculation priority:
        1. key_function parameter (if provided)
        2. Loaded Security DLL (if loaded)
        3. ECU Simulator default algorithm

        Supports variable-length seeds: the full
        seed payload from the ECU response is used,
        not a fixed 4-byte slice.

        Args:
            level: Security level (odd number).
            key_function: Function(seed_int) -> key_int
                for 4-byte seeds, or
                Function(seed_bytes) -> key_bytes
                for arbitrary length.

        Returns:
            Response payload from SendKey.
        """

        # Step 1: Request Seed
        seed_response = self._send_request(
            bytes([SID_SECURITY_ACCESS, level])
        )

        # Extract full seed (everything after
        # positive-response SID + subFunction)
        seed_bytes = seed_response[2:]

        if all(b == 0 for b in seed_bytes):
            return seed_response

        # Step 2: Calculate Key (priority order)
        key_bytes = self._compute_security_key(
            seed_bytes, level, key_function
        )

        # Step 3: Send Key
        return self._send_request(
            bytes([SID_SECURITY_ACCESS, level + 1])
            + key_bytes
        )

    def _compute_security_key(
        self, seed_bytes, level, key_function
    ):
        """Resolve and call the right key algorithm."""

        seed_len = len(seed_bytes)

        # 1) Explicit key_function parameter
        if key_function is not None:
            return self._call_key_func(
                key_function, seed_bytes, level,
                "key_function"
            )

        # 2) Loaded Security DLL
        if self._security_dll_func is not None:
            if self._security_dll_is_bytes:
                return self._security_dll_func(
                    seed_bytes, level
                )
            return self._call_key_func(
                self._security_dll_func,
                seed_bytes, level,
                "Security DLL"
            )

        # 3) Built-in dummy algorithm — process in
        #    4-byte chunks so any seed length works
        from communication.ecu_simulator import (
            EcuSimulator,
        )
        key_buf = bytearray()
        for i in range(0, seed_len, 4):
            chunk = seed_bytes[i:i + 4]
            if len(chunk) < 4:
                chunk = chunk + b'\x00' * (4 - len(chunk))
            seed_int = struct.unpack(">I", chunk)[0]
            key_int = EcuSimulator.compute_key(seed_int)
            key_buf += struct.pack(">I", key_int)
        return bytes(key_buf[:seed_len])

    @staticmethod
    def _call_key_func(func, seed_bytes, level, name):
        """Call a uint32->uint32 key function,
        requiring a 4-byte seed."""
        if len(seed_bytes) != 4:
            raise UdsError(
                f"{name} expects a 4-byte seed but "
                f"ECU sent {len(seed_bytes)} bytes. "
                f"Use a Security DLL that supports "
                f"variable-length seeds."
            )
        seed_int = struct.unpack(">I", seed_bytes)[0]
        key_int = func(seed_int)
        return struct.pack(">I", key_int)

    # ==========================================
    # Communication Control (0x28)
    # ==========================================

    def communication_control(
        self,
        control_type,
        communication_type=0x01,
        functional=False,
    ) -> bytes:
        """
        Control communication.

        Args:
            control_type: 0x00=Enable, 0x03=Disable.
            communication_type: 0x01=Normal, 0x02=NM,
                               0x03=Both.
            functional: If True, send functionally
                       (network-wide).
        """

        return self._send_request(
            bytes([
                SID_COMMUNICATION_CONTROL,
                control_type,
                communication_type,
            ]),
            functional=functional,
        )

    # ==========================================
    # Control DTC Setting (0x85)
    # ==========================================

    def control_dtc_setting(
        self,
        setting_type=0x02,
        option_record=b'',
        functional=False,
    ) -> bytes:
        """
        Control DTC Setting.

        Args:
            setting_type: 0x01=ON, 0x02=OFF.
            option_record: Optional manufacturer-specific
                          DTCSettingControlOptionRecord
                          bytes appended after setting_type.
            functional: If True, send functionally
                       (network-wide).
        """

        return self._send_request(
            bytes([SID_CONTROL_DTC, setting_type])
            + bytes(option_record),
            functional=functional,
        )

    # ==========================================
    # Write Data By Identifier (0x2E)
    # ==========================================

    def write_data_by_identifier(
        self,
        did,
        data,
    ) -> bytes:
        """
        Write data to a DID.

        Args:
            did: Data Identifier (16-bit).
            data: Data bytes to write.
        """

        did_bytes = struct.pack(">H", did)

        return self._send_request(
            bytes([SID_WRITE_DATA])
            + did_bytes
            + bytes(data)
        )

    # ==========================================
    # Routine Control (0x31)
    # ==========================================

    def routine_control(
        self,
        sub_function,
        routine_id,
        option_record=b'',
    ) -> bytes:
        """
        Start/Stop/Request results of a routine.

        Args:
            sub_function: 0x01=Start, 0x02=Stop,
                         0x03=RequestResults.
            routine_id: Routine ID (16-bit).
            option_record: Optional data.
        """

        rid_bytes = struct.pack(">H", routine_id)

        return self._send_request(
            bytes([SID_ROUTINE_CONTROL, sub_function])
            + rid_bytes
            + bytes(option_record)
        )

    # ==========================================
    # Request Download (0x34)
    # ==========================================

    def request_download(
        self,
        memory_address,
        memory_size,
        compression=0x00,
        encrypting=0x00,
        addr_length=4,
        size_length=4,
    ) -> int:
        """
        Request download to ECU memory.

        Args:
            memory_address: Start address.
            memory_size: Number of bytes.
            compression: Compression method.
            encrypting: Encryption method.
            addr_length: Address byte length (1-4).
            size_length: Size byte length (1-4).

        Returns:
            maxNumberOfBlockLength from ECU.
        """

        # dataFormatIdentifier
        data_format = (
            (compression << 4) | encrypting
        )

        # addressAndLengthFormatIdentifier
        addr_len_format = (
            (size_length << 4) | addr_length
        )

        # Encode address and size
        addr_bytes = memory_address.to_bytes(
            addr_length, 'big'
        )
        size_bytes = memory_size.to_bytes(
            size_length, 'big'
        )

        # ISO 14229-1 order: SID, dataFormatIdentifier,
        # addressAndLengthFormatIdentifier, then address/size.
        response = self._send_request(
            bytes([
                SID_REQUEST_DOWNLOAD,
                data_format,
                addr_len_format,
            ])
            + addr_bytes
            + size_bytes
        )

        # Parse maxNumberOfBlockLength
        length_format = response[1]
        num_bytes = (length_format >> 4) & 0x0F

        max_block = 0
        for i in range(num_bytes):
            max_block = (
                (max_block << 8) | response[2 + i]
            )

        return max_block

    # ==========================================
    # Transfer Data (0x36)
    # ==========================================

    def transfer_data(
        self,
        block_sequence_counter,
        data,
    ) -> bytes:
        """
        Transfer a block of data to ECU.

        Args:
            block_sequence_counter: Block number (0-255).
            data: Data bytes for this block.
        """

        bsc = block_sequence_counter & 0xFF

        return self._send_request(
            bytes([SID_TRANSFER_DATA, bsc])
            + bytes(data)
        )

    # ==========================================
    # Request Transfer Exit (0x37)
    # ==========================================

    def request_transfer_exit(self) -> bytes:
        """Request end of data transfer."""

        return self._send_request(
            bytes([SID_TRANSFER_EXIT])
        )

    # ==========================================
    # Tester Present (0x3E)
    # ==========================================

    def tester_present(
        self,
        suppress_response=False,
        functional=False,
    ) -> Optional[bytes]:
        """Keep session alive."""

        sub = 0x80 if suppress_response else 0x00
        target_id = self._functional_id if functional else None

        if suppress_response:
            self._can.send_isotp(
                bytes([SID_TESTER_PRESENT, sub]),
                target_id=target_id,
            )
            if self._trace_callback:
                self._trace_callback(
                    "TX(FUNC)" if functional else "TX",
                    bytes([SID_TESTER_PRESENT, sub])
                )
            return None

        return self._send_request(
            bytes([SID_TESTER_PRESENT, sub]),
            functional=functional,
        )

    # ==========================================
    # High-Level: Download Firmware
    # ==========================================

    def download_firmware(
        self,
        memory_address,
        data,
        progress_callback=None,
        addr_length=4,
        size_length=4,
    ):
        """
        High-level firmware download.

        Performs:
        1. RequestDownload (0x34)
        2. TransferData (0x36) × N blocks
        3. RequestTransferExit (0x37)

        Args:
            memory_address: Flash start address.
            data: Firmware data bytes.
            progress_callback: Optional callback(
                bytes_sent, total_bytes).
            addr_length: memoryAddress byte length
                        for RequestDownload (ECU/OEM
                        specific, default 4).
            size_length: memorySize byte length
                        for RequestDownload (ECU/OEM
                        specific, default 4).
        """

        total_bytes = len(data)

        # Step 1: Request Download
        max_block = self.request_download(
            memory_address,
            total_bytes,
            addr_length=addr_length,
            size_length=size_length,
        )

        # Usable data per block
        # (max_block - 2 for SID + BSC)
        chunk_size = max_block - 2

        if chunk_size <= 0:
            chunk_size = 4094  # fallback

        # Step 2: Transfer Data
        offset = 0
        block_seq = 1

        while offset < total_bytes:

            end = min(offset + chunk_size, total_bytes)
            chunk = data[offset:end]

            self.transfer_data(block_seq, chunk)

            offset = end
            block_seq += 1

            if progress_callback:
                progress_callback(offset, total_bytes)

        # Step 3: Request Transfer Exit
        self.request_transfer_exit()

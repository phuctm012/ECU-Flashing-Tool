# ==================================================
# Flash Controller
# ==================================================
#
# QObject-based worker that runs the flash sequence
# in a separate QThread.
#
# Now uses UDS client for real communication
# (or virtual ECU simulator when no hardware).
# ==================================================

import time
import struct
from PySide6.QtCore import QObject, Signal

from core.flash_sequence import FlashStep
from communication.can_interface import CanMessage
from communication.ecu_simulator import EcuSimulator


class FlashWorker(QObject):

    # ==========================================
    # Signals
    # ==========================================

    step_started = Signal(str)          # step description

    progress_changed = Signal(int)      # 0-100

    information_message = Signal(str)   # user-facing log

    trace_message = Signal(str)         # narrative trace log (non-frame)

    trace_row = Signal(dict)            # paired UDS request/response row

    segment_progress = Signal(         # (seg_idx, sent, total)
        int, int, int
    )

    ecu_info_message = Signal(dict)     # ECU identification data

    flash_finished = Signal()

    flash_aborted = Signal()

    # ==========================================
    # Constructor
    # ==========================================

    def __init__(
        self,
        steps=None,
        datablocks=None,
        uds_client=None,
        use_virtual=True,
        security_dll_path=None,
        keepalive_functional=False,
        can_channel=0,
        can_tx_id=0x778,
        can_rx_id=0x788,
        can_bitrate=500000,
        can_fd=False,
        can_data_bitrate=2000000,
    ):
        super().__init__()

        self._abort_requested = False
        self.steps = steps or []
        self.datablocks = datablocks or []
        self._uds_client = uds_client
        self._use_virtual = use_virtual
        self._security_dll_path = security_dll_path
        self._keepalive_functional = keepalive_functional

        # CAN bus parameters (real hardware) — read from
        # the Configure -> Communication page. Ignored for
        # the Virtual ECU Simulator, which only uses tx/rx.
        self._can_channel = can_channel
        self._can_tx_id = can_tx_id
        self._can_rx_id = can_rx_id
        self._can_bitrate = can_bitrate
        self._can_fd = can_fd
        self._can_data_bitrate = can_data_bitrate

        # CAN interface reference (for cleanup)
        self._can_interface = None

        # Structured trace row builder (see _on_uds_trace)
        self._functional_id = 0x700
        self._flash_start_time = None
        self._pending_trace_row = None

    # ==========================================
    # Run
    # ==========================================

    def run(self):

        self._flash_start_time = time.time()

        self.information_message.emit(
            "Starting Flash..."
        )

        self.trace_message.emit(
            "Flash sequence started."
        )

        self.progress_changed.emit(0)

        # ------------------------------------------
        # Setup UDS client if not provided
        # ------------------------------------------

        if self._uds_client is None:
            try:
                self._setup_uds_client()
            except Exception as e:
                self.information_message.emit(
                    f"Connection failed: {e}"
                )
                self.flash_aborted.emit()
                return

        # Start TesterPresent keepalive
        try:
            self._uds_client.start_keepalive(
                interval=2.0,
                functional=self._keepalive_functional,
            )
            self.trace_message.emit(
                "TesterPresent keepalive started (2s)"
            )
        except Exception:
            pass  # Not critical

        total_steps = len(self.steps)

        if total_steps == 0:
            self.information_message.emit(
                "No flash steps configured."
            )
            self._cleanup()
            self.flash_finished.emit()
            return

        # ------------------------------------------
        # Execute flash sequence
        # ------------------------------------------

        for current_step, step in enumerate(self.steps):

            if self._abort_requested:

                self.trace_message.emit(
                    "Flash sequence aborted."
                )

                self.information_message.emit(
                    "Flash aborted by user."
                )

                self._cleanup()
                self.flash_aborted.emit()
                return

            # Tell GUI which step started
            self.step_started.emit(
                step.description
            )

            # Trace
            self.trace_message.emit(
                f"Executing: {step.description}"
            )

            # Execute step
            success = self._execute_step(step)

            if not success:

                self.information_message.emit(
                    f"Step failed: {step.description}"
                )

                self.trace_message.emit(
                    f"FAILED: {step.description}"
                )

                self._cleanup()
                self.flash_aborted.emit()
                return

            # Calculate progress
            progress = int(
                (
                    (current_step + 1)
                    / total_steps
                )
                * 100
            )

            self.progress_changed.emit(progress)

        self.progress_changed.emit(100)

        self.information_message.emit(
            "Flash completed successfully."
        )

        self.trace_message.emit(
            "Flash sequence finished."
        )

        self._cleanup()
        self.flash_finished.emit()

    # ==========================================
    # Setup UDS Client
    # ==========================================

    def _setup_uds_client(self):
        """
        Create and connect UDS client.
        Uses Virtual CAN if no hardware available.
        """

        from communication.uds_client import UdsClient

        if self._use_virtual:

            from communication.virtual_can import (
                VirtualCanInterface,
            )

            self._can_interface = VirtualCanInterface(
                response_delay_ms=10,
                error_rate=0.0,
            )

            self._can_interface.connect(
                tx_id=self._can_tx_id,
                rx_id=self._can_rx_id,
            )

            self.information_message.emit(
                "Connected to Virtual ECU Simulator"
            )

            self.trace_message.emit(
                "CAN: Virtual Bus (no hardware)"
            )

        else:

            from communication.vector_can import (
                VectorCanInterface,
            )

            self._can_interface = VectorCanInterface()
            self._can_interface.connect(
                channel=self._can_channel,
                bitrate=self._can_bitrate,
                tx_id=self._can_tx_id,
                rx_id=self._can_rx_id,
                fd=self._can_fd,
                data_bitrate=self._can_data_bitrate,
            )

            self.information_message.emit(
                f"Connected to Vector Hardware "
                f"(channel {self._can_channel}, "
                f"TX=0x{self._can_tx_id:X}, "
                f"RX=0x{self._can_rx_id:X}, "
                f"{self._can_bitrate} bps)"
            )

        # Create UDS client with trace callback
        self._uds_client = UdsClient(
            self._can_interface,
            trace_callback=self._on_uds_trace,
            functional_id=self._functional_id,
        )

        # Load external Security Access DLL, if configured.
        # Not applicable to the Virtual ECU Simulator, which
        # always uses the built-in seed/key algorithm.
        if self._security_dll_path and not self._use_virtual:

            self._uds_client.load_security_dll(
                self._security_dll_path
            )

            self.information_message.emit(
                f"Security DLL loaded: "
                f"{self._security_dll_path}"
            )

    # ==========================================
    # UDS Trace Callback
    # ==========================================
    #
    # Builds one table row per logical UDS transaction —
    # a TX frame paired with its (final) RX response —
    # matching the column layout of a real CAN trace tool
    # export (docs/*_Report_Trace.csv): Request TimeStamp,
    # Request Target, Request Data, Response TimeStamp,
    # Response Source, Response Data.
    #
    # NRC 0x78 (ResponsePending) keeps the row open instead
    # of flushing it, so a long-running routine (e.g. Erase)
    # still ends up as a single row with its final response —
    # same as the reference trace.
    # ==========================================

    def _elapsed(self):

        if self._flash_start_time is None:
            return 0.0

        return time.time() - self._flash_start_time

    def _flush_pending_trace_row(self):

        if self._pending_trace_row is not None:
            self.trace_row.emit(self._pending_trace_row)
            self._pending_trace_row = None

    def _on_uds_trace(self, direction, data):

        elapsed = self._elapsed()

        if direction in ("TX", "TX(FUNC)"):

            hex_str = " ".join(f"{b:02X}" for b in data)

            # Flush any still-open row (e.g. a suppressed
            # TesterPresent that never got a response).
            self._flush_pending_trace_row()

            target = (
                f"FuncGroup-0x{self._functional_id:03X}"
                if direction == "TX(FUNC)"
                else f"0x{self._can_tx_id:03X}"
            )

            self._pending_trace_row = {
                "req_ts": elapsed,
                "req_target": target,
                "req_data": hex_str,
                "resp_ts": None,
                "resp_source": None,
                "resp_data": None,
            }

        elif direction == "RX":

            hex_str = " ".join(f"{b:02X}" for b in data)
            source = f"0x{self._can_rx_id:03X}"

            if self._pending_trace_row is None:
                # Unexpected standalone RX — emit alone.
                self.trace_row.emit({
                    "req_ts": None,
                    "req_target": None,
                    "req_data": None,
                    "resp_ts": elapsed,
                    "resp_source": source,
                    "resp_data": hex_str,
                })
                return

            self._pending_trace_row["resp_ts"] = elapsed
            self._pending_trace_row["resp_source"] = source
            self._pending_trace_row["resp_data"] = hex_str

            # NRC 0x78 (ResponsePending): keep the row open,
            # the ECU will send a further response later.
            is_pending = (
                len(data) >= 3
                and data[0] == 0x7F
                and data[2] == 0x78
            )

            if not is_pending:
                self._flush_pending_trace_row()

        else:
            # RETRY / INFO / TP_ERR / any other narrative tag
            self._flush_pending_trace_row()

            try:
                text = bytes(data).decode()
            except (UnicodeDecodeError, AttributeError):
                text = " ".join(f"{b:02X}" for b in data)

            self.trace_row.emit({
                "req_ts": elapsed,
                "req_target": direction,
                "req_data": text,
                "resp_ts": None,
                "resp_source": None,
                "resp_data": None,
            })

    # ==========================================
    # Execute Step (Real UDS)
    # ==========================================

    def _execute_step(self, step):
        """
        Execute a flash step using UDS client.

        Returns:
            True if step succeeded, False if failed.
        """

        try:

            if step.step_type == FlashStep.TYPE_SESSION:
                self._execute_session(step)

            elif step.step_type == FlashStep.TYPE_SECURITY:
                self._execute_security(step)

            elif step.step_type == FlashStep.TYPE_COMMUNICATION:
                self._execute_comm_control(step)

            elif step.step_type == FlashStep.TYPE_DTC:
                self._execute_dtc_control(step)

            elif step.step_type == FlashStep.TYPE_ROUTINE:
                self._execute_routine(step)

            elif step.step_type == FlashStep.TYPE_DOWNLOAD:
                self._execute_download(step)

            elif step.step_type == FlashStep.TYPE_RESET:
                self._execute_reset(step)

            elif step.step_type == FlashStep.TYPE_READ_DID:
                self._execute_read_did(step)

            elif step.step_type == FlashStep.TYPE_WRITE_DID:
                self._execute_write_did(step)

            elif step.step_type == FlashStep.TYPE_CUSTOM:
                self._execute_custom(step)

            else:
                self.trace_message.emit(
                    f"Unknown step type: {step.step_type}"
                )
                time.sleep(0.3)

            return True

        except Exception as e:

            self.trace_message.emit(
                f"Error: {e}"
            )

            self.information_message.emit(
                f"Error: {e}"
            )

            return False

    # ==========================================
    # Step Handlers
    # ==========================================

    def _execute_session(self, step):
        """DiagnosticSessionControl (0x10)."""

        session_map = {
            "default": 0x01,
            "programming": 0x02,
            "extended": 0x03,
        }

        session = session_map.get(
            step.params.get("session", "default"),
            0x01
        )

        functional = step.params.get("functional", False)

        self._uds_client.diagnostic_session_control(
            session, functional=functional
        )

        session_names = {
            0x01: "Default",
            0x02: "Programming",
            0x03: "Extended",
        }

        self.information_message.emit(
            f"Session: {session_names.get(session, '?')}"
        )

    def _execute_security(self, step):
        """SecurityAccess (0x27)."""

        level = step.params.get("level", 1)

        # Use simulator's key function for virtual mode
        key_func = None
        if self._use_virtual:
            key_func = EcuSimulator.compute_key

        # If no key_function and no Security DLL is loaded,
        # UdsClient.security_access() falls back to the same
        # dummy seed/key algorithm as the ECU Simulator. That
        # matches ECUs (e.g. Suzuki Radar) currently running
        # dummy security access on their end.
        dll_loaded = (
            getattr(self._uds_client, '_security_dll_func', None)
            is not None
        )
        if not self._use_virtual and not dll_loaded:
            self.trace_message.emit(
                "Security Access: no DLL loaded — "
                "using dummy seed/key algorithm"
            )

        self._uds_client.security_access(
            level=level,
            key_function=key_func,
        )

        self.information_message.emit(
            "ECU unlocked (Security Access OK)"
        )

    def _execute_comm_control(self, step):
        """CommunicationControl (0x28)."""

        action = step.params.get("action", "disable")
        control_type = 0x03 if action == "disable" else 0x00
        comm_type = step.params.get("comm_type", 0x03)
        functional = step.params.get("functional", False)

        self._uds_client.communication_control(
            control_type=control_type,
            communication_type=comm_type,
            functional=functional,
        )

        self.information_message.emit(
            f"Communication: "
            f"{'Disabled' if action == 'disable' else 'Enabled'}"
        )

    def _execute_dtc_control(self, step):
        """ControlDTCSetting (0x85)."""

        action = step.params.get("action", "disable")
        setting = 0x02 if action == "disable" else 0x01
        option_record = step.params.get("option_record", b'')
        functional = step.params.get("functional", False)

        self._uds_client.control_dtc_setting(
            setting_type=setting,
            option_record=option_record,
            functional=functional,
        )

        self.information_message.emit(
            f"DTC Setting: "
            f"{'OFF' if action == 'disable' else 'ON'}"
        )

    def _execute_routine(self, step):
        """RoutineControl (0x31)."""

        routine_id = step.params.get(
            "routine_id", 0xFF00
        )
        option_record = step.params.get(
            "option_record", b''
        )
        action = step.params.get("action", "")

        try:
            self._uds_client.routine_control(
                sub_function=0x01,  # startRoutine
                routine_id=routine_id,
                option_record=option_record,
            )
        except Exception:
            # A failed Verify Memory already aborts the flash
            # sequence via _execute_step()'s generic "Error: {e}"
            # message (the exception re-raised here) — this adds
            # an unambiguous PASS/FAIL line specifically for
            # Verify Memory, matching what vFlash always shows
            # after its own verify step (docs/gui_todo.md #9).
            if action == "verify":
                self.information_message.emit(
                    "✗ Verify Memory: FAILED"
                )
            raise

        if action == "erase":
            self.information_message.emit(
                "Memory erased"
            )
        elif action == "verify":
            self.information_message.emit(
                "✓ Verify Memory: PASS"
            )
        else:
            self.information_message.emit(
                f"Routine 0x{routine_id:04X} completed"
            )

    def _execute_download(self, step):
        """
        RequestDownload (0x34) +
        TransferData (0x36) +
        RequestTransferExit (0x37)
        """

        address = step.params.get(
            "start_address", 0x0000
        )

        data = step.params.get("data", b'')

        if not data:
            # No real data — simulate with dummy
            data_length = step.params.get(
                "data_length", 1024
            )
            data = bytes([0xFF] * data_length)

        seg_idx = step.params.get("segment_index", 0)
        total_bytes = len(data)

        addr_length = step.params.get("addr_length", 4)
        size_length = step.params.get("size_length", 4)

        self.information_message.emit(
            f"Downloading to 0x{address:08X} "
            f"({total_bytes} bytes)..."
        )

        def on_progress(bytes_sent, total):
            self.segment_progress.emit(
                seg_idx, bytes_sent, total
            )

        self._uds_client.download_firmware(
            memory_address=address,
            data=data,
            progress_callback=on_progress,
            addr_length=addr_length,
            size_length=size_length,
        )

        self.information_message.emit(
            f"Download complete: {total_bytes} bytes "
            f"at 0x{address:08X}"
        )

    def _execute_reset(self, step):
        """ECUReset (0x11)."""

        reset_map = {
            "hard": 0x01,
            "key_off_on": 0x02,
            "soft": 0x03,
        }

        reset_type = reset_map.get(
            step.params.get("reset_type", "hard"),
            0x01
        )

        self._uds_client.ecu_reset(
            reset_type=reset_type
        )

        self.information_message.emit(
            "ECU reset completed"
        )

    def _execute_read_did(self, step):
        """ReadDataByIdentifier (0x22)."""

        dids = step.params.get("dids", [])
        phase = step.params.get("phase", "")

        # DID name mapping
        did_names = {
            0xF189: "SW Version",
            0xF191: "HW Version",
            0xF180: "Boot SW Version",
            0xF18C: "Serial Number",
            0xF187: "Part Number",
            0xF18A: "Supplier ID",
            0xF15A: "Fingerprint",
            0xF186: "Active Session",
        }

        ecu_info = {}

        for did in dids:

            name = did_names.get(
                did, f"DID 0x{did:04X}"
            )

            try:

                data = (
                    self._uds_client
                    .read_data_by_identifier(did)
                )

                # Try ASCII decode, fallback to hex
                try:
                    value = data.decode(
                        "ascii"
                    ).strip('\x00')
                except (UnicodeDecodeError, ValueError):
                    value = data.hex().upper()

                ecu_info[name] = value

                self.information_message.emit(
                    f"{name}: {value}"
                )

            except Exception as e:
                ecu_info[name] = "N/A"

                self.trace_message.emit(
                    f"ReadDID {name} failed: {e}"
                )

        # Emit all collected info
        if ecu_info:
            self.ecu_info_message.emit(ecu_info)

        self.information_message.emit(
            f"ECU identification read ({phase})"
        )

    def _execute_write_did(self, step):
        """WriteDataByIdentifier (0x2E) — generic DID/data."""

        did = step.params.get("did")
        data = step.params.get("data", b'')

        self._uds_client.write_data_by_identifier(
            did=did,
            data=data,
        )

        self.information_message.emit(
            f"Wrote DID 0x{did:04X} "
            f"({len(data)} bytes)"
        )

    def _execute_custom(self, step):
        """Custom step (e.g., Write Fingerprint)."""

        did = step.params.get("did", None)

        if did == 0xF15A:
            # Write Fingerprint
            # Date + Tester Serial Number
            fingerprint = bytes([
                0x20, 0x26, 0x08, 0x20,  # Date
                0x01, 0x02, 0x03,        # Tester ID
            ])

            self._uds_client.write_data_by_identifier(
                did=0xF15A,
                data=fingerprint,
            )

            self.information_message.emit(
                "Fingerprint written"
            )

        else:
            self.trace_message.emit(
                f"Custom step: {step.description}"
            )
            time.sleep(0.3)

    # ==========================================
    # Cleanup
    # ==========================================

    def _cleanup(self):
        """Stop keepalive and disconnect CAN."""

        # Flush any still-open trace row
        self._flush_pending_trace_row()

        # Stop TesterPresent keepalive
        if self._uds_client is not None:
            try:
                self._uds_client.stop_keepalive()
            except Exception:
                pass

        if self._can_interface is not None:
            try:
                self._can_interface.disconnect()
            except Exception:
                pass

    # ==========================================
    # Abort
    # ==========================================

    def request_abort(self):

        self._abort_requested = True

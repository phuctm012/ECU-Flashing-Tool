# ==================================================
# Test Connection Worker
# ==================================================
#
# QObject-based worker (run on a QThread, same pattern as
# FlashWorker) driving a safe, read-only connectivity probe
# as cli.py's cmd_test_connection() — connects, Extended
# session (+ functional pre-steps for the Suzuki sequence),
# then reads ECU Identification. Never touches Programming
# session, Security Access, Erase Memory, TransferData, or
# any write. Always tries to restore Default session before
# finishing, regardless of where it stopped — the try/finally
# below is exactly why this doesn't go through the linear,
# abort-on-first-failure FlashStep sequence (see CLAUDE.md).
#
# Only reuses FlashWorker for its CAN/UDS connection setup
# (_setup_uds_client()/_cleanup()) — everything else is driven
# directly against the resulting UdsClient, same as cli.py.
# ==================================================

from PySide6.QtCore import QObject, Signal

from core.flash_controller import FlashWorker


class TestConnectionWorker(QObject):

    # ==========================================
    # Signals
    # ==========================================

    step_message = Signal(str)         # "Extended Session (Network)"
    trace_message = Signal(str)        # narrative trace log
    trace_row = Signal(dict)           # paired UDS request/response row
    ecu_info_message = Signal(dict)    # ECU identification data
    finished = Signal(bool, str)       # (passed, summary message)

    # ==========================================
    # Constructor
    # ==========================================

    def __init__(
        self,
        use_virtual=True,
        security_dll_path=None,
        functional=False,
        can_channel=0,
        can_serial=None,
        can_tx_id=0x778,
        can_rx_id=0x788,
        can_bitrate=500000,
        can_fd=False,
        can_data_bitrate=2000000,
    ):
        super().__init__()

        self._use_virtual = use_virtual
        self._security_dll_path = security_dll_path
        self._functional = functional
        self._can_channel = can_channel
        self._can_serial = can_serial
        self._can_tx_id = can_tx_id
        self._can_rx_id = can_rx_id
        self._can_bitrate = can_bitrate
        self._can_fd = can_fd
        self._can_data_bitrate = can_data_bitrate

    # ==========================================
    # Run (called on the worker thread)
    # ==========================================

    def run(self):

        worker = FlashWorker(
            steps=[],
            datablocks=[],
            use_virtual=self._use_virtual,
            security_dll_path=self._security_dll_path,
            can_channel=self._can_channel,
            can_serial=self._can_serial,
            can_tx_id=self._can_tx_id,
            can_rx_id=self._can_rx_id,
            can_bitrate=self._can_bitrate,
            can_fd=self._can_fd,
            can_data_bitrate=self._can_data_bitrate,
        )
        worker.trace_message.connect(self.trace_message)
        worker.trace_row.connect(self.trace_row)

        try:
            worker._setup_uds_client()
        except Exception as e:
            self.finished.emit(False, f"Connection failed: {e}")
            return

        uds = worker._uds_client
        ok = True
        message = "Connection test PASSED — ECU reachable."

        try:
            if self._functional:
                uds.diagnostic_session_control(0x03, functional=True)
                self.step_message.emit("Extended Session (Network)")
                uds.control_dtc_setting(
                    setting_type=0x02, option_record=bytes([0x00]),
                    functional=True,
                )
                self.step_message.emit(
                    "Disable DTC Settings (Network)"
                )
                uds.communication_control(
                    control_type=0x03, communication_type=0x01,
                    functional=True,
                )
                self.step_message.emit(
                    "Disable Normal Communication (Network)"
                )
            else:
                uds.diagnostic_session_control(0x03)
                self.step_message.emit("Extended Session")

            info = uds.read_ecu_identification()
            self.ecu_info_message.emit(info)
            self.step_message.emit("Read ECU Identification")

        except Exception as e:
            ok = False
            message = f"Connection test FAILED: {e}"

        finally:
            # Best-effort cleanup: restore Default session (and
            # re-enable DTC/Communication if we disabled them),
            # regardless of where the probe above stopped.
            try:
                if self._functional:
                    uds.communication_control(
                        control_type=0x00, communication_type=0x01,
                        functional=True,
                    )
                    uds.control_dtc_setting(
                        setting_type=0x01, functional=True
                    )
                uds.diagnostic_session_control(
                    0x01, functional=self._functional
                )
                self.step_message.emit("Restored Default session")
            except Exception:
                pass

            worker._cleanup()

        self.finished.emit(ok, message)

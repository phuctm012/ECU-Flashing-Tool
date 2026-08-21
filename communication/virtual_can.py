# ==================================================
# Virtual CAN Interface
# ==================================================
#
# A CAN adapter that runs entirely in-memory,
# with an integrated ECU Simulator responding
# to requests. No hardware needed.
#
# Perfect for:
# - Development & testing without hardware
# - CI/CD pipelines
# - Demo/training purposes
# ==================================================

import time
import threading
from queue import Queue, Empty
from typing import Optional

from communication.can_interface import (
    CanInterface,
    CanMessage,
    CanError,
    CanConnectionError,
)

from communication.ecu_simulator import EcuSimulator


class VirtualCanInterface(CanInterface):
    """
    Virtual CAN bus with built-in ECU simulator.
    No physical hardware required.
    """

    def __init__(
        self,
        response_delay_ms=5,
        error_rate=0.0,
    ):
        super().__init__()

        self._response_delay_ms = response_delay_ms
        self._error_rate = error_rate

        # CAN IDs
        self._tx_id = 0x778    # Request ID
        self._rx_id = 0x788    # Response ID
        self._func_id = 0x700  # Functional ID

        # Message queues
        self._rx_queue = Queue()
        self._tx_log = []
        self._rx_log = []

        # ECU Simulator
        self._ecu = None

        # ISO-TP state (simplified)
        self._isotp_buffer = bytearray()
        self._isotp_expected_length = 0

    # ==========================================
    # Connect
    # ==========================================

    def connect(
        self,
        channel=0,
        bitrate=500000,
        **kwargs
    ):
        """Connect to virtual CAN bus."""

        tx_id = kwargs.get("tx_id", self._tx_id)
        rx_id = kwargs.get("rx_id", self._rx_id)

        self._tx_id = tx_id
        self._rx_id = rx_id

        self._ecu = EcuSimulator(
            response_delay_ms=self._response_delay_ms,
            error_rate=self._error_rate,
        )

        self._connected = True

        return True

    # ==========================================
    # Disconnect
    # ==========================================

    def disconnect(self):
        """Disconnect from virtual CAN bus."""

        self._connected = False
        self._ecu = None

        # Clear queues
        while not self._rx_queue.empty():
            try:
                self._rx_queue.get_nowait()
            except Empty:
                break

    # ==========================================
    # Send (with ISO-TP framing)
    # ==========================================

    def send(self, msg: CanMessage):
        """
        Send a CAN message to the virtual bus.
        The ECU simulator processes it and queues
        a response.
        """

        if not self._connected:
            raise CanError("Not connected")

        # Log TX
        msg._is_tx = True
        self._tx_log.append(msg)

        # Notify callback
        if self._on_message_callback:
            self._on_message_callback(msg)

    def send_isotp(self, data: bytes, target_id=None):
        """
        Send data using ISO-TP framing.
        Handles Single Frame and multi-frame
        (First Frame + Consecutive Frames).

        Args:
            data: Raw UDS payload.
            target_id: Optional arbitration ID override
                      for this request (e.g. functional/
                      broadcast addressing). Defaults to
                      the physical request ID (tx_id).
        """

        if not self._connected:
            raise CanError("Not connected")

        tx_id = self._tx_id if target_id is None else target_id

        if len(data) <= 7:
            # =============================
            # Single Frame (SF)
            # PCI: [0L] where L = length
            # =============================

            frame_data = bytes([len(data)]) + data
            frame_data = frame_data.ljust(8, b'\xAA')

            msg = CanMessage(
                arbitration_id=tx_id,
                data=frame_data,
            )

            self.send(msg)

            # Process in ECU and get response
            self._process_ecu_request(data)

        else:
            # =============================
            # First Frame (FF) +
            # Consecutive Frames (CF)
            # =============================

            total_len = len(data)

            # First Frame: PCI = [1H HL] (12-bit length)
            pci_byte0 = 0x10 | (
                (total_len >> 8) & 0x0F
            )
            pci_byte1 = total_len & 0xFF

            ff_data = bytes([pci_byte0, pci_byte1])
            ff_data += data[:6]

            msg = CanMessage(
                arbitration_id=tx_id,
                data=ff_data,
            )
            self.send(msg)

            # Simulate FC (Flow Control) from ECU
            fc_msg = CanMessage(
                arbitration_id=self._rx_id,
                data=bytes([
                    0x30,  # FC: CTS (Continue To Send)
                    0x00,  # BS = 0 (no block limit)
                    0x0A,  # STmin = 10ms
                    0xAA, 0xAA, 0xAA, 0xAA, 0xAA,
                ]),
            )
            self._rx_log.append(fc_msg)

            if self._on_message_callback:
                self._on_message_callback(fc_msg)

            # Consecutive Frames
            offset = 6
            seq_num = 1

            while offset < total_len:

                pci = 0x20 | (seq_num & 0x0F)
                chunk = data[offset:offset + 7]
                cf_data = bytes([pci]) + chunk
                cf_data = cf_data.ljust(8, b'\xAA')

                cf_msg = CanMessage(
                    arbitration_id=tx_id,
                    data=cf_data,
                )
                self.send(cf_msg)

                offset += 7
                seq_num += 1

                # Small delay between CFs
                time.sleep(0.001)

            # All frames sent → process in ECU
            self._process_ecu_request(data)

    # ==========================================
    # Process ECU Request
    # ==========================================

    def _process_ecu_request(self, request_data):
        """
        Send request data to ECU simulator
        and queue the response.
        """

        if self._ecu is None:
            return

        response = self._ecu.process_request(
            request_data
        )

        if response is None:
            return  # suppressPositiveResponse

        # Frame the response as ISO-TP
        if len(response) <= 7:
            # Single Frame
            frame_data = bytes([len(response)])
            frame_data += response
            frame_data = frame_data.ljust(8, b'\xAA')

            rx_msg = CanMessage(
                arbitration_id=self._rx_id,
                data=frame_data,
            )

            self._rx_log.append(rx_msg)
            self._rx_queue.put(rx_msg)

            if self._on_message_callback:
                self._on_message_callback(rx_msg)

        else:
            # Multi-frame response
            # For simplicity, concatenate into
            # single queue entry with full data
            self._send_multiframe_response(response)

    def _send_multiframe_response(self, response):
        """Send multi-frame ISO-TP response."""

        total_len = len(response)

        # First Frame
        pci0 = 0x10 | ((total_len >> 8) & 0x0F)
        pci1 = total_len & 0xFF

        ff = bytes([pci0, pci1]) + response[:6]
        ff = ff.ljust(8, b'\xAA')

        ff_msg = CanMessage(
            arbitration_id=self._rx_id,
            data=ff,
        )
        self._rx_log.append(ff_msg)

        if self._on_message_callback:
            self._on_message_callback(ff_msg)

        # We need FC from tester — simulate auto-FC
        # Consecutive Frames
        offset = 6
        seq = 1

        while offset < total_len:
            pci = 0x20 | (seq & 0x0F)
            chunk = response[offset:offset + 7]
            cf = bytes([pci]) + chunk
            cf = cf.ljust(8, b'\xAA')

            cf_msg = CanMessage(
                arbitration_id=self._rx_id,
                data=cf,
            )
            self._rx_log.append(cf_msg)

            offset += 7
            seq += 1

        # Put full reassembled UDS payload in queue
        # Use _reassembled flag so receive_isotp can
        # extract the raw UDS payload directly.
        full_msg = CanMessage(
            arbitration_id=self._rx_id,
            data=response,  # raw UDS payload
        )
        full_msg._reassembled = True
        self._rx_queue.put(full_msg)

        if self._on_message_callback:
            self._on_message_callback(full_msg)

    # ==========================================
    # Receive
    # ==========================================

    def receive(
        self,
        timeout: float = 1.0
    ) -> Optional[CanMessage]:
        """Receive a response from virtual bus."""

        try:
            return self._rx_queue.get(
                timeout=timeout
            )
        except Empty:
            return None

    # ==========================================
    # Receive ISO-TP (extract UDS payload)
    # ==========================================

    def receive_isotp(
        self,
        timeout: float = 2.0
    ) -> Optional[bytes]:
        """
        Receive and reassemble ISO-TP message.

        Returns:
            UDS response payload bytes, or None.
        """

        msg = self.receive(timeout=timeout)

        if msg is None:
            return None

        # Check if already reassembled (multi-frame)
        if getattr(msg, '_reassembled', False):
            return bytes(msg.data)

        raw = msg.data

        if len(raw) < 2:
            return None

        pci_type = (raw[0] >> 4) & 0x0F

        if pci_type == 0:
            # Single Frame
            length = raw[0] & 0x0F
            return bytes(raw[1:1 + length])

        return bytes(raw[1:])

    # ==========================================
    # Filter (no-op for virtual)
    # ==========================================

    def set_filter(
        self,
        can_id: int,
        mask: int = 0x7FF
    ):
        pass

    # ==========================================
    # Get ECU Simulator
    # ==========================================

    @property
    def ecu(self):
        return self._ecu

    # ==========================================
    # Logs
    # ==========================================

    def get_tx_log(self):
        return list(self._tx_log)

    def get_rx_log(self):
        return list(self._rx_log)

    def clear_logs(self):
        self._tx_log.clear()
        self._rx_log.clear()

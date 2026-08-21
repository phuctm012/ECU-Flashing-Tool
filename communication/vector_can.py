# ==================================================
# Vector CAN Interface
# ==================================================
#
# CAN adapter for Vector hardware (VN1640A, VN1630)
# using python-can library with 'vector' interface.
#
# Requires:
#   - pip install python-can
#   - Vector XL Driver Library installed
#   - Vector hardware connected
# ==================================================

from typing import Optional

from communication.can_interface import (
    CanInterface,
    CanMessage,
    CanError,
    CanConnectionError,
    CanTimeoutError,
)


class VectorCanInterface(CanInterface):
    """
    CAN interface for Vector hardware.
    Uses python-can with interface='vector'.
    """

    def __init__(self):
        super().__init__()
        self._bus = None
        self._tx_id = 0x778
        self._rx_id = 0x788

    # ==========================================
    # Connect
    # ==========================================

    def connect(
        self,
        channel=0,
        bitrate=500000,
        **kwargs
    ):
        """
        Connect to Vector CAN hardware.

        Args:
            channel: CAN channel number (0-based).
            bitrate: CAN baudrate.
            **kwargs: Additional params:
                - app_name: Vector application name
                - fd: True for CAN FD
                - data_bitrate: CAN FD data baudrate
                - tx_id: Transmit CAN ID
                - rx_id: Receive CAN ID
        """

        try:
            import can
        except ImportError:
            raise CanConnectionError(
                "python-can not installed. "
                "Run: pip install python-can"
            )

        app_name = kwargs.get(
            "app_name", "FlashTool"
        )
        fd = kwargs.get("fd", False)
        data_bitrate = kwargs.get(
            "data_bitrate", 2000000
        )

        self._tx_id = kwargs.get("tx_id", 0x778)
        self._rx_id = kwargs.get("rx_id", 0x788)

        try:

            bus_kwargs = {
                "interface": "vector",
                "channel": channel,
                "bitrate": bitrate,
                "app_name": app_name,
            }

            if fd:
                bus_kwargs["fd"] = True
                bus_kwargs["data_bitrate"] = data_bitrate

            self._bus = can.Bus(**bus_kwargs)
            self._connected = True

            return True

        except Exception as e:
            raise CanConnectionError(
                f"Failed to connect to Vector hardware: "
                f"{e}"
            )

    # ==========================================
    # Disconnect
    # ==========================================

    def disconnect(self):

        if self._bus is not None:
            try:
                self._bus.shutdown()
            except Exception:
                pass
            self._bus = None

        self._connected = False

    # ==========================================
    # Send
    # ==========================================

    def send(self, msg: CanMessage):

        if not self._connected or self._bus is None:
            raise CanError("Not connected")

        try:
            import can

            can_msg = can.Message(
                arbitration_id=msg.arbitration_id,
                data=msg.data,
                is_extended_id=msg.is_extended_id,
                is_fd=msg.is_fd,
                bitrate_switch=msg.bitrate_switch,
            )

            self._bus.send(can_msg)

            if self._on_message_callback:
                msg._is_tx = True
                self._on_message_callback(msg)

        except Exception as e:
            raise CanError(f"Send failed: {e}")

    # ==========================================
    # Send ISO-TP
    # ==========================================

    def send_isotp(self, data: bytes, target_id=None):
        """
        Send UDS data with ISO-TP framing.

        For real hardware, this should use
        can-isotp library or manual framing.

        Args:
            data: Raw UDS payload.
            target_id: Optional arbitration ID override
                      for this request (e.g. functional/
                      broadcast addressing). Defaults to
                      the physical request ID (tx_id).
        """

        tx_id = self._tx_id if target_id is None else target_id

        if len(data) <= 7:
            # Single Frame
            frame_data = bytes([len(data)]) + data
            frame_data = frame_data.ljust(8, b'\xAA')

            self.send(CanMessage(
                arbitration_id=tx_id,
                data=frame_data,
            ))

        else:
            # First Frame
            total_len = len(data)
            pci0 = 0x10 | ((total_len >> 8) & 0x0F)
            pci1 = total_len & 0xFF

            ff = bytes([pci0, pci1]) + data[:6]
            self.send(CanMessage(
                arbitration_id=tx_id,
                data=ff,
            ))

            # Wait for Flow Control
            fc = self.receive(timeout=1.0)
            if fc is None:
                raise CanTimeoutError(
                    "No Flow Control received"
                )

            # Parse FC
            fc_flag = fc.data[0] & 0x0F
            block_size = fc.data[1]
            st_min = fc.data[2]

            if fc_flag != 0:  # 0 = ContinueToSend
                raise CanError(
                    f"Flow Control rejected: {fc_flag}"
                )

            # Consecutive Frames
            import time

            offset = 6
            seq = 1

            while offset < total_len:

                pci = 0x20 | (seq & 0x0F)
                chunk = data[offset:offset + 7]
                cf = bytes([pci]) + chunk
                cf = cf.ljust(8, b'\xAA')

                self.send(CanMessage(
                    arbitration_id=tx_id,
                    data=cf,
                ))

                offset += 7
                seq += 1

                # STmin delay
                if st_min > 0:
                    time.sleep(st_min / 1000.0)

    # ==========================================
    # Receive ISO-TP
    # ==========================================

    def receive_isotp(
        self,
        timeout: float = 2.0
    ) -> Optional[bytes]:
        """
        Receive and reassemble ISO-TP response.
        """

        msg = self.receive(timeout=timeout)
        if msg is None:
            return None

        raw = msg.data
        pci_type = (raw[0] >> 4) & 0x0F

        if pci_type == 0:
            # Single Frame
            length = raw[0] & 0x0F
            return bytes(raw[1:1 + length])

        elif pci_type == 1:
            # First Frame → need to receive CFs
            total_len = (
                ((raw[0] & 0x0F) << 8) | raw[1]
            )
            buffer = bytearray(raw[2:8])

            # Send Flow Control
            fc = bytes([
                0x30, 0x00, 0x0A,
                0xAA, 0xAA, 0xAA, 0xAA, 0xAA,
            ])
            self.send(CanMessage(
                arbitration_id=self._tx_id,
                data=fc,
            ))

            # Receive Consecutive Frames
            import time
            while len(buffer) < total_len:
                cf = self.receive(timeout=timeout)
                if cf is None:
                    raise CanTimeoutError(
                        "Timeout waiting for CF"
                    )
                cf_pci = (cf.data[0] >> 4) & 0x0F
                if cf_pci != 2:
                    raise CanError(
                        f"Expected CF, got PCI={cf_pci}"
                    )
                buffer.extend(cf.data[1:8])

            return bytes(buffer[:total_len])

        return bytes(raw[1:])

    # ==========================================
    # Receive
    # ==========================================

    def receive(
        self,
        timeout: float = 1.0
    ) -> Optional[CanMessage]:

        if not self._connected or self._bus is None:
            return None

        try:
            can_msg = self._bus.recv(timeout=timeout)

            if can_msg is None:
                return None

            msg = CanMessage(
                arbitration_id=can_msg.arbitration_id,
                data=bytes(can_msg.data),
                timestamp=can_msg.timestamp,
                is_extended_id=can_msg.is_extended_id,
                is_fd=can_msg.is_fd,
            )

            if self._on_message_callback:
                self._on_message_callback(msg)

            return msg

        except Exception:
            return None

    # ==========================================
    # Filter
    # ==========================================

    def set_filter(
        self,
        can_id: int,
        mask: int = 0x7FF
    ):

        if self._bus is not None:
            try:
                self._bus.set_filters([{
                    "can_id": can_id,
                    "can_mask": mask,
                }])
            except Exception:
                pass

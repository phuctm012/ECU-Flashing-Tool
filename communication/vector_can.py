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

import sys
import subprocess
from typing import Optional

from communication.can_interface import (
    CanInterface,
    CanMessage,
    CanError,
    CanConnectionError,
    CanTimeoutError,
)

# Vector desktop tools whose process names we check for when
# warning about a possible CAN bus conflict — see
# detect_running_vector_tools().
_KNOWN_VECTOR_TOOL_NAMES = ("canoe", "canalyzer", "canape")


def detect_vector_channels():
    """
    Enumerate real Vector hardware channels currently present
    on this machine (VN1640A/VN1630/...), via the XL Driver
    Library through python-can.

    Returns an empty list — never raises — if python-can isn't
    installed, the Vector XL Driver isn't installed, or no
    Vector hardware is plugged in. All of these are normal,
    expected states (most users run the Virtual ECU Simulator),
    not errors.

    Returns:
        List of dicts: {"label": str, "channel": int,
        "is_on_bus": bool}. "channel" is the value to pass
        to VectorCanInterface.connect(channel=...) to open
        that specific channel. "is_on_bus" is a best-effort
        signal (straight from the driver, not verified
        against real hardware in this codebase) that some
        application — possibly this one, possibly CANoe/
        CANalyzer/another XL API tool — already has an
        active bus connection on that channel right now.
    """

    try:
        from can.interfaces.vector import canlib
        configs = canlib.get_channel_configs()
    except Exception:
        return []

    channels = []

    for cfg in configs:

        channel_index = getattr(cfg, "channel_index", None)

        if channel_index is None:
            continue

        hw_name = (
            getattr(cfg, "hw_name", "")
            or getattr(cfg, "name", "Vector")
        )
        hw_channel = getattr(cfg, "hw_channel", 0)

        serial = (
            getattr(cfg, "serial_number", 0)
            or getattr(cfg, "serial", 0)
        )

        channels.append({
            "label": f"{hw_name} - Channel {hw_channel + 1}",
            "channel": channel_index,
            "hw_channel": hw_channel,
            "serial": serial if serial else None,
            "is_on_bus": bool(getattr(cfg, "is_on_bus", False)),
        })

    return channels


def detect_running_vector_tools():
    """
    Best-effort check for other Vector desktop tools
    (CANoe/CANalyzer/CANape) that might already be running —
    a common way users end up with two testers talking to the
    same ECU at once (see docs/walkthrough.md Phase 4.23).

    Windows-only (uses `tasklist`); returns [] on any other
    platform or if the check fails for any reason. This is a
    heads-up signal, not a guarantee — it only detects that
    the *process* is running, not whether it's actively
    transmitting on the same CAN channel this tool is about
    to use (a CANoe window just sitting open, not measuring,
    is harmless).

    Returns:
        List of matched tool names (empty if none found/
        unknown), e.g. ["canoe"].
    """

    if sys.platform != "win32":
        return []

    try:
        result = subprocess.run(
            ["tasklist"],
            capture_output=True, text=True, timeout=5,
        )
        output = result.stdout.lower()
    except Exception:
        return []

    return [
        name for name in _KNOWN_VECTOR_TOOL_NAMES
        if name in output
    ]


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
        serial = kwargs.get("serial")

        self._tx_id = kwargs.get("tx_id", 0x778)
        self._rx_id = kwargs.get("rx_id", 0x788)

        try:

            bus_kwargs = {
                "interface": "vector",
                "channel": channel,
                "bitrate": bitrate,
                "app_name": app_name,
            }

            if serial:
                bus_kwargs["serial"] = serial

            if fd:
                bus_kwargs["fd"] = True
                bus_kwargs["data_bitrate"] = data_bitrate

            try:
                self._bus = can.Bus(**bus_kwargs)
            except TypeError:
                if "serial" in bus_kwargs:
                    del bus_kwargs["serial"]
                    self._bus = can.Bus(**bus_kwargs)
                else:
                    raise
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

            # Wait for Flow Control (from target ECU only)
            import time as _time
            fc_deadline = _time.time() + 1.0
            while True:
                fc_remaining = fc_deadline - _time.time()
                if fc_remaining <= 0:
                    raise CanTimeoutError(
                        "No Flow Control received"
                    )
                fc = self.receive(timeout=fc_remaining)
                if fc is None:
                    raise CanTimeoutError(
                        "No Flow Control received"
                    )
                if fc.arbitration_id == self._rx_id:
                    break

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

        Filters by self._rx_id so that frames from
        other ECUs (e.g. stale responses to a previous
        functional request) are silently discarded.
        """

        import time

        deadline = time.time() + timeout

        # Loop until we get a frame from the target ECU
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                return None

            msg = self.receive(timeout=remaining)
            if msg is None:
                return None

            if msg.arbitration_id != self._rx_id:
                continue

            break

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

            # Receive Consecutive Frames (target ECU only)
            while len(buffer) < total_len:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise CanTimeoutError(
                        "Timeout waiting for CF"
                    )
                cf = self.receive(timeout=remaining)
                if cf is None:
                    raise CanTimeoutError(
                        "Timeout waiting for CF"
                    )
                if cf.arbitration_id != self._rx_id:
                    continue
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

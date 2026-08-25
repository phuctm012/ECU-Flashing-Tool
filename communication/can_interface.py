# ==================================================
# CAN Interface — Abstract Base Class
# ==================================================
#
# Defines the contract for all CAN adapters
# (Virtual, Vector, PEAK, etc.)
# ==================================================

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
import time


@dataclass
class CanMessage:
    """Represents a single CAN frame."""

    arbitration_id: int
    data: bytes
    timestamp: float = field(default_factory=time.time)
    is_extended_id: bool = False
    is_fd: bool = False
    bitrate_switch: bool = False
    dlc: int = 0

    def __post_init__(self):
        if self.dlc == 0:
            self.dlc = len(self.data)

    def hex_string(self):
        """Return data as hex string like '10 02 FF 00'."""
        return self.data.hex(" ").upper()

    def __repr__(self):
        direction = "TX" if hasattr(self, '_is_tx') else "  "
        return (
            f"[{self.timestamp:.3f}] "
            f"ID=0x{self.arbitration_id:03X} "
            f"DLC={self.dlc} "
            f"Data=[{self.hex_string()}]"
        )


class CanError(Exception):
    """Base exception for CAN errors."""
    pass


class CanTimeoutError(CanError):
    """Raised when a CAN operation times out."""
    pass


class CanConnectionError(CanError):
    """Raised when CAN connection fails."""
    pass


class CanInterface(ABC):
    """
    Abstract base class for CAN bus interfaces.

    All CAN adapters (Virtual, Vector, PEAK, SocketCAN)
    must implement this interface.
    """

    def __init__(self):
        self._connected = False
        self._on_message_callback = None

    # ==========================================
    # Properties
    # ==========================================

    @property
    def is_connected(self):
        return self._connected

    # ==========================================
    # Abstract Methods
    # ==========================================

    @abstractmethod
    def connect(
        self,
        channel=0,
        bitrate=500000,
        **kwargs
    ):
        """
        Connect to the CAN bus.

        Args:
            channel: CAN channel number.
            bitrate: CAN baudrate in bits/sec.
            **kwargs: Additional adapter-specific params.

        Raises:
            CanConnectionError: If connection fails.
        """
        pass

    @abstractmethod
    def disconnect(self):
        """Disconnect from the CAN bus."""
        pass

    @abstractmethod
    def send(self, msg: CanMessage):
        """
        Send a CAN message.

        Args:
            msg: CanMessage to send.

        Raises:
            CanError: If send fails.
        """
        pass

    @abstractmethod
    def receive(
        self,
        timeout: float = 1.0
    ) -> Optional[CanMessage]:
        """
        Receive a CAN message.

        Args:
            timeout: Max wait time in seconds.

        Returns:
            CanMessage or None if timeout.
        """
        pass

    @abstractmethod
    def set_filter(
        self,
        can_id: int,
        mask: int = 0x7FF
    ):
        """
        Set a receive filter.

        Args:
            can_id: CAN ID to accept.
            mask: Filter mask.
        """
        pass

    # ==========================================
    # Common Methods
    # ==========================================

    def set_message_callback(self, callback):
        """
        Set a callback for received messages.

        Args:
            callback: Function(CanMessage) to call.
        """
        self._on_message_callback = callback

    def send_and_receive(
        self,
        msg: CanMessage,
        response_id: int,
        timeout: float = 2.0
    ) -> Optional[CanMessage]:
        """
        Send a message and wait for response.

        Args:
            msg: Message to send.
            response_id: Expected response CAN ID.
            timeout: Max wait time.

        Returns:
            Response CanMessage or None.
        """
        self.send(msg)
        return self.receive(timeout=timeout)

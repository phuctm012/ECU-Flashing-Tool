# ==================================================
# TesterPresent Keepalive Thread
# ==================================================
#
# Background thread that periodically sends
# TesterPresent (0x3E) to keep the ECU session
# alive during flash operations.
#
# Real ECUs typically have a P3 timeout of 5 seconds.
# If no request is received within P3, the ECU
# reverts to DefaultSession.
#
# This thread sends TesterPresent every 2 seconds
# (configurable) to prevent session timeout.
# ==================================================

import threading
import time
from typing import Optional


class TesterPresentThread:
    """
    Background thread that sends TesterPresent (0x3E)
    at regular intervals to keep the diagnostic
    session alive.

    Usage:
        tp = TesterPresentThread(uds_client, interval=2.0)
        tp.start()

        # ... perform flash operations ...

        tp.stop()
    """

    def __init__(
        self,
        uds_client,
        interval=2.0,
        suppress_response=True,
        functional=False,
        on_error=None,
        on_sent=None,
    ):
        """
        Args:
            uds_client: UDS client instance.
            interval: Seconds between TesterPresent
                     messages. Typical: 2.0s (P3 = 5s).
            suppress_response: If True, use
                              suppressPositiveResponse
                              bit (0x80) — no response
                              expected from ECU.
            functional: If True, send TesterPresent to
                       the functional (broadcast) address
                       instead of the physical ECU address.
            on_error: Optional callback(error_message).
            on_sent: Optional callback() when TP sent.
        """

        self._uds_client = uds_client
        self._interval = interval
        self._suppress = suppress_response
        self._functional = functional
        self._on_error = on_error
        self._on_sent = on_sent

        self._thread = None
        self._stop_event = threading.Event()
        self._paused = threading.Event()
        self._paused.set()  # Not paused initially

        self._running = False
        self._send_count = 0
        self._error_count = 0

    # ==========================================
    # Start / Stop
    # ==========================================

    def start(self):
        """Start the keepalive thread."""

        if self._running:
            return

        self._stop_event.clear()
        self._paused.set()
        self._running = True
        self._send_count = 0
        self._error_count = 0

        self._thread = threading.Thread(
            target=self._keepalive_loop,
            daemon=True,
            name="TesterPresent-Keepalive",
        )
        self._thread.start()

    def stop(self):
        """Stop the keepalive thread."""

        self._stop_event.set()
        self._running = False

        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None

    # ==========================================
    # Pause / Resume
    # ==========================================

    def pause(self):
        """
        Pause sending TesterPresent.
        Use when a UDS request is in progress
        (to avoid interference).
        """
        self._paused.clear()

    def resume(self):
        """Resume sending TesterPresent."""
        self._paused.set()

    # ==========================================
    # Properties
    # ==========================================

    @property
    def is_running(self):
        return self._running

    @property
    def send_count(self):
        return self._send_count

    @property
    def error_count(self):
        return self._error_count

    # ==========================================
    # Keepalive Loop
    # ==========================================

    def _keepalive_loop(self):
        """Main loop: send TesterPresent periodically."""

        while not self._stop_event.is_set():

            # Wait for interval or stop signal
            self._stop_event.wait(
                timeout=self._interval
            )

            if self._stop_event.is_set():
                break

            # Check if paused
            if not self._paused.is_set():
                continue

            # Send TesterPresent
            try:

                self._uds_client.tester_present(
                    suppress_response=self._suppress,
                    functional=self._functional,
                )

                self._send_count += 1

                if self._on_sent:
                    self._on_sent()

            except Exception as e:

                self._error_count += 1

                if self._on_error:
                    self._on_error(str(e))

    # ==========================================
    # Context Manager
    # ==========================================

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()

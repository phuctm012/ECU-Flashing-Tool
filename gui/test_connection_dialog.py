# ==================================================
# Test Connection Dialog
# ==================================================
#
# Tools -> Test Connection... — GUI front-end for
# core.test_connection.TestConnectionWorker, the same safe
# session + Security Access probe as cli.py's test-connection
# subcommand, now reachable without leaving the GUI.
#
# Threading follows the exact same lifecycle rules as
# gui/flash_tab.py's flash_button_clicked() (see CLAUDE.md
# "Threading model"): TestConnectionWorker.finished is emitted
# from inside run() itself, while the worker thread is still
# executing, so the slot connected to it must never touch
# self._thread/self._worker directly — only _cleanup_thread(),
# wired to thread.finished, does that.
# ==================================================

from PySide6.QtCore import QThread
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QPlainTextEdit,
    QDialogButtonBox,
)

from core.test_connection import TestConnectionWorker


class TestConnectionDialog(QDialog):

    def __init__(
        self,
        parent,
        use_virtual,
        security_dll_path,
        functional,
        can_config,
    ):
        super().__init__(parent)

        self.setWindowTitle("Test Connection")
        self.resize(560, 380)

        self._thread = None
        self._worker = None

        layout = QVBoxLayout(self)

        self.logText = QPlainTextEdit(self)
        self.logText.setReadOnly(True)
        layout.addWidget(self.logText)

        self.buttonBox = QDialogButtonBox(
            QDialogButtonBox.Close, self
        )
        self.buttonBox.rejected.connect(self.close)
        self.buttonBox.accepted.connect(self.close)
        self.buttonBox.button(
            QDialogButtonBox.Close
        ).setEnabled(False)
        layout.addWidget(self.buttonBox)

        self._start(use_virtual, security_dll_path, functional, can_config)

    # ==================================================
    # Start the probe
    # ==================================================

    def _start(
        self, use_virtual, security_dll_path, functional, can_config
    ):

        self._log(
            "Target: "
            + (
                "Virtual ECU Simulator"
                if use_virtual
                else f"Vector channel {can_config.get('channel', 0)}"
            )
            + f" | Tx=0x{can_config.get('tx_id', 0x778):X}"
            f" Rx=0x{can_config.get('rx_id', 0x788):X}"
        )
        self._log("")

        self._thread = QThread()
        self._worker = TestConnectionWorker(
            use_virtual=use_virtual,
            security_dll_path=security_dll_path,
            functional=functional,
            can_channel=can_config.get("channel", 0),
            can_tx_id=can_config.get("tx_id", 0x778),
            can_rx_id=can_config.get("rx_id", 0x788),
            can_bitrate=can_config.get("bitrate", 500000),
            can_fd=can_config.get("fd", False),
            can_data_bitrate=can_config.get("data_bitrate", 2000000),
        )
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)

        self._worker.step_message.connect(self._on_step_message)
        self._worker.trace_row.connect(self._on_trace_row)
        self._worker.ecu_info_message.connect(self._on_ecu_info)
        self._worker.finished.connect(self._on_finished)

        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)

        # NOTE: intentionally NOT connecting thread.finished ->
        # thread.deleteLater here — see module docstring.
        self._thread.finished.connect(self._cleanup_thread)

        self._thread.start()

    # ==================================================
    # Worker signal handlers
    # ==================================================

    def _log(self, text):
        self.logText.appendPlainText(text)

    def _on_step_message(self, message):
        self._log(f"[OK] {message}")

    def _on_trace_row(self, row):
        req = f"{row.get('req_target') or '':<16} {row.get('req_data') or ''}"
        resp = ""
        if row.get("resp_data"):
            resp = f" -> {row.get('resp_source')}: {row['resp_data']}"
        self._log(f"    TRACE: {req}{resp}")

    def _on_ecu_info(self, info):
        self._log("--- ECU Identification ---")
        for key, value in info.items():
            self._log(f"  {key}: {value}")

    def _on_finished(self, passed, message):
        self._log("")
        self._log(message)
        self.buttonBox.button(
            QDialogButtonBox.Close
        ).setEnabled(True)

    # ==================================================
    # Thread cleanup
    # ==================================================

    def _cleanup_thread(self):

        if self._thread is not None:
            self._thread.wait()

        self._thread = None
        self._worker = None

    def closeEvent(self, event):

        if self._thread is not None and self._thread.isRunning():
            # self._worker.finished -> self._thread.quit is a
            # queued cross-thread connection (worker lives on
            # _thread, quit()'s receiver — _thread itself —
            # lives on the main thread), so it's only delivered
            # once the MAIN thread's event loop next runs.
            # Calling wait() here would block that very event
            # loop, so the queued quit() could never arrive —
            # deadlock. quit() is thread-safe and fine to call
            # directly (same reasoning as MainWindow.closeEvent()
            # in gui/main_window.py), so do that first: the
            # probe is a short, bounded sequence (no long loop
            # to abort), so this just waits for it to finish
            # naturally rather than tearing down mid-request.
            self._thread.quit()
            self._thread.wait()

        event.accept()

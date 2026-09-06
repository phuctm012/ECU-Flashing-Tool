# ==================================================
# Parallel Flash
# ==================================================
#
# ParallelFlashMixin — a new "Parallel Flash" tab that flashes up
# to 4 ECUs simultaneously, each on its own CAN channel, sharing
# one firmware and one set of CAN protocol settings (Configure ->
# Communication). Unlike gui/batch_flash.py's BatchFlashMixin
# (which reuses ONE self.thread/self.worker pair sequentially),
# this manages 4 fully independent slots, each with its own
# TestConnectionWorker+FlashWorker pair — see
# docs/superpowers/specs/2026-09-06-parallel-flash-design.md.
#
# Every slot follows the exact same QThread lifecycle rules
# documented in CLAUDE.md's "Threading model", applied 4 times
# independently: a worker's own *_finished/finished signal
# connects to thread.quit + worker.deleteLater; only a slot
# connected to thread.finished (never the worker's own signal)
# clears that slot's own thread/worker references.
#
# Two rules specific to this file, since every other QThread pair
# in this codebase is a single shared self.thread/self.worker (no
# per-instance routing needed):
#
# 1. A cross-thread signal (emitted from inside a worker's own
#    run()) must ALWAYS be connected to a genuine bound method of
#    a QObject, never a lambda. PySide6 can only detect a queued
#    connection's target thread when the receiver is a QObject-
#    bound method; a lambda has no such affinity, so the
#    connection silently degrades to a direct (synchronous,
#    wrong-thread) call instead — even when
#    ConnectionType.QueuedConnection is explicitly requested. Hit
#    for real while building Task 6: panel["flash_thread"] never
#    reached isRunning()==False even though the flash worker had
#    genuinely finished, because _start_flash_for_panel() itself
#    had run on the wrong (already-dying) Identify worker thread.
#
# 2. self.sender() is NOT a reliable way to recover which panel a
#    shared slot is handling, because every worker here also wires
#    its own finished/flash_finished signal to its own
#    deleteLater() (a same-thread direct connection) - by the time
#    a queued cross-thread slot actually runs on the main thread,
#    the worker can already be destroyed, and Qt correctly reports
#    a destroyed sender as None. Instead, each panel gets one
#    small, permanent _PanelSignalRouter QObject (created once,
#    living in the main thread for the panel's whole lifetime) whose
#    bound methods are the actual connection targets; panel context
#    comes from the router's own plain attribute, never from
#    inspecting the emitting worker.
# ==================================================

import threading

from PySide6.QtCore import QObject, QThread
from PySide6.QtWidgets import (
    QComboBox,
    QLabel,
    QProgressBar,
    QPushButton,
)

from core.test_connection import TestConnectionWorker
from core.flash_controller import FlashWorker
from core.flash_sequence import (
    build_flash_sequence,
    build_suzuki_slp1_flash_sequence,
)

_PANEL_COUNT = 4


class _PanelSignalRouter(QObject):
    """
    Permanent per-panel QObject (created once, in the main thread,
    alongside the panel itself) that exists only to give
    cross-thread worker signals a genuine bound-method receiver —
    see this module's docstring, rules 1 and 2, for why a lambda or
    self.sender() can't do this job safely.
    """

    def __init__(self, mixin, panel):
        super().__init__()
        self._mixin = mixin
        self.panel = panel

    def on_identify_finished(self, passed, message):
        self._mixin._on_identify_finished_for_panel(
            self.panel, passed, message
        )

    def on_ecu_info(self, info_dict):
        self._mixin._on_parallel_ecu_info(self.panel, info_dict)

    def on_identify_thread_finished(self):
        self._mixin._cleanup_identify_thread_for_panel(self.panel)

    def on_progress_changed(self, pct):
        self._mixin._on_panel_progress_changed(self.panel, pct)

    def on_step_started(self, desc):
        self._mixin._on_panel_step_started(self.panel, desc)

    def on_log_message(self, message):
        self._mixin._on_panel_log_message(self.panel, message)

    def on_flash_finished(self):
        self._mixin._on_panel_flash_finished(self.panel)

    def on_flash_aborted(self):
        self._mixin._on_panel_flash_aborted(self.panel)

    def on_flash_thread_finished(self):
        self._mixin._cleanup_flash_thread_for_panel(self.panel)


class ParallelFlashMixin:
    """Mixin adding the Parallel Flash tab to MainWindow."""

    # ==================================================
    # Setup
    # ==================================================

    def setup_parallel_flash(self):

        self._parallel_panels = []

        group_boxes = [
            self.ui.groupBoxParallelChannel1,
            self.ui.groupBoxParallelChannel2,
            self.ui.groupBoxParallelChannel3,
            self.ui.groupBoxParallelChannel4,
        ]
        log_widgets = [
            self.ui.textEditParallelChannel1Log,
            self.ui.textEditParallelChannel2Log,
            self.ui.textEditParallelChannel3Log,
            self.ui.textEditParallelChannel4Log,
        ]

        for i in range(_PANEL_COUNT):
            panel = self._build_parallel_panel(
                group_boxes[i], log_widgets[i], i
            )
            self._parallel_panels.append(panel)

        if hasattr(self.ui, 'buttonParallelStartAll'):
            self.ui.buttonParallelStartAll.clicked.connect(
                self.parallel_start_all
            )
        if hasattr(self.ui, 'buttonParallelAbortAll'):
            self.ui.buttonParallelAbortAll.clicked.connect(
                self.parallel_abort_all
            )

    def _build_parallel_panel(self, group_box, log_widget, index):

        combo = QComboBox()
        combo.addItem("Not Selected", userData="not-selected")
        self.populate_hardware_combo_widget_append(combo)

        serial_label = QLabel("SN: —")
        flash_button = QPushButton("Flash")
        flash_button.setEnabled(False)
        progress_bar = QProgressBar()
        progress_bar.setRange(0, 100)
        progress_bar.setValue(0)
        status_label = QLabel("No channel selected.")

        group_box.layout().addWidget(combo)
        group_box.layout().addWidget(serial_label)
        group_box.layout().addWidget(flash_button)
        group_box.layout().addWidget(progress_bar)
        group_box.layout().addWidget(status_label)

        panel = {
            "index": index,
            "combo": combo,
            "serial_label": serial_label,
            "flash_button": flash_button,
            "progress_bar": progress_bar,
            "status_label": status_label,
            "log_widget": log_widget,
            "phase": "idle",
            "serial": None,
            "stopping": False,
            "identify_thread": None,
            "identify_worker": None,
            "flash_thread": None,
            "flash_worker": None,
        }

        panel["router"] = _PanelSignalRouter(self, panel)

        combo.currentIndexChanged.connect(
            lambda _, p=panel: self._on_parallel_channel_changed(p)
        )
        flash_button.clicked.connect(
            lambda _, p=panel: self._on_parallel_flash_clicked(p)
        )

        return panel

    def populate_hardware_combo_widget_append(self, combo):
        """
        Appends the same "Virtual ECU Simulator" + real-channel
        entries populate_hardware_combo_widget() puts in a fresh
        combo, onto a combo that already has a leading "Not
        Selected" entry — Parallel Flash panels default to no
        channel picked, unlike the global comboBoxHardware, which
        always defaults to Virtual ECU Simulator.
        """

        from communication.vector_can import (
            detect_vector_channels_with_error,
        )

        combo.addItem("Virtual ECU Simulator (No Hardware)", userData=None)

        channels, _error = detect_vector_channels_with_error()
        for ch in channels:
            combo.addItem(ch["label"], userData=ch)

        combo.setCurrentIndex(0)

    def _on_parallel_channel_changed(self, panel):

        if panel["phase"] in ("identifying", "flashing"):
            return

        selected = panel["combo"].currentData() != "not-selected"
        panel["flash_button"].setEnabled(selected)
        if panel["phase"] not in ("pass", "fail", "abort"):
            panel["status_label"].setText(
                "Idle — ready to flash." if selected
                else "No channel selected."
            )

    def _on_parallel_flash_clicked(self, panel):

        if panel["phase"] in ("identifying", "flashing"):
            self._abort_panel(panel)
            return

        self._start_identify_for_panel(panel)

    def _start_identify_for_panel(self, panel):

        panel["phase"] = "identifying"
        panel["serial"] = None
        panel["serial_label"].setText("SN: —")
        panel["flash_button"].setText("Abort")
        panel["combo"].setEnabled(False)
        panel["status_label"].setText(
            "Identifying ECU — reading Serial Number (DID 0xF18C)..."
        )
        self._log_parallel_panel(
            panel, "Identify: reading Serial Number (DID 0xF18C)..."
        )

        data = panel["combo"].currentData()
        use_virtual = data is None
        channel = 0
        serial_hw = None
        if data not in (None, "not-selected"):
            channel = data.get("hw_channel", data.get("channel", 0))
            serial_hw = data.get("serial")

        can_config = (
            self.get_can_config() if hasattr(self, 'get_can_config') else {}
        )
        security_dll_path = getattr(
            self, '_security_dll_path', ''
        ) or None
        use_suzuki_sequence = False
        if hasattr(self.ui, 'comboBoxFlashSequence'):
            use_suzuki_sequence = (
                "Suzuki" in self.ui.comboBoxFlashSequence.currentText()
            )

        panel["identify_thread"] = QThread()
        panel["identify_worker"] = TestConnectionWorker(
            use_virtual=use_virtual,
            security_dll_path=security_dll_path,
            functional=use_suzuki_sequence,
            can_channel=channel,
            can_serial=serial_hw,
            can_tx_id=can_config.get("tx_id", 0x778),
            can_rx_id=can_config.get("rx_id", 0x788),
            can_bitrate=can_config.get("bitrate", 500000),
            can_fd=can_config.get("fd", False),
            can_data_bitrate=can_config.get("data_bitrate", 2000000),
        )
        panel["identify_worker"].moveToThread(panel["identify_thread"])

        router = panel["router"]

        panel["identify_worker"].ecu_info_message.connect(
            router.on_ecu_info
        )

        panel["identify_thread"].started.connect(
            panel["identify_worker"].run
        )
        panel["identify_worker"].finished.connect(
            router.on_identify_finished
        )
        panel["identify_worker"].finished.connect(
            panel["identify_thread"].quit
        )
        panel["identify_worker"].finished.connect(
            panel["identify_worker"].deleteLater
        )
        # NOTE: intentionally NOT connecting thread.finished ->
        # thread.deleteLater — see module docstring and CLAUDE.md's
        # "Threading model".
        panel["identify_thread"].finished.connect(
            router.on_identify_thread_finished
        )

        panel["identify_thread"].start()

    def _cleanup_identify_thread_for_panel(self, panel):

        if panel["identify_thread"] is not None:
            panel["identify_thread"].wait()

        panel["identify_thread"] = None
        panel["identify_worker"] = None

    def _on_identify_finished_for_panel(self, panel, passed, message):

        if panel["stopping"]:
            # Same async-ordering guard as batch_flash.py's
            # _on_identify_finished(): TestConnectionWorker.finished
            # is a queued cross-thread signal, still pending
            # delivery even after this panel's own abort path
            # called identify_thread.wait() — without this guard a
            # probe that succeeded right as Abort was clicked would
            # still auto-start a real Flash here.
            panel["stopping"] = False
            self._reset_panel_to_idle(panel)
            return

        if not passed:
            panel["phase"] = "fail"
            panel["status_label"].setText("No ECU detected on the bus.")
            self._log_parallel_panel(
                panel, "Identify: no ECU detected on the bus."
            )
            panel["flash_button"].setText("Flash")
            panel["combo"].setEnabled(True)
            return

        ecu_info = getattr(self, '_parallel_last_ecu_info', {}).get(
            panel["index"], {}
        )
        serial = ecu_info.get("ECU Serial Number", "UNKNOWN")
        panel["serial"] = serial
        panel["serial_label"].setText(f"SN: {serial}")
        self._log_parallel_panel(
            panel, f"Identify: Serial Number = {serial}."
        )
        self._start_flash_for_panel(panel, serial)

    def _reset_panel_to_idle(self, panel):
        panel["phase"] = "idle"
        panel["flash_button"].setText("Flash")
        panel["combo"].setEnabled(True)
        panel["status_label"].setText("Idle — ready to flash.")

    def _log_parallel_panel(self, panel, message):
        panel["log_widget"].append(message)

    def _on_parallel_ecu_info(self, panel, info_dict):
        if not hasattr(self, '_parallel_last_ecu_info'):
            self._parallel_last_ecu_info = {}
        self._parallel_last_ecu_info[panel["index"]] = info_dict

    def _start_flash_for_panel(self, panel, serial):

        datablocks = (
            self.get_checked_datablocks()
            if hasattr(self, 'get_checked_datablocks')
            else getattr(self, '_loaded_datablocks', [])
        )

        if not datablocks:
            self._log_parallel_panel(
                panel, "No firmware loaded — cannot flash."
            )
            self._reset_panel_to_idle(panel)
            return

        if not hasattr(self, '_parallel_security_lock'):
            self._parallel_security_lock = threading.Lock()

        panel["phase"] = "flashing"
        panel["progress_bar"].setValue(0)
        panel["status_label"].setText("Flashing...")

        use_suzuki_sequence = False
        if hasattr(self.ui, 'comboBoxFlashSequence'):
            use_suzuki_sequence = (
                "Suzuki" in self.ui.comboBoxFlashSequence.currentText()
            )

        if use_suzuki_sequence:
            tester_serial_number = (
                self.get_tester_serial_number()
                if hasattr(self, 'get_tester_serial_number')
                else None
            )
            steps = build_suzuki_slp1_flash_sequence(
                datablocks, tester_serial_number=tester_serial_number,
            )
        else:
            steps = build_flash_sequence(datablocks)

        data = panel["combo"].currentData()
        use_virtual = data is None
        channel = 0
        serial_hw = None
        if data not in (None, "not-selected"):
            channel = data.get("hw_channel", data.get("channel", 0))
            serial_hw = data.get("serial")

        can_config = (
            self.get_can_config() if hasattr(self, 'get_can_config') else {}
        )
        security_dll_path = getattr(
            self, '_security_dll_path', ''
        ) or None
        data_format_config = (
            self.get_data_format_config()
            if hasattr(self, 'get_data_format_config')
            else {}
        )

        panel["flash_thread"] = QThread()
        panel["flash_worker"] = FlashWorker(
            steps=steps,
            datablocks=datablocks,
            use_virtual=use_virtual,
            security_dll_path=security_dll_path,
            security_lock=self._parallel_security_lock,
            keepalive_functional=use_suzuki_sequence,
            can_channel=channel,
            can_serial=serial_hw,
            can_tx_id=can_config.get("tx_id", 0x778),
            can_rx_id=can_config.get("rx_id", 0x788),
            can_bitrate=can_config.get("bitrate", 500000),
            can_fd=can_config.get("fd", False),
            can_data_bitrate=can_config.get("data_bitrate", 2000000),
            download_compression=data_format_config.get(
                "compression", 0x00
            ),
            download_encrypting=data_format_config.get(
                "encrypting", 0x00
            ),
        )
        panel["flash_worker"].moveToThread(panel["flash_thread"])

        router = panel["router"]

        panel["flash_thread"].started.connect(panel["flash_worker"].run)

        panel["flash_worker"].flash_finished.connect(
            panel["flash_thread"].quit
        )
        panel["flash_worker"].flash_aborted.connect(
            panel["flash_thread"].quit
        )
        panel["flash_worker"].flash_finished.connect(
            panel["flash_worker"].deleteLater
        )
        panel["flash_worker"].flash_aborted.connect(
            panel["flash_worker"].deleteLater
        )

        panel["flash_worker"].progress_changed.connect(
            router.on_progress_changed
        )
        panel["flash_worker"].step_started.connect(
            router.on_step_started
        )
        panel["flash_worker"].information_message.connect(
            router.on_log_message
        )
        panel["flash_worker"].trace_message.connect(
            router.on_log_message
        )

        panel["flash_worker"].flash_finished.connect(
            router.on_flash_finished
        )
        panel["flash_worker"].flash_aborted.connect(
            router.on_flash_aborted
        )

        panel["flash_thread"].finished.connect(
            router.on_flash_thread_finished
        )

        panel["flash_thread"].start()

    def _on_panel_progress_changed(self, panel, pct):
        panel["progress_bar"].setValue(pct)

    def _on_panel_step_started(self, panel, desc):
        panel["status_label"].setText(desc)

    def _on_panel_log_message(self, panel, message):
        self._log_parallel_panel(panel, message)

    def _cleanup_flash_thread_for_panel(self, panel):

        if panel["flash_thread"] is not None:
            panel["flash_thread"].wait()

        panel["flash_thread"] = None
        panel["flash_worker"] = None

    def _on_panel_flash_finished(self, panel):

        if panel["stopping"]:
            panel["stopping"] = False
            self._reset_panel_to_idle(panel)
            return

        panel["phase"] = "pass"
        panel["flash_button"].setText("Flash")
        panel["combo"].setEnabled(True)
        panel["status_label"].setText("PASS.")
        self._log_parallel_panel(panel, "Flash completed successfully.")

    def _on_panel_flash_aborted(self, panel):

        if panel["stopping"]:
            panel["stopping"] = False
            self._reset_panel_to_idle(panel)
            return

        panel["phase"] = "fail"
        panel["flash_button"].setText("Flash")
        panel["combo"].setEnabled(True)
        panel["status_label"].setText("FAIL / ABORTED.")
        self._log_parallel_panel(panel, "Flash aborted.")

    def parallel_start_all(self):
        pass

    def parallel_abort_all(self):
        pass

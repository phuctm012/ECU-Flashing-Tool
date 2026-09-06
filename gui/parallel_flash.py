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
# ==================================================

from PySide6.QtWidgets import (
    QComboBox,
    QLabel,
    QProgressBar,
    QPushButton,
)

_PANEL_COUNT = 4


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
        pass

    def parallel_start_all(self):
        pass

    def parallel_abort_all(self):
        pass

# ==================================================
# Parallel Channel Settings Dialog
# ==================================================
#
# ParallelChannelSettingsDialog — per-channel "Basic Communication"
# popup for a Parallel Flash panel (gui/parallel_flash.py's
# "Settings" button, next to "View Log"). Lets the operator override
# that one channel's Request/Response/Functional Request CAN ID,
# independently of the shared Configure tab settings every panel
# otherwise uses — see
# docs/superpowers/specs/2026-09-06-parallel-flash-design.md and the
# approved mockup for why this exists.
#
# A plain form dialog — no QObject/QThread worker involved, unlike
# TestConnectionDialog/GitLabFetchDialog, so none of CLAUDE.md's
# "Threading model" rules apply here. Save/Reset logic lives in
# methods callable directly (not only from a button click), so tests
# can exercise validation without ever calling exec() (which would
# block waiting for real input) — same reasoning as
# gui/main_window.py's _write_log_file() vs its dialog-opening
# counterpart.
# ==================================================

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
)


class ParallelChannelSettingsDialog(QDialog):

    def __init__(
        self,
        parent,
        channel_label,
        tx_id,
        rx_id,
        functional_id,
        is_customized,
    ):
        super().__init__(parent)

        self.setWindowTitle(f"{channel_label} — Basic Communication")

        self._reset_requested = False
        self._result_values = None

        layout = QVBoxLayout(self)

        subtitle = QLabel(
            "Overrides the shared Configure tab settings for this "
            "channel only."
        )
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        form = QFormLayout()
        self.txIdEdit = QLineEdit(f"0x{tx_id:X}")
        self.rxIdEdit = QLineEdit(f"0x{rx_id:X}")
        self.functionalIdEdit = QLineEdit(f"0x{functional_id:X}")
        form.addRow("Request CAN ID", self.txIdEdit)
        form.addRow("Response CAN ID", self.rxIdEdit)
        form.addRow("Functional Request CAN ID", self.functionalIdEdit)
        layout.addLayout(form)

        self.errorLabel = QLabel("")
        self.errorLabel.setStyleSheet("color: #b23b3b;")
        self.errorLabel.setWordWrap(True)
        self.errorLabel.hide()
        layout.addWidget(self.errorLabel)

        note = QLabel(
            "This channel already has its own values, independent "
            "of the Configure tab." if is_customized else
            "Currently using the shared Configure tab defaults. "
            "Saving will give this channel its own values, "
            "independent of future Configure tab changes."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(note)

        button_row = QHBoxLayout()
        self.resetButton = QPushButton("Reset to shared")
        self.cancelButton = QPushButton("Cancel")
        self.saveButton = QPushButton("Save")
        button_row.addWidget(self.resetButton)
        button_row.addStretch(1)
        button_row.addWidget(self.cancelButton)
        button_row.addWidget(self.saveButton)
        layout.addLayout(button_row)

        self.resetButton.clicked.connect(self._on_reset_clicked)
        self.cancelButton.clicked.connect(self.reject)
        self.saveButton.clicked.connect(self._on_save_clicked)

    def _parse_hex_fields(self):
        """
        Returns (tx_id, rx_id, functional_id) as ints, or None if
        any field isn't valid hex — showing which field failed via
        errorLabel rather than raising, matching how
        gui/configure_tab.py's get_can_config() silently tolerates
        bad table input rather than crashing the app.
        """

        fields = (
            ("Request CAN ID", self.txIdEdit),
            ("Response CAN ID", self.rxIdEdit),
            ("Functional Request CAN ID", self.functionalIdEdit),
        )
        values = []
        for label, edit in fields:
            text = edit.text().strip()
            try:
                values.append(int(text, 16))
            except ValueError:
                self.errorLabel.setText(
                    f"{label}: enter a hex value, e.g. 778 or 0x778."
                )
                self.errorLabel.show()
                return None

        self.errorLabel.hide()
        return tuple(values)

    def _on_save_clicked(self):
        parsed = self._parse_hex_fields()
        if parsed is None:
            return
        self._result_values = parsed
        self.accept()

    def _on_reset_clicked(self):
        self._reset_requested = True
        self.accept()

    def reset_requested(self):
        return self._reset_requested

    def result_values(self):
        return self._result_values

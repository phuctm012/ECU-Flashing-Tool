# ==================================================
# Parallel Flash
# ==================================================
#
# ParallelFlashMixin — a new "Parallel Flash" tab that flashes up
# to 6 ECUs simultaneously, each on its own CAN channel, sharing
# one firmware and one set of CAN protocol settings (Configure ->
# Communication). Unlike gui/batch_flash.py's BatchFlashMixin
# (which reuses ONE self.thread/self.worker pair sequentially),
# this manages 6 fully independent slots, each with its own
# TestConnectionWorker+FlashWorker pair — see
# docs/superpowers/specs/2026-09-06-parallel-flash-design.md (the
# original design fixed this at 4 panels in a 2x2 grid; docs/
# walkthrough.md's later phases cover the move to 6 panels/3x2 and
# the compact card layout below).
#
# Every slot follows the exact same QThread lifecycle rules
# documented in CLAUDE.md's "Threading model", applied independently
# per panel: a worker's own *_finished/finished signal
# connects to thread.quit + worker.deleteLater; only a slot
# connected to thread.finished (never the worker's own signal)
# clears that slot's own thread/worker references.
#
# Per-panel log/trace display reuses the app's existing, single
# Information/Trace tabs (outputTabWidget in gui/main_window.ui —
# already a sibling of the top-level tabWidget, so already visible
# under the Parallel Flash tab) instead of a duplicate embedded
# widget: each panel keeps its OWN buffered history
# (info_lines/trace_entries, always appended to regardless of what's
# currently displayed), and clicking that panel's "View Log" action
# makes it the active one (_parallel_active_panel_index), replaying
# its buffer into the shared tabs. A worker's messages are only
# ALSO written live to those shared tabs while its panel is the
# active one — otherwise they're buffered silently, the same way a
# real CAN trace tool only renders what you're currently looking at.
#
# Each card's secondary actions (Settings, View Log, Save Report)
# live as QAction objects inside one "..." QMenu instead of 3
# separate always-visible buttons — a real mockup-driven compaction
# (docs/walkthrough.md) so 6 repeated action rows don't compete with
# Flash, the actual primary action, for attention. Test Connection
# stays a real, separate QPushButton (not folded into the menu)
# specifically because it needs a persistent Pass/Fail color visible
# without opening anything — the one deliberate exception, decided
# with the user when this menu was introduced.
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
#    (Same-thread UI signals — QPushButton.clicked, QAction.triggered,
#    QComboBox.currentIndexChanged — are unaffected; lambdas on those
#    are fine and used throughout this file.)
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

import html
import threading
from datetime import datetime

from PySide6.QtCore import QObject, QThread
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
)

from core.test_connection import TestConnectionWorker
from core.flash_controller import FlashWorker
from core.flash_sequence import (
    build_flash_sequence,
    build_suzuki_slp1_flash_sequence,
)
from gui.parallel_channel_settings_dialog import (
    ParallelChannelSettingsDialog,
)
from gui.test_connection_dialog import TestConnectionDialog
from config.settings import (
    APP_NAME,
    APP_VERSION,
    ACCENT_COLOR,
    ACCENT_HOVER_COLOR,
    ACCENT_COLOR_DARK,
    ACCENT_HOVER_COLOR_DARK,
    DANGER_COLOR,
    DANGER_HOVER_COLOR,
    DANGER_COLOR_DARK,
    DANGER_HOVER_COLOR_DARK,
    SUCCESS_COLOR,
    SUCCESS_COLOR_DARK,
    DISABLED_BUTTON_BG,
    DISABLED_BUTTON_FG,
    DISABLED_BUTTON_BORDER,
    DISABLED_BUTTON_BG_DARK,
    DISABLED_BUTTON_FG_DARK,
    DISABLED_BUTTON_BORDER_DARK,
)

_PANEL_COUNT = 6

# (background, hover) pairs for the per-panel Flash/Abort button,
# keyed by semantic "kind" — see this module's docstring for why
# these are applied as a per-instance stylesheet rather than a
# static QSS #id rule.
_BUTTON_COLOR_PAIRS = {
    "accent": (ACCENT_COLOR, ACCENT_HOVER_COLOR),
    "danger": (DANGER_COLOR, DANGER_HOVER_COLOR),
}
_BUTTON_COLOR_PAIRS_DARK = {
    "accent": (ACCENT_COLOR_DARK, ACCENT_HOVER_COLOR_DARK),
    "danger": (DANGER_COLOR_DARK, DANGER_HOVER_COLOR_DARK),
}

# Progress bar chunk color overrides — None means "no override",
# falling back to the app-wide QProgressBar::chunk rule (already
# themed blue in both resources/style.qss and style_dark.qss).
_PROGRESS_COLORS = {"success": SUCCESS_COLOR, "danger": DANGER_COLOR}
_PROGRESS_COLORS_DARK = {"success": SUCCESS_COLOR_DARK, "danger": DANGER_COLOR_DARK}

# Small status-dot color per panel phase, shown next to the channel
# title — "idle" reuses the disabled-button gray (already a neutral/
# muted tone in this app's palette) since a plain gray has no other
# semantic constant of its own.
_DOT_COLORS = {
    "idle": DISABLED_BUTTON_FG,
    "busy": ACCENT_COLOR,
    "success": SUCCESS_COLOR,
    "danger": DANGER_COLOR,
}
_DOT_COLORS_DARK = {
    "idle": DISABLED_BUTTON_FG_DARK,
    "busy": ACCENT_COLOR_DARK,
    "success": SUCCESS_COLOR_DARK,
    "danger": DANGER_COLOR_DARK,
}


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

    def on_information_message(self, message):
        self._mixin._log_parallel_panel(self.panel, message)

    def on_trace_message(self, message):
        self._mixin._on_panel_trace_message(self.panel, message)

    def on_trace_row(self, row):
        self._mixin._on_panel_trace_row(self.panel, row)

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
        # None until a panel's "View Log" is clicked — no panel
        # auto-claims the shared Information/Trace tabs just because
        # the Parallel Flash top-level tab became active, since
        # those tabs are shared with Single Flash/Batch Flash too.
        self._parallel_active_panel_index = None

        group_boxes = [
            self.ui.groupBoxParallelChannel1,
            self.ui.groupBoxParallelChannel2,
            self.ui.groupBoxParallelChannel3,
            self.ui.groupBoxParallelChannel4,
            self.ui.groupBoxParallelChannel5,
            self.ui.groupBoxParallelChannel6,
        ]

        for i in range(_PANEL_COUNT):
            panel = self._build_parallel_panel(group_boxes[i], i)
            self._parallel_panels.append(panel)

        if hasattr(self.ui, 'buttonParallelStartAll'):
            self.ui.buttonParallelStartAll.clicked.connect(
                self.parallel_start_all
            )
        if hasattr(self.ui, 'buttonParallelAbortAll'):
            self.ui.buttonParallelAbortAll.clicked.connect(
                self.parallel_abort_all
            )

    def _build_parallel_panel(self, group_box, index):

        # The QGroupBox's own native title is left blank — a custom
        # header row below (dot + label + "..." menu) replaces it,
        # since a plain title string can't carry a colored dot or an
        # inline button the way the approved mockup shows.
        group_box.setTitle("")

        status_dot = QLabel("●")
        status_dot.setObjectName("labelParallelStatusDot")

        title_label = QLabel(f"Channel {index + 1}")
        title_label.setObjectName("labelParallelChannelTitle")
        title_label.setStyleSheet("font-weight: 600;")

        menu_button = QPushButton("⋯")
        menu_button.setObjectName("buttonParallelChannelMenu")

        menu = QMenu(menu_button)
        action_settings = menu.addAction("Settings")
        action_view_log = menu.addAction("View Log")
        action_save_report = menu.addAction("Save Report")
        menu_button.setMenu(menu)

        header_row = QHBoxLayout()
        header_row.addWidget(status_dot)
        header_row.addWidget(title_label, 1)
        header_row.addWidget(menu_button)

        combo = QComboBox()
        combo.addItem("Not Selected", userData="not-selected")
        self.populate_hardware_combo_widget_append(combo)

        test_connection_button = QPushButton("Test Connection")
        test_connection_button.setObjectName(
            "buttonParallelTestConnection"
        )
        test_connection_button.setEnabled(False)

        flash_button = QPushButton("Flash")
        flash_button.setEnabled(False)
        progress_bar = QProgressBar()
        progress_bar.setRange(0, 100)
        progress_bar.setValue(0)
        status_label = QLabel("No channel selected.")

        controls_row = QHBoxLayout()
        controls_row.addWidget(test_connection_button)
        controls_row.addWidget(flash_button)
        controls_row.addWidget(progress_bar, 1)

        group_box.layout().addLayout(header_row)
        group_box.layout().addWidget(combo)
        group_box.layout().addLayout(controls_row)
        group_box.layout().addWidget(status_label)

        panel = {
            "index": index,
            "group_box": group_box,
            "status_dot": status_dot,
            "title_label": title_label,
            "menu_button": menu_button,
            "action_settings": action_settings,
            "action_view_log": action_view_log,
            "action_save_report": action_save_report,
            "combo": combo,
            "test_connection_button": test_connection_button,
            "flash_button": flash_button,
            "progress_bar": progress_bar,
            "status_label": status_label,
            "info_lines": [],
            "trace_entries": [],
            "phase": "idle",
            "serial": None,
            "stopping": False,
            "identify_thread": None,
            "identify_worker": None,
            "flash_thread": None,
            "flash_worker": None,
            "comm_settings": None,
            "_button_kind": "accent",
            "_progress_kind": None,
            "_test_connection_kind": None,
            "_dot_kind": "idle",
            "_status_text": "No channel selected.",
        }

        panel["router"] = _PanelSignalRouter(self, panel)

        combo.currentIndexChanged.connect(
            lambda _, p=panel: self._on_parallel_channel_changed(p)
        )
        flash_button.clicked.connect(
            lambda _, p=panel: self._on_parallel_flash_clicked(p)
        )
        test_connection_button.clicked.connect(
            lambda _, p=panel: self._test_connection_for_panel(p)
        )
        action_view_log.triggered.connect(
            lambda _, idx=index: self._view_parallel_panel_log(idx)
        )
        action_settings.triggered.connect(
            lambda _, p=panel: self._open_parallel_channel_settings(p)
        )
        action_save_report.triggered.connect(
            lambda _, p=panel: self._save_parallel_panel_report(p)
        )

        self._apply_panel_button_style(panel, "accent")
        self._apply_panel_status_dot_style(panel, "idle")

        return panel

    def _resolve_panel_comm_ids(self, panel, can_config):
        """
        Returns (tx_id, rx_id, functional_id) for a panel: its own
        Basic Communication override
        (gui/parallel_channel_settings_dialog.py) if it has one,
        otherwise the shared Configure tab values plus the
        hardcoded 0x700 functional default (Functional Request CAN
        ID has no global UI field of its own — only per-panel
        overrides exist).
        """

        if panel["comm_settings"]:
            return (
                panel["comm_settings"]["tx_id"],
                panel["comm_settings"]["rx_id"],
                panel["comm_settings"]["functional_id"],
            )
        return (
            can_config.get("tx_id", 0x778),
            can_config.get("rx_id", 0x788),
            0x700,
        )

    def _open_parallel_channel_settings(self, panel):

        can_config = (
            self.get_can_config() if hasattr(self, 'get_can_config') else {}
        )
        tx_id, rx_id, functional_id = self._resolve_panel_comm_ids(
            panel, can_config
        )

        dialog = ParallelChannelSettingsDialog(
            self,
            f"Channel {panel['index'] + 1}",
            tx_id,
            rx_id,
            functional_id,
            bool(panel["comm_settings"]),
        )
        dialog.exec()

        if not dialog.result():
            return

        if dialog.reset_requested():
            panel["comm_settings"] = None
        else:
            tx_id, rx_id, functional_id = dialog.result_values()
            panel["comm_settings"] = {
                "tx_id": tx_id,
                "rx_id": rx_id,
                "functional_id": functional_id,
            }

    def _view_parallel_panel_log(self, index):
        """
        Makes panel `index` the active one for the shared
        Information/Trace tabs (outputTabWidget) — clears them and
        replays this panel's own buffered history into them, then
        appends a "Now viewing: Channel N" marker as the last line of
        each, so it's clear at a glance which channel's data is on
        screen. The marker is written straight to the widgets, not
        through _log_parallel_panel()/_on_panel_trace_message() — it
        must NOT land in the panel's own info_lines/trace_entries
        buffer, or every future replay would re-print every past
        marker along with it. Live messages from OTHER panels keep
        buffering silently in the background; only this panel's
        future messages will also be written live from here on, until
        a different panel's "View Log" is triggered (see this
        module's docstring).
        """

        self._parallel_active_panel_index = index
        panel = self._parallel_panels[index]
        channel_label = f"Channel {panel['index'] + 1}"

        if hasattr(self.ui, 'informationText'):
            self.ui.informationText.clear()
            for line in panel["info_lines"]:
                self._append_information_line(line)
            timestamp = datetime.now().strftime("%H:%M:%S")
            self._append_information_line(
                f"[{timestamp}] Now viewing: {channel_label}"
            )

        if hasattr(self.ui, 'traceTable'):
            self.ui.traceTable.setRowCount(0)
            for entry in panel["trace_entries"]:
                if entry[0] == "system":
                    _, timestamp, message = entry
                    self._add_trace_row(
                        timestamp, "SYSTEM", message, "", "", ""
                    )
                else:
                    _, row = entry
                    self.log_trace_row(row)
            timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            self._add_trace_row(
                timestamp, "SYSTEM",
                f"Now viewing: {channel_label}", "", "", "",
            )

    # ==================================================
    # Test Connection (per panel)
    #
    # Reuses TestConnectionDialog as-is (gui/menu_bar.py's Tools >
    # Test Connection... uses the exact same class) — it's a
    # self-contained modal dialog that builds its OWN QThread/
    # TestConnectionWorker internally, so no per-panel thread-
    # lifecycle bookkeeping is needed here. The only thing this
    # method does that open_test_connection_dialog() doesn't is
    # resolve THIS panel's own hardware channel/comm ID overrides
    # instead of the global Configure tab ones — same resolution
    # _start_identify_for_panel() already uses.
    # ==================================================

    def _test_connection_for_panel(self, panel):

        data = panel["combo"].currentData()
        use_virtual = data is None
        channel = 0
        serial_hw = None
        label = None
        if data not in (None, "not-selected"):
            channel = data.get("hw_channel", data.get("channel", 0))
            serial_hw = data.get("serial")
            label = data.get("label")

        if not use_virtual and hasattr(self, 'detect_can_conflict_warning'):
            warning = self.detect_can_conflict_warning()
            if warning:
                choice = QMessageBox.warning(
                    self,
                    "Possible CAN Bus Conflict",
                    warning + "\n\nContinue with Test Connection anyway?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if choice != QMessageBox.Yes:
                    return

        can_config_global = (
            self.get_can_config() if hasattr(self, 'get_can_config') else {}
        )
        tx_id, rx_id, functional_id = self._resolve_panel_comm_ids(
            panel, can_config_global
        )
        use_suzuki_sequence = False
        if hasattr(self.ui, 'comboBoxFlashSequence'):
            use_suzuki_sequence = (
                "Suzuki" in self.ui.comboBoxFlashSequence.currentText()
            )
        security_dll_path = getattr(
            self, '_security_dll_path', ''
        ) or None

        can_config = {
            "channel": channel,
            "serial": serial_hw,
            "label": label,
            "tx_id": tx_id,
            "rx_id": rx_id,
            "bitrate": can_config_global.get("bitrate", 500000),
            "fd": can_config_global.get("fd", False),
            "data_bitrate": can_config_global.get("data_bitrate", 2000000),
        }

        dialog = TestConnectionDialog(
            self, use_virtual, security_dll_path,
            use_suzuki_sequence, can_config,
        )
        dialog.exec()

        if dialog.passed is None:
            # Declined the CAN conflict warning, or closed the
            # dialog before the probe finished — no result to show,
            # leave whatever color was already there (same as
            # gui/configure_tab.py's test_connection_button_clicked()).
            return
        self._log_parallel_panel(
            panel,
            "Test Connection: PASS." if dialog.passed
            else "Test Connection: FAIL.",
        )
        self._apply_test_connection_button_style(
            panel, "done" if dialog.passed else "error"
        )

    # ==================================================
    # Save Report (HTML, per panel)
    #
    # Same shape as gui/report_export.py's Export Report... (Tools
    # menu, Single Flash/Batch Flash) — a self-contained HTML
    # snapshot — but built from THIS panel's own buffered
    # info_lines/trace_entries instead of the shared widgets, since
    # those only ever hold whichever channel is currently active
    # (see _view_parallel_panel_log()). Datablocks are shared
    # firmware across all channels, so that section reuses
    # ReportExportMixin._report_datablocks_table() unchanged.
    # ==================================================

    def _save_parallel_panel_report(self, panel):

        default_name = (
            f"flash_report_channel{panel['index'] + 1}_"
            + datetime.now().strftime("%Y%m%d_%H%M%S")
            + ".html"
        )

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Flash Report",
            default_name,
            "HTML Files (*.html);;All Files (*)",
        )

        if not file_path:
            return

        self._write_parallel_panel_report_file(panel, file_path)

    def _write_parallel_panel_report_file(self, panel, file_path):

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(self._build_parallel_panel_report_html(panel))

        except OSError as e:
            QMessageBox.critical(
                self, "Export Report Failed", str(e)
            )
            return

        self._log_parallel_panel(
            panel, f"Report exported to {file_path}"
        )

    def _build_parallel_panel_report_html(self, panel):

        e = html.escape
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        channel_label = f"Channel {panel['index'] + 1}"

        return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{e(APP_NAME)} Parallel Flash Report — {e(channel_label)} — {e(now)}</title>
<style>{self._report_html_style()}</style>
</head>
<body>
<h1>{e(APP_NAME)} v{e(APP_VERSION)} — Parallel Flash Report — {e(channel_label)}</h1>
<div class="subtitle">Exported {e(now)}</div>

<h2>Summary</h2>
{self._parallel_panel_report_summary_table(panel)}

<h2>Datablocks</h2>
{self._report_datablocks_table()}

<h2>Trace</h2>
{self._report_trace_table(self._panel_trace_rows(panel))}

<h2>Information Log</h2>
<pre>{e(chr(10).join(panel["info_lines"])) or "No information recorded."}</pre>

</body>
</html>
"""

    def _parallel_panel_report_summary_table(self, panel):

        e = html.escape

        data = panel["combo"].currentData()
        if data is None:
            hardware = "Virtual ECU Simulator (No Hardware)"
        elif data == "not-selected":
            hardware = "Not Selected"
        else:
            hardware = data.get("label", "Vector Hardware")

        radar_side = "N/A"
        if hasattr(self.ui, 'comboBoxRadarSide'):
            radar_side = self.ui.comboBoxRadarSide.currentText()

        sequence = "N/A"
        if hasattr(self.ui, 'comboBoxFlashSequence'):
            sequence = self.ui.comboBoxFlashSequence.currentText()

        security_dll = "Built-in algorithm"
        if hasattr(self.ui, 'lineEditSecurityDll'):
            security_dll = (
                self.ui.lineEditSecurityDll.text()
                or "Built-in algorithm"
            )

        can_config_global = (
            self.get_can_config() if hasattr(self, 'get_can_config') else {}
        )
        tx_id, rx_id, functional_id = self._resolve_panel_comm_ids(
            panel, can_config_global
        )
        comm_ids = (
            f"Tx=0x{tx_id:X} Rx=0x{rx_id:X} Functional=0x{functional_id:X}"
            + (
                " (channel override)" if panel["comm_settings"]
                else " (shared/global)"
            )
        )

        rows = [
            ("Channel", f"Channel {panel['index'] + 1}"),
            ("Hardware", hardware),
            ("Basic Communication", comm_ids),
            ("Serial Number", panel["serial"] or "N/A"),
            ("Radar Side", radar_side),
            ("Flash Sequence", sequence),
            ("Security Access DLL", security_dll),
            ("Result", panel["_status_text"]),
        ]

        body = "".join(
            f"<tr><td>{e(k)}</td><td>{e(v)}</td></tr>" for k, v in rows
        )

        return f'<table class="summary">{body}</table>'

    def _panel_trace_rows(self, panel):
        """
        Converts a panel's buffered trace_entries into plain row
        lists — the same shape gui/main_window.py's _add_trace_row()
        takes — so ReportExportMixin._report_trace_table() can
        render them without ever touching the shared traceTable
        widget.
        """

        rows = []
        for entry in panel["trace_entries"]:
            if entry[0] == "system":
                _, timestamp, message = entry
                rows.append([timestamp, "SYSTEM", message, "", "", ""])
            else:
                _, row = entry
                rows.append(list(self._format_trace_row_cells(row)))
        return rows

    # ==================================================
    # Dynamic per-panel coloring (theme-aware)
    #
    # The status dot, Flash/Abort button, progress bar and Test
    # Connection button are all built at runtime (6 of each, no
    # unique object name per instance), so — unlike #flashButton/
    # #buttonStopBatch/#buttonExportBatchReport — they can't be
    # colored with a static QSS #id rule. Each panel's current
    # "kind" is stashed (_button_kind/_progress_kind/
    # _test_connection_kind/_dot_kind) so a later Dark Mode toggle
    # can re-derive the right color instead of the widget staying
    # stuck in whichever theme was active when it was last colored —
    # the exact same staleness bug already fixed for stepsTable/
    # segmentsTable/the Batch Log table (see docs/walkthrough.md
    # Phase 4.90/4.91).
    # ==================================================

    def _apply_panel_button_style(self, panel, kind):
        pairs = (
            _BUTTON_COLOR_PAIRS_DARK
            if getattr(self, '_dark_mode_active', False)
            else _BUTTON_COLOR_PAIRS
        )
        bg, hover = pairs[kind]
        disabled_bg, disabled_fg, disabled_border = (
            (DISABLED_BUTTON_BG_DARK, DISABLED_BUTTON_FG_DARK,
             DISABLED_BUTTON_BORDER_DARK)
            if getattr(self, '_dark_mode_active', False)
            else (DISABLED_BUTTON_BG, DISABLED_BUTTON_FG,
                  DISABLED_BUTTON_BORDER)
        )
        panel["flash_button"].setStyleSheet(
            "QPushButton {"
            f" background-color: {bg}; color: white; border: none;"
            " border-radius: 6px; padding: 6px 14px; font-weight: 600;"
            " }"
            f"QPushButton:hover:!disabled {{ background-color: {hover}; }}"
            "QPushButton:disabled {"
            f" background-color: {disabled_bg}; color: {disabled_fg};"
            f" border: 1px solid {disabled_border}; }}"
        )
        panel["_button_kind"] = kind

    def _apply_panel_progress_style(self, panel, kind):
        if kind is None:
            # No override — falls back to the app-wide themed
            # QProgressBar::chunk rule (already blue in both
            # resources/style.qss and style_dark.qss).
            panel["progress_bar"].setStyleSheet("")
        else:
            colors = (
                _PROGRESS_COLORS_DARK
                if getattr(self, '_dark_mode_active', False)
                else _PROGRESS_COLORS
            )
            panel["progress_bar"].setStyleSheet(
                "QProgressBar::chunk {"
                f" background-color: {colors[kind]}; border-radius: 6px;"
                " }"
            )
        panel["_progress_kind"] = kind

    def _apply_test_connection_button_style(self, panel, kind):
        """
        Colors the Test Connection button green/red on a definitive
        Pass/Fail result, reusing gui/flash_tab.py's shared
        _status_colors("done"/"error") — the same palette already
        used for the Configure tab's own Test Connection button
        (buttonTestConnectionHardware) and Batch Log rows, so a
        result looks identical everywhere in the app. `kind=None`
        clears back to the plain pill look (falls back to the
        static #buttonParallelTestConnection QSS rule).
        """

        panel["_test_connection_kind"] = kind

        if kind is None:
            panel["test_connection_button"].setStyleSheet("")
            return

        bg, fg = self._status_colors(kind)
        panel["test_connection_button"].setStyleSheet(
            "QPushButton {"
            f" background-color: {bg}; color: {fg}; border: none;"
            " border-radius: 10px; font-size: 11px; padding: 4px 10px;"
            " font-weight: 600;"
            " }"
        )

    def _apply_panel_status_dot_style(self, panel, kind):
        colors = (
            _DOT_COLORS_DARK
            if getattr(self, '_dark_mode_active', False)
            else _DOT_COLORS
        )
        panel["status_dot"].setStyleSheet(
            f"color: {colors[kind]}; font-size: 14px;"
        )
        panel["_dot_kind"] = kind

    def _recolor_parallel_panels(self):
        for panel in getattr(self, '_parallel_panels', []):
            self._apply_panel_button_style(
                panel, panel.get("_button_kind", "accent")
            )
            self._apply_panel_progress_style(
                panel, panel.get("_progress_kind")
            )
            self._apply_test_connection_button_style(
                panel, panel.get("_test_connection_kind")
            )
            self._apply_panel_status_dot_style(
                panel, panel.get("_dot_kind", "idle")
            )

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

    def _set_panel_status_text(self, panel, text):
        """
        Renders `text` into the status label, prefixed with this
        panel's Serial Number once known ("SN: X · text") — a single
        combined line instead of a separate SN row/title, per the
        approved compact card mockup. The raw `text` (without the SN
        prefix) is kept in panel["_status_text"] too, since the Save
        Report "Result" field would otherwise redundantly repeat the
        SN it already shows in its own row.
        """

        panel["_status_text"] = text
        if panel["serial"]:
            panel["status_label"].setText(f"SN: {panel['serial']} · {text}")
        else:
            panel["status_label"].setText(text)

    def _on_parallel_channel_changed(self, panel):

        if panel["phase"] in ("identifying", "flashing"):
            return

        selected = panel["combo"].currentData() != "not-selected"
        panel["flash_button"].setEnabled(selected)
        panel["test_connection_button"].setEnabled(selected)
        # A stale green/red from a previous run would otherwise keep
        # showing after picking a different channel — clear it, same
        # reasoning as gui/configure_tab.py's own Test Connection
        # button reset on comboBoxHardware change.
        self._apply_test_connection_button_style(panel, None)
        self._apply_panel_status_dot_style(panel, "idle")
        # Same reasoning for a stale Serial Number — it belongs to
        # whichever channel was previously selected, not this new one.
        panel["serial"] = None
        if panel["phase"] not in ("pass", "fail", "abort"):
            self._set_panel_status_text(
                panel,
                "Idle — ready to flash." if selected
                else "No channel selected.",
            )

    def _on_parallel_flash_clicked(self, panel):

        if panel["phase"] in ("identifying", "flashing"):
            self._abort_panel(panel)
            return

        self._start_identify_for_panel(panel)

    def _start_identify_for_panel(self, panel):

        panel["phase"] = "identifying"
        panel["serial"] = None
        panel["flash_button"].setText("Abort")
        panel["combo"].setEnabled(False)
        panel["action_settings"].setEnabled(False)
        panel["test_connection_button"].setEnabled(False)
        self._set_panel_status_text(
            panel, "Identifying ECU — reading Serial Number (DID 0xF18C)..."
        )
        self._apply_panel_button_style(panel, "danger")
        self._apply_panel_progress_style(panel, None)
        self._apply_panel_status_dot_style(panel, "busy")
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

        tx_id, rx_id, functional_id = self._resolve_panel_comm_ids(
            panel, can_config
        )

        panel["identify_thread"] = QThread()
        panel["identify_worker"] = TestConnectionWorker(
            use_virtual=use_virtual,
            security_dll_path=security_dll_path,
            functional=use_suzuki_sequence,
            can_channel=channel,
            can_serial=serial_hw,
            can_tx_id=tx_id,
            can_rx_id=rx_id,
            can_bitrate=can_config.get("bitrate", 500000),
            can_fd=can_config.get("fd", False),
            can_data_bitrate=can_config.get("data_bitrate", 2000000),
            functional_id=functional_id,
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
        self._update_parallel_abort_all_state()

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
            self._set_panel_status_text(panel, "No ECU detected on the bus.")
            self._log_parallel_panel(
                panel, "Identify: no ECU detected on the bus."
            )
            panel["flash_button"].setText("Flash")
            panel["combo"].setEnabled(True)
            panel["action_settings"].setEnabled(True)
            panel["test_connection_button"].setEnabled(True)
            self._apply_panel_button_style(panel, "accent")
            self._apply_panel_progress_style(panel, "danger")
            self._apply_panel_status_dot_style(panel, "danger")
            self._update_parallel_abort_all_state()
            return

        ecu_info = getattr(self, '_parallel_last_ecu_info', {}).get(
            panel["index"], {}
        )
        serial = ecu_info.get("ECU Serial Number", "UNKNOWN")
        panel["serial"] = serial
        self._log_parallel_panel(
            panel, f"Identify: Serial Number = {serial}."
        )
        self._start_flash_for_panel(panel, serial)

    def _reset_panel_to_idle(self, panel):
        panel["phase"] = "idle"
        panel["flash_button"].setText("Flash")
        panel["combo"].setEnabled(True)
        panel["action_settings"].setEnabled(True)
        panel["test_connection_button"].setEnabled(True)
        self._set_panel_status_text(panel, "Idle — ready to flash.")
        self._apply_panel_button_style(panel, "accent")
        self._apply_panel_progress_style(panel, None)
        self._apply_panel_status_dot_style(panel, "idle")
        self._update_parallel_abort_all_state()

    def _log_parallel_panel(self, panel, message):
        """
        Buffers an information-style message for `panel` (its own
        internal narrative log, e.g. "Identify: Serial Number =
        ..."), and — only if this panel is the one currently shown
        in the shared Information tab — writes it there live too.
        """

        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}"
        panel["info_lines"].append(line)

        if self._parallel_active_panel_index == panel["index"]:
            self._append_information_line(line)

    def _on_panel_trace_message(self, panel, message):
        """
        Buffers a narrative trace-log line (FlashWorker.trace_message
        — "Executing: ...", errors) as a SYSTEM row for `panel`,
        live-writing to the shared Trace tab only while this panel
        is the active one — same reasoning as _log_parallel_panel().
        """

        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        panel["trace_entries"].append(("system", timestamp, message))

        if self._parallel_active_panel_index == panel["index"]:
            self._add_trace_row(timestamp, "SYSTEM", message, "", "", "")

    def _on_panel_trace_row(self, panel, row):
        """
        Buffers a structured UDS request/response row
        (FlashWorker.trace_row) for `panel`, live-writing to the
        shared Trace tab only while this panel is the active one.
        """

        panel["trace_entries"].append(("row", row))

        if self._parallel_active_panel_index == panel["index"]:
            self.log_trace_row(row)

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
        self._set_panel_status_text(panel, "Flashing...")

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

        tx_id, rx_id, functional_id = self._resolve_panel_comm_ids(
            panel, can_config
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
            can_tx_id=tx_id,
            can_rx_id=rx_id,
            can_bitrate=can_config.get("bitrate", 500000),
            can_fd=can_config.get("fd", False),
            can_data_bitrate=can_config.get("data_bitrate", 2000000),
            functional_id=functional_id,
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
            router.on_information_message
        )
        panel["flash_worker"].trace_message.connect(
            router.on_trace_message
        )
        panel["flash_worker"].trace_row.connect(
            router.on_trace_row
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
        self._set_panel_status_text(panel, desc)

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
        panel["action_settings"].setEnabled(True)
        panel["test_connection_button"].setEnabled(True)
        self._set_panel_status_text(panel, "PASS.")
        self._log_parallel_panel(panel, "Flash completed successfully.")
        self._apply_panel_button_style(panel, "accent")
        self._apply_panel_progress_style(panel, "success")
        self._apply_panel_status_dot_style(panel, "success")
        self._update_parallel_abort_all_state()

    def _on_panel_flash_aborted(self, panel):

        if panel["stopping"]:
            panel["stopping"] = False
            self._reset_panel_to_idle(panel)
            return

        panel["phase"] = "fail"
        panel["flash_button"].setText("Flash")
        panel["combo"].setEnabled(True)
        panel["action_settings"].setEnabled(True)
        panel["test_connection_button"].setEnabled(True)
        self._set_panel_status_text(panel, "FAIL / ABORTED.")
        self._log_parallel_panel(panel, "Flash aborted.")
        self._apply_panel_button_style(panel, "accent")
        self._apply_panel_progress_style(panel, "danger")
        self._apply_panel_status_dot_style(panel, "danger")
        self._update_parallel_abort_all_state()

    def _abort_panel(self, panel):

        if panel["phase"] == "flashing" and panel["flash_thread"] is not None:
            panel["stopping"] = True
            panel["flash_worker"].request_abort()
            panel["flash_thread"].quit()
            panel["flash_thread"].wait()
            return

        if (panel["phase"] == "identifying"
                and panel["identify_thread"] is not None):
            panel["stopping"] = True
            panel["identify_thread"].quit()
            panel["identify_thread"].wait()
            return

    def parallel_start_all(self):

        for panel in self._parallel_panels:
            if panel["phase"] in ("identifying", "flashing"):
                continue
            if panel["combo"].currentData() == "not-selected":
                continue
            self._start_identify_for_panel(panel)

    def parallel_abort_all(self):

        for panel in self._parallel_panels:
            if panel["phase"] in ("identifying", "flashing"):
                self._abort_panel(panel)

    def _update_parallel_abort_all_state(self):
        if not hasattr(self.ui, 'buttonParallelAbortAll'):
            return
        any_busy = any(
            p["phase"] in ("identifying", "flashing")
            for p in self._parallel_panels
        )
        self.ui.buttonParallelAbortAll.setEnabled(any_busy)

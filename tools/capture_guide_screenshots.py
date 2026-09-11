# ==================================================
# User Guide Screenshot Capture
# ==================================================
#
# Regenerates every screenshot in docs/user_guide.html by driving the
# real app headlessly, then (with --embed) writes them back into the
# guide. Run it whenever the UI changes so the guide's pictures stop
# disagreeing with the app.
#
#   python tools/capture_guide_screenshots.py            # capture only
#   python tools/capture_guide_screenshots.py --embed    # capture + update the guide
#
# Each shot is matched to its <img> in the guide by a data-shot="..."
# attribute, not by position, so reordering the guide cannot mis-file a
# picture. A shot whose data-shot attribute is missing from the guide is
# reported rather than silently dropped.
#
# Four things here are deliberate and easy to get wrong (all four have
# been shipped wrong before — see docs/walkthrough.md Phase 4.110-4.112):
#
#   1. The theme is applied by main.py, NOT by MainWindow. Without
#      app.setStyleSheet() every shot comes out in bare native widgets
#      instead of the app's real look.
#   2. pump_until() uses a LOCAL QEventLoop. app.quit() also closes every
#      window, and MainWindow.closeEvent() aborts a running flash, which
#      silently turns "captured mid-flash" into an aborted run.
#   3. A menu or dropdown is its own top-level widget: window.grab()
#      never contains it. Popups are grabbed separately and painted on.
#   4. States are reached by running the real thing (real firmware, real
#      flashes), never by poking widgets into a plausible-looking state —
#      otherwise the surrounding tables still show empty placeholders.
# ==================================================

import argparse
import base64
import os
import re
import sys
import time
import unittest.mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

# Must be set before QApplication is created.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile

from PySide6.QtCore import QEventLoop, QPoint, QRect, QSettings, QTimer, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QApplication

# Keep the capture run out of the real user profile (theme, recent
# files, saved parallel-channel selections).
QSettings.setPath(
    QSettings.IniFormat, QSettings.UserScope,
    tempfile.mkdtemp(prefix="sflash_guide_shots_"),
)

from communication.virtual_can import VirtualCanInterface
from gui.main_window import MainWindow
from gui.style import is_dark_mode_enabled, load_stylesheet
from gui.test_connection_dialog import TestConnectionDialog

SAMPLE_HEX = os.path.join(REPO, "tests", "sample.hex")
GUIDE = os.path.join(REPO, "docs", "user_guide.html")
DEFAULT_OUT = os.path.join(REPO, "docs", "guide_images")

WINDOW_SIZE = (1100, 850)
ACCENT = "#2b579a"

# Every shot this script produces, in guide order. The name is the
# data-shot attribute on the matching <img> in docs/user_guide.html.
SHOT_NAMES = (
    "flash-step-1", "flash-step-2", "flash-step-3", "flash-step-4",
    "batch-step-1", "batch-step-2", "batch-step-3", "batch-step-4",
    "parallel-step-1", "parallel-step-2", "parallel-step-3",
    "parallel-step-4",
)


# ==================================================
# Helpers
# ==================================================

class Capturer:

    def __init__(self, out_dir, only=None):
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.app.setStyleSheet(
            load_stylesheet(dark=is_dark_mode_enabled())
        )
        self.out_dir = out_dir
        self.only = set(only) if only else None
        self.saved = []
        os.makedirs(out_dir, exist_ok=True)

        self.w = MainWindow()
        self.w.resize(*WINDOW_SIZE)
        self.w.show()
        self.app.processEvents()

    # --------------------------------------------------
    # Event loop
    # --------------------------------------------------

    def pump_until(self, predicate, timeout_ms=60000, interval_ms=10):
        """Run the event loop until predicate() is true.

        Uses a local QEventLoop on purpose: app.quit() would close every
        window, and MainWindow.closeEvent() aborts a running flash.
        """

        loop = QEventLoop()
        state = {"elapsed": 0, "ok": False}

        def tick():
            if predicate():
                state["ok"] = True
                loop.quit()
            elif state["elapsed"] >= timeout_ms:
                loop.quit()
            else:
                state["elapsed"] += interval_ms

        timer = QTimer()
        timer.setInterval(interval_ms)
        timer.timeout.connect(tick)
        timer.start()
        loop.exec()
        timer.stop()
        return state["ok"]

    def pump_ms(self, ms):
        self.pump_until(lambda: False, timeout_ms=ms)

    def settle(self):
        self.app.processEvents()

    # --------------------------------------------------
    # Geometry
    # --------------------------------------------------

    def rect_of(self, widget):
        """A widget's rectangle in main-window coordinates."""

        return QRect(widget.mapTo(self.w, QPoint(0, 0)), widget.size())

    def popup_offset(self, popup):
        """Where a popup sits inside the main window's screenshot."""

        gp = popup.mapToGlobal(QPoint(0, 0))
        wp = self.w.mapToGlobal(QPoint(0, 0))
        return QPoint(gp.x() - wp.x(), gp.y() - wp.y())

    def popup_rect(self, popup):
        return QRect(self.popup_offset(popup), popup.size())

    # --------------------------------------------------
    # Capture
    # --------------------------------------------------

    def shoot(self, name, targets=(), popups=()):
        """Grab the window, paint popups over it, outline the targets.

        `targets` are the widgets (or QRects) the guide step is telling
        the reader to look at.
        """

        assert name in SHOT_NAMES, f"unknown shot name: {name}"
        if self.only is not None and name not in self.only:
            return

        pix = self.w.grab()
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.Antialiasing)

        for popup in popups:
            off = self.popup_offset(popup)
            painter.drawPixmap(off.x(), off.y(), popup.grab())

        for target in targets:
            rect = target if isinstance(target, QRect) else self.rect_of(target)
            rect = rect.adjusted(-5, -5, 5, 5)
            painter.setBrush(Qt.NoBrush)
            # White halo first, so the outline stays visible on dark fills.
            painter.setPen(QPen(QColor(255, 255, 255, 210), 7))
            painter.drawRoundedRect(rect, 9, 9)
            painter.setPen(QPen(QColor(ACCENT), 3))
            painter.drawRoundedRect(rect, 9, 9)
        painter.end()

        path = os.path.join(self.out_dir, f"{name}.png")
        pix.save(path)
        self.saved.append(name)
        print(f"  captured {name}")

    # --------------------------------------------------
    # Shared setup
    # --------------------------------------------------

    def load_firmware(self):
        """Load tests/sample.hex through the real UI path, so the
        Datablocks table and Details really fill in."""

        with unittest.mock.patch(
            "gui.configure_tab.QFileDialog.getOpenFileNames",
            return_value=([SAMPLE_HEX], ""),
        ):
            self.w.add_new_datablock()
        self.settle()
        assert self.w._loaded_datablocks, "firmware did not load"

    def run_test_connection(self, button, silent_ecu=False):
        """Click a Test Connection button and let the real probe finish.

        The dialog is modal, so exec() is replaced by an event-loop pump
        (never a lambda on a cross-thread signal). With silent_ecu the
        virtual bus answers nothing, which is how a real "ECU not
        responding" failure looks — Test Connection does session + DID
        reads only, so a bad security key would NOT fail it.
        """

        def pumped_exec(dialog):
            deadline = time.time() + 15
            while dialog._thread is not None and dialog._thread.isRunning():
                self.app.processEvents()
                time.sleep(0.01)
                if time.time() > deadline:
                    print("  !! Test Connection dialog timed out")
                    break
            self.app.processEvents()
            dialog.close()
            return 0

        with unittest.mock.patch.object(
            TestConnectionDialog, "exec", pumped_exec
        ):
            if silent_ecu:
                with unittest.mock.patch.object(
                    VirtualCanInterface, "receive_isotp",
                    lambda self, timeout=1.0: None,
                ):
                    button.click()
            else:
                button.click()
        self.settle()


# ==================================================
# Flash Mode — steps 1..4
# ==================================================

def capture_flash_mode(c):
    w = c.w

    w.ui.tabWidget.setCurrentWidget(w.ui.configureTab)
    w.ui.navListWidget.setCurrentRow(0)  # Data
    c.settle()
    c.load_firmware()
    c.shoot("flash-step-1", targets=[w.ui.tableWidgetDatablocks])

    w.ui.navListWidget.setCurrentRow(1)  # Communication
    c.settle()
    c.shoot("flash-step-2", targets=[w.ui.comboBoxHardware])

    w.ui.tabWidget.setCurrentWidget(w.ui.flashTab)
    c.settle()
    w.ui.flashButton.click()
    assert c.pump_until(
        lambda: w.ui.stepsTable.rowCount() >= 6
        and w.thread is not None and w.thread.isRunning()
    ), "flash never reached mid-run"
    c.shoot("flash-step-3", targets=[w.ui.flashButton])

    assert c.pump_until(
        lambda: w.thread is None and w.worker is None
    ), "flash never finished"
    w.ui.stepsTable.scrollToTop()
    w.ui.segmentsTable.scrollToTop()
    c.settle()
    c.shoot("flash-step-4",
            targets=[w.ui.stepsTable, w.ui.segmentsTable])


# ==================================================
# Batch Flash — steps 1..4
# ==================================================

def capture_batch_mode(c):
    w = c.w

    # Tools > Mode > Batch Flash, both menus open over the window.
    bar = w.menuBar()
    tools_rect = bar.actionGeometry(w.ui.menuTools.menuAction())
    w.ui.menuTools.popup(bar.mapToGlobal(tools_rect.bottomLeft()))
    c.settle()
    mode_rect = w.ui.menuTools.actionGeometry(w.ui.menuMode.menuAction())
    w.ui.menuMode.popup(
        w.ui.menuTools.mapToGlobal(mode_rect.topRight()) + QPoint(-4, 0)
    )
    c.settle()
    item = w.ui.menuMode.actionGeometry(w.ui.actionModeBatchFlash)
    c.shoot("batch-step-1",
            popups=[w.ui.menuTools, w.ui.menuMode],
            targets=[item.translated(c.popup_offset(w.ui.menuMode))])
    w.ui.menuMode.close()
    w.ui.menuTools.close()
    c.settle()

    w.ui.actionModeBatchFlash.setChecked(True)
    c.settle()

    # Unit 1 — mid-run.
    w.ui.flashButton.click()
    assert c.pump_until(
        lambda: w.ui.stepsTable.rowCount() >= 6
        and w.thread is not None and w.thread.isRunning()
    ), "batch unit 1 never reached mid-flash"
    c.shoot("batch-step-2", targets=[w.ui.flashButton])

    # Unit 1 done — button reads Next, first row logged.
    assert c.pump_until(
        lambda: len(w._batch_records) >= 1
    ), "batch unit 1 never finished"
    w.ui.stepsTable.scrollToTop()
    c.settle()
    c.shoot("batch-step-3",
            targets=[w.ui.flashButton, w.ui.tableWidgetBatchLog])

    # A couple more units (one aborted for a second result color), then
    # the finished session.
    w.ui.flashButton.click()
    assert c.pump_until(
        lambda: w.thread is not None and w.thread.isRunning()
    ), "batch unit 2 never started"
    w.ui.flashButton.click()  # abort this one
    assert c.pump_until(
        lambda: len(w._batch_records) >= 2
    ), "batch unit 2 never settled"
    w.ui.flashButton.click()
    assert c.pump_until(
        lambda: len(w._batch_records) >= 3
    ), "batch unit 3 never finished"
    w.stop_batch()
    assert c.pump_until(
        lambda: not w.ui.buttonStopBatch.isEnabled()
    ), "batch never stopped"
    w.ui.stepsTable.scrollToTop()
    c.settle()
    c.shoot("batch-step-4",
            targets=[w.ui.buttonStopBatch, w.ui.buttonExportBatchReport])

    w.ui.actionModeFlash.setChecked(True)
    c.settle()


# ==================================================
# Parallel Flash — steps 1..4
# ==================================================

def capture_parallel_mode(c):
    w = c.w

    w.ui.tabWidget.setCurrentWidget(w.ui.parallelFlashTab)
    # Start the section with a clean log, so Batch Flash's lines don't
    # linger in the Parallel screenshots.
    w.ui.actionClearInformationLog.trigger()
    w.ui.actionClearTrace.trigger()
    c.settle()
    panels = w._parallel_panels

    # Hardware dropdown open on Channel 1.
    panels[0]["combo"].showPopup()
    c.settle()
    popup = QApplication.activePopupWidget()
    c.shoot("parallel-step-1",
            popups=[popup] if popup else [],
            targets=[c.popup_rect(popup)] if popup else [])
    panels[0]["combo"].hidePopup()
    c.settle()

    for i in range(4):
        panels[i]["combo"].setCurrentIndex(1)  # Virtual ECU Simulator
    c.settle()

    # One real pass, one real failure (an ECU that never answers).
    c.run_test_connection(panels[0]["test_connection_button"])
    c.run_test_connection(panels[1]["test_connection_button"],
                          silent_ecu=True)
    c.shoot("parallel-step-2",
            targets=[panels[0]["test_connection_button"],
                     panels[1]["test_connection_button"]])

    # The operator would fix the wiring and re-check; without this,
    # channel 2 keeps a red pill beside a PASS in the later shots.
    c.run_test_connection(panels[1]["test_connection_button"])

    # Several channels flashing at once, started a beat apart so their
    # progress bars sit at genuinely different points.
    for i in range(4):
        panels[i]["flash_button"].click()
        c.pump_ms(160)

    def busy_count():
        return sum(1 for i in range(4)
                   if panels[i]["phase"] in ("identifying", "flashing"))

    c.pump_until(
        lambda: busy_count() >= 3
        and max(panels[i]["progress_bar"].value() for i in range(4)) >= 25,
        timeout_ms=8000,
    )
    c.shoot("parallel-step-3",
            targets=[w.ui.buttonParallelStartAll, w.ui.buttonParallelAbortAll])

    assert c.pump_until(
        lambda: all(panels[i]["phase"] in ("pass", "fail") for i in range(4))
    ), "parallel channels never settled"

    # The per-channel "..." menu over a finished board.
    panels[0]["action_view_log"].trigger()
    c.settle()
    panels[0]["menu_button"].click()
    c.settle()
    menu = QApplication.activePopupWidget()
    targets = [panels[0]["menu_button"]]
    if menu is not None:
        targets.append(c.popup_rect(menu))
    c.shoot("parallel-step-4",
            popups=[menu] if menu else [], targets=targets)
    if menu is not None:
        menu.close()
    c.settle()


# ==================================================
# Embedding into docs/user_guide.html
# ==================================================

IMG_RE = re.compile(r'<img\s+src="data:image/png;base64,[A-Za-z0-9+/=]+"'
                    r'([^>]*)>')


def embed(out_dir, names):
    """Replace each guide <img data-shot="NAME"> with the captured PNG.

    Matching is by the data-shot attribute, never by position, so a
    reordered guide cannot get its pictures crossed.
    """

    html = open(GUIDE, encoding="utf-8").read()
    replaced, missing = [], []

    for name in names:
        png = os.path.join(out_dir, f"{name}.png")
        if not os.path.isfile(png):
            missing.append(f"{name} (no captured file)")
            continue

        marker = f'data-shot="{name}"'
        hits = [m for m in IMG_RE.finditer(html) if marker in m.group(1)]
        if len(hits) != 1:
            missing.append(f"{name} ({len(hits)} matching <img> in the guide)")
            continue

        m = hits[0]
        b64 = base64.b64encode(open(png, "rb").read()).decode("ascii")
        new = f'<img src="data:image/png;base64,{b64}"{m.group(1)}>'
        html = html[:m.start()] + new + html[m.end():]
        replaced.append(name)

    if replaced:
        open(GUIDE, "w", encoding="utf-8").write(html)

    print(f"\nembedded {len(replaced)}/{len(names)} into docs/user_guide.html")
    for problem in missing:
        print(f"  !! not embedded: {problem}")
    return not missing


# ==================================================
# Entry point
# ==================================================

def main():
    parser = argparse.ArgumentParser(
        description="Capture the screenshots used by docs/user_guide.html.",
    )
    parser.add_argument(
        "--embed", action="store_true",
        help="also write the captured images into docs/user_guide.html",
    )
    parser.add_argument(
        "--out", default=DEFAULT_OUT,
        help=f"where to write the PNGs (default: {DEFAULT_OUT})",
    )
    parser.add_argument(
        "--only", nargs="+", metavar="SHOT", choices=SHOT_NAMES,
        help="save only these shots (the whole flow still runs, since "
             "later states depend on earlier ones)",
    )
    args = parser.parse_args()

    print(f"capturing into {args.out}")
    c = Capturer(args.out, only=args.only)
    capture_flash_mode(c)
    capture_batch_mode(c)
    capture_parallel_mode(c)
    c.w.close()
    c.settle()

    print(f"\ncaptured {len(c.saved)} shot(s)")
    ok = True
    if args.embed:
        ok = embed(args.out, c.saved)
    else:
        print("(run again with --embed to update docs/user_guide.html)")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

# ==================================================
# Headless End-to-End Stress Test
# ==================================================
#
# Step 3 of the pre-push protocol in CLAUDE.md's "Rules" section: drive
# the real app through many real actions, back to back, in ONE process,
# looking for the crashes isolated unit tests cannot reach.
#
#   python tools/stress_test.py                     # everything
#   python tools/stress_test.py --section parallel  # one section
#   python tools/stress_test.py --list
#
# Sections:
#   single    one flash to completion, one aborted, Dark Mode, resize,
#             a real Test Connection dialog
#   batch     Batch Flash mode: three units (one aborted), Stop Batch,
#             Export Report
#   parallel  six channels flashed concurrently, repeatedly; channels
#             started while others run; partial aborts; Start/Abort All;
#             UI work during live threads; window closed mid-flash
#   races     abort timed into every phase, double start/abort, Start
#             All over running channels, tab churn, close during Identify
#
# Why this exists in a file rather than as an inline snippet: the
# threading traps below are easy to reintroduce from memory, and this
# way they stay fixed once.
#
#   * A Qt message handler is installed, so "QThread: Destroyed while
#     thread is still running" and friends FAIL the run instead of
#     scrolling past in stderr. Those warnings are the main symptom of
#     the crash class this script exists to catch, and they raise no
#     Python exception.
#   * pump_until() uses a LOCAL QEventLoop, never app.quit(): app.quit()
#     closes every window, and MainWindow.closeEvent() aborts a running
#     flash — so any step after the first wait would run against a
#     closed window and a cancelled flash.
#   * Thread accounting checks for a surviving TesterPresent keepalive
#     (CLAUDE.md's documented leak) and for real worker threads. It does
#     NOT use threading.active_count(): Qt worker threads leave
#     _DummyThread placeholders behind that linger until GC, so that
#     number climbs to a plateau even when nothing leaked.
# ==================================================

import argparse
import faulthandler
import gc
import os
import sys
import tempfile
import threading
import time
import unittest.mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

faulthandler.enable()

from PySide6.QtCore import (
    QEventLoop, QSettings, QTimer, QtMsgType, qInstallMessageHandler,
)
from PySide6.QtWidgets import QApplication

QSettings.setPath(
    QSettings.IniFormat, QSettings.UserScope,
    tempfile.mkdtemp(prefix="sflash_stress_"),
)

from gui.main_window import MainWindow
from gui.style import is_dark_mode_enabled, load_stylesheet
from gui.test_connection_dialog import TestConnectionDialog

SAMPLE_HEX = os.path.join(REPO, "tests", "sample.hex")

# Qt messages that mean a threading bug, not noise.
FATAL_PATTERNS = (
    "QThread: Destroyed while thread is still running",
    "Signal source has been deleted",
    "Timers cannot be stopped from another thread",
    "Timers can only be used with threads started with QThread",
    "QObject: Cannot create children for a parent in a different thread",
    "was not called from the main thread",
)

qt_problems = []
failures = []
checkpoints = []


def _qt_message_handler(mode, context, message):
    for pattern in FATAL_PATTERNS:
        if pattern.lower() in message.lower():
            qt_problems.append(message)
    if mode in (QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
        qt_problems.append(message)


qInstallMessageHandler(_qt_message_handler)


def check(label, condition, detail=""):
    if condition:
        checkpoints.append(label)
        print(f"  ok   {label}", flush=True)
    else:
        failures.append(f"{label} {detail}".strip())
        print(f"  FAIL {label} {detail}", flush=True)


app = QApplication.instance() or QApplication(sys.argv)
app.setStyleSheet(load_stylesheet(dark=is_dark_mode_enabled()))


def pump_until(predicate, timeout_ms=120000, interval_ms=10):
    """Run the event loop until predicate() is true.

    Local QEventLoop on purpose — see the header note about app.quit().
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


def pump_ms(ms):
    pump_until(lambda: False, timeout_ms=ms)


def pumped_exec(dialog):
    """Stand-in for a modal exec() that pumps instead of blocking.

    Never connect a lambda to the worker's finished signal to close the
    dialog — a lambda has no thread affinity, so PySide6 cannot queue it
    and the call lands on the wrong thread.
    """

    deadline = time.time() + 20
    while dialog._thread is not None and dialog._thread.isRunning():
        app.processEvents()
        time.sleep(0.01)
        if time.time() > deadline:
            print("  !! dialog timed out", flush=True)
            break
    app.processEvents()
    dialog.close()
    return 0


def build_window():
    w = MainWindow()
    w.resize(1100, 850)
    w.show()
    app.processEvents()
    with unittest.mock.patch(
        "gui.configure_tab.QFileDialog.getOpenFileNames",
        return_value=([SAMPLE_HEX], ""),
    ):
        w.add_new_datablock()
    app.processEvents()
    assert w._loaded_datablocks, "firmware did not load"
    return w


def select_all_channels(w):
    w.ui.tabWidget.setCurrentWidget(w.ui.parallelFlashTab)
    app.processEvents()
    for panel in w._parallel_panels:
        panel["combo"].setCurrentIndex(1)  # Virtual ECU Simulator
    app.processEvents()
    return w._parallel_panels


def panel_quiet(panels, idxs):
    return all(
        panels[i]["phase"] in ("pass", "fail", "idle")
        and panels[i]["identify_thread"] is None
        and panels[i]["flash_thread"] is None
        for i in idxs
    )


# ==================================================
# single — the original end-to-end pass
# ==================================================

def section_single():
    print("\n[single] flash, abort, theme, resize, dialog", flush=True)
    w = build_window()

    w.ui.flashButton.click()
    check("single flash ran to completion",
          pump_until(lambda: w.thread is None and w.worker is None))
    check("stats label filled in", bool(w.ui.statsLabel.text()))

    w.ui.flashButton.click()
    pump_until(lambda: w.thread is not None and w.thread.isRunning())
    w.ui.flashButton.click()  # abort
    check("second flash aborted and cleaned up",
          pump_until(lambda: w.thread is None and w.worker is None))

    w.ui.actionDarkMode.setChecked(True)
    app.processEvents()
    check("dark mode on", w._dark_mode_active is True)
    w.ui.actionDarkMode.setChecked(False)
    app.processEvents()
    check("dark mode off", w._dark_mode_active is False)

    w.ui.actionResizeMedium.trigger()
    app.processEvents()
    w.ui.actionResizeDefault.trigger()
    app.processEvents()
    check("window resized", True)

    with unittest.mock.patch.object(TestConnectionDialog, "exec", pumped_exec):
        dialog = w.open_test_connection_dialog()
    check("test connection dialog passed",
          dialog is not None and dialog.passed is True)

    w.close()
    app.processEvents()
    check("window closed", True)


# ==================================================
# batch — sequential multi-ECU mode
# ==================================================

def section_batch():
    print("\n[batch] three units, one aborted, stop, export", flush=True)
    w = build_window()
    w.ui.actionModeBatchFlash.setChecked(True)
    app.processEvents()

    w.ui.flashButton.click()
    check("batch unit 1 finished",
          pump_until(lambda: len(w._batch_records) >= 1))

    w.ui.flashButton.click()
    pump_until(lambda: w.thread is not None and w.thread.isRunning())
    w.ui.flashButton.click()  # abort this unit
    check("batch unit 2 aborted and settled",
          pump_until(lambda: len(w._batch_records) >= 2))

    w.ui.flashButton.click()
    check("batch unit 3 finished",
          pump_until(lambda: len(w._batch_records) >= 3))

    w.stop_batch()
    check("batch stopped",
          pump_until(lambda: not w.ui.buttonStopBatch.isEnabled()))

    report = tempfile.mktemp(suffix=".html")
    with unittest.mock.patch(
        "gui.batch_flash.QFileDialog.getSaveFileName",
        return_value=(report, ""),
    ):
        w.ui.buttonExportBatchReport.click()
    check("batch report written", os.path.exists(report))
    if os.path.exists(report):
        os.unlink(report)

    w.ui.actionModeFlash.setChecked(True)
    app.processEvents()
    w.close()
    app.processEvents()


# ==================================================
# parallel — the concurrency workout
# ==================================================

def section_parallel():
    print("\n[parallel] concurrent channels, overlap, aborts, UI churn",
          flush=True)
    w = build_window()
    panels = select_all_channels(w)
    n = len(panels)

    def quiet(idxs=None):
        return panel_quiet(panels, range(n) if idxs is None else idxs)

    # All channels at once, repeatedly.
    for rnd in range(3):
        for i in range(n):
            panels[i]["flash_button"].click()
        ok = pump_until(quiet)
        passes = sum(1 for p in panels if p["phase"] == "pass")
        check(f"round {rnd + 1}: all {n} channels settled", ok)
        check(f"round {rnd + 1}: all {n} passed", passes == n,
              f"passes={passes}")

    # Start a second wave while the first is still running.
    for i in (0, 1, 2):
        panels[i]["flash_button"].click()
    pump_until(lambda: any(panels[i]["progress_bar"].value() > 20
                           for i in (0, 1, 2)), timeout_ms=20000)
    still_busy = sum(1 for i in (0, 1, 2)
                     if panels[i]["phase"] in ("identifying", "flashing"))
    for i in (3, 4, 5):
        panels[i]["flash_button"].click()
        pump_ms(80)
    check("second wave started while first still running", still_busy >= 1)
    check("both waves settled", pump_until(quiet))

    # Finish a group, then flash one more channel on its own.
    for i in range(4):
        panels[i]["flash_button"].click()
    check("group of four settled", pump_until(lambda: quiet(range(4))))
    panels[5]["flash_button"].click()
    check("extra channel after a finished group",
          pump_until(lambda: quiet([5])) and panels[5]["phase"] == "pass")

    # Abort half mid-flight, let the rest run on.
    for i in range(n):
        panels[i]["flash_button"].click()
    pump_until(lambda: sum(1 for i in range(n)
                           if panels[i]["phase"] in ("identifying", "flashing"))
               >= 4, timeout_ms=20000)
    for i in (0, 2, 4):
        panels[i]["flash_button"].click()  # abort
    check("partial abort settled", pump_until(quiet))

    # Start All / Abort All.
    for rnd in range(3):
        w.ui.buttonParallelStartAll.click()
        pump_until(lambda: any(panels[i]["phase"] in ("identifying", "flashing")
                               for i in range(n)), timeout_ms=20000)
        pump_ms(250)
        w.ui.buttonParallelAbortAll.click()
        check(f"Start All / Abort All #{rnd + 1}", pump_until(quiet))

    # One channel, many times over.
    ok = True
    for _ in range(8):
        panels[1]["flash_button"].click()
        if not pump_until(lambda: quiet([1])):
            ok = False
            break
    check("one channel re-flashed 8x", ok)

    # UI work while threads are live.
    for i in range(n):
        panels[i]["flash_button"].click()
    pump_until(lambda: sum(1 for i in range(n)
                           if panels[i]["phase"] in ("identifying", "flashing"))
               >= 4, timeout_ms=20000)

    w.ui.actionDarkMode.setChecked(True)
    app.processEvents()
    for i in range(n):
        panels[i]["action_view_log"].trigger()
        app.processEvents()
    per_channel = tempfile.mktemp(suffix=".html")
    combined = tempfile.mktemp(suffix=".html")
    with unittest.mock.patch(
        "gui.parallel_flash.QFileDialog.getSaveFileName",
        return_value=(per_channel, ""),
    ):
        panels[0]["action_save_report"].trigger()
    with unittest.mock.patch(
        "gui.parallel_flash.QFileDialog.getSaveFileName",
        return_value=(combined, ""),
    ):
        w.export_all_parallel_reports()
    with unittest.mock.patch(
        "gui.parallel_flash.ParallelChannelSettingsDialog"
    ) as mock_dialog:
        mock_dialog.return_value.result.return_value = False
        panels[2]["action_settings"].trigger()
    w.ui.actionDarkMode.setChecked(False)
    app.processEvents()

    check("reports written while flashing",
          os.path.exists(per_channel) and os.path.exists(combined))
    check("settings dialog opened while flashing", mock_dialog.called)
    check("all settled after UI churn", pump_until(quiet))
    for path in (per_channel, combined):
        if os.path.exists(path):
            os.unlink(path)

    # Test Connection beside live flashes.
    for i in (0, 1, 2, 3):
        panels[i]["flash_button"].click()
    pump_until(lambda: sum(1 for i in (0, 1, 2, 3)
                           if panels[i]["phase"] in ("identifying", "flashing"))
               >= 2, timeout_ms=20000)
    with unittest.mock.patch.object(TestConnectionDialog, "exec", pumped_exec):
        panels[5]["test_connection_button"].click()
    check("test connection ran beside live flashes",
          panels[5]["_test_connection_kind"] == "done")
    check("settled afterwards", pump_until(quiet))

    audit_threads("parallel")

    # Close with everything running.
    for i in range(n):
        panels[i]["flash_button"].click()
    pump_until(lambda: sum(1 for i in range(n)
                           if panels[i]["phase"] in ("identifying", "flashing"))
               >= 4, timeout_ms=20000)
    started = time.time()
    w.close()
    app.processEvents()
    elapsed = time.time() - started
    check("close during live flashes returned (no deadlock)", elapsed < 30,
          f"{elapsed:.1f}s")
    pump_ms(1500)


# ==================================================
# races — the narrow windows
# ==================================================

def section_races():
    print("\n[races] abort timing, double clicks, tab churn, close on identify",
          flush=True)
    w = build_window()
    panels = select_all_channels(w)
    n = len(panels)

    def quiet(idxs=None):
        return panel_quiet(panels, range(n) if idxs is None else idxs)

    # Abort landing in every phase of start-up.
    for delay in (0, 5, 20, 60, 150, 400):
        panels[0]["flash_button"].click()
        if delay:
            pump_ms(delay)
        else:
            app.processEvents()
        panels[0]["flash_button"].click()
        check(f"abort {delay}ms after start settled",
              pump_until(lambda: quiet([0]), timeout_ms=60000))

    # Double / triple clicks.
    for _ in range(3):
        panels[1]["flash_button"].click()
    check("triple click on one channel settled",
          pump_until(lambda: quiet([1]), timeout_ms=60000))

    w.ui.buttonParallelStartAll.click()
    w.ui.buttonParallelStartAll.click()
    pump_ms(300)
    w.ui.buttonParallelAbortAll.click()
    w.ui.buttonParallelAbortAll.click()
    check("double Start All + double Abort All settled",
          pump_until(quiet, timeout_ms=90000))

    # Start All on top of already-running channels.
    for i in (0, 1):
        panels[i]["flash_button"].click()
    pump_until(lambda: any(panels[i]["phase"] in ("identifying", "flashing")
                           for i in (0, 1)), timeout_ms=20000)
    w.ui.buttonParallelStartAll.click()
    check("Start All over running channels settled", pump_until(quiet))

    # Tab switching mid-flash.
    for i in range(n):
        panels[i]["flash_button"].click()
    pump_until(lambda: sum(1 for i in range(n)
                           if panels[i]["phase"] in ("identifying", "flashing"))
               >= 3, timeout_ms=20000)
    for target in (w.ui.flashTab, w.ui.configureTab, w.ui.parallelFlashTab,
                   w.ui.flashTab, w.ui.parallelFlashTab):
        w.ui.tabWidget.setCurrentWidget(target)
        app.processEvents()
        pump_ms(60)
    check("tab switching during flashes settled", pump_until(quiet))

    # The combo must be locked while its channel runs.
    panels[2]["flash_button"].click()
    pump_until(lambda: panels[2]["phase"] in ("identifying", "flashing"),
               timeout_ms=20000)
    check("combo locked mid-run", not panels[2]["combo"].isEnabled())
    pump_until(lambda: quiet([2]))
    check("combo unlocked after run", panels[2]["combo"].isEnabled())

    w.close()
    app.processEvents()
    pump_ms(400)

    # Close during Identify, before any flash worker exists.
    w2 = build_window()
    p2 = select_all_channels(w2)
    for panel in p2:
        panel["flash_button"].click()
    pump_until(lambda: sum(1 for p in p2 if p["phase"] == "identifying") >= 2,
               timeout_ms=15000)
    started = time.time()
    w2.close()
    app.processEvents()
    elapsed = time.time() - started
    check("close during Identify returned (no deadlock)", elapsed < 30,
          f"{elapsed:.1f}s")
    pump_ms(1500)


# ==================================================
# Thread accounting
# ==================================================

def audit_threads(label):
    gc.collect()
    app.processEvents()
    pump_ms(400)
    gc.collect()

    alive = threading.enumerate()
    dummies = [t for t in alive if type(t).__name__ == "_DummyThread"]
    keepalive = [t for t in alive
                 if "testerpresent" in type(t).__name__.lower()
                 or "tester" in (t.name or "").lower()]
    real = [t for t in alive
            if t is not threading.main_thread() and t not in dummies]

    print(f"  threads after {label}: total={len(alive)} "
          f"qt-placeholders={len(dummies)} real={len(real)}", flush=True)
    check(f"no TesterPresent keepalive survived {label}", not keepalive,
          str([t.name for t in keepalive]))
    check(f"no real worker thread survived {label}", not real,
          str([t.name for t in real]))


SECTIONS = {
    "single": section_single,
    "batch": section_batch,
    "parallel": section_parallel,
    "races": section_races,
}


def main():
    parser = argparse.ArgumentParser(
        description="Headless end-to-end stress test (CLAUDE.md step 3).",
    )
    parser.add_argument(
        "--section", action="append", choices=sorted(SECTIONS),
        help="run only this section (repeatable; default: all)",
    )
    parser.add_argument("--list", action="store_true",
                        help="list the sections and exit")
    args = parser.parse_args()

    if args.list:
        for name in SECTIONS:
            print(name)
        return 0

    names = args.section or list(SECTIONS)
    started = time.time()
    for name in names:
        SECTIONS[name]()

    audit_threads("all sections")

    print("\n" + "=" * 52, flush=True)
    print(f"sections: {', '.join(names)}", flush=True)
    print(f"checkpoints passed: {len(checkpoints)}", flush=True)
    print(f"elapsed: {time.time() - started:.0f}s", flush=True)

    if qt_problems:
        print(f"\nQt threading warnings ({len(qt_problems)}) "
              f"— these are the crash symptoms:", flush=True)
        for message in qt_problems[:20]:
            print("  -", message, flush=True)

    if failures or qt_problems:
        print(f"\nFAILURES: {len(failures)} check(s), "
              f"{len(qt_problems)} Qt warning(s)", flush=True)
        for item in failures:
            print("  -", item, flush=True)
        print("STRESS_RESULT=FAIL", flush=True)
        return 1

    print("no Qt threading warnings", flush=True)
    print("STRESS_RESULT=PASS", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

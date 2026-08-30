# Sequential Batch Flash Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Batch Flash" mode to SFlash's existing Flash tab — flash the same firmware to
many ECUs in a row on one CAN channel, with automatic per-unit Serial Number identification,
PASS/FAIL/ABORTED tracking, and an HTML batch report export.

**Architecture:** A new `gui/batch_flash.py` mixin (`BatchFlashMixin`) orchestrates two already-
hardened `QThread`-based workers sequentially, never concurrently: `TestConnectionWorker`
(identify — reads Serial Number via DID `0xF18C`, unmodified) then `FlashWorker` (flash,
unmodified). `flash_button_clicked()` gains a 2-line branch to `BatchFlashMixin`'s own handler
when Batch mode is active; its single-flash body is otherwise untouched. Mode switches via a new
`Tools → Mode → Flash / Batch Flash` menu.

**Tech Stack:** PySide6/Qt (`QThread`, `QActionGroup`, `QTableWidget`), reusing this codebase's
existing `core/test_connection.py` and `core/flash_controller.py` as-is.

**Spec:** `docs/superpowers/specs/2026-08-30-sequential-batch-flash-design.md`

## Global Constraints

- Branch: all work happens on `feature/sequential-batch-flash` (already checked out) — never
  `main`.
- `FlashWorker` (`core/flash_controller.py`) and `TestConnectionWorker` (`core/test_connection.py`)
  are **not modified** — every new behavior is orchestration around them.
- `gui/flash_tab.py`'s existing `flash_button_clicked()` body (the single-flash path) is
  **untouched** beyond the 2-line branch at its very top.
- GUI widgets are added to `gui/main_window.ui` first, then `gui/ui_main_window.py` is
  regenerated with `pyside6-uic gui/main_window.ui -o gui/ui_main_window.py` — never hand-edited.
- Every `QThread` this plan creates follows the lifecycle rules in `CLAUDE.md`'s "Threading
  model" section: a worker's own `finished`-family signal connects to `thread.quit` +
  `worker.deleteLater`; only a slot connected to `thread.finished` (never the worker's signal)
  clears the Python references to `thread`/`worker`; any `closeEvent()` that must tear down a
  running thread synchronously calls `thread.quit()` directly (not via a queued signal) before
  `thread.wait()`.
- After every task: run `python -m unittest discover -s tests -p "test_*.py"` (full suite) and,
  for any task touching `gui/flash_tab.py`, `gui/batch_flash.py`, `gui/main_window.py`, or the new
  `tests/test_batch_flash_threading.py`, also run
  `python -m unittest tests.test_flash_threading -v` explicitly — this codebase has a documented
  history of `QThread` lifecycle crashes that only real-`QThread` tests catch (see `CLAUDE.md`).
- `PASS`/`FAIL`/`ABORTED` are told apart via a local `self._batch_operator_abort` flag set by the
  batch orchestrator itself (not from any `FlashWorker` signal payload — `flash_finished`/
  `flash_aborted` are bare signals, no payload) — see spec §3.6.

---

### Task 1: `.ui` scaffolding — Mode menu actions + Batch Flash section widgets

**Files:**
- Modify: `gui/main_window.ui`
- Regenerate: `gui/ui_main_window.py` (via `pyside6-uic`, not hand-edited)
- Test: `tests/test_gui_smoke.py`

**Interfaces:**
- Produces: `self.ui.actionModeFlash`, `self.ui.actionModeBatchFlash` (checkable `QAction`s, not
  yet wired to any group/handler — that's Task 2), `self.ui.groupBoxBatchFlash` (`QGroupBox`,
  `visible=false`), `self.ui.labelEcuCounter`, `self.ui.labelBatchTally`,
  `self.ui.buttonStopBatch` (disabled by default), `self.ui.buttonExportBatchReport` (disabled by
  default), `self.ui.labelBatchStatus`, `self.ui.labelBatchStatusCaption`,
  `self.ui.tableWidgetBatchLog` (5 columns: `#`, `Serial Number`, `Timestamp`, `Result`,
  `Duration`).

- [ ] **Step 1: Add the `Tools → Mode` submenu to `gui/main_window.ui`**

Find `menuTools`'s `<addaction>` list (currently ends with
`<addaction name="actionExportReport"/>`) and add a nested menu after it:

```xml
   <widget class="QMenu" name="menuTools">
    <property name="title">
     <string>Tools</string>
    </property>
    <addaction name="actionFlash"/>
    <addaction name="actionAbort"/>
    <addaction name="separator"/>
    <addaction name="actionTestConnection"/>
    <addaction name="actionExportReport"/>
    <addaction name="separator"/>
    <addaction name="menuMode"/>
   </widget>
   <widget class="QMenu" name="menuMode">
    <property name="title">
     <string>Mode</string>
    </property>
    <addaction name="actionModeFlash"/>
    <addaction name="actionModeBatchFlash"/>
   </widget>
```

`menuMode` must be declared as a sibling `<widget class="QMenu">` at the same level as
`menuTools` (same pattern `menuRecentFiles` already uses under `menuFile` elsewhere in this
file) — grep the file for `name="menuRecentFiles"` to see the exact sibling-menu placement
convention before adding this.

- [ ] **Step 2: Add the two `QAction` definitions**

Find the `<action name="actionExportReport">` block and add two new actions directly after it:

```xml
  <action name="actionModeFlash">
   <property name="checkable">
    <bool>true</bool>
   </property>
   <property name="checked">
    <bool>true</bool>
   </property>
   <property name="text">
    <string>Flash</string>
   </property>
  </action>
  <action name="actionModeBatchFlash">
   <property name="checkable">
    <bool>true</bool>
   </property>
   <property name="text">
    <string>Batch Flash</string>
   </property>
  </action>
```

(`QActionGroup` exclusivity is set up in Python in Task 2, not via a Designer `<actiongroup>` XML
tag — keeps this step to plain, unambiguous `<action>` blocks.)

- [ ] **Step 3: Add the Batch Flash section to the Flash tab**

Find `verticalLayout_flashTab` (it currently has exactly 2 `<item>`s: the header row
`horizontalLayout_flashHeader`, then the tables row `horizontalLayout_flashTables`, with
`stretch="0,0"` on the parent `<layout>` tag). Change `stretch="0,0"` to `stretch="0,0,0"` and add
a third `<item>` after the tables row, before its closing `</layout>`:

```xml
          <item>
           <widget class="QGroupBox" name="groupBoxBatchFlash">
            <property name="visible">
             <bool>false</bool>
            </property>
            <property name="title">
             <string>Batch Flash</string>
            </property>
            <layout class="QVBoxLayout" name="verticalLayout_batchFlash">
             <item>
              <layout class="QHBoxLayout" name="horizontalLayout_batchControls">
               <item>
                <widget class="QLabel" name="labelEcuCounter">
                 <property name="text">
                  <string>ECU #0</string>
                 </property>
                </widget>
               </item>
               <item>
                <widget class="QLabel" name="labelBatchTally">
                 <property name="text">
                  <string>0 PASS · 0 FAIL · 0 ABORTED</string>
                 </property>
                </widget>
               </item>
               <item>
                <spacer name="horizontalSpacer_batchControls">
                 <property name="orientation">
                  <enum>Qt::Orientation::Horizontal</enum>
                 </property>
                </spacer>
               </item>
               <item>
                <widget class="QPushButton" name="buttonStopBatch">
                 <property name="enabled">
                  <bool>false</bool>
                 </property>
                 <property name="text">
                  <string>Stop Batch</string>
                 </property>
                </widget>
               </item>
               <item>
                <widget class="QPushButton" name="buttonExportBatchReport">
                 <property name="enabled">
                  <bool>false</bool>
                 </property>
                 <property name="text">
                  <string>Export Report</string>
                 </property>
                </widget>
               </item>
              </layout>
             </item>
             <item>
              <widget class="QLabel" name="labelBatchStatus">
               <property name="text">
                <string/>
               </property>
              </widget>
             </item>
             <item>
              <widget class="QLabel" name="labelBatchStatusCaption">
               <property name="text">
                <string/>
               </property>
              </widget>
             </item>
             <item>
              <widget class="QTableWidget" name="tableWidgetBatchLog">
               <column>
                <property name="text">
                 <string>#</string>
                </property>
               </column>
               <column>
                <property name="text">
                 <string>Serial Number</string>
                </property>
               </column>
               <column>
                <property name="text">
                 <string>Timestamp</string>
                </property>
               </column>
               <column>
                <property name="text">
                 <string>Result</string>
                </property>
               </column>
               <column>
                <property name="text">
                 <string>Duration</string>
                </property>
               </column>
              </widget>
             </item>
            </layout>
           </widget>
          </item>
```

- [ ] **Step 4: Regenerate the compiled UI file**

Run: `pyside6-uic gui/main_window.ui -o gui/ui_main_window.py`
Expected: exits 0, no errors printed.

- [ ] **Step 5: Write the failing test**

Add to `tests/test_gui_smoke.py`, in a new class near `TestMenuBar`:

```python
class TestBatchFlashScaffolding(unittest.TestCase):
    """
    Covers the static widgets added for Batch Flash mode
    (gui/main_window.ui) — pure structure/defaults, before any
    behavior is wired (see BatchFlashMixin, Task 2+).
    """

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()

    def test_mode_actions_exist_and_default_to_flash_checked(self):
        self.assertTrue(self.window.ui.actionModeFlash.isCheckable())
        self.assertTrue(self.window.ui.actionModeBatchFlash.isCheckable())
        self.assertTrue(self.window.ui.actionModeFlash.isChecked())
        self.assertFalse(self.window.ui.actionModeBatchFlash.isChecked())

    def test_batch_section_hidden_by_default(self):
        self.assertFalse(self.window.ui.groupBoxBatchFlash.isVisible())

    def test_batch_log_table_has_five_columns(self):
        table = self.window.ui.tableWidgetBatchLog
        self.assertEqual(table.columnCount(), 5)
        headers = [
            table.horizontalHeaderItem(i).text()
            for i in range(5)
        ]
        self.assertEqual(
            headers,
            ["#", "Serial Number", "Timestamp", "Result", "Duration"],
        )

    def test_stop_and_export_buttons_start_disabled(self):
        self.assertFalse(self.window.ui.buttonStopBatch.isEnabled())
        self.assertFalse(self.window.ui.buttonExportBatchReport.isEnabled())
```

- [ ] **Step 6: Run test to verify it fails**

Run: `python -m unittest tests.test_gui_smoke.TestBatchFlashScaffolding -v`
Expected: FAIL — `AttributeError: 'Ui_MainWindow' object has no attribute 'actionModeFlash'` (or
similar) if Steps 1-4 weren't done yet; run this only after Step 4 to confirm PASS instead — this
task's "test" is really a structural regression guard, not TDD-before-the-XML (Designer XML isn't
something to drive from a failing test). Confirm it fails before Step 4 by checking out the
unmodified `gui/main_window.ui`/`gui/ui_main_window.py` mentally — do not actually revert files.

- [ ] **Step 7: Run test to verify it passes**

Run: `python -m unittest tests.test_gui_smoke.TestBatchFlashScaffolding -v`
Expected: 4 tests, all PASS.

- [ ] **Step 8: Run the full suite**

Run: `python -m unittest discover -s tests -p "test_*.py"`
Expected: all pass, no regressions (widget additions only — no behavior changed yet).

- [ ] **Step 9: Commit**

```bash
git add gui/main_window.ui gui/ui_main_window.py tests/test_gui_smoke.py
git commit -m "Add Batch Flash UI scaffolding (Tools > Mode menu, batch section widgets)"
```

---

### Task 2: `BatchFlashMixin` skeleton + Mode toggle wiring

**Files:**
- Create: `gui/batch_flash.py`
- Modify: `gui/main_window.py`, `gui/menu_bar.py`
- Test: `tests/test_gui_smoke.py`

**Interfaces:**
- Consumes: `self.ui.actionModeFlash`/`actionModeBatchFlash`/`groupBoxBatchFlash`/
  `flashButton`/`buttonStopBatch`/`buttonExportBatchReport` (Task 1).
- Produces: `BatchFlashMixin.setup_batch_flash()`, `self._batch_mode_active` (bool),
  `self._identify_thread` / `self._identify_worker` (both `None` when idle),
  `self.on_batch_mode_toggled(is_batch: bool)`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_gui_smoke.py`:

```python
class TestBatchModeToggle(unittest.TestCase):
    """
    Covers switching Tools > Mode > Flash / Batch Flash —
    gui/batch_flash.py's BatchFlashMixin.on_batch_mode_toggled().
    """

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()

    def test_selecting_batch_flash_shows_section_and_relabels_button(self):
        self.window.ui.actionModeBatchFlash.setChecked(True)
        self.assertTrue(self.window.ui.groupBoxBatchFlash.isVisible())
        self.assertEqual(self.window.ui.flashButton.text(), "Start Batch")
        self.assertTrue(self.window._batch_mode_active)

    def test_selecting_flash_hides_section_and_restores_button(self):
        self.window.ui.actionModeBatchFlash.setChecked(True)
        self.window.ui.actionModeFlash.setChecked(True)
        self.assertFalse(self.window.ui.groupBoxBatchFlash.isVisible())
        self.assertEqual(self.window.ui.flashButton.text(), "Flash")
        self.assertFalse(self.window._batch_mode_active)

    def test_mode_actions_are_mutually_exclusive(self):
        self.window.ui.actionModeBatchFlash.setChecked(True)
        self.assertFalse(self.window.ui.actionModeFlash.isChecked())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_gui_smoke.TestBatchModeToggle -v`
Expected: FAIL — `AttributeError: 'MainWindow' object has no attribute '_batch_mode_active'`.

- [ ] **Step 3: Create `gui/batch_flash.py`**

```python
# ==================================================
# Batch Flash
# ==================================================
#
# BatchFlashMixin — "Batch Flash" mode for the existing Flash
# tab (Tools > Mode > Flash / Batch Flash). Orchestrates two
# already-hardened QThread-based workers sequentially, never
# concurrently: TestConnectionWorker (identify — reads Serial
# Number via DID 0xF18C) then FlashWorker (flash). Neither
# worker class is modified — see
# docs/superpowers/specs/2026-08-30-sequential-batch-flash-design.md.
#
# Threading follows the exact lifecycle rules documented in
# CLAUDE.md's "Threading model": a worker's own *_finished
# signal connects to thread.quit + worker.deleteLater; only a
# slot connected to thread.finished (never the worker's own
# signal) clears self._identify_thread/self._identify_worker or
# self.thread/self.worker (the latter pair is owned by
# gui/flash_tab.py's flash_button_clicked() for single-flash,
# and reused here for the batch Flash step too).
# ==================================================

from datetime import datetime

from PySide6.QtCore import QThread
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QMessageBox

from core.test_connection import TestConnectionWorker
from core.flash_controller import FlashWorker
from core.flash_sequence import (
    build_flash_sequence,
    build_suzuki_slp1_flash_sequence,
)


class BatchFlashMixin:
    """Mixin adding Batch Flash mode to MainWindow's Flash tab."""

    # ==================================================
    # Setup
    # ==================================================

    def setup_batch_flash(self):

        self._batch_mode_active = False
        self._identify_thread = None
        self._identify_worker = None
        self._batch_operator_abort = False
        self._batch_last_information_message = ""
        self._reset_batch_session()

        if hasattr(self.ui, 'buttonStopBatch'):
            self.ui.buttonStopBatch.clicked.connect(
                self.stop_batch
            )

        if hasattr(self.ui, 'buttonExportBatchReport'):
            self.ui.buttonExportBatchReport.clicked.connect(
                self.export_batch_report
            )

    def _reset_batch_session(self):

        self._batch_ecu_index = 0
        self._batch_counts = {"pass": 0, "fail": 0, "abort": 0}
        self._batch_records = []
        self._batch_session_start_time = None
        self._batch_stopping = False

        if hasattr(self.ui, 'labelEcuCounter'):
            self.ui.labelEcuCounter.setText("ECU #0")
        if hasattr(self.ui, 'labelBatchTally'):
            self._update_batch_tally_label()
        if hasattr(self.ui, 'tableWidgetBatchLog'):
            self.ui.tableWidgetBatchLog.setRowCount(0)
        if hasattr(self.ui, 'buttonExportBatchReport'):
            self.ui.buttonExportBatchReport.setEnabled(False)

    # ==================================================
    # Mode toggle
    # ==================================================

    def on_batch_mode_toggled(self, is_batch):

        self._batch_mode_active = is_batch

        if hasattr(self.ui, 'groupBoxBatchFlash'):
            self.ui.groupBoxBatchFlash.setVisible(is_batch)

        if hasattr(self.ui, 'flashButton'):
            self.ui.flashButton.setText(
                "Start Batch" if is_batch else "Flash"
            )

    def _update_batch_tally_label(self):

        c = self._batch_counts
        self.ui.labelBatchTally.setText(
            f"{c['pass']} PASS · {c['fail']} FAIL · {c['abort']} ABORTED"
        )
```

- [ ] **Step 4: Compose `BatchFlashMixin` into `MainWindow`**

Modify `gui/main_window.py`. Add the import next to the other mixin imports:

```python
from gui.batch_flash import BatchFlashMixin
```

Add `BatchFlashMixin` to the class bases (after `FlashTabMixin`, since Batch Flash extends the
Flash tab):

```python
class MainWindow(
    FlashTabMixin,
    BatchFlashMixin,
    ConfigureTabMixin,
    SettingsProfileMixin,
    ReportExportMixin,
    ProjectFileMixin,
    IssueExportMixin,
    StressTestMixin,
    MenuBarMixin,
    QMainWindow
):
```

Call `setup_batch_flash()` in `__init__`, right after `setup_flash_tab()` and before
`setup_settings_profile()` — Task 7 adds Mode persistence to `load_profile()`, which needs
`on_batch_mode_toggled` already connected (via Task 2 Step 5 below) before it sets a saved
checked-state:

```python
        self.setup_flash_tab()
        self.setup_batch_flash()
        self.setup_configure_tab()
        self.setup_stress_test()
```

- [ ] **Step 5: Wire the Mode actions in `gui/menu_bar.py`**

Add to `setup_menu_bar()`, near the `actionDarkMode` block:

```python
        if hasattr(self.ui, 'actionModeBatchFlash'):
            self.ui.actionModeBatchFlash.toggled.connect(
                self.on_batch_mode_toggled
            )
```

(Only `actionModeBatchFlash.toggled` needs a connection — `QActionGroup`'s mutual exclusivity
means this single `bool` payload already tells `on_batch_mode_toggled` everything it needs;
`actionModeFlash` doesn't need its own connection.)

Also add, near where `self.ui.menuTools` is referenced (the `_sync_flash_abort_menu_state`
block), the `QActionGroup` construction — the actions are checkable per Task 1's `.ui` XML, but
Qt only makes a set of `QAction`s *mutually* exclusive when they share a `QActionGroup`:

```python
        if hasattr(self.ui, 'actionModeFlash') and hasattr(
            self.ui, 'actionModeBatchFlash'
        ):
            self._mode_action_group = QActionGroup(self)
            self._mode_action_group.addAction(self.ui.actionModeFlash)
            self._mode_action_group.addAction(self.ui.actionModeBatchFlash)
```

Add `QActionGroup` to the existing `from PySide6.QtGui import QAction, QDesktopServices` line:

```python
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m unittest tests.test_gui_smoke.TestBatchModeToggle -v`
Expected: 3 tests, all PASS.

- [ ] **Step 7: Run the full suite + flash threading explicitly**

Run: `python -m unittest discover -s tests -p "test_*.py"`
Run: `python -m unittest tests.test_flash_threading -v`
Expected: both all-pass — `flash_button_clicked()` itself is not yet touched (that's Task 3), so
this only proves the new mixin's wiring doesn't disturb existing behavior.

- [ ] **Step 8: Commit**

```bash
git add gui/batch_flash.py gui/main_window.py gui/menu_bar.py tests/test_gui_smoke.py
git commit -m "Add BatchFlashMixin skeleton and Tools > Mode toggle wiring"
```

---

### Task 3: Identify step — `_batch_main_button_clicked()` + `TestConnectionWorker` lifecycle

**Files:**
- Modify: `gui/flash_tab.py`, `gui/batch_flash.py`
- Test: `tests/test_batch_flash_threading.py` (new)

**Interfaces:**
- Consumes: `TestConnectionWorker(use_virtual, security_dll_path, functional, can_channel,
  can_serial, can_tx_id, can_rx_id, can_bitrate, can_fd, can_data_bitrate)` →
  `.step_message(str)`, `.trace_row(dict)`, `.ecu_info_message(dict)`, `.finished(bool, str)`
  signals (`core/test_connection.py`, unmodified). `self.get_checked_datablocks()`
  (`gui/configure_tab.py`). `self.get_can_config()` (`gui/configure_tab.py`).
- Produces: `_batch_main_button_clicked()`, `_start_identify()`, `_on_identify_finished(passed,
  message)` (stub — actually starting the flash is Task 4).

- [ ] **Step 1: Add the 2-line branch to `flash_button_clicked()`**

Modify `gui/flash_tab.py` — the very first line inside `flash_button_clicked()`'s body:

```python
    def flash_button_clicked(self):

        if getattr(self, '_batch_mode_active', False):
            self._batch_main_button_clicked()
            return

        if (self.thread is not None
                and self.thread.isRunning()):
```

(`getattr(..., False)` rather than `self._batch_mode_active` directly — `setup_batch_flash()`
always sets it, but this mirrors this file's existing defensive `hasattr`/`getattr` style for
mixin-provided attributes, e.g. `getattr(self, '_loaded_datablocks', [])` a few lines below.)

- [ ] **Step 2: Write the failing test**

Create `tests/test_batch_flash_threading.py`:

```python
# ==================================================
# Batch Flash QThread Lifecycle Tests
# ==================================================
#
# Same discipline as tests/test_flash_threading.py: calling a
# worker's run() directly (synchronously) cannot catch QThread
# lifecycle bugs (see CLAUDE.md's "Threading model") — these
# tests always go through moveToThread() + thread.start().
# ==================================================

import os
import sys
import unittest

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from PySide6.QtCore import QTimer

from tests.qt_test_utils import get_app
from gui.main_window import MainWindow
from parsers.hex_parser import parse_hex_file

SAMPLE_HEX = os.path.join(os.path.dirname(__file__), "sample.hex")


def _run_until(app, predicate, timeout_ms=15000, interval_ms=20):
    state = {"elapsed": 0, "satisfied": False}

    def tick():
        if predicate():
            state["satisfied"] = True
            app.quit()
        elif state["elapsed"] >= timeout_ms:
            app.quit()
        else:
            state["elapsed"] += interval_ms

    timer = QTimer()
    timer.setInterval(interval_ms)
    timer.timeout.connect(tick)
    timer.start()
    app.exec()
    timer.stop()
    if not state["satisfied"]:
        raise RuntimeError("timed out waiting for condition")
    return state["satisfied"]


class TestIdentifyRealThread(unittest.TestCase):
    """
    Covers _start_identify()/_on_identify_finished() against
    the Virtual ECU Simulator — real QThread, no mocking of
    TestConnectionWorker itself (it's reused unmodified).
    """

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()
        self.window.ui.actionModeBatchFlash.setChecked(True)
        self.window._load_firmware_file(SAMPLE_HEX)

    def test_no_firmware_loaded_blocks_before_starting_identify(self):
        self.window.ui.actionModeFlash.setChecked(True)
        self.window.ui.actionModeBatchFlash.setChecked(True)
        self.window._loaded_datablocks = []

        self.window.flash_button_clicked()

        self.assertIsNone(self.window._identify_thread)

    def test_start_batch_runs_identify_against_virtual_ecu(self):
        self.window.flash_button_clicked()

        self.assertIsNotNone(self.window._identify_thread)

        _run_until(
            self.app,
            lambda: self.window._identify_thread is None,
        )

        self.assertIsNone(self.window._identify_thread)
        self.assertIsNone(self.window._identify_worker)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m unittest tests.test_batch_flash_threading -v`
Expected: FAIL — `AttributeError` (`_batch_main_button_clicked` not defined) or the "no firmware"
test failing because nothing blocks yet.

- [ ] **Step 4: Implement `_batch_main_button_clicked()` and `_start_identify()`**

Add to `gui/batch_flash.py`:

```python
    # ==================================================
    # Main button (Start Batch / Abort / Next ECU)
    # ==================================================

    def _batch_main_button_clicked(self):

        if self.thread is not None and self.thread.isRunning():
            # Flashing — Abort this unit only (batch keeps
            # going, see stop_batch() for ending the session).
            self._batch_operator_abort = True
            self.worker.request_abort()
            return

        if (self._identify_thread is not None
                and self._identify_thread.isRunning()):
            # Already identifying — ignore (mirrors
            # flash_button_clicked()'s own re-entrancy
            # assumption: the button/menu are the only entry
            # points, and both are disabled while a thread is
            # alive — see Task 8).
            return

        datablocks = (
            self.get_checked_datablocks()
            if hasattr(self, 'get_checked_datablocks')
            else getattr(self, '_loaded_datablocks', [])
        )

        if not datablocks:
            QMessageBox.warning(
                self,
                "No Firmware Loaded",
                "No firmware file is loaded (or ticked) "
                "to flash.\n\nLoad a datablock in the Data "
                "tab first, or tick at least one row in "
                "the Datablocks table.",
            )
            return

        self._start_identify()

    def _start_identify(self):

        if self._batch_session_start_time is None:
            # Set once per session, on the very first Identify -
            # a "Next ECU" retry after "No ECU detected" must
            # not push this forward, and the batch report needs
            # this as a stable session-start marker, not a
            # per-unit timestamp.
            self._batch_session_start_time = datetime.now()

        self.ui.buttonStopBatch.setEnabled(True)
        self.ui.labelBatchStatus.setText(
            "Identifying ECU — reading Serial Number..."
        )
        self.ui.labelBatchStatusCaption.setText(
            "Reads DID 0xF18C via the same probe as Tools > "
            "Test Connection — independent of the flash "
            "sequence itself."
        )

        use_virtual = True
        if hasattr(self.ui, 'comboBoxHardware'):
            use_virtual = (
                self.ui.comboBoxHardware.currentData() is None
            )

        security_dll_path = getattr(
            self, '_security_dll_path', ''
        ) or None

        use_suzuki_sequence = False
        if hasattr(self.ui, 'comboBoxFlashSequence'):
            use_suzuki_sequence = (
                "Suzuki"
                in self.ui.comboBoxFlashSequence.currentText()
            )

        can_config = (
            self.get_can_config()
            if hasattr(self, 'get_can_config')
            else {}
        )

        self._batch_identify_ecu_info = {}

        self._identify_thread = QThread()
        self._identify_worker = TestConnectionWorker(
            use_virtual=use_virtual,
            security_dll_path=security_dll_path,
            functional=use_suzuki_sequence,
            can_channel=can_config.get("channel", 0),
            can_serial=can_config.get("serial"),
            can_tx_id=can_config.get("tx_id", 0x778),
            can_rx_id=can_config.get("rx_id", 0x788),
            can_bitrate=can_config.get("bitrate", 500000),
            can_fd=can_config.get("fd", False),
            can_data_bitrate=can_config.get(
                "data_bitrate", 2000000
            ),
        )
        self._identify_worker.moveToThread(self._identify_thread)

        self._identify_thread.started.connect(
            self._identify_worker.run
        )

        self._identify_worker.ecu_info_message.connect(
            self._on_identify_ecu_info
        )
        self._identify_worker.finished.connect(
            self._on_identify_finished
        )

        self._identify_worker.finished.connect(
            self._identify_thread.quit
        )
        self._identify_worker.finished.connect(
            self._identify_worker.deleteLater
        )

        # NOTE: intentionally NOT connecting thread.finished ->
        # thread.deleteLater here — see module docstring and
        # CLAUDE.md's "Threading model".
        self._identify_thread.finished.connect(
            self._cleanup_identify_thread
        )

        self._identify_thread.start()

    def _on_identify_ecu_info(self, info_dict):
        self._batch_identify_ecu_info = info_dict

    def _cleanup_identify_thread(self):

        if self._identify_thread is not None:
            self._identify_thread.wait()

        self._identify_thread = None
        self._identify_worker = None

    def _on_identify_finished(self, passed, message):

        if not passed:
            self.ui.labelBatchStatus.setText(
                "No ECU detected on the bus."
            )
            self.ui.labelBatchStatusCaption.setText(
                "Check connection and try again — not logged, "
                "does not count against the batch."
            )
            self.ui.buttonStopBatch.setEnabled(False)
            return

        serial = self._batch_identify_ecu_info.get(
            "ECU Serial Number", "UNKNOWN"
        )
        # Task 4 replaces this stub with a call to
        # self._start_flash_for_current_ecu(serial).
        self.ui.labelBatchStatus.setText(
            f"Identified ECU (Serial {serial}) — flash not "
            "yet wired (Task 4)."
        )
```

Add the required imports to `gui/batch_flash.py`'s top (extending the block from Task 2):

```python
from PySide6.QtCore import QThread
```

(already present from Task 2 — no change needed there, just confirming it's used now.)

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m unittest tests.test_batch_flash_threading -v`
Expected: 2 tests, both PASS.

- [ ] **Step 6: Run the full suite + flash threading explicitly**

Run: `python -m unittest discover -s tests -p "test_*.py"`
Run: `python -m unittest tests.test_flash_threading -v`
Expected: both all-pass.

- [ ] **Step 7: Commit**

```bash
git add gui/flash_tab.py gui/batch_flash.py tests/test_batch_flash_threading.py
git commit -m "Add Batch Flash Identify step (TestConnectionWorker lifecycle)"
```

---

### Task 4: Flash step after Identify + PASS/FAIL/ABORTED logging

**Files:**
- Modify: `gui/batch_flash.py`
- Test: `tests/test_batch_flash_threading.py`

**Interfaces:**
- Consumes: `FlashWorker(steps, datablocks, use_virtual, security_dll_path,
  keepalive_functional, can_channel, can_serial, can_tx_id, can_rx_id, can_bitrate, can_fd,
  can_data_bitrate, download_compression, download_encrypting)` →
  `.step_started(str)`, `.progress_changed(int)`, `.information_message(str)`,
  `.trace_message(str)`, `.trace_row(dict)`, `.segment_progress(...)`, `.ecu_info_message(dict)`,
  `.flash_finished()`, `.flash_aborted()` (`core/flash_controller.py`, unmodified).
  `self.prepare_flash_ui(datablocks)`, `self.on_step_started`, `self.on_progress_changed`,
  `self.on_trace_message`, `self.on_trace_row`, `self.on_segment_progress`, `self.on_ecu_info`,
  `self._status_colors(kind)` (all `gui/flash_tab.py`).
- Produces: `_start_flash_for_current_ecu(serial)`, `_on_batch_flash_finished()`,
  `_on_batch_flash_aborted()`, `_on_batch_unit_finished(result, serial, duration, reason)`,
  `_append_batch_log_row(...)`.

- [ ] **Step 1: Write the failing test**

Add these imports to `tests/test_batch_flash_threading.py`'s top, alongside the existing ones:

```python
import unittest.mock

from parsers.hex_parser import Segment, Datablock
from core.flash_controller import FlashWorker
```

Add a new test class:

```python
class TestFullBatchCycleRealThread(unittest.TestCase):
    """
    Identify -> Flash -> PASS/FAIL/ABORTED, end to end, against
    the Virtual ECU Simulator. Real QThread throughout (both
    the Identify probe and the flash itself).
    """

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()
        self.window.ui.actionModeBatchFlash.setChecked(True)
        self.window._load_firmware_file(SAMPLE_HEX)

    def _run_full_cycle(self):
        self.window.flash_button_clicked()  # Start Batch -> Identify
        _run_until(
            self.app,
            lambda: self.window._identify_thread is None,
        )
        # Identify success auto-starts Flash - wait for that too.
        _run_until(
            self.app,
            lambda: self.window.thread is None,
        )

    def test_full_cycle_logs_a_pass_row_and_advances_to_next_ecu(self):
        self._run_full_cycle()

        table = self.window.ui.tableWidgetBatchLog
        self.assertEqual(table.rowCount(), 1)
        self.assertEqual(table.item(0, 0).text(), "1")
        self.assertTrue(len(table.item(0, 1).text()) > 0)  # Serial Number
        self.assertEqual(table.item(0, 3).text(), "PASS")

        self.assertEqual(self.window._batch_counts["pass"], 1)
        self.assertEqual(self.window.ui.flashButton.text(), "Next ECU")
        self.assertEqual(self.window.ui.labelEcuCounter.text(), "ECU #1")

    def test_operator_abort_mid_flash_logs_aborted_not_fail(self):
        # Large payload so there's still a step to abort
        # partway through (Virtual ECU flashes fast otherwise) —
        # same technique as tests/test_flash_threading.py.
        db = Datablock(file_path="synthetic_batch.bin")
        db.segments.append(
            Segment(start_address=0x1000, data=bytes([0xAA]) * 200_000)
        )
        self.window._loaded_datablocks = [db]

        self.window.flash_button_clicked()  # Start Batch -> Identify
        _run_until(
            self.app,
            lambda: self.window._identify_thread is None,
        )
        # Now flashing - click again to Abort.
        self.window.flash_button_clicked()

        _run_until(self.app, lambda: self.window.thread is None)

        table = self.window.ui.tableWidgetBatchLog
        self.assertEqual(table.item(0, 3).text(), "ABORTED")
        self.assertEqual(self.window._batch_counts["abort"], 1)
        self.assertEqual(self.window._batch_counts["fail"], 0)

    def test_step_failure_logs_fail_not_aborted_with_a_reason(self):
        # Deterministic FAIL without needing the simulator to
        # naturally reject anything: force the very first step to
        # report failure, exactly like a real NRC/UDS error would
        # (core/flash_controller.py's own "if not success:" branch
        # - see FlashWorker.run()) - patched at the class level so
        # it applies to the FlashWorker this test's own flash
        # start-up creates.
        with unittest.mock.patch.object(
            FlashWorker, '_execute_step', return_value=False
        ):
            self._run_full_cycle()

        table = self.window.ui.tableWidgetBatchLog
        self.assertEqual(table.item(0, 3).text(), "FAIL")
        self.assertEqual(self.window._batch_counts["fail"], 1)
        self.assertEqual(self.window._batch_counts["abort"], 0)
        self.assertIn(
            "Step failed",
            self.window._batch_records[0]["reason"],
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_batch_flash_threading.TestFullBatchCycleRealThread -v`
Expected: FAIL — the Identify step finishes but nothing logs a row (Task 3's stub just sets a
status label).

- [ ] **Step 3: Implement the Flash step + outcome logging**

Replace the stub body of `_on_identify_finished()` in `gui/batch_flash.py` (the branch after
`if not passed: ... return`) with:

```python
        serial = self._batch_identify_ecu_info.get(
            "ECU Serial Number", "UNKNOWN"
        )
        self._start_flash_for_current_ecu(serial)
```

Add the new methods to `gui/batch_flash.py`:

```python
    # ==================================================
    # Flash step (after a successful Identify)
    # ==================================================

    def _start_flash_for_current_ecu(self, serial):

        datablocks = (
            self.get_checked_datablocks()
            if hasattr(self, 'get_checked_datablocks')
            else getattr(self, '_loaded_datablocks', [])
        )

        if not datablocks:
            # Edge case: the operator unchecked every datablock
            # during the ~1s Identify probe. FlashWorker.run()
            # treats 0 steps as an immediate flash_finished
            # (see core/flash_controller.py) - without this
            # guard that would silently log a false PASS row
            # with no actual work done, corrupting the batch's
            # traceability report. Bail out the same way
            # _batch_main_button_clicked() already does for the
            # "Start Batch" case.
            QMessageBox.warning(
                self,
                "No Firmware Loaded",
                "No firmware file is loaded (or ticked) to "
                "flash.\n\nLoad a datablock in the Data tab "
                "first, or tick at least one row in the "
                "Datablocks table, then click Next ECU again.",
            )
            self.ui.buttonStopBatch.setEnabled(False)
            return

        self._batch_current_serial = serial
        self._batch_flash_start_time = datetime.now()
        self._batch_operator_abort = False
        self._batch_last_information_message = ""

        self.prepare_flash_ui(datablocks)
        self.ui.flashButton.setText("Abort")

        use_virtual = True
        if hasattr(self.ui, 'comboBoxHardware'):
            use_virtual = (
                self.ui.comboBoxHardware.currentData() is None
            )

        use_suzuki_sequence = False
        if hasattr(self.ui, 'comboBoxFlashSequence'):
            use_suzuki_sequence = (
                "Suzuki"
                in self.ui.comboBoxFlashSequence.currentText()
            )

        if use_suzuki_sequence:
            tester_serial_number = (
                self.get_tester_serial_number()
                if hasattr(self, 'get_tester_serial_number')
                else None
            )
            steps = build_suzuki_slp1_flash_sequence(
                datablocks,
                tester_serial_number=tester_serial_number,
            )
        else:
            steps = build_flash_sequence(datablocks)

        security_dll_path = getattr(
            self, '_security_dll_path', ''
        ) or None

        can_config = (
            self.get_can_config()
            if hasattr(self, 'get_can_config')
            else {}
        )

        data_format_config = (
            self.get_data_format_config()
            if hasattr(self, 'get_data_format_config')
            else {}
        )

        self.thread = QThread()
        self.worker = FlashWorker(
            steps=steps,
            datablocks=datablocks,
            use_virtual=use_virtual,
            security_dll_path=security_dll_path,
            keepalive_functional=use_suzuki_sequence,
            can_channel=can_config.get("channel", 0),
            can_serial=can_config.get("serial"),
            can_tx_id=can_config.get("tx_id", 0x778),
            can_rx_id=can_config.get("rx_id", 0x788),
            can_bitrate=can_config.get("bitrate", 500000),
            can_fd=can_config.get("fd", False),
            can_data_bitrate=can_config.get(
                "data_bitrate", 2000000
            ),
            download_compression=data_format_config.get(
                "compression", 0x00
            ),
            download_encrypting=data_format_config.get(
                "encrypting", 0x00
            ),
        )

        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)

        self.worker.flash_finished.connect(self.thread.quit)
        self.worker.flash_aborted.connect(self.thread.quit)
        self.worker.flash_finished.connect(self.worker.deleteLater)
        self.worker.flash_aborted.connect(self.worker.deleteLater)

        # Same signals flash_button_clicked() connects for a
        # normal single flash, reused as-is - none of these
        # touch flashButton's text, so they're safe to share.
        self.worker.step_started.connect(self.on_step_started)
        self.worker.progress_changed.connect(self.on_progress_changed)
        self.worker.information_message.connect(
            self.on_information_message
        )
        self.worker.information_message.connect(
            self._capture_last_information_message
        )
        self.worker.trace_message.connect(self.on_trace_message)
        self.worker.trace_row.connect(self.on_trace_row)
        self.worker.segment_progress.connect(self.on_segment_progress)
        self.worker.ecu_info_message.connect(self.on_ecu_info)

        # Batch-specific finish handlers (NOT on_flash_finished/
        # on_flash_aborted - those set flashButton back to
        # "Flash", which single-flash mode needs but batch mode
        # must not).
        self.worker.flash_finished.connect(self._on_batch_flash_finished)
        self.worker.flash_aborted.connect(self._on_batch_flash_aborted)

        # NOTE: intentionally NOT connecting thread.finished ->
        # thread.deleteLater - _cleanup_thread() (gui/flash_tab.py,
        # shared with single-flash) is the single owner of this
        # QThread's lifetime, same reasoning as flash_button_clicked().
        self.thread.finished.connect(self._cleanup_thread)

        self.thread.start()

    def _capture_last_information_message(self, message):
        self._batch_last_information_message = message

    def _on_batch_flash_finished(self):

        self._color_last_step_row('done')
        duration = self._batch_elapsed_seconds()
        self._on_batch_unit_finished(
            "pass", self._batch_current_serial, duration
        )

    def _on_batch_flash_aborted(self):

        result = "abort" if self._batch_operator_abort else "fail"
        self._color_last_step_row(
            'running' if result == "abort" else 'error'
        )
        duration = self._batch_elapsed_seconds()
        reason = (
            None if result == "abort"
            else self._batch_last_information_message
        )
        self._on_batch_unit_finished(
            result, self._batch_current_serial, duration, reason
        )

    def _color_last_step_row(self, kind):

        row = self.ui.stepsTable.rowCount() - 1
        if row < 0:
            return
        bg, fg = self._status_colors(kind)
        for col in range(2):
            item = self.ui.stepsTable.item(row, col)
            if item:
                item.setBackground(QColor(bg))
                item.setForeground(QColor(fg))

    def _batch_elapsed_seconds(self):
        return int(
            (datetime.now() - self._batch_flash_start_time)
            .total_seconds()
        )

    # ==================================================
    # Batch Log / tally / ECU counter
    # ==================================================

    def _on_batch_unit_finished(self, result, serial, duration, reason=None):

        self._batch_ecu_index += 1
        self._batch_counts[result] += 1
        self._batch_records.append({
            "index": self._batch_ecu_index,
            "serial": serial,
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "result": result,
            "duration": duration,
            "reason": reason,
        })

        self._append_batch_log_row(
            self._batch_ecu_index, serial, result, duration, reason
        )
        self._update_batch_tally_label()
        self.ui.labelEcuCounter.setText(f"ECU #{self._batch_ecu_index}")
        self.ui.buttonExportBatchReport.setEnabled(True)

        result_labels = {
            "pass": "PASS", "fail": "FAIL", "abort": "ABORTED",
        }

        if self._batch_stopping:
            # stop_batch() requested this abort and is waiting
            # for it to actually land before touching button/
            # label state (see stop_batch()'s own comment) - this
            # is that landing point. Do NOT set "Next ECU" here.
            self._batch_stopping = False
            self.ui.flashButton.setText("Start Batch")
            self.ui.buttonStopBatch.setEnabled(False)
            self.ui.labelBatchStatus.setText(
                f"Batch stopped after ECU #{self._batch_ecu_index} "
                f"({result_labels[result]}). Log kept below — "
                "click Start Batch to begin a new session."
            )
            self.ui.labelBatchStatusCaption.setText("")
            return

        self.ui.labelBatchStatus.setText(
            f"ECU #{self._batch_ecu_index} — "
            f"{result_labels[result]} ({serial}, {duration}s)."
        )
        self.ui.labelBatchStatusCaption.setText(
            "Swap in the next ECU, then click Next ECU."
        )
        self.ui.flashButton.setText("Next ECU")

    def _append_batch_log_row(self, index, serial, result, duration, reason):

        table = self.ui.tableWidgetBatchLog
        row = table.rowCount()
        table.insertRow(row)

        result_labels = {
            "pass": "PASS", "fail": "FAIL", "abort": "ABORTED",
        }
        color_kind = {"pass": "done", "fail": "error", "abort": "running"}

        cells = [
            str(index), serial, datetime.now().strftime("%H:%M:%S"),
            result_labels[result], f"{duration}s",
        ]
        for col, text in enumerate(cells):
            item = QTableWidgetItem(text)
            bg, fg = self._status_colors(color_kind[result])
            item.setBackground(QColor(bg))
            item.setForeground(QColor(fg))
            table.setItem(row, col, item)

        if reason:
            table.item(row, 3).setToolTip(reason)

        table.scrollToBottom()
```

Add `QTableWidgetItem` to `gui/batch_flash.py`'s imports:

```python
from PySide6.QtWidgets import QMessageBox, QTableWidgetItem
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_batch_flash_threading -v`
Expected: 5 tests total (2 from Task 3 + 3 new), all PASS.

- [ ] **Step 5: Run the full suite + flash threading explicitly**

Run: `python -m unittest discover -s tests -p "test_*.py"`
Run: `python -m unittest tests.test_flash_threading -v`
Expected: both all-pass.

- [ ] **Step 6: Commit**

```bash
git add gui/batch_flash.py tests/test_batch_flash_threading.py
git commit -m "Add Batch Flash flash step and PASS/FAIL/ABORTED logging"
```

---

### Task 5: Stop Batch + `closeEvent()` cleanup for the Identify thread

**Files:**
- Modify: `gui/batch_flash.py`, `gui/main_window.py`
- Test: `tests/test_batch_flash_threading.py`

**Interfaces:**
- Produces: `stop_batch()`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_batch_flash_threading.py`:

```python
class TestStopBatchRealThread(unittest.TestCase):

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()
        self.window.ui.actionModeBatchFlash.setChecked(True)

    def test_stop_batch_mid_identify_logs_nothing_and_resets_button(self):
        db = Datablock(file_path="synthetic_batch.bin")
        db.segments.append(
            Segment(start_address=0x1000, data=bytes([0xAA]) * 1000)
        )
        self.window._loaded_datablocks = [db]

        self.window.flash_button_clicked()  # Start Batch -> Identify
        self.assertIsNotNone(self.window._identify_thread)

        self.window.stop_batch()

        _run_until(
            self.app,
            lambda: self.window._identify_thread is None,
        )

        self.assertEqual(self.window.ui.flashButton.text(), "Start Batch")
        self.assertEqual(self.window.ui.tableWidgetBatchLog.rowCount(), 0)

    def test_close_window_mid_identify_does_not_crash(self):
        db = Datablock(file_path="synthetic_batch.bin")
        db.segments.append(
            Segment(start_address=0x1000, data=bytes([0xAA]) * 1000)
        )
        self.window._loaded_datablocks = [db]

        self.window.flash_button_clicked()  # Start Batch -> Identify
        self.window.close()
        self.app.processEvents()

    def test_stop_batch_mid_flash_logs_aborted_and_settles_on_start_batch(self):
        # Regression test for an async-ordering bug caught during
        # planning: stop_batch() must not set flashButton's text
        # itself when a flash is in flight - flash_aborted's
        # queued delivery to _on_batch_unit_finished (which is
        # what actually appends the ABORTED row) hasn't run yet
        # even after thread.wait() returns, so setting "Start
        # Batch" too early gets silently overwritten back to
        # "Next ECU" once that queued signal finally lands.
        db = Datablock(file_path="synthetic_batch.bin")
        db.segments.append(
            Segment(start_address=0x1000, data=bytes([0xAA]) * 200_000)
        )
        self.window._loaded_datablocks = [db]

        self.window.flash_button_clicked()  # Start Batch -> Identify
        _run_until(
            self.app,
            lambda: self.window._identify_thread is None,
        )
        # Now flashing (large payload keeps it running long
        # enough to Stop mid-way, same technique as
        # tests/test_flash_threading.py's TestAbortMidFlash).
        self.assertIsNotNone(self.window.thread)

        self.window.stop_batch()

        _run_until(self.app, lambda: self.window.thread is None)
        self.app.processEvents()  # let the queued flash_aborted land

        self.assertEqual(self.window.ui.tableWidgetBatchLog.rowCount(), 1)
        self.assertEqual(
            self.window.ui.tableWidgetBatchLog.item(0, 3).text(), "ABORTED"
        )
        self.assertEqual(self.window.ui.flashButton.text(), "Start Batch")
        self.assertFalse(self.window.ui.buttonStopBatch.isEnabled())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_batch_flash_threading.TestStopBatchRealThread -v`
Expected: FAIL — `AttributeError: 'MainWindow' object has no attribute 'stop_batch'`, and the
close-mid-identify test likely crashes or hangs (no cleanup wired yet).

- [ ] **Step 3: Implement `stop_batch()`**

Add to `gui/batch_flash.py`:

```python
    # ==================================================
    # Stop Batch
    # ==================================================

    def stop_batch(self):

        if self.thread is not None and self.thread.isRunning():
            # Do NOT set flashButton/label state here. worker.
            # flash_aborted is a queued cross-thread signal - by
            # the time thread.wait() returns below, the worker's
            # OS thread has genuinely stopped (it already called
            # flash_aborted.emit() right before returning), but
            # that queued signal has only been *posted*, not yet
            # *delivered* to _on_batch_flash_aborted - delivery
            # only happens once the GUI thread's event loop next
            # runs, which is after this method returns. Setting
            # "Start Batch" here would just get silently
            # overwritten back to "Next ECU" a moment later when
            # _on_batch_unit_finished() (called from
            # _on_batch_flash_aborted) finally runs. Instead, set
            # this flag and let _on_batch_unit_finished() do the
            # actual UI update once it's really the one running.
            self._batch_stopping = True
            self._batch_operator_abort = True
            self.worker.request_abort()
            self.thread.quit()
            self.thread.wait()
            return

        if (self._identify_thread is not None
                and self._identify_thread.isRunning()):
            # No async completion pending here - an Identify
            # probe never logs a row (see "No ECU detected"
            # handling), so it's safe to reset the UI immediately.
            self._identify_thread.quit()
            self._identify_thread.wait()

        self.ui.flashButton.setText("Start Batch")
        self.ui.buttonStopBatch.setEnabled(False)
        self.ui.labelBatchStatus.setText(
            "Batch stopped. Log kept below — click Start Batch "
            "to begin a new session."
        )
        self.ui.labelBatchStatusCaption.setText("")
```

- [ ] **Step 4: Wire `_identify_thread` cleanup into `MainWindow.closeEvent()`**

Modify `gui/main_window.py`'s `closeEvent()`:

```python
    def closeEvent(self, event):
        """Hàm này được gọi tự động khi bấm nút [X] tắt cửa sổ"""

        if (self.thread is not None
                and self.thread.isRunning()):

            self.worker.request_abort()
            self.thread.quit()
            self.thread.wait()

        if (getattr(self, '_identify_thread', None) is not None
                and self._identify_thread.isRunning()):

            # Same reasoning as the Flash thread above and as
            # gui/test_connection_dialog.py's closeEvent(): quit()
            # is thread-safe to call directly and doesn't need to
            # wait for a queued signal, so call it before wait()
            # rather than relying on TestConnectionWorker.finished
            # -> thread.quit (which wouldn't be delivered until
            # this very event loop runs again).
            self._identify_thread.quit()
            self._identify_thread.wait()

        event.accept()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m unittest tests.test_batch_flash_threading -v`
Expected: 8 tests total, all PASS.

- [ ] **Step 6: Run the full suite + flash threading explicitly**

Run: `python -m unittest discover -s tests -p "test_*.py"`
Run: `python -m unittest tests.test_flash_threading -v`
Expected: both all-pass.

- [ ] **Step 7: Commit**

```bash
git add gui/batch_flash.py gui/main_window.py tests/test_batch_flash_threading.py
git commit -m "Add Stop Batch and closeEvent cleanup for the Identify thread"
```

---

### Task 6: Batch report export (HTML)

**Files:**
- Modify: `gui/batch_flash.py`
- Test: `tests/test_gui_smoke.py`

**Interfaces:**
- Consumes: `self._report_datablocks_table()` (`gui/report_export.py`'s `ReportExportMixin`,
  reused cross-mixin, unmodified — same composition pattern `flash_tab.py` already uses to call
  `self.get_can_config()` from `configure_tab.py`). `self._batch_records` (Task 4).
- Produces: `export_batch_report()`, `_write_batch_report_file(file_path)`,
  `_build_batch_report_html()`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_gui_smoke.py`:

```python
class TestBatchReportExport(unittest.TestCase):
    """
    Covers gui/batch_flash.py's export — mirrors
    TestReportExport's split between the pure HTML-building
    method and the QFileDialog-opening wrapper.
    """

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()
        self.window.ui.actionModeBatchFlash.setChecked(True)
        self.window._load_firmware_file(SAMPLE_HEX)
        self.window._batch_records = [
            {
                "index": 1, "serial": "AB12-3391", "timestamp": "09:14:02",
                "result": "pass", "duration": 38, "reason": None,
            },
            {
                "index": 2, "serial": "AB12-3415", "timestamp": "09:16:25",
                "result": "fail", "duration": 12,
                "reason": "Error: Security Access denied",
            },
        ]
        self.window._batch_counts = {"pass": 1, "fail": 1, "abort": 0}

    def test_html_contains_summary_and_per_unit_rows(self):
        html_out = self.window._build_batch_report_html()
        self.assertIn("AB12-3391", html_out)
        self.assertIn("AB12-3415", html_out)
        self.assertIn("PASS", html_out)
        self.assertIn("FAIL", html_out)
        self.assertIn("Security Access denied", html_out)
        self.assertIn("1 PASS", html_out)
        self.assertIn("1 FAIL", html_out)

    def test_write_batch_report_file_creates_file(self):
        tmp_dir = tempfile.mkdtemp()
        file_path = os.path.join(tmp_dir, "batch_report.html")

        self.window._write_batch_report_file(file_path)

        self.assertTrue(os.path.isfile(file_path))
        with open(file_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("AB12-3391", content)

    def test_export_button_disabled_until_first_row_logged(self):
        fresh = MainWindow()
        fresh.ui.actionModeBatchFlash.setChecked(True)
        self.assertFalse(fresh.ui.buttonExportBatchReport.isEnabled())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_gui_smoke.TestBatchReportExport -v`
Expected: FAIL — `AttributeError: 'MainWindow' object has no attribute '_build_batch_report_html'`.

- [ ] **Step 3: Implement the export**

Add to `gui/batch_flash.py`:

```python
    # ==================================================
    # Batch report export (HTML)
    # ==================================================

    def export_batch_report(self):

        default_name = (
            "batch_flash_report_"
            + datetime.now().strftime("%Y%m%d_%H%M%S")
            + ".html"
        )

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Batch Flash Report",
            default_name,
            "HTML Files (*.html);;All Files (*)",
        )

        if not file_path:
            return

        self._write_batch_report_file(file_path)

    def _write_batch_report_file(self, file_path):

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(self._build_batch_report_html())

        except OSError as e:
            QMessageBox.critical(
                self, "Export Batch Report Failed", str(e)
            )
            return

        self.log_information(
            f"Batch report exported to {file_path}"
        )

    def _build_batch_report_html(self):

        e = html.escape
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c = self._batch_counts

        can_summary = "N/A"
        if hasattr(self.ui, 'comboBoxHardware'):
            can_summary = self.ui.comboBoxHardware.currentText()

        radar_side = "N/A"
        if hasattr(self.ui, 'comboBoxRadarSide'):
            radar_side = self.ui.comboBoxRadarSide.currentText()

        sequence = "N/A"
        if hasattr(self.ui, 'comboBoxFlashSequence'):
            sequence = self.ui.comboBoxFlashSequence.currentText()

        session_start = (
            self._batch_session_start_time.strftime("%Y-%m-%d %H:%M:%S")
            if self._batch_session_start_time else "N/A"
        )

        rows_html = "".join(
            f'<tr><td>{r["index"]}</td><td>{e(r["serial"])}</td>'
            f'<td>{e(r["timestamp"])}</td>'
            f'<td>{e(r["result"].upper())}</td>'
            f'<td>{r["duration"]}s</td>'
            f'<td>{e(r["reason"] or "")}</td></tr>'
            for r in self._batch_records
        )
        if not rows_html:
            rows_html = (
                '<tr><td colspan="6">No units flashed yet.</td></tr>'
            )

        return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{e(APP_NAME)} Batch Flash Report — {e(now)}</title>
<style>
  body {{ font-family: Segoe UI, Arial, sans-serif; margin: 24px; color: #1a1a1a; }}
  h1 {{ font-size: 20px; margin-bottom: 0; }}
  .subtitle {{ color: #666; margin-top: 4px; margin-bottom: 24px; }}
  h2 {{ font-size: 15px; background: #E0E0E0; padding: 6px 8px; margin-top: 28px; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 8px; }}
  th, td {{ border: 1px solid #ccc; padding: 4px 8px; text-align: left; font-size: 13px; }}
  th {{ background: #f2f2f2; }}
  .summary td:first-child {{ font-weight: bold; width: 220px; }}
</style>
</head>
<body>
<h1>{e(APP_NAME)} v{e(APP_VERSION)} — Batch Flash Report</h1>
<div class="subtitle">Exported {e(now)}</div>

<h2>Summary</h2>
<table class="summary">
<tr><td>Hardware</td><td>{e(can_summary)}</td></tr>
<tr><td>Radar Side</td><td>{e(radar_side)}</td></tr>
<tr><td>Flash Sequence</td><td>{e(sequence)}</td></tr>
<tr><td>Session Start</td><td>{e(session_start)}</td></tr>
<tr><td>Total PASS</td><td>{c['pass']}</td></tr>
<tr><td>Total FAIL</td><td>{c['fail']}</td></tr>
<tr><td>Total ABORTED</td><td>{c['abort']}</td></tr>
</table>

<h2>Firmware</h2>
{self._report_datablocks_table()}

<h2>Batch Log</h2>
<table>
<tr><th>#</th><th>Serial Number</th><th>Timestamp</th><th>Result</th>
<th>Duration</th><th>Reason</th></tr>
{rows_html}
</table>

</body>
</html>
"""
```

Add the required imports to `gui/batch_flash.py`'s top:

```python
import html

from PySide6.QtWidgets import QFileDialog, QMessageBox, QTableWidgetItem
from config.settings import APP_NAME, APP_VERSION
```

(merge these with the existing `from PySide6.QtWidgets import ...` and add-on imports already in
the file from Tasks 2-4, rather than a second, duplicate `import` line.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_gui_smoke.TestBatchReportExport -v`
Expected: 3 tests, all PASS.

- [ ] **Step 5: Run the full suite**

Run: `python -m unittest discover -s tests -p "test_*.py"`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add gui/batch_flash.py tests/test_gui_smoke.py
git commit -m "Add Batch Flash HTML report export"
```

---

### Task 7: Mode persistence (`flash/mode` setting)

**Files:**
- Modify: `gui/settings_profile.py`
- Test: `tests/test_gui_smoke.py`

**Interfaces:**
- Consumes: `self.ui.actionModeFlash`/`actionModeBatchFlash` (Task 1).
- Produces: QSettings key `flash/mode` (`"flash"` default, or `"batch"`).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_gui_smoke.py`, inside (or near) the existing `TestSettingsProfile` class:

```python
    def test_batch_mode_persists_across_restart(self):
        window1 = MainWindow()
        window1.ui.actionModeBatchFlash.setChecked(True)

        window2 = MainWindow()
        self.assertTrue(window2.ui.actionModeBatchFlash.isChecked())
        self.assertTrue(window2._batch_mode_active)

    def test_flash_mode_is_the_default(self):
        window = MainWindow()
        self.assertTrue(window.ui.actionModeFlash.isChecked())
        self.assertFalse(window._batch_mode_active)
```

(If `TestSettingsProfile` doesn't already create `MainWindow()` instances directly in each test —
check its existing tests first; follow whatever `setUp()`/per-test construction pattern the class
already uses, e.g. some tests in this class construct a fresh `MainWindow()` inline specifically
to test restart behavior, same idea as `test_radar_side_and_flash_sequence_persist_across_restart`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_gui_smoke.TestSettingsProfile.test_batch_mode_persists_across_restart -v`
Expected: FAIL — mode resets to Flash on the second `MainWindow()` instance.

- [ ] **Step 3: Add persistence to `gui/settings_profile.py`**

Add to `setup_settings_profile()`, alongside the other `hasattr(...).connect(lambda _: self.save_profile())` blocks:

```python
        if hasattr(self.ui, 'actionModeBatchFlash'):
            self.ui.actionModeBatchFlash.toggled.connect(
                lambda _: self.save_profile()
            )
```

Add to `save_profile()`:

```python
        if hasattr(self.ui, 'actionModeBatchFlash'):
            s.setValue(
                "flash/mode",
                "batch"
                if self.ui.actionModeBatchFlash.isChecked()
                else "flash",
            )
```

Add to `load_profile()` (near the other combo-restoring blocks — read the existing method first
to match its exact style/guard pattern):

```python
        if hasattr(self.ui, 'actionModeBatchFlash'):
            mode = s.value("flash/mode", "flash", type=str)
            self.ui.actionModeBatchFlash.setChecked(mode == "batch")
```

This relies on `setup_batch_flash()` (Task 2) and the `actionModeBatchFlash.toggled ->
on_batch_mode_toggled` connection (also Task 2, wired in `gui/menu_bar.py`'s `setup_menu_bar()`)
already being in place before `setup_settings_profile()` runs — confirmed by `gui/main_window.py`'s
`__init__` ordering (Task 2, Step 4): `setup_batch_flash()` before `setup_settings_profile()`,
but `setup_menu_bar()` (which wires the `toggled` connection) runs *after*
`setup_settings_profile()` per the existing `__init__` order. This means `load_profile()`'s
`setChecked(True)` call would fire `toggled` with **no listener connected yet** the first time
through, so `on_batch_mode_toggled` would never run and `groupBoxBatchFlash`/`flashButton` would
be left showing the wrong state on startup even though `self.ui.actionModeBatchFlash.isChecked()`
is correctly `True`.

Fix: after `setup_menu_bar()` runs (which is unconditional, always after
`setup_settings_profile()`), explicitly sync the mode UI once. Add this line in
`gui/main_window.py`'s `__init__`, immediately after the `self.setup_menu_bar()` call:

```python
        self.setup_menu_bar()

        self.on_batch_mode_toggled(
            self.ui.actionModeBatchFlash.isChecked()
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_gui_smoke.TestSettingsProfile -v`
Expected: all `TestSettingsProfile` tests PASS, including the 2 new ones.

- [ ] **Step 5: Run the full suite**

Run: `python -m unittest discover -s tests -p "test_*.py"`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add gui/settings_profile.py gui/main_window.py tests/test_gui_smoke.py
git commit -m "Persist Batch Flash mode across restarts"
```

---

### Task 8: Disable Mode switching while a batch thread is alive

**Files:**
- Modify: `gui/menu_bar.py`
- Test: `tests/test_batch_flash_threading.py`

**Interfaces:**
- Consumes: `self.thread`, `self._identify_thread` (Tasks 3-4).
- Produces: extends `_sync_flash_abort_menu_state()` — no new public interface.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_batch_flash_threading.py`:

```python
class TestModeActionsDisabledWhileRunningRealThread(unittest.TestCase):

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()
        self.window.ui.actionModeBatchFlash.setChecked(True)

    def test_mode_actions_disabled_during_identify(self):
        db = Datablock(file_path="synthetic_batch.bin")
        db.segments.append(
            Segment(start_address=0x1000, data=bytes([0xAA]) * 1000)
        )
        self.window._loaded_datablocks = [db]

        self.window.flash_button_clicked()  # Start Batch -> Identify
        self.window._sync_flash_abort_menu_state()

        self.assertFalse(self.window.ui.actionModeFlash.isEnabled())
        self.assertFalse(self.window.ui.actionModeBatchFlash.isEnabled())

        _run_until(
            self.app,
            lambda: self.window._identify_thread is None,
        )
        _run_until(self.app, lambda: self.window.thread is None)

        self.window._sync_flash_abort_menu_state()
        self.assertTrue(self.window.ui.actionModeFlash.isEnabled())
        self.assertTrue(self.window.ui.actionModeBatchFlash.isEnabled())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_batch_flash_threading.TestModeActionsDisabledWhileRunningRealThread -v`
Expected: FAIL — Mode actions stay enabled throughout (nothing disables them yet).

- [ ] **Step 3: Extend `_sync_flash_abort_menu_state()`**

Modify `gui/menu_bar.py`:

```python
    def _sync_flash_abort_menu_state(self):
        """
        Enable exactly one of Tools > Flash / Abort at a time,
        matching whichever action flashButton itself currently
        represents (same button, same flash_button_clicked()
        toggle — see gui/flash_tab.py). Also disables Tools >
        Mode while either a Flash or an Identify QThread is
        alive, so switching modes can't happen out from under a
        running batch unit. Read-only checks against
        self.thread/self._identify_thread; never touches them,
        so this can't interact with the QThread lifecycle rules
        documented on flash_button_clicked()/on_flash_finished().
        """

        running = (
            self.thread is not None and self.thread.isRunning()
        )
        identifying = (
            getattr(self, '_identify_thread', None) is not None
            and self._identify_thread.isRunning()
        )

        if hasattr(self.ui, 'actionFlash'):
            self.ui.actionFlash.setEnabled(not running)

        if hasattr(self.ui, 'actionAbort'):
            self.ui.actionAbort.setEnabled(running)

        busy = running or identifying

        if hasattr(self.ui, 'actionModeFlash'):
            self.ui.actionModeFlash.setEnabled(not busy)

        if hasattr(self.ui, 'actionModeBatchFlash'):
            self.ui.actionModeBatchFlash.setEnabled(not busy)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_batch_flash_threading -v`
Expected: 9 tests total, all PASS.

- [ ] **Step 5: Run the full suite + flash threading explicitly**

Run: `python -m unittest discover -s tests -p "test_*.py"`
Run: `python -m unittest tests.test_flash_threading -v`
Expected: both all-pass.

- [ ] **Step 6: Commit**

```bash
git add gui/menu_bar.py tests/test_batch_flash_threading.py
git commit -m "Disable Tools > Mode while a batch Identify/Flash thread is alive"
```

---

## After all tasks

Run the full CLAUDE.md stress-test protocol before considering this branch mergeable: full suite,
`tests/test_flash_threading.py` explicitly, `tests/test_batch_flash_threading.py` explicitly, and
a real headless multi-action pass through the app (`QT_QPA_PLATFORM=offscreen`) that chains: load
real firmware, switch to Batch mode, run 2-3 full Identify→Flash cycles against the Virtual ECU
(mix of PASS/FAIL/ABORTED via the "large payload + click Abort" technique), Stop Batch, Export
Report, switch back to Flash mode, run one normal single flash to confirm it's still completely
unaffected, close the window — watching for any exception and a clean exit code. Update
`docs/walkthrough.md` with an entry covering this feature (per `CLAUDE.md`'s rule), then use
`superpowers:finishing-a-development-branch` to decide how `feature/sequential-batch-flash`
integrates back into `main`.

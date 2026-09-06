# Parallel Flash Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Parallel Flash" tab that flashes up to 4 ECUs simultaneously, each on its own CAN
channel, sharing one firmware and one set of CAN protocol settings.

**Architecture:** A new `ParallelFlashMixin` (`gui/parallel_flash.py`) manages 4 independent
"slots", each with its own hardware-channel combo, Flash/Abort button, progress bar, status label,
Serial Number label, and its own `TestConnectionWorker`+`FlashWorker` thread pair — following the
exact per-pair `QThread` lifecycle rules already used everywhere else in this codebase, just
applied 4 times independently instead of once. `FlashWorker`/`UdsClient` gain one small, additive,
backward-compatible change: an optional shared `threading.Lock` serializing Security DLL calls
across concurrently-running workers.

**Tech Stack:** PySide6 (QThread/QObject/Signal), existing `TestConnectionWorker`/`FlashWorker`/
`UdsClient`, Qt Designer `.ui` XML + `pyside6-uic`.

**Spec:** `docs/superpowers/specs/2026-09-06-parallel-flash-design.md`

## Global Constraints

- Branch: all work happens on `feature/parallel-flash` (already checked out), never `main`.
- Same firmware and same CAN protocol settings (tx_id/rx_id/bitrate/FD/data_bitrate/Security DLL/
  flash sequence choice) for all 4 panels — only the physical hardware channel differs per panel.
- 4 fixed panels always shown, regardless of how many real channels are actually detected. A panel
  with no channel selected stays disabled, never removed or hidden.
- Every new `QThread`-based worker pair follows `CLAUDE.md`'s "Threading model" exactly: a worker's
  own `*_finished`/`finished` signal connects to `thread.quit` + `worker.deleteLater`; only a slot
  connected to `thread.finished` (never the worker's own signal) clears that slot's thread/worker
  references.
- `FlashWorker`/`UdsClient` get exactly one additive change (the `security_lock`/`key_lock`
  parameters in Tasks 4) — every other existing call site (Single Flash, Sequential Batch Flash,
  `cli.py`, the whole existing test suite) must keep working unmodified with the new parameters
  omitted (default `None`).
- After every task: run the full suite (`python -m unittest discover -s tests -p "test_*.py"`) and
  `tests/test_flash_threading.py` explicitly, per `CLAUDE.md` — not just the new test file.
- Before any push covering this branch's session work: the full `CLAUDE.md` stress-test protocol
  (full suite, `test_flash_threading.py`, a real headless end-to-end pass — see Task 11).

---

## File Structure

- **Modify `gui/main_window.ui`** — rename the "Flash" tab's display text to "Single Flash"; add a
  new "Parallel Flash" tab between it and "Configure", containing the static skeleton (Start
  All/Abort All buttons, a 2×2 grid of 4 `QGroupBox` shells, a `QTabWidget` with 4 log tabs). Each
  panel's *interior* (combo, labels, button, progress bar) is built in Python, not XML — see Task 3.
- **Modify `gui/ui_main_window.py`** — regenerated via `pyside6-uic`, never hand-edited.
- **Modify `gui/menu_bar.py`** — fix the 2 hardcoded `tabWidget.setCurrentIndex(1)` calls that break
  once Configure is no longer at index 1 (Task 1).
- **Modify `gui/configure_tab.py`** — extract `populate_hardware_combo_widget(combo)` out of
  `populate_hardware_combo()` (Task 2).
- **Create `gui/parallel_flash.py`** — new `ParallelFlashMixin`, the whole feature's orchestration
  (Tasks 3, 5-9).
- **Modify `gui/main_window.py`** — mix in `ParallelFlashMixin`, call `setup_parallel_flash()`,
  extend `closeEvent()` (Task 9).
- **Modify `core/flash_controller.py`** — `FlashWorker.__init__()` gains `security_lock=None`
  (Task 4).
- **Modify `communication/uds_client.py`** — `security_access()`/`_compute_security_key()` gain
  `key_lock=None`, used only around the Security DLL branch (Task 4).
- **Modify `gui/settings_profile.py`** — persist each panel's selected channel (Task 10).
- **Create `tests/test_parallel_flash_threading.py`** — real-`QThread` tests, including genuinely
  concurrent panels (Task 11).
- **Modify `tests/test_gui_smoke.py`** — scaffolding/widget tests per task, plus the 2 fixed
  `tabWidget` index assertions (Task 1) and the `security_lock` serialization test (Task 4).
- **Modify `docs/walkthrough.md`** — a new `## Phase` entry (Task 11), per `CLAUDE.md`.

---

### Task 1: Rename "Flash" tab, insert "Parallel Flash" tab skeleton, fix the tab-index bug

**Files:**
- Modify: `gui/main_window.ui`
- Modify: `gui/ui_main_window.py` (regenerated)
- Modify: `gui/menu_bar.py:235,252`
- Test: `tests/test_gui_smoke.py`

**Interfaces:**
- Produces: `self.ui.parallelFlashTab` (new tab widget), `self.ui.groupBoxParallelChannel1..4` (the
  4 panel shells, empty `QVBoxLayout` inside each — Task 3 fills them), `self.ui.buttonParallelStartAll`,
  `self.ui.buttonParallelAbortAll`, `self.ui.tabWidgetParallelDetail` with 4 tabs each containing
  `self.ui.textEditParallelChannel1Log` .. `textEditParallelChannel4Log` (read-only `QTextEdit`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_gui_smoke.py — add near TestMainWindowConstruction

class TestParallelFlashTabScaffolding(unittest.TestCase):

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()

    def test_tab_order_is_single_flash_parallel_configure(self):
        tw = self.window.ui.tabWidget
        self.assertEqual(tw.tabText(0), "Single Flash")
        self.assertEqual(tw.tabText(1), "Parallel Flash")
        self.assertEqual(tw.tabText(2), "Configure")

    def test_four_channel_panel_shells_exist(self):
        for i in range(1, 5):
            self.assertTrue(
                hasattr(self.window.ui, f"groupBoxParallelChannel{i}")
            )

    def test_start_all_abort_all_buttons_exist(self):
        self.assertTrue(hasattr(self.window.ui, "buttonParallelStartAll"))
        self.assertTrue(hasattr(self.window.ui, "buttonParallelAbortAll"))

    def test_four_detail_log_tabs_exist(self):
        tabs = self.window.ui.tabWidgetParallelDetail
        self.assertEqual(tabs.count(), 4)
        for i in range(1, 5):
            self.assertTrue(
                hasattr(self.window.ui, f"textEditParallelChannel{i}Log")
            )
```

Also fix the 2 existing tab-index assertions that this task's reordering breaks:

```python
# tests/test_gui_smoke.py — find the 2 existing assertions
# `self.assertEqual(self.window.ui.tabWidget.currentIndex(), 1)`
# (one near line 1910, one near line 2360) and change both to:
        self.assertEqual(
            self.window.ui.tabWidget.currentIndex(),
            self.window.ui.tabWidget.indexOf(self.window.ui.configureTab),
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_gui_smoke.TestParallelFlashTabScaffolding -v`
Expected: FAIL — `groupBoxParallelChannel1` etc. don't exist yet, tab text is still "Flash"/"Configure"
with no "Parallel Flash" tab.

- [ ] **Step 3: Edit `gui/main_window.ui`**

Find the `flashTab`'s `<attribute name="title"><string>Flash</string></attribute>` and change the
string to `Single Flash`. Then, right after `flashTab`'s closing `</widget>` and before
`configureTab`'s opening `<widget class="QWidget" name="configureTab">`, insert:

```xml
       <widget class="QWidget" name="parallelFlashTab">
        <attribute name="title">
         <string>Parallel Flash</string>
        </attribute>
        <layout class="QVBoxLayout" name="verticalLayout_parallelFlashTab">
         <item>
          <layout class="QHBoxLayout" name="horizontalLayout_parallelGlobalControls">
           <item>
            <widget class="QPushButton" name="buttonParallelStartAll">
             <property name="text">
              <string>Start All</string>
             </property>
            </widget>
           </item>
           <item>
            <widget class="QPushButton" name="buttonParallelAbortAll">
             <property name="enabled">
              <bool>false</bool>
             </property>
             <property name="text">
              <string>Abort All</string>
             </property>
            </widget>
           </item>
           <item>
            <spacer name="horizontalSpacer_parallelGlobalControls">
             <property name="orientation">
              <enum>Qt::Orientation::Horizontal</enum>
             </property>
            </spacer>
           </item>
          </layout>
         </item>
         <item>
          <layout class="QGridLayout" name="gridLayout_parallelChannels">
           <item row="0" column="0">
            <widget class="QGroupBox" name="groupBoxParallelChannel1">
             <property name="title">
              <string>Channel 1</string>
             </property>
             <layout class="QVBoxLayout" name="verticalLayout_parallelChannel1"/>
            </widget>
           </item>
           <item row="0" column="1">
            <widget class="QGroupBox" name="groupBoxParallelChannel2">
             <property name="title">
              <string>Channel 2</string>
             </property>
             <layout class="QVBoxLayout" name="verticalLayout_parallelChannel2"/>
            </widget>
           </item>
           <item row="1" column="0">
            <widget class="QGroupBox" name="groupBoxParallelChannel3">
             <property name="title">
              <string>Channel 3</string>
             </property>
             <layout class="QVBoxLayout" name="verticalLayout_parallelChannel3"/>
            </widget>
           </item>
           <item row="1" column="1">
            <widget class="QGroupBox" name="groupBoxParallelChannel4">
             <property name="title">
              <string>Channel 4</string>
             </property>
             <layout class="QVBoxLayout" name="verticalLayout_parallelChannel4"/>
            </widget>
           </item>
          </layout>
         </item>
         <item>
          <widget class="QTabWidget" name="tabWidgetParallelDetail">
           <widget class="QWidget" name="tabParallelChannel1Log">
            <attribute name="title">
             <string>Channel 1</string>
            </attribute>
            <layout class="QVBoxLayout" name="verticalLayout_parallelChannel1Log">
             <item>
              <widget class="QTextEdit" name="textEditParallelChannel1Log">
               <property name="readOnly">
                <bool>true</bool>
               </property>
              </widget>
             </item>
            </layout>
           </widget>
           <widget class="QWidget" name="tabParallelChannel2Log">
            <attribute name="title">
             <string>Channel 2</string>
            </attribute>
            <layout class="QVBoxLayout" name="verticalLayout_parallelChannel2Log">
             <item>
              <widget class="QTextEdit" name="textEditParallelChannel2Log">
               <property name="readOnly">
                <bool>true</bool>
               </property>
              </widget>
             </item>
            </layout>
           </widget>
           <widget class="QWidget" name="tabParallelChannel3Log">
            <attribute name="title">
             <string>Channel 3</string>
            </attribute>
            <layout class="QVBoxLayout" name="verticalLayout_parallelChannel3Log">
             <item>
              <widget class="QTextEdit" name="textEditParallelChannel3Log">
               <property name="readOnly">
                <bool>true</bool>
               </property>
              </widget>
             </item>
            </layout>
           </widget>
           <widget class="QWidget" name="tabParallelChannel4Log">
            <attribute name="title">
             <string>Channel 4</string>
            </attribute>
            <layout class="QVBoxLayout" name="verticalLayout_parallelChannel4Log">
             <item>
              <widget class="QTextEdit" name="textEditParallelChannel4Log">
               <property name="readOnly">
                <bool>true</bool>
               </property>
              </widget>
             </item>
            </layout>
           </widget>
          </widget>
         </item>
        </layout>
       </widget>
```

- [ ] **Step 4: Regenerate the compiled UI file**

Run: `pyside6-uic gui/main_window.ui -o gui/ui_main_window.py`

- [ ] **Step 5: Fix the 2 hardcoded tab-index call sites in `gui/menu_bar.py`**

```python
# gui/menu_bar.py — action_load_firmware(), replace:
        if hasattr(self.ui, 'tabWidget'):
            self.ui.tabWidget.setCurrentIndex(1)
# with:
        if hasattr(self.ui, 'tabWidget') and hasattr(self.ui, 'configureTab'):
            self.ui.tabWidget.setCurrentIndex(
                self.ui.tabWidget.indexOf(self.ui.configureTab)
            )
```

Apply the identical replacement in `load_recent_file()` (the second occurrence).

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m unittest tests.test_gui_smoke -v`
Expected: PASS — all of `TestParallelFlashTabScaffolding` plus the 2 fixed index assertions.

- [ ] **Step 7: Run the full suite and `test_flash_threading.py`**

Run: `python -m unittest discover -s tests -p "test_*.py"` and
`python -m unittest tests.test_flash_threading -v`
Expected: both PASS, no regressions.

- [ ] **Step 8: Commit**

```bash
git add gui/main_window.ui gui/ui_main_window.py gui/menu_bar.py tests/test_gui_smoke.py
git commit -m "Rename Flash tab to Single Flash, add Parallel Flash tab skeleton"
```

---

### Task 2: Factor out `populate_hardware_combo_widget(combo)`

**Files:**
- Modify: `gui/configure_tab.py:605-661` (`populate_hardware_combo()`)
- Test: `tests/test_gui_smoke.py`

**Interfaces:**
- Produces: `ConfigureTabMixin.populate_hardware_combo_widget(self, combo)` — same enumeration
  logic `populate_hardware_combo()` already has, parameterized by target `QComboBox`. Returns
  nothing; fills `combo` in place exactly like `populate_hardware_combo()` does for
  `self.ui.comboBoxHardware` today.
- Consumes: `communication.vector_can.detect_vector_channels_with_error()` (unchanged).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gui_smoke.py

class TestPopulateHardwareComboWidget(unittest.TestCase):

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()

    def test_populates_an_arbitrary_combo_with_virtual_entry(self):
        from PySide6.QtWidgets import QComboBox
        combo = QComboBox()
        self.window.populate_hardware_combo_widget(combo)
        self.assertGreaterEqual(combo.count(), 1)
        self.assertIsNone(combo.itemData(0))
        self.assertIn("Virtual ECU Simulator", combo.itemText(0))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_gui_smoke.TestPopulateHardwareComboWidget -v`
Expected: FAIL with `AttributeError: 'MainWindow' object has no attribute 'populate_hardware_combo_widget'`

- [ ] **Step 3: Refactor `populate_hardware_combo()`**

```python
# gui/configure_tab.py — replace populate_hardware_combo()'s body from
# "combo = self.ui.comboBoxHardware" onward with:

    def populate_hardware_combo(self):
        """
        (docstring unchanged)
        """

        if not hasattr(self.ui, 'comboBoxHardware'):
            return

        self._reset_test_connection_button_status()

        error = self.populate_hardware_combo_widget(
            self.ui.comboBoxHardware
        )

        if error and hasattr(self, 'log_information'):
            self.log_information(
                f"No real Vector hardware detected: {error}"
            )

    def populate_hardware_combo_widget(self, combo):
        """
        Fills `combo` with "Virtual ECU Simulator" plus one entry
        per real Vector channel detected right now — the same
        enumeration populate_hardware_combo() uses for the global
        comboBoxHardware, factored out so gui/parallel_flash.py's
        4 per-panel combos can share it instead of duplicating the
        detection call 5 times.

        Returns the error string from detect_vector_channels_with_error()
        (None if detection succeeded, even if it found 0 channels).
        """

        combo.blockSignals(True)

        combo.clear()
        combo.addItem(
            "Virtual ECU Simulator (No Hardware)", userData=None
        )

        from communication.vector_can import (
            detect_vector_channels_with_error,
        )

        channels, error = detect_vector_channels_with_error()

        for ch in channels:
            combo.addItem(ch["label"], userData=ch)

        combo.setCurrentIndex(0)
        combo.blockSignals(False)

        return error
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_gui_smoke.TestPopulateHardwareComboWidget -v`
Expected: PASS

- [ ] **Step 5: Run the full suite and `test_flash_threading.py`**

Run: `python -m unittest discover -s tests -p "test_*.py"` and
`python -m unittest tests.test_flash_threading -v`
Expected: both PASS — `populate_hardware_combo()`'s observable behavior is unchanged (existing
hardware-detection tests still pass).

- [ ] **Step 6: Commit**

```bash
git add gui/configure_tab.py tests/test_gui_smoke.py
git commit -m "Factor out populate_hardware_combo_widget() for reuse by Parallel Flash panels"
```

---

### Task 3: `gui/parallel_flash.py` scaffolding — build the 4 panels' inner widgets

**Files:**
- Create: `gui/parallel_flash.py`
- Modify: `gui/main_window.py` (mix in `ParallelFlashMixin`, call `setup_parallel_flash()`)
- Test: `tests/test_gui_smoke.py`

**Interfaces:**
- Produces: `self._parallel_panels` — a list of 4 dicts, each with keys `combo` (`QComboBox`),
  `serial_label` (`QLabel`), `flash_button` (`QPushButton`), `progress_bar` (`QProgressBar`),
  `status_label` (`QLabel`), `log_widget` (the tab's `QTextEdit`), `phase` (str, one of `"idle"`,
  `"identifying"`, `"flashing"`, `"pass"`, `"fail"`, `"abort"`), `identify_thread`/`identify_worker`/
  `flash_thread`/`flash_worker` (all `None` initially), `serial` (`None` initially),
  `stopping` (bool, `False` initially). `ParallelFlashMixin.setup_parallel_flash(self)`.
- Consumes: `ConfigureTabMixin.populate_hardware_combo_widget(combo)` (Task 2), the 8 `.ui` widgets
  from Task 1 (`groupBoxParallelChannel1..4`, `tabWidgetParallelDetail`'s 4 `QTextEdit`s,
  `buttonParallelStartAll`/`buttonParallelAbortAll`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_gui_smoke.py

class TestParallelFlashPanelScaffolding(unittest.TestCase):

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()

    def test_four_panels_created_with_expected_widgets(self):
        self.assertEqual(len(self.window._parallel_panels), 4)
        for panel in self.window._parallel_panels:
            self.assertIn(panel["combo"].count(), range(1, 10))
            self.assertEqual(panel["phase"], "idle")
            self.assertFalse(panel["flash_button"].isEnabled())
            self.assertIsNone(panel["identify_thread"])
            self.assertIsNone(panel["flash_thread"])

    def test_panel_combo_starts_on_virtual_and_flash_disabled(self):
        panel = self.window._parallel_panels[0]
        self.assertIsNone(panel["combo"].currentData())
        # "Not Selected" is a distinct first entry, ahead of Virtual —
        # see Step 3's combo item order.
        self.assertEqual(panel["combo"].currentIndex(), 0)
        self.assertFalse(panel["flash_button"].isEnabled())

    def test_selecting_a_channel_enables_that_panels_flash_button(self):
        panel = self.window._parallel_panels[0]
        panel["combo"].setCurrentIndex(1)  # Virtual ECU Simulator
        self.assertTrue(panel["flash_button"].isEnabled())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_gui_smoke.TestParallelFlashPanelScaffolding -v`
Expected: FAIL — `_parallel_panels` doesn't exist yet.

- [ ] **Step 3: Create `gui/parallel_flash.py`**

```python
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
```

Note: `combo.currentData()` for "Not Selected" is the literal string `"not-selected"` (not `None`,
which is reserved for the Virtual ECU Simulator entry, matching `comboBoxHardware`'s existing
`userData=None` convention) — this is how `_on_parallel_channel_changed()` and later tasks tell
"nothing picked yet" apart from "Virtual ECU Simulator picked."

`_on_parallel_flash_clicked`/`parallel_start_all`/`parallel_abort_all` are stubbed as no-ops for
this task (Tasks 5-7 give them real bodies) — add empty method bodies now so the button wiring
above doesn't raise `AttributeError` on click during this task's own tests:

```python
    def _on_parallel_flash_clicked(self, panel):
        pass

    def parallel_start_all(self):
        pass

    def parallel_abort_all(self):
        pass
```

- [ ] **Step 4: Mix `ParallelFlashMixin` into `MainWindow`**

```python
# gui/main_window.py — add the import next to the other mixin imports:
from gui.parallel_flash import ParallelFlashMixin

# add ParallelFlashMixin to the class bases, right after BatchFlashMixin:
class MainWindow(
    FlashTabMixin,
    BatchFlashMixin,
    ParallelFlashMixin,
    ConfigureTabMixin,
    ...
):

# in __init__, call setup_parallel_flash() right after setup_batch_flash():
        self.setup_flash_tab()
        self.setup_batch_flash()
        self.setup_parallel_flash()
        self.setup_configure_tab()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m unittest tests.test_gui_smoke.TestParallelFlashPanelScaffolding -v`
Expected: PASS

- [ ] **Step 6: Run the full suite and `test_flash_threading.py`**

Run: `python -m unittest discover -s tests -p "test_*.py"` and
`python -m unittest tests.test_flash_threading -v`
Expected: both PASS.

- [ ] **Step 7: Commit**

```bash
git add gui/parallel_flash.py gui/main_window.py tests/test_gui_smoke.py
git commit -m "Scaffold the 4 Parallel Flash panels' widgets"
```

---

### Task 4: `security_lock`/`key_lock` — shared lock for concurrent Security DLL calls

**Files:**
- Modify: `communication/uds_client.py:634-724` (`security_access()`, `_compute_security_key()`)
- Modify: `core/flash_controller.py:51-96` (`FlashWorker.__init__()`), `:551-576` (`_execute_security()`)
- Test: `tests/test_gui_smoke.py` (or a new small test module — `tests/test_uds_client.py` already
  exists and is the natural home; add there)

**Interfaces:**
- Produces: `UdsClient.security_access(level=1, key_function=None, key_lock=None)`;
  `FlashWorker.__init__(..., security_lock=None)`; `FlashWorker._security_lock` (stored, `None`
  unless passed).
- Consumes: nothing new — `threading.Lock` is stdlib.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_uds_client.py — add near the other SecurityAccess tests

import threading
import time


class TestSecurityAccessKeyLock(unittest.TestCase):

    def test_key_lock_serializes_concurrent_dll_style_calls(self):
        # Build 2 UdsClients against 2 independent Virtual buses,
        # both with a "DLL-style" key_function slow enough that
        # overlapping calls would show up as overlapping windows.
        calls = []
        lock = threading.Lock()

        def slow_key_function(seed_bytes, level):
            calls.append(("start", time.time()))
            time.sleep(0.05)
            calls.append(("end", time.time()))
            return bytes(len(seed_bytes))

        def make_client():
            bus = VirtualCanInterface(response_delay_ms=1, error_rate=0.0)
            bus.connect(tx_id=0x778, rx_id=0x788)
            return UdsClient(bus)

        client_a = make_client()
        client_b = make_client()

        def run(client):
            client.diagnostic_session_control(0x02)
            try:
                # slow_key_function's returned key won't match
                # what the simulator actually expects - a
                # resulting UdsNegativeResponse on SendKey is
                # expected and irrelevant here; the assertions
                # only care about slow_key_function's own call
                # timing, captured before SendKey is even sent.
                client.security_access(
                    level=1, key_function=slow_key_function, key_lock=lock,
                )
            except Exception:
                pass

        t1 = threading.Thread(target=run, args=(client_a,))
        t2 = threading.Thread(target=run, args=(client_b,))
        t1.start(); t2.start()
        t1.join(timeout=5); t2.join(timeout=5)

        # Serialized: the 2nd "start" must come after the 1st "end".
        starts = [t for kind, t in calls if kind == "start"]
        ends = [t for kind, t in calls if kind == "end"]
        self.assertEqual(len(starts), 2)
        self.assertTrue(
            max(starts) >= min(ends),
            "key_function calls overlapped despite key_lock",
        )
```

(Uses this file's existing `VirtualCanInterface`/`UdsClient` imports — no new imports needed beyond
`threading`/`time` at the top of the added test class.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_uds_client.TestSecurityAccessKeyLock -v`
Expected: FAIL with `TypeError: security_access() got an unexpected keyword argument 'key_lock'`

- [ ] **Step 3: Add `key_lock` to `UdsClient`**

```python
# communication/uds_client.py — security_access() signature and body:

    def security_access(
        self,
        level=1,
        key_function=None,
        key_lock=None,
    ) -> bytes:
        """
        (docstring: add one line)
        key_lock: optional threading.Lock, acquired only around
            the actual key-computation call — passed by
            gui/parallel_flash.py so concurrently-running
            FlashWorkers sharing one Security DLL don't call into
            it from multiple threads at once (unknown DLL
            thread-safety). Never required for a single-flash
            caller; defaults to None (no locking).
        """

        seed_response = self._send_request(
            bytes([SID_SECURITY_ACCESS, level])
        )

        seed_bytes = seed_response[2:]

        if all(b == 0 for b in seed_bytes):
            return seed_response

        key_bytes = self._compute_security_key(
            seed_bytes, level, key_function, key_lock
        )

        return self._send_request(
            bytes([SID_SECURITY_ACCESS, level + 1])
            + key_bytes
        )

    def _compute_security_key(
        self, seed_bytes, level, key_function, key_lock=None
    ):
        """Resolve and call the right key algorithm."""

        seed_len = len(seed_bytes)

        # 1) Explicit key_function parameter — locked too when a
        # lock is given, since the test above (and a real
        # Security DLL wrapped as a key_function by a caller)
        # exercises exactly this path; pure-Python callers (e.g.
        # EcuSimulator.compute_key) pay a negligible uncontended
        # lock cost.
        if key_function is not None:
            if key_lock is not None:
                with key_lock:
                    return self._call_key_func(
                        key_function, seed_bytes, level, "key_function"
                    )
            return self._call_key_func(
                key_function, seed_bytes, level, "key_function"
            )

        # 2) Loaded Security DLL
        if self._security_dll_func is not None:
            if key_lock is not None:
                with key_lock:
                    return self._call_dll_key_func(seed_bytes, level)
            return self._call_dll_key_func(seed_bytes, level)

        # 3) Built-in dummy algorithm (unchanged)
        from communication.ecu_simulator import EcuSimulator
        key_buf = bytearray()
        for i in range(0, seed_len, 4):
            chunk = seed_bytes[i:i + 4]
            if len(chunk) < 4:
                chunk = chunk + b'\x00' * (4 - len(chunk))
            seed_int = struct.unpack(">I", chunk)[0]
            key_int = EcuSimulator.compute_key(seed_int)
            key_buf += struct.pack(">I", key_int)
        return bytes(key_buf[:seed_len])

    def _call_dll_key_func(self, seed_bytes, level):
        if self._security_dll_is_bytes:
            return self._security_dll_func(seed_bytes, level)
        return self._call_key_func(
            self._security_dll_func, seed_bytes, level, "Security DLL"
        )
```

(`_call_dll_key_func` is a small extraction of the DLL-branch body that already existed inline —
needed so both the locked and unlocked paths above call one place instead of duplicating it.)

- [ ] **Step 4: Add `security_lock` to `FlashWorker`**

```python
# core/flash_controller.py — __init__() signature: add security_lock=None
# right after security_dll_path=None, and store it:

    def __init__(
        self,
        steps=None,
        datablocks=None,
        uds_client=None,
        use_virtual=True,
        security_dll_path=None,
        security_lock=None,
        keepalive_functional=False,
        ...
    ):
        super().__init__()

        self._abort_requested = False
        self.steps = steps or []
        self.datablocks = datablocks or []
        self._uds_client = uds_client
        self._use_virtual = use_virtual
        self._security_dll_path = security_dll_path
        self._security_lock = security_lock
        self._keepalive_functional = keepalive_functional
        ...

# _execute_security() — pass it through:

    def _execute_security(self, step):
        """SecurityAccess (0x27)."""

        level = step.params.get("level", 1)

        key_func = None
        if self._use_virtual:
            key_func = EcuSimulator.compute_key

        dll_loaded = (
            getattr(self._uds_client, '_security_dll_func', None)
            is not None
        )
        if not self._use_virtual and not dll_loaded:
            self.trace_message.emit(
                "Security Access: no DLL loaded — "
                "using dummy seed/key algorithm"
            )

        self._uds_client.security_access(
            level=level,
            key_function=key_func,
            key_lock=self._security_lock,
        )

        self.information_message.emit(
            "ECU unlocked (Security Access OK)"
        )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m unittest tests.test_uds_client.TestSecurityAccessKeyLock -v`
Expected: PASS

- [ ] **Step 6: Run the full suite and `test_flash_threading.py`**

Run: `python -m unittest discover -s tests -p "test_*.py"` and
`python -m unittest tests.test_flash_threading -v`
Expected: both PASS — every existing `security_access()`/`FlashWorker(...)` call site omits the new
parameters and keeps working exactly as before (default `None` means no locking, same as today).

- [ ] **Step 7: Commit**

```bash
git add communication/uds_client.py core/flash_controller.py tests/test_uds_client.py
git commit -m "Add optional key_lock/security_lock to serialize concurrent Security DLL calls"
```

---

### Task 5: Per-panel Identify flow

**Files:**
- Modify: `gui/parallel_flash.py`
- Test: `tests/test_parallel_flash_threading.py` (new)

**Interfaces:**
- Produces: `ParallelFlashMixin._start_identify_for_panel(self, panel)`,
  `_on_identify_finished_for_panel(self, panel, passed, message)`,
  `_cleanup_identify_thread_for_panel(self, panel)`. Sets `panel["phase"] = "identifying"` while
  running; on success calls `self._start_flash_for_panel(panel, serial)` (Task 6 gives this a real
  body — stub it as a no-op that just records `panel["serial"]` for this task's own tests).
- Consumes: `TestConnectionWorker` (unmodified), `panel` dict from Task 3,
  `ConfigureTabMixin.get_can_config()` (unmodified, shared across all panels).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_parallel_flash_threading.py — new file

import os
import sys
import unittest

from PySide6.QtCore import QTimer

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from tests.qt_test_utils import get_app
from gui.main_window import MainWindow


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


class TestPerPanelIdentify(unittest.TestCase):

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()

    def test_identify_succeeds_against_virtual_ecu_and_captures_serial(self):
        panel = self.window._parallel_panels[0]
        panel["combo"].setCurrentIndex(1)  # Virtual ECU Simulator

        self.window._start_identify_for_panel(panel)
        self.assertEqual(panel["phase"], "identifying")

        _run_until(self.app, lambda: panel["identify_thread"] is None)

        self.assertIsNotNone(panel["serial"])
        self.assertNotEqual(panel["phase"], "identifying")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_parallel_flash_threading.TestPerPanelIdentify -v`
Expected: FAIL — `_start_identify_for_panel` doesn't exist yet.

- [ ] **Step 3: Implement the per-panel Identify flow**

```python
# gui/parallel_flash.py — add imports at the top:

from PySide6.QtCore import QThread

from core.test_connection import TestConnectionWorker

# add these methods to ParallelFlashMixin (replacing the earlier stub
# for _on_parallel_flash_clicked from Task 3 with a real body):

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

        panel["identify_thread"].started.connect(
            panel["identify_worker"].run
        )
        panel["identify_worker"].finished.connect(
            lambda passed, message, p=panel:
                self._on_identify_finished_for_panel(p, passed, message)
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
            lambda p=panel: self._cleanup_identify_thread_for_panel(p)
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

    def _start_flash_for_panel(self, panel, serial):
        # Real body added in Task 6 — for this task, just prove the
        # Identify -> "would start flash" handoff works.
        panel["phase"] = "pass"
```

`_on_identify_finished_for_panel()` needs `ecu_info_message`'s payload, which
`TestConnectionWorker.finished` doesn't carry directly (it only sends `(passed, message)`) — wire
`ecu_info_message` into a per-panel capture the same way `_start_identify()` does for batch, add
right after `panel["identify_worker"] = TestConnectionWorker(...)` in `_start_identify_for_panel()`:

```python
        panel["identify_worker"].ecu_info_message.connect(
            lambda info, p=panel: self._on_parallel_ecu_info(p, info)
        )
```

and add:

```python
    def _on_parallel_ecu_info(self, panel, info_dict):
        if not hasattr(self, '_parallel_last_ecu_info'):
            self._parallel_last_ecu_info = {}
        self._parallel_last_ecu_info[panel["index"]] = info_dict
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_parallel_flash_threading.TestPerPanelIdentify -v`
Expected: PASS

- [ ] **Step 5: Run the full suite and `test_flash_threading.py`**

Run: `python -m unittest discover -s tests -p "test_*.py"` and
`python -m unittest tests.test_flash_threading -v`
Expected: both PASS.

- [ ] **Step 6: Commit**

```bash
git add gui/parallel_flash.py tests/test_parallel_flash_threading.py
git commit -m "Add per-panel Identify flow to Parallel Flash"
```

---

### Task 6: Per-panel Flash flow

**Files:**
- Modify: `gui/parallel_flash.py`
- Test: `tests/test_parallel_flash_threading.py`

**Interfaces:**
- Produces: real `ParallelFlashMixin._start_flash_for_panel(self, panel, serial)` (replaces Task
  5's stub), `_on_panel_flash_finished(self, panel)`, `_on_panel_flash_aborted(self, panel)`,
  `_cleanup_flash_thread_for_panel(self, panel)`.
- Consumes: `FlashWorker` (with `security_lock`, Task 4), `core.flash_sequence.build_flash_sequence`/
  `build_suzuki_slp1_flash_sequence` (unmodified), `ConfigureTabMixin.get_checked_datablocks()`/
  `get_data_format_config()`/`get_tester_serial_number()` (all unmodified, shared across panels),
  `self._parallel_security_lock` (created once per Start — see Task 7 for `parallel_start_all()`;
  for this task, `setup_parallel_flash()` gains a fallback so a single panel's own Flash button
  still works standalone: create the lock lazily if it doesn't exist yet).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_parallel_flash_threading.py

class TestPerPanelFlash(unittest.TestCase):

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()
        ok = self.window._load_firmware_file(
            os.path.join(
                os.path.dirname(__file__), "sample.hex"
            )
        )
        assert ok

    def test_full_identify_then_flash_reaches_pass(self):
        panel = self.window._parallel_panels[0]
        panel["combo"].setCurrentIndex(1)  # Virtual ECU Simulator

        self.window._start_identify_for_panel(panel)
        _run_until(self.app, lambda: panel["identify_thread"] is None)
        _run_until(self.app, lambda: panel["flash_thread"] is None)

        self.assertEqual(panel["phase"], "pass")
        self.assertEqual(panel["progress_bar"].value(), 100)

    def test_second_panel_can_flash_independently(self):
        # Only this panel gets a channel selected/started - proves
        # a single panel's flow doesn't depend on any other panel's
        # state existing.
        panel = self.window._parallel_panels[2]
        panel["combo"].setCurrentIndex(1)

        self.window._start_identify_for_panel(panel)
        _run_until(self.app, lambda: panel["identify_thread"] is None)
        _run_until(self.app, lambda: panel["flash_thread"] is None)

        self.assertEqual(panel["phase"], "pass")
        for other in self.window._parallel_panels:
            if other is not panel:
                self.assertEqual(other["phase"], "idle")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_parallel_flash_threading.TestPerPanelFlash -v`
Expected: FAIL — `panel["phase"]` becomes `"pass"` immediately (Task 5's stub), never sets
`flash_thread`, so `_run_until(... panel["flash_thread"] is None)` returns immediately without ever
having gone through a real flash — the `progress_bar.value() == 100` assertion fails (still 0).

- [ ] **Step 3: Implement the real `_start_flash_for_panel()` and finish/abort handlers**

```python
# gui/parallel_flash.py — add these imports:

import threading

from core.flash_controller import FlashWorker
from core.flash_sequence import (
    build_flash_sequence,
    build_suzuki_slp1_flash_sequence,
)

# replace Task 5's stub _start_flash_for_panel() with:

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
            lambda pct, p=panel: p["progress_bar"].setValue(pct)
        )
        panel["flash_worker"].step_started.connect(
            lambda desc, p=panel: p["status_label"].setText(desc)
        )
        panel["flash_worker"].information_message.connect(
            lambda msg, p=panel: self._log_parallel_panel(p, msg)
        )
        panel["flash_worker"].trace_message.connect(
            lambda msg, p=panel: self._log_parallel_panel(p, msg)
        )

        panel["flash_worker"].flash_finished.connect(
            lambda p=panel: self._on_panel_flash_finished(p)
        )
        panel["flash_worker"].flash_aborted.connect(
            lambda p=panel: self._on_panel_flash_aborted(p)
        )

        panel["flash_thread"].finished.connect(
            lambda p=panel: self._cleanup_flash_thread_for_panel(p)
        )

        panel["flash_thread"].start()

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest tests.test_parallel_flash_threading.TestPerPanelFlash -v`
Expected: PASS

- [ ] **Step 5: Run the full suite and `test_flash_threading.py`**

Run: `python -m unittest discover -s tests -p "test_*.py"` and
`python -m unittest tests.test_flash_threading -v`
Expected: both PASS.

- [ ] **Step 6: Commit**

```bash
git add gui/parallel_flash.py tests/test_parallel_flash_threading.py
git commit -m "Add per-panel Flash flow to Parallel Flash"
```

---

### Task 7: Per-panel Abort, Start All / Abort All

**Files:**
- Modify: `gui/parallel_flash.py`
- Test: `tests/test_parallel_flash_threading.py`

**Interfaces:**
- Produces: real `ParallelFlashMixin._abort_panel(self, panel)` (referenced by Task 5's
  `_on_parallel_flash_clicked()`), real `parallel_start_all(self)`/`parallel_abort_all(self)`
  (replacing Task 3's no-op stubs).
- Consumes: `panel["phase"]`, `panel["identify_thread"]`/`panel["flash_thread"]` (Tasks 5-6).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_parallel_flash_threading.py

class TestAbortAndStartAll(unittest.TestCase):

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()
        ok = self.window._load_firmware_file(
            os.path.join(os.path.dirname(__file__), "sample.hex")
        )
        assert ok

    def test_abort_mid_flash_settles_panel_without_crash(self):
        from parsers.hex_parser import Segment, Datablock
        db = Datablock(file_path="synthetic_parallel.bin")
        db.segments.append(
            Segment(start_address=0x1000, data=bytes([0xAA]) * 200_000)
        )
        self.window._loaded_datablocks = [db]

        panel = self.window._parallel_panels[0]
        panel["combo"].setCurrentIndex(1)
        self.window._start_identify_for_panel(panel)
        _run_until(self.app, lambda: panel["identify_thread"] is None)
        self.assertEqual(panel["phase"], "flashing")

        self.window._abort_panel(panel)
        _run_until(self.app, lambda: panel["flash_thread"] is None)
        self.app.processEvents()

        self.assertIn(panel["phase"], ("fail", "idle"))
        self.assertEqual(panel["flash_button"].text(), "Flash")

    def test_start_all_only_starts_panels_with_a_channel_selected(self):
        panels = self.window._parallel_panels
        panels[0]["combo"].setCurrentIndex(1)  # Virtual
        panels[1]["combo"].setCurrentIndex(1)  # Virtual
        # panels[2], panels[3] left on "Not Selected"

        self.window.parallel_start_all()

        self.assertEqual(panels[0]["phase"], "identifying")
        self.assertEqual(panels[1]["phase"], "identifying")
        self.assertEqual(panels[2]["phase"], "idle")
        self.assertEqual(panels[3]["phase"], "idle")

        _run_until(
            self.app,
            lambda: panels[0]["flash_thread"] is None
            and panels[1]["flash_thread"] is None,
        )
        self.assertEqual(panels[0]["phase"], "pass")
        self.assertEqual(panels[1]["phase"], "pass")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_parallel_flash_threading.TestAbortAndStartAll -v`
Expected: FAIL — `_abort_panel` doesn't exist; `parallel_start_all()` is still Task 3's no-op stub.

- [ ] **Step 3: Implement `_abort_panel`, `parallel_start_all`, `parallel_abort_all`**

```python
# gui/parallel_flash.py

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
```

Also update `buttonParallelAbortAll`'s enabled state to reflect whether *any* panel is busy — add a
small helper called from the finish/abort/identify-start paths added in Tasks 5-6
(`_start_identify_for_panel`, `_on_identify_finished_for_panel`'s early-return branch,
`_on_panel_flash_finished`, `_on_panel_flash_aborted`, `_reset_panel_to_idle`):

```python
    def _update_parallel_abort_all_state(self):
        if not hasattr(self.ui, 'buttonParallelAbortAll'):
            return
        any_busy = any(
            p["phase"] in ("identifying", "flashing")
            for p in self._parallel_panels
        )
        self.ui.buttonParallelAbortAll.setEnabled(any_busy)
```

Call `self._update_parallel_abort_all_state()` at the end of `_start_identify_for_panel()`,
`_reset_panel_to_idle()`, `_on_identify_finished_for_panel()`'s "no ECU detected" branch,
`_on_panel_flash_finished()`, and `_on_panel_flash_aborted()`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest tests.test_parallel_flash_threading.TestAbortAndStartAll -v`
Expected: PASS

- [ ] **Step 5: Run the full suite and `test_flash_threading.py`**

Run: `python -m unittest discover -s tests -p "test_*.py"` and
`python -m unittest tests.test_flash_threading -v`
Expected: both PASS.

- [ ] **Step 6: Commit**

```bash
git add gui/parallel_flash.py tests/test_parallel_flash_threading.py
git commit -m "Add per-panel Abort and Start All / Abort All to Parallel Flash"
```

---

### Task 8: True concurrency — 2+ panels flashing at the same time

Tasks 5-7 gave each panel an independent flow, but no test yet proves 2 panels' real `QThread`s can
run *simultaneously* without interfering — this is the one genuinely new risk class this feature
introduces (every other flash path in this app only ever runs one worker pair at a time).

**Files:**
- Test: `tests/test_parallel_flash_threading.py`

**Interfaces:**
- Consumes: everything from Tasks 5-7. No production code changes expected — this task's job is to
  prove the design already holds up under real concurrency, or surface a bug to fix before moving
  on.

- [ ] **Step 1: Write the test**

```python
# tests/test_parallel_flash_threading.py

class TestGenuineConcurrency(unittest.TestCase):

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()
        ok = self.window._load_firmware_file(
            os.path.join(os.path.dirname(__file__), "sample.hex")
        )
        assert ok

    def test_two_panels_flash_concurrently_and_both_reach_pass(self):
        panels = self.window._parallel_panels
        panels[0]["combo"].setCurrentIndex(1)
        panels[1]["combo"].setCurrentIndex(1)

        self.window._start_identify_for_panel(panels[0])
        self.window._start_identify_for_panel(panels[1])

        # Both identify threads alive at once at some point - not
        # asserted directly (too timing-sensitive), but neither
        # start call may raise/crash back-to-back like this.
        _run_until(
            self.app,
            lambda: panels[0]["flash_thread"] is None
            and panels[1]["flash_thread"] is None,
        )

        self.assertEqual(panels[0]["phase"], "pass")
        self.assertEqual(panels[1]["phase"], "pass")
        self.assertEqual(panels[0]["progress_bar"].value(), 100)
        self.assertEqual(panels[1]["progress_bar"].value(), 100)

    def test_aborting_one_panel_does_not_affect_the_other(self):
        from parsers.hex_parser import Segment, Datablock
        db = Datablock(file_path="synthetic_parallel2.bin")
        db.segments.append(
            Segment(start_address=0x1000, data=bytes([0xAA]) * 200_000)
        )

        panels = self.window._parallel_panels
        panels[0]["combo"].setCurrentIndex(1)
        panels[1]["combo"].setCurrentIndex(1)

        # Panel 0 gets the large payload (still running when we
        # abort it); panel 1 uses whatever's already loaded and is
        # left alone to finish normally.
        self.window._loaded_datablocks = [db]
        self.window._start_identify_for_panel(panels[0])
        _run_until(self.app, lambda: panels[0]["identify_thread"] is None)
        self.assertEqual(panels[0]["phase"], "flashing")

        self.window._start_identify_for_panel(panels[1])
        _run_until(self.app, lambda: panels[1]["identify_thread"] is None)

        self.window._abort_panel(panels[0])
        _run_until(
            self.app,
            lambda: panels[0]["flash_thread"] is None
            and panels[1]["flash_thread"] is None,
        )
        self.app.processEvents()

        self.assertIn(panels[0]["phase"], ("fail", "idle"))
        self.assertEqual(panels[1]["phase"], "pass")
```

- [ ] **Step 2: Run the tests**

Run: `python -m unittest tests.test_parallel_flash_threading.TestGenuineConcurrency -v`
Expected: PASS on the first try if Tasks 5-7's design is sound (each panel's state is fully
self-contained in its own `panel` dict, with no shared mutable state between panels other than the
read-only `get_can_config()`/`get_checked_datablocks()` calls and the `security_lock`, which is
designed to be shared). If it fails, treat it as a real bug in Tasks 5-7 — most likely a lambda
closure capturing the wrong `panel` by reference instead of by the `p=panel` default-argument
pattern used consistently above; fix in `gui/parallel_flash.py`, not by weakening this test.

- [ ] **Step 3: Commit**

```bash
git add tests/test_parallel_flash_threading.py
git commit -m "Add genuine-concurrency regression tests for Parallel Flash"
```

---

### Task 9: `closeEvent()` — stop all busy panels safely

**Files:**
- Modify: `gui/main_window.py:365-403` (`closeEvent()`)
- Test: `tests/test_parallel_flash_threading.py`

**Interfaces:**
- Consumes: `self._parallel_panels`, `self._abort_panel(panel)` (Task 7).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_parallel_flash_threading.py

class TestCloseWindowMidParallelFlash(unittest.TestCase):

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()
        ok = self.window._load_firmware_file(
            os.path.join(os.path.dirname(__file__), "sample.hex")
        )
        assert ok

    def test_close_window_with_two_panels_running_does_not_crash(self):
        panels = self.window._parallel_panels
        panels[0]["combo"].setCurrentIndex(1)
        panels[1]["combo"].setCurrentIndex(1)
        self.window._start_identify_for_panel(panels[0])
        self.window._start_identify_for_panel(panels[1])

        self.window.close()
        self.app.processEvents()
        # No crash/hang reaching this line is the assertion — matches
        # tests/test_flash_threading.py's TestCloseWindowMidFlash style.
```

- [ ] **Step 2: Run test to verify it fails or hangs**

Run: `python -m unittest tests.test_parallel_flash_threading.TestCloseWindowMidParallelFlash -v`
Expected: FAIL or hang — `closeEvent()` doesn't know about `_parallel_panels` yet, so it returns
immediately while 2 identify threads are still alive, and the interpreter either crashes on exit
with "QThread: Destroyed while thread is still running" or the test process hangs waiting on
non-daemon threads.

- [ ] **Step 3: Extend `closeEvent()`**

```python
# gui/main_window.py — closeEvent(), add right after the existing
# self._identify_thread block, before event.accept():

        for panel in getattr(self, '_parallel_panels', []):
            if (panel["flash_thread"] is not None
                    and panel["flash_thread"].isRunning()):
                panel["stopping"] = True
                panel["flash_worker"].request_abort()
                panel["flash_thread"].quit()
                panel["flash_thread"].wait()

            if (panel["identify_thread"] is not None
                    and panel["identify_thread"].isRunning()):
                panel["stopping"] = True
                panel["identify_thread"].quit()
                panel["identify_thread"].wait()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_parallel_flash_threading.TestCloseWindowMidParallelFlash -v`
Expected: PASS

- [ ] **Step 5: Run the full suite and `test_flash_threading.py`**

Run: `python -m unittest discover -s tests -p "test_*.py"` and
`python -m unittest tests.test_flash_threading -v`
Expected: both PASS.

- [ ] **Step 6: Commit**

```bash
git add gui/main_window.py tests/test_parallel_flash_threading.py
git commit -m "Stop all busy Parallel Flash panels in closeEvent()"
```

---

### Task 10: Persist each panel's selected channel

**Files:**
- Modify: `gui/settings_profile.py` (`save_profile()`, `load_profile()`)
- Test: `tests/test_gui_smoke.py`

**Interfaces:**
- Produces: `QSettings` keys `parallel/panel{1..4}/isVirtual`, `parallel/panel{1..4}/channel`,
  `parallel/panel{1..4}/serial` — same shape as the existing `hardware/*` keys.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gui_smoke.py

class TestParallelPanelChannelPersistence(unittest.TestCase):

    def setUp(self):
        self.app = get_app()

    def test_panel_channel_selection_persists_across_restart(self):
        window1 = MainWindow()
        window1._parallel_panels[0]["combo"].setCurrentIndex(1)  # Virtual
        window1.save_profile()

        window2 = MainWindow()
        self.assertEqual(
            window2._parallel_panels[0]["combo"].currentData(), None
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_gui_smoke.TestParallelPanelChannelPersistence -v`
Expected: FAIL — with only the default "Not Selected" applying on restart (`currentData()` would be
`"not-selected"`, not `None`).

- [ ] **Step 3: Add save/load for each panel**

```python
# gui/settings_profile.py — save_profile(), add after the existing
# comboBoxHardware block:

        for i, panel in enumerate(getattr(self, '_parallel_panels', []), start=1):
            data = panel["combo"].currentData()
            if data == "not-selected":
                s.setValue(f"parallel/panel{i}/selected", False)
                continue
            s.setValue(f"parallel/panel{i}/selected", True)
            s.setValue(f"parallel/panel{i}/isVirtual", data is None)
            s.setValue(
                f"parallel/panel{i}/channel",
                data.get("hw_channel", data.get("channel", -1))
                if data is not None else -1
            )
            s.setValue(
                f"parallel/panel{i}/serial",
                (data.get("serial") or -1) if data is not None else -1
            )

# load_profile(), add after the existing comboBoxHardware block:

        for i, panel in enumerate(getattr(self, '_parallel_panels', []), start=1):
            selected = s.value(f"parallel/panel{i}/selected", False, type=bool)
            if not selected:
                continue
            is_virtual = s.value(
                f"parallel/panel{i}/isVirtual", True, type=bool
            )
            channel = s.value(f"parallel/panel{i}/channel", -1, type=int)
            serial = s.value(f"parallel/panel{i}/serial", -1, type=int)
            target = None if is_virtual else (channel, serial)

            combo = panel["combo"]
            for idx in range(combo.count()):
                item_data = combo.itemData(idx)
                if item_data == "not-selected":
                    continue
                key = (
                    None if item_data is None
                    else (
                        item_data.get(
                            "hw_channel", item_data.get("channel")
                        ),
                        item_data.get("serial") or -1,
                    )
                )
                if key == target:
                    combo.setCurrentIndex(idx)
                    break
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_gui_smoke.TestParallelPanelChannelPersistence -v`
Expected: PASS

- [ ] **Step 5: Run the full suite and `test_flash_threading.py`**

Run: `python -m unittest discover -s tests -p "test_*.py"` and
`python -m unittest tests.test_flash_threading -v`
Expected: both PASS.

- [ ] **Step 6: Commit**

```bash
git add gui/settings_profile.py tests/test_gui_smoke.py
git commit -m "Persist each Parallel Flash panel's selected channel across restart"
```

---

### Task 11: Final stress test, walkthrough entry

**Files:**
- Modify: `docs/walkthrough.md`

- [ ] **Step 1: Run the full suite**

Run: `python -m unittest discover -s tests -p "test_*.py"`
Expected: PASS, no regressions from any of Tasks 1-10.

- [ ] **Step 2: Run `tests/test_flash_threading.py` explicitly**

Run: `python -m unittest tests.test_flash_threading -v`
Expected: PASS.

- [ ] **Step 3: Real headless end-to-end pass, per `CLAUDE.md`**

Write a throwaway script (`QT_QPA_PLATFORM=offscreen`) that, in one process without restarting:
loads real firmware, selects Virtual ECU Simulator on 2 Parallel Flash panels, clicks Start All,
waits for both to reach PASS, aborts a 3rd panel mid-flash (large synthetic payload, same technique
as `tests/test_flash_threading.py`), toggles Dark Mode, resizes the window, opens/closes the Test
Connection dialog, then closes the main window — watching for any exception/traceback and a clean
exit code. Remember the two real environment gotchas already discovered and fixed by earlier
scripts this session (do not rediscover them the hard way): never call `window.show()` when the
script's own wait-helper calls `app.quit()` (triggers `closeEvent()` mid-run); check visibility with
`isHidden()`, not `isVisible()`.

- [ ] **Step 4: Add a `docs/walkthrough.md` entry**

Follow the file's existing format exactly (intro paragraph, `### Thay đổi` with bolded file names,
`### Đã kiểm tra` with what was actually verified) — write it in Vietnamese, matching every other
entry in the file. Cover: the new tab and its 4-panel design, the `security_lock`/`key_lock`
addition and why (the risk flagged in Sequential's own spec), the tab-index bug caught during the
spec's self-review and how it was fixed, and the concurrency test results from Task 8.

- [ ] **Step 5: Commit**

```bash
git add docs/walkthrough.md
git commit -m "Document Parallel Flash implementation in walkthrough.md"
```

---

## Self-Review Notes (completed during plan writing, not a step to re-run)

1. **Spec coverage**: §3.1 (N independent worker pairs) → Tasks 5-8. §3.2 (shared combo enumeration)
   → Task 2. §3.3 (per-panel CAN config override) → Tasks 5-6. §3.4 (Identify-then-Flash per panel,
   concurrent across panels) → Tasks 5, 6, 8. §3.5 (Security DLL lock) → Task 4. §3.6 (per-panel
   progress projection) → Task 6. §3.7 (shared Detail/log tabs) → Tasks 3, 5-6 (`_log_parallel_panel`).
   §3.8 (Start All/Abort All) → Task 7. §3.9 (`.ui` changes + the tab-index bug fix) → Task 1. §3.10
   (`gui/parallel_flash.py` method list) → Tasks 3, 5-7. §4 (error handling) → covered across Tasks
   5-9 (no-channel-selected disables Flash in Task 3; No-ECU-detected in Task 5; FAIL/Abort
   independence proven in Task 8; `closeEvent()` in Task 9; duplicate-channel non-validation is a
   documented non-goal, no task needed). §5 (persistence) → Task 10. §6 (testing) → Tasks 5, 6, 8
   (concurrency), 4 (lock serialization test).
2. **Placeholder scan**: no TBD/TODO; every step has real code. The one deliberately-deferred detail
   (exact per-slot method naming) was resolved concretely in Task 3 rather than left open.
3. **Type consistency check performed**: `panel` dict keys (`combo`, `serial_label`, `flash_button`,
   `progress_bar`, `status_label`, `log_widget`, `phase`, `serial`, `stopping`, `identify_thread`,
   `identify_worker`, `flash_thread`, `flash_worker`, `index`) are defined once in Task 3 and used
   identically (same key names) through Tasks 5-10 — verified by re-reading each task's code against
   Task 3's dict literal. `_start_flash_for_panel(panel, serial)`'s signature (Task 5's stub) matches
   Task 6's real implementation exactly. `FlashWorker(security_lock=...)` (Task 6) matches the
   parameter name added in Task 4 (not `security_lock_obj` or similar drift).

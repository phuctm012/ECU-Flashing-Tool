# Parallel Flash — Design

Status: approved by user (design + UI mockup), pending spec review before implementation planning.
Mockup: interactive HTML, reviewed and approved (2 revisions: added the per-channel Identify step,
then moved/renamed tabs to "Single Flash | Parallel Flash | Configure").
Branch: `feature/parallel-flash`, branched from the tip of `feature/sequential-batch-flash` (not yet
merged to `main`) rather than from `main` directly — Parallel Flash reuses several conventions
(`TestConnectionWorker`/`FlashWorker` wiring, mixin composition, `Tools` menu patterns) that only
exist on that branch today. If `feature/sequential-batch-flash` merges to `main` first, this branch
rebases onto `main` before its own PR; if not, the two PRs land in sequence. All work for this
feature (spec, plan, implementation) happens here, not on `main`, per the user's standing preference
for feature work established during Sequential Batch Flash.

## 1. Motivation

SFlash can flash one ECU at a time (Single Flash) or many ECUs one-after-another on one CAN channel
(Sequential Batch Flash, shipped on `feature/sequential-batch-flash`). This was flagged as a separate,
higher-risk feature during that feature's own brainstorming and deferred: flash **multiple ECUs at
the same time**, each on its own physical CAN channel — cutting total flash time roughly N-fold on
a bench with a multi-channel Vector device (VN1640A has 4 channels) instead of running N sequential
single-flashes back to back.

## 2. Scope

**In scope:**
- A new **Parallel Flash** tab, positioned between **Single Flash** (the existing "Flash" tab,
  renamed for clarity now that 3 flashing tabs exist) and **Configure**.
- **4 fixed channel panels** in a 2×2 grid — matches the VN1640A's 4-channel maximum. Not dynamic
  based on how many channels are actually detected; a panel with no real channel selected just sits
  disabled.
- Each panel **independently** picks its own hardware channel (reusing the same real-channel
  detection already used by `comboBoxHardware`) and has its **own** Flash/Abort button, progress
  bar, and one-line status — no shared/global "select N channels then Start" list.
- **One firmware for all 4 channels** — loaded once on the Configure tab, exactly like today. No
  per-panel firmware picker.
- **One set of CAN protocol settings for all 4 channels** (Physical Request/Response CAN ID,
  baudrate, CAN FD, Security DLL, flash sequence choice) — read once from the Configure ->
  Communication page, same as today. Only the **physical hardware channel** differs per panel; this
  follows directly from "one firmware for all channels" (same ECU type wired to every channel).
- **Per-panel Identify step** — reads Serial Number (DID `0xF18C`) via the same `TestConnectionWorker`
  probe as Sequential Batch Flash, once per panel per run (not repeated per ECU — Parallel doesn't
  rotate ECUs on a channel the way Sequential does).
- **Start All / Abort All** convenience buttons — trigger (or abort) every *ready* panel
  independently; a panel with no channel selected is silently skipped by Start All, never blocks it.
- A shared, **tabbed Detail/log area** below the grid (one tab per channel) showing that channel's
  narrative log — avoids 4 duplicate full log panels taking up the whole tab.
- A shared `threading.Lock` serializing Security DLL key-computation calls across concurrently
  running `FlashWorker`s (see §3.5) — the one deliberate, additive exception to "FlashWorker/UdsClient
  are reused unmodified" that Sequential Batch Flash's design established as a goal.

**Explicitly out of scope (non-goals):**
- Per-channel firmware selection (confirmed during brainstorming: always shared).
- More than 4 channels, or a dynamic panel count — fixed at 4 regardless of how many real channels
  a given machine's Vector hardware reports.
- ECU rotation / swap-and-continue on an individual channel — that is what Sequential Batch Flash is
  for; Parallel Flash is single-shot per channel per run. Combining the two (rotate ECUs on each of
  4 channels) is a materially bigger feature, not requested, and not designed here.
- A batch-style report export (HTML, like Sequential's Export Report) — not requested for this
  feature and not in the reviewed mockup. Can be added later as a small follow-up if wanted; the
  per-panel log data already collected would make it straightforward.
- Detecting/preventing two panels being pointed at the same physical channel by operator mistake —
  each panel's channel picker is independent; a real conflict surfaces as a normal connection
  failure on the second panel to actually connect, reported the same way any other flash failure is.
  Not adding cross-panel validation in this pass (YAGNI unless it turns out to be a real footgun).
- Persisting/restoring "which tab was last active" beyond Qt's own default — not a new requirement.

## 3. Architecture

### 3.1 The core structural change: N independent worker pairs, not one

Every existing flash path in this app — Single Flash, Sequential Batch Flash, even `cli.py` — manages
exactly **one** `QThread`/worker pair alive at a time (`self.thread`/`self.worker`, or
`self._identify_thread`/`self._identify_worker`). Parallel Flash is the first feature where up to 4
`TestConnectionWorker`+`FlashWorker` pairs can be genuinely alive **at the same time**, one per panel.

This is handled by giving each of the 4 panels its own independent slot state (not 4 sets of
`self.thread`-style instance attributes glued together ad hoc) — see §3.10. Each slot follows the
**exact same** per-pair lifecycle rules already documented in `CLAUDE.md`'s "Threading model" for
every other worker in this codebase: a worker's own `finished`-family signal connects to
`thread.quit` + `worker.deleteLater`; only a slot connected to `thread.finished` (never the worker's
own signal) clears that slot's thread/worker references. Nothing about the lifecycle rules
themselves changes — they are simply applied 4 times independently instead of once.

### 3.2 Per-panel hardware channel picker — factor out the enumeration logic

`gui/configure_tab.py`'s `populate_hardware_combo()` currently only fills the single global
`comboBoxHardware`. Refactor its body into `populate_hardware_combo_widget(combo)` (parameterized by
target combo box — same "Virtual ECU Simulator" + one item per `detect_vector_channels_with_error()`
result, with `userData` in the same shape), and have `populate_hardware_combo()` call it for the
existing global combo. Reuse `populate_hardware_combo_widget()` for each of the 4 new per-panel
combos, so channel enumeration stays in exactly one place. The existing "Refresh" button
(Configure -> Communication) re-populates the global combo **and** all 4 panel combos together, one
call each, sharing a single `detect_vector_channels_with_error()` result per Refresh click rather
than calling it 5 times.

### 3.3 Per-panel CAN config — shared protocol settings, per-panel channel override

`get_can_config()` (`gui/configure_tab.py`) already returns exactly the dict shape every
`TestConnectionWorker`/`FlashWorker` constructor expects (`channel`, `serial`, `tx_id`, `rx_id`,
`bitrate`, `fd`, `data_bitrate`). It is **not modified**. `ParallelFlashMixin` calls it once per
Start click to get the shared protocol settings, then for each panel makes a shallow copy and
overrides `channel`/`serial` (and drops `label`, which is display-only) from that panel's own combo's
`currentData()`. `use_virtual` is derived the same way `flash_button_clicked()`/`batch_flash.py`
already do it: `combo.currentData() is None`.

### 3.4 Per-panel flow mirrors Sequential's Identify → Flash order; panels run concurrently with each other

Within a single panel, the order is identical to Sequential Batch Flash: `TestConnectionWorker`
(Identify) runs to completion first, then `FlashWorker` (Flash) starts — never two threads alive for
the *same* panel/channel at once, preserving the existing "one active UDS session per CAN interface"
invariant. What's new is that **different panels'** Identify/Flash threads can be alive
**simultaneously**, since each panel connects to its own physical CAN channel independently.

### 3.5 Security DLL concurrency — the flagged risk from Sequential's own spec, now addressed

This is the risk `docs/superpowers/specs/2026-08-30-sequential-batch-flash-design.md` explicitly
deferred Parallel Flash over: `UdsClient.load_security_dll()` (`communication/uds_client.py`) calls
`ctypes.CDLL(dll_path)` independently per `UdsClient` instance, and each `FlashWorker` builds its own
`UdsClient`. If N `FlashWorker`s each load the *same* DLL file and call `GenerateKeyEx`/
`GenerateKeyExOpt` concurrently from N threads, a vendor DLL of unknown thread-safety could return
wrong keys, corrupt internal state, or crash the process — Sequential never hit this because it only
ever ran one `FlashWorker` at a time.

Mitigation — a new optional constructor parameter, not a behavior change for any existing caller:

- `FlashWorker.__init__()` gains `security_lock=None` (a `threading.Lock`, default `None`). When
  set, it is passed down to the point in `communication/uds_client.py` that actually invokes the
  Security DLL's exported function (`_resolve_key_function()`'s DLL branch), and acquired **only**
  around that call — not the whole `security_access()` exchange — so the lock never serializes CAN
  traffic itself, only the one operation of genuinely unknown thread-safety.
- `ParallelFlashMixin` creates **one** `threading.Lock()` per Start/Start-All session and passes the
  *same* instance to every panel's `FlashWorker`. An uncontended `Lock.acquire()`/`release()` costs
  negligible time, so the lock is always created and passed — no conditional branch for "DLL
  configured or not."
- Omitting `security_lock` (every existing call site: Single Flash, Sequential Batch Flash, `cli.py`,
  the full existing test suite) preserves today's exact behavior — this is additive, not a rewrite of
  `FlashWorker`/`UdsClient`'s security-access logic.

### 3.6 Progress/status projection — same worker signals, new per-panel destinations

Each panel wires the same `FlashWorker` signals every other flash path already uses
(`step_started`, `progress_changed`, `information_message`, `trace_message`, `trace_row`,
`segment_progress`, `ecu_info_message`, `flash_finished`, `flash_aborted`) — but instead of routing
into the single shared `stepsTable`/`segmentsTable`/`progressBar` (as Single Flash and Sequential
Batch Flash do), each panel's handlers update only *that panel's* own progress bar, one-line status
label, and its slot's in-memory log line list. No `stepsTable`/`segmentsTable` per panel — matching
the reviewed mockup's "gọn" (compact) per-panel display; that level of step-by-step detail stays
exclusive to the Single Flash tab.

### 3.7 Shared Detail/log area

4 tabs (Channel 1-4), each showing that channel's accumulated narrative log lines (from its
`information_message`/`trace_message` signals) in a plain read-only text widget — mirrors the
mockup's `detail-tabs`/`detail-body`. A panel's log list is cleared and starts fresh each time that
panel's own Start begins (not accumulated across multiple runs the way Sequential's Batch Log is,
since Parallel has no equivalent "keep the whole session's history" requirement — confirmed
out-of-scope: no report export).

### 3.8 Start All / Abort All semantics

**Start All**: iterates the 4 panels; for each one that has a real channel selected (not "Not
Selected") and isn't already busy (Identifying or Flashing), triggers that panel's own start path —
the exact same code path as clicking that panel's own Flash button. A panel with no channel selected
is silently skipped, never blocks the others or shows an error. **Abort All**: iterates panels
currently busy (Identifying or Flashing) and calls each one's own abort path independently.

### 3.9 `gui/main_window.ui` changes

- Rename the existing "Flash" tab's **display text** to "Single Flash" (`<string>Single Flash</string>`
  on its tab-text property). The tab's objectName (`flashTab`) is left unchanged — nothing in the
  codebase looks it up by display text, only by objectName, and CLAUDE.md's rename rule ("grep
  `gui/*.py` for `self.ui.<name>` first") is about renaming an objectName, not a label; no Python
  reference needs to change.
- New tab **"Parallel Flash"**, inserted between the (renamed) Single Flash tab and Configure —
  `parallelFlashTab`, following the naming style of `flashTab`/`configureTab`.
- **Found during spec self-review — a real, easy-to-miss break**: inserting a tab between Single
  Flash (index 0) and Configure shifts Configure from index 1 to index 2. `gui/menu_bar.py`'s
  `action_load_firmware()` and `load_recent_file()` both hardcode
  `self.ui.tabWidget.setCurrentIndex(1)` to jump to Configure after loading a file — both would
  silently land on the new Parallel Flash tab instead once this tab is inserted.
  `tests/test_gui_smoke.py` has 2 matching assertions (`currentIndex() == 1`) that would then pass
  for the wrong reason (or fail, if the implementation is fixed but the tests aren't). Fix: replace
  both hardcoded `setCurrentIndex(1)` calls with
  `self.ui.tabWidget.setCurrentIndex(self.ui.tabWidget.indexOf(self.ui.configureTab))` — looks up
  Configure by its actual widget instead of a magic number, so it can never drift out of sync with
  tab order again — and update the 2 tests' expected index to match. This must land in the same task
  as the tab insertion, not as an afterthought, or those two menu actions silently misbehave the
  moment the new tab exists.
- Inside it, declared directly in the `.ui` XML (Designer can express all of this without a loop):
  a `QGridLayout` with 2 rows × 2 columns holding 4 `QGroupBox` containers (`groupBoxParallelChannel1`
  through `4`) as the panels' outer shells, a `QHBoxLayout` above the grid holding
  `buttonParallelStartAll`/`buttonParallelAbortAll`, and a `QTabWidget` below the grid
  (`tabWidgetParallelDetail`) with 4 tabs ("Channel 1".."Channel 4"), each holding one read-only
  `QTextEdit` (`textEditParallelChannel1Log` etc.).
- Each `QGroupBox` panel's **inner** contents (channel combo, Serial Number label, Flash/Abort
  button, progress bar, status label) are identical ×4 and are constructed in Python at
  `setup_parallel_flash()` time, looped once per panel — the documented exception in CLAUDE.md for
  "logic-driven content" Designer/the `.ui` format can't loop over. The 4 `QGroupBox` shells
  themselves stay Designer-editable (title, position, size policy); only their repeated interior is
  code, mirroring how `flash_tab.py` already adds `statsLabel` at runtime for a single widget, scaled
  to a loop of 4.
- Regenerate `gui/ui_main_window.py` via `pyside6-uic` after the `.ui` edit, as always.

### 3.10 `gui/parallel_flash.py` (new) — `ParallelFlashMixin`

New file, mixed into `MainWindow` alongside `FlashTabMixin`/`ConfigureTabMixin`/`BatchFlashMixin`.
Holds `self._parallel_panels`, a list of 4 slot dicts, each with: its combo box, Flash/Abort button,
progress bar, status label, Serial Number label, log-tab text widget, current phase
(`idle`/`identifying`/`flashing`/`pass`/`fail`/`abort`), and that slot's own
`identify_thread`/`identify_worker`/`flash_thread`/`flash_worker` references (`None` when idle).

Key methods (named per-slot, taking a slot index or the slot dict — exact signature is an
implementation-plan decision, not fixed here):
- `setup_parallel_flash()` — builds the 4 panels' inner widgets, wires each panel's own Flash button,
  wires `buttonParallelStartAll`/`buttonParallelAbortAll`.
- `_parallel_flash_clicked(slot)` — the per-panel button handler: if busy, abort; else start Identify.
- `_start_identify_for_slot(slot)` / `_on_identify_finished_for_slot(slot, passed, message)` —
  mirrors `batch_flash.py`'s `_start_identify()`/`_on_identify_finished()` almost exactly, but scoped
  to one slot's own thread/worker references instead of `self._identify_thread`.
- `_start_flash_for_slot(slot, serial)` — mirrors `batch_flash.py`'s `_start_flash_for_current_ecu()`,
  building a per-slot `FlashWorker` with the shared `security_lock` (§3.5) and this slot's own
  channel-overridden CAN config (§3.3).
- `_on_slot_flash_finished(slot)` / `_on_slot_flash_aborted(slot)` — update that slot's own progress
  bar/status/log only; never touch another slot.
- `_abort_slot(slot)` — cooperative abort for whichever thread (identify or flash) is currently alive
  on that slot, following the same `_batch_stopping`-style guard pattern from CLAUDE.md's Fourth
  failure mode (queued-signal-not-yet-delivered) — applied per slot instead of once globally.
- `start_all()` / `abort_all()` — the two convenience buttons from §3.8.

## 4. Error handling

- No channel selected on a panel → that panel's Flash button stays disabled; Start All silently
  skips it.
- "No ECU detected" during a panel's Identify → that panel's status shows the failure and its button
  resets to ready; no crash, no effect on any other panel.
- FAIL (an NRC during flashing) on one panel → that panel shows FAIL; the other 3 panels are
  completely unaffected — no cross-panel coupling of any kind.
- Abort on one panel → only that panel's own thread pair receives `request_abort()`; the other
  panels' threads are never touched.
- `closeEvent()` must be extended to stop-and-wait every *busy* slot's thread(s) — up to 4 identify
  threads and 4 flash threads — not just the single existing `self.thread`/`self.worker`. Each slot
  needs its own `_slot_stopping`-style guard (the Fourth failure mode pattern from `CLAUDE.md`) set
  before that slot's `quit()`/`wait()`, since that slot's own queued completion signal could still be
  in flight when the window is closing — exactly the bug class Sequential Batch Flash hit and fixed
  for its single pair, now needed independently per slot.
- Two panels pointed at the same physical channel by operator mistake — not specially
  detected; the second one to actually connect gets whatever error `CanInterface.connect()` already
  raises for a channel that's busy/unavailable, surfaced as a normal flash failure for that panel
  only (see Scope: explicitly not adding cross-panel validation in this pass).

## 5. Settings & persistence

- Each panel's last-selected channel persists across app restart via `QSettings`, one key per panel
  (e.g. `parallel/panel1/channel`, `parallel/panel1/serial` — mirroring the existing
  `hardware/channel`+`hardware/serial` pair already used for the global `comboBoxHardware` in
  `settings_profile.py`), so a fixed bench wiring (Channel 1 -> ECU position A, etc.) doesn't need
  re-selecting every launch.
- No new persistence for the Detail tabs' log contents — they are session-only, matching every other
  narrative log in the app.

## 6. Testing (for the implementation plan)

- Real-`QThread` tests mirroring `tests/test_batch_flash_threading.py`'s discipline, extended to
  cover the one genuinely new risk class: **multiple real `QThread`s running concurrently in the
  same test process** (Sequential never needed this — it only ever ran one at a time). At minimum:
  2 panels started together both reach PASS independently; aborting one panel mid-flash does not
  affect the other's progress; both panels' `closeEvent()` cleanup paths are exercised together.
- A dedicated test for the shared `security_lock`: two concurrent `FlashWorker`s given a mocked
  `key_function` that records overlapping-call timestamps (e.g. via a shared list guarded by the same
  lock the test asserts on) — verifies calls are actually serialized, not just that the parameter is
  accepted.
- `tests/test_flash_threading.py` re-run unmodified after every change, per `CLAUDE.md` — especially
  important here since `FlashWorker.__init__()` gains a new parameter that every existing call site
  must remain compatible with.
- Full `CLAUDE.md` stress-test protocol before any push: full suite, `test_flash_threading.py`
  explicitly, and a real headless end-to-end pass that includes at least 2 panels flashing
  concurrently via the Virtual ECU Simulator, one aborted mid-flight, in the same process without
  restarting.

## 7. Open items resolved during brainstorming

- Same firmware for every channel — confirmed; no per-channel firmware picker.
- Fixed 4 panels (matches VN1640A's 4-channel maximum), each independently picks its own hardware
  channel and has its own Flash/Abort button — not a single "select which of the detected channels to
  use" list feeding one Start button.
- Compact per-panel detail (progress bar + one status line); full log lives in a shared tabbed Detail
  area below the grid, not 4 duplicate full log panels.
- A new standalone **Parallel Flash** tab, not a `Tools > Mode` toggle inside an existing tab —
  the 4-panel grid layout is different enough from Single Flash's single progress bar/steps/segments
  layout that mode-switching within one tab (Sequential's approach) doesn't fit.
- Start All / Abort All convenience buttons added on top of the 4 independent per-panel buttons,
  without changing their independent nature (still 4 separate calls underneath).
- Per-panel Identify step (DID `0xF18C`) confirmed required, reusing `TestConnectionWorker` exactly
  like Sequential — but once per panel per run, not repeated per ECU (Parallel doesn't rotate ECUs).
- Existing "Flash" tab renamed to "Single Flash" for clarity now that 3 flashing-related tabs exist;
  final tab order: **Single Flash, Parallel Flash, Configure**.
- Security DLL concurrency (the risk Sequential's own spec flagged and deferred Parallel over) is
  addressed via a shared `threading.Lock` threaded through a new optional `FlashWorker` parameter —
  the one deliberate, additive exception to "FlashWorker/UdsClient reused unmodified."

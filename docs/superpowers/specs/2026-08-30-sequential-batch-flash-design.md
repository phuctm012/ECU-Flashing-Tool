# Sequential Batch Flash — Design

Status: approved by user (design + UI mockup), pending spec review before implementation planning.
Mockup: interactive HTML, reviewed and approved (integrated-into-Flash-tab revision — a first pass
with a standalone "Batch Flash" tab was reviewed and explicitly rejected in favor of this one).
Branch: `feature/sequential-batch-flash` — all work for this feature (spec, plan, implementation)
happens here, not on `main`, per the user's explicit request.

## 1. Motivation

Today SFlash flashes exactly one ECU per run: pick firmware, click Flash, done. Production/bench
use often needs the same firmware flashed to many ECUs in a row on the same CAN channel, with a
per-unit PASS/FAIL record for traceability. This feature adds that as an alternate **mode** of the
existing Flash tab, reusing its live per-run view rather than building a parallel workflow
elsewhere.

Deferred to a separate, later spec: flashing multiple ECUs *simultaneously* across multiple CAN
channels ("Parallel" batch flash) — a materially different, higher-risk feature (concurrent
`QThread` workers, multi-channel hardware, unknown Security Access DLL thread-safety) that the user
explicitly chose to sequence after this one during brainstorming.

## 2. Scope

**In scope:**
- A new **Batch Flash** mode for the existing Flash tab, switched via **Tools → Mode → Flash /
  Batch Flash** (mutually exclusive, `QActionGroup`).
- Manual ECU swap confirmation — the operator clicks the same primary button (relabeled "Next
  ECU") after physically swapping the ECU. No automatic bus-based swap detection.
- Automatic ECU identification per unit: reads Serial Number (DID `0xF18C`) via a lightweight,
  read-only probe before each flash — the same mechanism as **Tools → Test Connection**,
  independent of the flash sequence itself. Works identically for the Generic and Suzuki SLP1
  sequences, since it never touches `FlashStep`/`build_flash_sequence()` — this matters because
  the Suzuki sequence deliberately has no `ReadDataByIdentifier` steps of its own (must stay
  byte-for-byte trace-matched, per `CLAUDE.md`).
- Unlimited batch size — the operator stops the session explicitly (**Stop Batch**); no upfront
  target count.
- Continue-on-failure — one ECU's FAIL doesn't halt the batch; log it and let the operator proceed
  to the next unit.
- Three distinct per-unit outcomes — **PASS**, **FAIL**, **ABORTED** (operator-initiated) — tracked
  separately in the tally, the log, and the report.
- An in-tab Batch Log table and an HTML batch report export (Export-Report-style), available any
  time the log has at least one row.

**Explicitly out of scope (non-goals):**
- Parallel multi-channel batch flashing (separate future spec).
- Automatic ECU-swap detection (bus polling) — considered and rejected during brainstorming: with
  no explicit disconnect/reconnect signal, the tool can't reliably distinguish "the same ECU is
  still connected" from "a new ECU came in at the same address."
- A target/upfront batch count or progress-against-goal — rejected in favor of unlimited + manual
  stop.
- A "Clear Batch Log" action — the log persists for the app session; export before starting a new
  batch if a clean report is needed. Can be added later if requested.
- CSV export — HTML only, matching the existing Export Report format exactly.
- Changing the loaded firmware or CAN config mid-batch is not blocked or specially tracked — the
  Data/Communication tabs stay editable while a batch is between units, and the report doesn't
  record a per-unit firmware selection. If that turns out to matter in practice, disabling those
  tabs once a batch has started is a small follow-up, not a redesign.

## 3. Architecture

### 3.1 No new `QThread`-based worker class

The two pieces of real work — identifying an ECU and flashing it — already exist as independent,
already-hardened `QObject`+`QThread` pairs:

- **Identify** reuses `TestConnectionWorker` (`core/test_connection.py`) **unmodified**. It already
  reads `TEST_CONNECTION_DIDS` (Supplier SW Version, HW Version, **ECU Serial Number**, ECU SW
  Number, SW Version) and emits them via `ecu_info_message`; the batch orchestrator only pulls the
  `"ECU Serial Number"` key out of that dict. No new probe class, no DID-list trimming — the extra
  ~4 DID reads are a few cheap request/response round trips, not worth a bespoke variant.
- **Flash** reuses `FlashWorker` (`core/flash_controller.py`) **unmodified** — same construction,
  same signal wiring `flash_button_clicked()` already does for a normal single flash.

These two run **sequentially, never concurrently**: the Identify `QThread` fully finishes
(`thread.finished` fires, cleaned up) before the Flash `QThread` is even created. At any moment
during a batch run, exactly one `QThread` is alive — exactly like today's single flash. This
sidesteps the entire class of risk a Parallel/multi-channel design would carry (N workers alive at
once, N Security DLL calls in flight).

The only genuinely new code is GUI-thread-side orchestration — "Identify succeeded, so start
Flash", "Flash finished, so log the row and wait" — which already runs on the GUI thread (it reacts
to signals and touches widgets), so it needs no `QThread` of its own.

### 3.2 `gui/batch_flash.py` (new) — `BatchFlashMixin`

New mixin, same composition pattern as `FlashTabMixin`/`ConfigureTabMixin` (`MainWindow` gains it
via multiple inheritance, shares `self.ui`). Keeps `gui/flash_tab.py` from absorbing an unrelated
concern, matching this codebase's stated preference for smaller, focused files.

Owns:

- **Mode state**: `self._batch_mode_active` (bool), toggled by `actionModeFlash`/
  `actionModeBatchFlash` (see §3.4).
- **Batch session state**: current ECU index (starts at 1 on "Start Batch", incremented after
  every logged unit — a "No ECU detected" retry does not advance it), running tallies (pass/fail/
  abort counts), and the list of completed-unit records (serial, timestamp, result, duration, fail
  reason) backing both the Batch Log table and the report export.
- **`_batch_main_button_clicked()`** — the batch-mode counterpart to `flash_button_clicked()`'s
  single-flash logic, invoked via a 2-line branch at the very top of `flash_button_clicked()`:
  `if self._batch_mode_active: self._batch_main_button_clicked(); return`. **The existing
  single-flash body is untouched.** While in the Flashing state, this same handler's "Abort" branch
  sets a local `self._batch_operator_abort = True` flag *before* calling
  `self.worker.request_abort()` — this flag is how PASS/FAIL/ABORTED get told apart (see §3.6).
- **`_start_identify()`** — constructs `TestConnectionWorker` + `QThread`, following the exact
  lifecycle rules `CLAUDE.md`'s "Threading model" section documents: `worker.finished` connects to
  `thread.quit` + `worker.deleteLater`; only `thread.finished` clears
  `self._identify_thread`/`self._identify_worker`.
- **`_on_identify_finished(passed, message)`** — `passed=False` → "No ECU detected" status
  (retryable, not logged, doesn't touch the counter or tally); `passed=True` → reads the captured
  Serial Number, calls `_start_flash_for_current_ecu(serial)`.
- **`_start_flash_for_current_ecu(serial)`** — builds the flash sequence/`FlashWorker` exactly as
  `flash_button_clicked()` does today (same firmware/CAN config, read once from the Data/
  Communication tabs), reusing `prepare_flash_ui()` so `stepsTable`/`segmentsTable`/the progress bar
  behave identically to a normal single flash. Reuses the existing "no firmware loaded" guard
  (`get_checked_datablocks()` empty → warn, don't start) before ever constructing a worker.
- **`_on_batch_unit_finished()`** — connected to both `flash_finished` (→ PASS) and `flash_aborted`
  (→ FAIL or ABORTED, see §3.6) — appends a row to the Batch Log table and the internal record
  list, updates the tally, advances the ECU counter, relabels the button "Next ECU".
- **`export_batch_report()`** — see §3.5.

### 3.3 `gui/flash_tab.py` — minimal touch

The only change is the 2-line branch described in §3.2. Nothing else in this file changes —
`prepare_flash_ui()`, `on_step_started()`, and every other existing signal handler stay exactly as
they are; `BatchFlashMixin` reuses them rather than duplicating them.

### 3.4 `gui/main_window.ui` — new widgets

Per `CLAUDE.md`'s ".ui first" rule:

- `menuTools` gains a `menuMode` submenu with two checkable `QAction`s (`actionModeFlash`, checked
  by default, and `actionModeBatchFlash`) in a `QActionGroup` — native Qt exclusive-radio-menu
  behavior — placed after the existing Tools actions (Flash / Abort / Test Connection...).
- The Flash tab's `verticalLayout_flashTab` gains a third item, after the existing header row and
  the steps/segments row: a container `groupBoxBatchFlash` (`visible=false` by default), holding:
  - A control-row layout: `labelEcuCounter`, `labelBatchTally`, a stretch spacer,
    `buttonStopBatch`, `buttonExportBatchReport`.
  - `labelBatchStatus` / `labelBatchStatusCaption` (status line + italic caption, mirroring the
    mockup).
  - `tableWidgetBatchLog` — 5 columns (`#`, `Serial Number`, `Timestamp`, `Result`, `Duration`),
    same `QTableWidget` conventions as `stepsTable`/`segmentsTable` (alternating row colors,
    non-editable). `Result` cells are colored the same way `segmentsTable` already colors status
    cells — `item.setBackground(QColor(bg)); item.setForeground(QColor(fg))` via the existing
    `_status_colors()` helper — not a new embedded-widget pattern. The mockup's rounded "pill"
    look is a web approximation of a colored cell; the real widget is a plain colored
    `QTableWidgetItem`, exactly like every other status-colored table in this app.
  - The mockup's "Preview an outcome" row is a review-only device for exploring every state
    quickly — **not** part of the real UI; omitted from the actual widget tree.
- `BatchFlashMixin.setup_batch_flash()` wires `actionModeFlash.toggled`/
  `actionModeBatchFlash.toggled` to toggle `groupBoxBatchFlash.setVisible()` and relabel
  `flashButton`'s idle-state text ("Flash" vs "Start Batch"/"Next ECU").

### 3.5 Batch report export

A new function alongside `gui/report_export.py`'s existing helpers, producing an HTML document in
the same visual style as the existing single-flash Export Report:

- Summary header: firmware file name + checksum, CAN config used (channel / radar side /
  sequence), session start time, total PASS / FAIL / ABORTED counts.
- Detail table: one row per completed unit — `#`, Serial Number, Timestamp, Result, Duration, Fail
  Reason (blank unless FAIL).
- Same `QFileDialog.getSaveFileName(..., "HTML Files (*.html)")` pattern as
  `report_export.py::export_report()`.

### 3.6 Telling PASS / FAIL / ABORTED apart

`FlashWorker.flash_finished` and `flash_aborted` are **bare signals — no payload.** Today's
single-flash code doesn't need to distinguish *why* a flash didn't finish; batch mode does. Two
mechanisms, both reusing existing signals unmodified rather than changing `FlashWorker`:

- **Operator abort vs. a real failure**: the batch orchestrator's own Abort-button handler sets
  `self._batch_operator_abort = True` *immediately before* calling `self.worker.request_abort()`.
  When `flash_aborted` then fires, this flag — not the signal itself — is what's checked: set →
  **ABORTED**; unset → **FAIL** (the worker hit an error or a real UDS failure on its own).
- **The FAIL reason text**: `FlashWorker.run()` already emits a specific `information_message`
  right before `flash_aborted` in every one of its non-operator failure paths (`_execute_step()`'s
  `except Exception as e: information_message.emit(f"Error: {e}")`, plus the equivalent messages
  for a connection-setup failure). The orchestrator keeps a running `self._last_information_message`
  updated on every `information_message` signal (already connected for the live Information log)
  and uses its value as the FAIL row's reason when `flash_aborted` fires without the operator-abort
  flag set. No `FlashWorker` change needed — reading an existing signal's payload is enough.

Third status color: `config/settings.py` currently defines exactly `STATUS_COLOR_RUNNING`/`DONE`/
`ERROR` (+ dark variants) for 2 semantic outcomes plus a running state. Batch Flash needs a
distinct ABORTED color, deliberately not conflated with FAIL (see §2). Reuses the existing
`STATUS_COLOR_RUNNING` amber (`#FCE9B5` / `#4a3d1f` dark) — "running" never appears *in the Batch
Log itself* (a unit is only logged once finished), so there's no risk of the two meanings
colliding on screen. No new color constant needed.

## 4. Error handling

- **No ECU detected** (`TestConnectionWorker.finished(False, ...)`): shown as a status message,
  not logged as a batch row — doesn't advance the ECU counter or consume a tally slot. The operator
  can immediately retry (same button) once the ECU is actually connected.
- **Flash failure** (a real error during the flash sequence, `flash_aborted` without the
  operator-abort flag): logged as **FAIL**, with the captured reason text shown in the Batch Log
  row and included in the exported report.
- **Operator abort** (clicking "Abort" mid-flash): logged as **ABORTED**, distinct from FAIL — see
  §3.6.
- **Stop Batch clicked mid-flash**: aborts the in-flight unit immediately — the same synchronous
  `request_abort()` + `thread.quit()` + `thread.wait()` sequence `MainWindow.closeEvent()` already
  uses for the single-flash thread, never a queued/deferred abort — logs it as ABORTED, then ends
  the session (button resets to "Start Batch"; the Batch Log table stays populated).
- **Switching Mode mid-batch**: `actionModeFlash`/`actionModeBatchFlash` are disabled while either
  `self.thread is not None` (a Flash `QThread` is alive) or `self._identify_thread is not None` (an
  Identify `QThread` is alive) — two separate thread references (§3.2), both checked — the same
  "don't let something change out from under a running operation" guard already implicit in
  `flash_button_clicked()`'s own re-entrancy check.

## 5. Settings & persistence

- The active Mode (Flash / Batch Flash) persists via `gui/settings_profile.py`, the same
  "save on every change, load at startup" convention already used for Hardware / Radar Side /
  Flash Sequence / Security DLL path — new key `flash/mode` (`"flash"` default, or `"batch"`).
- The Batch Log's contents are **not** persisted (in-memory only, cleared on app restart) — the
  exported HTML report is the durable record, matching how `traceTable`/`informationText` aren't
  persisted either.

## 6. Testing (for the implementation plan)

- `tests/test_gui_smoke.py`: Mode menu toggles `groupBoxBatchFlash` visibility and `flashButton`'s
  label correctly; Batch Log table populates/colors rows correctly for all 3 outcomes; tally/
  counter update correctly; Export Batch Report writes the expected HTML structure (mirroring the
  existing `TestReportExport`); Mode actions disabled while a thread is alive; Mode persists across
  a simulated restart (`TestSettingsProfile` pattern).
- `tests/test_batch_flash_threading.py` (new, mirroring `tests/test_flash_threading.py`'s
  discipline exactly — `CLAUDE.md`'s documented history of `QThread` lifecycle crashes means this
  must go through real `QThread` runs, never a synchronous worker call): a full
  Identify → Flash → PASS cycle end-to-end; a FAIL cycle; an operator-Abort-mid-flash cycle; a "No
  ECU detected" retry that doesn't advance the counter; Stop Batch mid-flash cleaning up whichever
  thread (Identify or Flash) is in flight without a crash; several sequential units back-to-back
  (mirroring `TestRepeatedFlashRuns`).
- Reuses the Virtual ECU Simulator for all of the above — `EcuSimulator` already answers
  `ReadDataByIdentifier` for DID `0xF18C` (exercised today by `DEFAULT_FLASH_SEQUENCE`'s existing
  ReadDID step), so no simulator changes are needed for the Identify probe to work against it.

## 7. Open items resolved during brainstorming

- Scope: **Sequential (same-channel) batch only** for this spec — Parallel (multi-channel)
  deferred to a separate, later spec.
- ECU swap detection: **manual confirmation** (button click), not automatic bus polling.
- ECU identification: **automatic, via DID `0xF18C`**, reusing `TestConnectionWorker`'s existing
  probe mechanism unmodified.
- Batch size: **unlimited**, operator-stopped.
- Failure handling: **continue on FAIL**, not a hard stop.
- Report: **HTML export**, matching the existing Export Report format/mechanism.
- UI placement: **integrated into the existing Flash tab** (not a separate tab), switched via
  **Tools → Mode → Flash / Batch Flash**, with `flashButton` reused for both modes — revised
  mid-brainstorm after the first mockup pass, when the user reconsidered the standalone-tab
  approach in favor of reusing the Flash tab's live view directly.
- Branch: implementation happens on `feature/sequential-batch-flash`, not `main`, per explicit
  user request.

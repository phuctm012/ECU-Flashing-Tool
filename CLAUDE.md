# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

FFlash (v1.1) — a PySide6 desktop app that flashes ECU firmware over CAN using UDS (ISO 14229). It can run against a fully-simulated Virtual ECU (no hardware) or real Vector VN1640A/VN1630 hardware via `python-can`.

## Commands

```bash
# Setup (conda/miniforge recommended — see requirements.txt for the exact deps)
conda activate pyside6
pip install -r requirements.txt          # PySide6 only; python-can is commented out
pip install python-can                   # only needed for real Vector hardware

# Run the app (GUI)
python main.py

# Run the app (CLI — see README.md for full flag reference)
python cli.py info tests/sample.hex
python cli.py flash tests/sample.hex --dry-run
python cli.py flash tests/sample.hex --sequence suzuki --radar-side s1
python cli.py test-connection --sequence suzuki --verbose  # session + DID reads only, no Security Access, no Erase/Download

# Run all tests
python -m unittest discover -s tests -p "test_*.py" -v

# Run one test file / one test
python -m unittest tests.test_parsers -v
python -m unittest tests.test_flash_threading.TestSingleFlashRun -v

# Regenerate gui/ui_main_window.py after editing gui/main_window.ui in Qt Designer
pyside6-uic gui/main_window.ui -o gui/ui_main_window.py
```

There is no configured linter/formatter in this repo — don't invent lint commands.

## Rules

- **GUI changes go in `gui/main_window.ui` first, `.py` code second.** If a change can be expressed as a widget/layout/property in the `.ui` XML (adding a widget, moving it, changing a size policy, resize behavior, etc.), make it there and regenerate `gui/ui_main_window.py` with `pyside6-uic` — don't build the equivalent widget by hand in Python. Only fall back to Python-side widget construction for things Designer/the `.ui` format genuinely can't express (e.g. logic-driven content). See "GUI: mixin composition + Designer/generated-code split" below.
- **Before ending a session that touched the app, run the full test suite and confirm the app itself still launches without crashing** — `python -m unittest discover -s tests -p "test_*.py" -v`, and if `gui/flash_tab.py` or anything QThread-related changed, don't skip `tests/test_flash_threading.py` specifically (see "Threading model" below for why). A green test run is not optional polish here — this codebase has a history of a real crash (`QThread: Destroyed while thread is still running`) that shipped silently because it was only exercised by hand.
- **Name every widget and layout in `gui/main_window.ui` meaningfully — never leave Designer's auto-numbered defaults** (`verticalLayout_2`, `horizontalLayout_3`, `label_5`, ...). Use a name that says what it is/where it lives, matching the existing style: `verticalLayout_flashTab`, `horizontalLayout_checksumMethod`, `verticalLayout_comm`. If you add or move a widget and it still has a generic Designer name, rename it before moving on. Before renaming an existing one, `grep` `gui/*.py` for `self.ui.<name>` first — a few layouts are referenced directly at runtime (e.g. `flash_tab.py` adds `statsLabel` into `horizontalLayout_flashHeader`), so the Python side must be updated in the same change.
- **Before pushing "all of today's session changes" to the remote repo (i.e. the user explicitly asks for a push covering the whole session, not a one-off `git push` of something already reviewed), stress test the app first — this is stronger than the regular pre-end-of-session test run above, specifically to catch crashes isolated unit tests miss.** Run: (1) the full test suite, (2) `tests/test_flash_threading.py` explicitly even if nothing there looks touched, (3) a real headless end-to-end pass through the actually-running app (`QT_QPA_PLATFORM=offscreen`, e.g. a throwaway `python -c "..."` script — not just constructing `MainWindow()` and closing it) that chains several real actions back-to-back in one process without restarting: load real firmware, flash to completion via the Virtual ECU, start and abort a second flash mid-run, toggle Dark Mode, resize the window, open and close a dialog (e.g. Test Connection), close the main window — watching for any exception/traceback and a clean exit code. If it all passes, push directly, no need to ask again. If anything fails or crashes, **stop and report what broke — do not push on your own judgment**; wait for the user to say whether to keep debugging now, or push anyway with a note added to `docs/gui_todo.md` for later.
- **Before implementing a new feature the user requests (not a bug fix, not a change to something already agreed on), briefly brainstorm whether it actually fits this app first** — does it match FFlash's purpose (flashing ECU firmware over CAN/UDS) and its existing architecture/conventions, is the scope clear, does it risk conflicting with something already in place. If it clearly fits, go ahead and implement it in the same turn — no need to ask first. If something about the fit is genuinely unclear or questionable (scope creep, overlaps/conflicts with an existing feature, doesn't obviously belong in an ECU flashing tool), stop and lay out the concern for the user instead of implementing on your own judgment, and wait for their decision before writing code.

## Architecture

### Layering

`gui/` → `core/` → `communication/` → `parsers/`, with `config/settings.py` holding shared constants (hardware option lists, default CAN config, app metadata). GUI code never talks to CAN/UDS directly — it always goes through `core.flash_controller.FlashWorker`.

### GUI: mixin composition + Designer/generated-code split

`gui/main_window.py`'s `MainWindow` is composed via multiple inheritance from `FlashTabMixin` (`gui/flash_tab.py`) and `ConfigureTabMixin` (`gui/configure_tab.py`), both `QMainWindow`. All three share one `self.ui` (a `Ui_MainWindow` instance from `gui/ui_main_window.py`) and call each other's methods directly (e.g. `flash_tab.py` calls `self.get_can_config()`, defined in `configure_tab.py`).

**`gui/main_window.ui` is the source of truth; `gui/ui_main_window.py` is generated** — never hand-edit the generated file. Edit the `.ui` XML directly (there's no Qt Designer GUI available in this environment; edits are made as raw XML via the Edit tool) and regenerate with `pyside6-uic`. Widgets added purely at runtime in Python (bypassing the `.ui`) are a maintenance smell in this codebase — several were migrated into `.ui` XML specifically so Designer could show them (see `docs/walkthrough.md` Phase 4.8). Both files live in `gui/` alongside the mixins that use them (moved there from the project root in Phase 4.28 for consistency).

### Threading model — read before touching `flash_tab.py`

`FlashWorker` (in `core/flash_controller.py`) is a plain `QObject` moved to a `QThread` via `moveToThread()` in `flash_tab.py`'s `flash_button_clicked()`. **`FlashWorker.flash_finished`/`flash_aborted` are emitted from inside `FlashWorker.run()` itself, before `run()` returns** — i.e., while the worker thread is still actively executing. Any slot connected to those two signals must never touch `self.thread`/`self.worker` (dropping the last Python reference to a `QThread` object while it's still running crashes with `QThread: Destroyed while thread is still running`, a real bug that was hit and fixed in Phase 4.13 of `docs/walkthrough.md`). The only safe place to null out `self.thread`/`self.worker` is `_cleanup_thread()`, connected to `self.thread.finished` — Qt's own signal that fires only once the thread has genuinely stopped.

Corollary for testing: calling `FlashWorker.run()` directly (synchronously, bypassing `QThread`) — which is what `tests/test_flash_controller.py` does to test flash-sequence logic cheaply — **cannot catch thread-lifecycle bugs**. `tests/test_flash_threading.py` exists specifically to exercise the real `QThread` path and must be re-run after any change to `flash_button_clicked()`'s signal wiring.

**Second failure mode, hit while building `gui/test_connection_dialog.py` (Phase 4.34): a `closeEvent()` deadlock, not a crash.** If a worker/thread pair relies on `worker.finished.connect(thread.quit)` to stop the thread (rather than calling `thread.quit()` directly), that connection is a **queued** cross-thread call — `thread.quit` only actually runs once the thread that owns the `QThread` *object* (usually the main/GUI thread) gets back to its own event loop. If a `closeEvent()` (or any main-thread code) calls `thread.wait()` synchronously in response to the user closing a window mid-run, it blocks that very event loop — so the queued `quit()` can never be delivered, and `wait()` never returns. Fix: call `thread.quit()` **directly** (not via a signal) immediately before `thread.wait()` — `QThread.quit()` is thread-safe and fine to call from any thread, same as `MainWindow.closeEvent()` already does for the flash thread (`self.worker.request_abort(); self.thread.quit(); self.thread.wait()`). Any new QThread-based dialog in this codebase should follow that same order.

**Third failure mode, hit in Phase 4.36: `FlashWorker.run()` starts `UdsClient.start_keepalive()` (a real, plain `threading.Thread` — `communication/tester_present.py`'s `TesterPresentThread`, sending `TesterPresent` every 2s) *unconditionally* near the top of `run()`, before it even knows how many steps there are.** Every path out of `run()` after that point **must** call `self._cleanup()` (which calls `stop_keepalive()` + disconnects the CAN interface) before emitting `flash_finished`/`flash_aborted` — the empty-steps (`total_steps == 0`) early return was missing it, which left the keepalive thread running forever, still calling back into `_on_uds_trace()`/emitting `trace_row` on a `FlashWorker` nothing else referenced anymore. That produces an intermittent (timing-dependent, not every run) `RuntimeError: Signal source has been deleted` crash symptom minutes later, in an apparently unrelated part of a test run — exactly the kind of bug this project's "always run the full suite" rule exists to catch, even though it doesn't fail any single test's assertions (it's a background-thread exception, invisible to the test that leaked it). If you add a new early-return path to `run()`, it needs `self._cleanup()` too.

### CAN / UDS layer

`communication/can_interface.py` defines the abstract `CanInterface` (`connect`/`send`/`receive`/`set_filter`) plus `CanMessage`. Two concrete implementations, both providing `send_isotp(data, target_id=None)` / `receive_isotp(timeout)` for ISO-TP framing:
- `virtual_can.py` — in-memory bus wired directly to `ecu_simulator.py`'s `EcuSimulator`, which implements the ECU-side UDS state machine (session/security/download) so the whole flash flow can run with zero hardware.
- `vector_can.py` — real hardware via `python-can` (`interface='vector'`), lazily imports `can` so the app runs fine without `python-can` installed when only using the simulator.

`uds_client.py`'s `UdsClient` wraps a `CanInterface` and implements the ISO 14229 services (session control, security access, routine control, download/transfer, DID read/write, DTC/comm control, tester present). Notable behaviors:
- **Functional vs physical addressing**: pass `functional=True` to send to `UdsClient(functional_id=...)` instead of the physical request ID — used by the Suzuki sequence for network-wide session/DTC/comm-control steps (see below).
- **NRC retry**: `_send_request()` retries on `RETRYABLE_NRC` (busy, conditions-not-correct) and loops on `0x78` ResponsePending using `p2_star_timeout`, independent of the retry counter.
- **Security key resolution order**: explicit `key_function` arg > loaded Security DLL (`load_security_dll()`, `ctypes`) > `EcuSimulator.compute_key()` fallback — the fallback is intentional even on real hardware when the target ECU also runs "dummy" security access.
- **`RequestDownload` (0x34) byte order**: ISO 14229-1 requires `SID, dataFormatIdentifier, addressAndLengthFormatIdentifier, address, size`. This was previously swapped in both `uds_client.py` (encode) and `ecu_simulator.py` (decode) — self-consistent so the Virtual ECU never caught it, only found by diffing against a real ECU trace log. If you touch `request_download()` or `_handle_download()`, keep both sides in sync and re-run `tests/test_uds_client.py::TestRequestDownloadByteOrder`.

### Flash sequences are data, not code

`core/flash_sequence.py` defines `FlashStep` (a step type + params dict) and two step lists: `DEFAULT_FLASH_SEQUENCE` (generic/simulated) and `SUZUKI_SLP1_FLASH_SEQUENCE` (reverse-engineered from a real capture, `docs/*_Report_Trace.csv` — see `docs/walkthrough.md` Phase 4.6 for the byte-level analysis). `build_flash_sequence()` / `build_suzuki_slp1_flash_sequence()` take a step-list template plus the loaded datablocks and splice in one `TYPE_DOWNLOAD` step per segment right after the step named `"Erase Memory"`. `FlashWorker._execute_step()` dispatches each step to an `_execute_*` handler by `step.step_type`; adding a new step type means adding both a `FlashStep.TYPE_*` constant and a handler.

The Suzuki sequence differs from the generic one in several ISO-14229-relevant ways that must stay consistent with the real trace if extended further: no `ReadDataByIdentifier` calls at all, a single `0xFF00` routine call (no separate precondition-check step), functional addressing for the first three steps and the final post-reset session check, a 5-byte `RequestDownload` address field, and `optionRecord` bytes on the erase/verify routine calls.

### Trace/logging: two parallel channels into two different widgets

`FlashWorker` emits `trace_message` (plain narrative strings — "Executing: ...", errors) and `trace_row` (structured dicts built by `_on_uds_trace()`, which pairs a TX with its final RX — collapsing intermediate `0x78` ResponsePending frames into one row, matching how the reference CSV trace looks). In `gui/main_window.py`, `log_trace()` renders narrative messages as `SYSTEM` rows and `log_trace_row()` renders structured rows, both into the same `traceTable` (columns match `docs/*_Report_Trace.csv`: Request/Response Timestamp, Target/Source, Data). Information tab (`informationText`, plain `QTextEdit`) and Trace tab (`traceTable`, `QTableWidget`) have independent right-click "Save Log" actions saving `.txt` and `.csv` respectively — see `_write_log_file()` vs `_write_trace_table_csv()`, both split from their dialog-opening counterparts specifically so tests can exercise the write logic without popping a real `QFileDialog`/`QMessageBox`.

### Hardware combo is never hardcoded — always detected

`comboBoxHardware` starts with exactly one item, "Virtual ECU Simulator" (`userData=None`), and `gui/configure_tab.py`'s `populate_hardware_combo()` (called at startup and from the "Refresh" button) appends one entry per real Vector channel returned by `communication.vector_can.detect_vector_channels()`, each with `userData=<full channel dict>` (keys: `channel` / `hw_channel` / `serial` / `is_on_bus` / `label`). There used to be 6 hardcoded "VN1640A/VN1630 - Channel N" placeholder entries in `main_window.ui` regardless of whether any hardware was actually connected — removed because they were misleading (looked selectable/real but weren't backed by anything). `detect_vector_channels()` calls into `can.interfaces.vector.canlib.get_channel_configs()` wrapped in a broad `try/except` returning `[]` on any failure (no `python-can`, no XL Driver, nothing plugged in) — treat all of those as normal states, not something to surface as an error. `get_can_config()` reads channel info from `comboBoxHardware.currentData()` (a dict or None), not by parsing the display text — don't reintroduce text-parsing (e.g. regex on "Channel N") for this.

**Channel identification via serial number**: `VectorCanInterface.connect()` passes the device `serial` number (when available) to `can.Bus(serial=...)`, which tells python-can to select the physical hardware channel directly — bypassing `xlGetApplConfig` and the application channel mapping in Vector Hardware Config entirely. This was added because the global `channel_index` from the driver doesn't match the per-application channel index that `xlGetApplConfig` expects, causing `"Channel N of application 'FlashTool' is not assigned to any interface"` errors on real hardware. If the user's python-can doesn't support the `serial` parameter (pre-4.x), the code falls back to the old `app_name`+`channel_index` path — in that case Vector Hardware Config setup (README section B) is still required.

### CAN bus conflict warning (CANoe/CANalyzer/CANape)

Before touching real hardware (never for the Virtual ECU Simulator), the app checks for a likely conflict with another Vector desktop tool — added because users forget CANoe is still open with a measurement running, and its own TesterPresent/diagnostic traffic can collide with this tool's UDS session. Two independent, best-effort, never-raising signals feed this: `communication.vector_can.detect_running_vector_tools()` (Windows-only, greps `tasklist` output for `canoe`/`canalyzer`/`canape` process names) and the `is_on_bus` field on each dict from `detect_vector_channels()` (straight from the Vector driver, signals *some* app already has the selected channel open, regardless of which one). `gui/configure_tab.py`'s `ConfigureTabMixin.detect_can_conflict_warning()` combines both into one warning string (or `None`) — it does not filter by which hardware is selected; that's the caller's job. `gui/flash_tab.py`'s `flash_button_clicked()` only calls it when `use_virtual` is False, and on a hit shows a Yes/No `QMessageBox` defaulting to **No** (selecting No aborts the flash start before `prepare_flash_ui()` runs, so nothing gets cleared). `cli.py`'s `_warn_can_conflict()` mirrors the same two signals but only prints to stderr and continues — it must never prompt interactively, since the CLI has to stay scriptable.

### CLI (`cli.py`) and the shared firmware-parser dispatch

`cli.py` is a second entry point (`info`/`flash`/`list-hardware`/`test-connection` subcommands, argparse) that drives the same `core.flash_sequence`/`core.flash_controller` layer as the GUI, headless — no widgets are created, only Qt's signal/slot mechanism is used to receive progress. `parsers/auto_parser.py`'s `parse_firmware_file(path, base_address=...)` holds the extension → parser routing (`.hex` → HEX, `.s19`/`.s3`/etc. → S-Record, `.bin` → Binary at `base_address`) and is the **single shared source of truth** for that routing — both `cli.py` and `gui/configure_tab.py`'s `_parse_firmware_file()` call it. If you add a new firmware format/extension, add it here, not in either caller.

`test-connection` deliberately does **not** go through `build_flash_sequence()`/`FlashStep` — it only reuses `FlashWorker._setup_uds_client()` for CAN/UDS connection setup (virtual vs. Vector, Security DLL loading, trace wiring), then drives `UdsClient` calls directly inside its own `try/finally` in `cmd_test_connection()`. The linear FlashStep sequence aborts-on-first-failure and has no way to guarantee a cleanup step runs when an earlier step fails — but `test-connection` promises to always try to restore the ECU to Default session (re-enabling DTC/Communication if it disabled them) no matter where it stopped, which needs that `finally`. Keep this in mind if you're tempted to reuse `build_suzuki_slp1_flash_sequence()` for a "partial" run instead — it won't give you that guarantee.

`cli.py` uses `QApplication` (not the lighter `QCoreApplication`) specifically so it can share one Qt singleton instance with GUI code when both run in the same process — Qt allows only one `QCoreApplication`-family instance per process, and once one exists you cannot swap it for a different subclass. This mattered concretely: `tests/test_cli.py` and `tests/test_gui_smoke.py` both run under `python -m unittest discover`, in the same process — a `QCoreApplication` created first would make every later `QApplication.instance()` call (needed to construct `MainWindow`) fail with "Cannot create a QWidget without QApplication". If you ever need a truly minimal headless entry point again, make sure nothing else in the same test run needs real widgets, or keep everyone on `QApplication`.

## Reference docs

- `README.md` — user-facing setup/usage (Vietnamese).
- `docs/walkthrough.md` — phase-by-phase development log; read it before assuming something is unimplemented, and check it for the reasoning behind non-obvious decisions (e.g. why `RequestDownload` uses specific byte orders, why the Suzuki sequence has no `ReadDID` calls).
- `docs/*_Report_Trace.csv` — real ECU CAN trace used to validate `SUZUKI_SLP1_FLASH_SEQUENCE` and the byte-order fix above.

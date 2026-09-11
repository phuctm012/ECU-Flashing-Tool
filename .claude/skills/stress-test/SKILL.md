# Stress Test

Full stress test before pushing session changes — catches crashes that isolated unit tests miss. This mirrors the mandatory pre-push protocol in `CLAUDE.md`'s "Rules" section (the authority if this ever drifts from that): required before any push that covers a whole session's changes, and worth running any time `gui/flash_tab.py`, `gui/parallel_flash.py`, `gui/batch_flash.py` or other QThread-related code changed.

## Steps

1. Run the full test suite (slow — around 25-30 minutes here, so start it first and let it run while you do steps 2 and 3):
   ```bash
   python -m unittest discover -s tests -p "test_*.py"
   ```

2. Run the threading tests explicitly, even if nothing there looks touched:
   ```bash
   python -m unittest tests.test_flash_threading tests.test_parallel_flash_threading \
                      tests.test_batch_flash_threading tests.test_test_connection_dialog
   ```

3. Run the headless end-to-end stress test — real actions chained back to back in one process, against the real app:
   ```bash
   python tools/stress_test.py
   ```

   Takes about a minute and prints `STRESS_RESULT=PASS` or `STRESS_RESULT=FAIL` plus a checkpoint count. Sections can be run alone while narrowing something down:
   ```bash
   python tools/stress_test.py --section parallel --section races
   python tools/stress_test.py --list
   ```

   What it covers:
   - **single** — a flash to completion, a second one aborted mid-run, Dark Mode, resize, a real Test Connection dialog
   - **batch** — Batch Flash mode: three units (one aborted mid-flight), Stop Batch, Export Report
   - **parallel** — six channels flashed concurrently and repeatedly; a second wave started while the first is live; a lone channel flashed after a group finished; partial aborts; Start All / Abort All; Dark Mode, View Log, reports and the settings dialog exercised *while threads are running*; the window closed with six channels mid-flash
   - **races** — aborts timed into every phase of start-up (0/5/20/60/150/400 ms), double Start All and double Abort All, Start All on top of running channels, tab switching mid-flash, and the window closed during **Identify**, before any flash worker exists

4. If everything passes, report success. If anything fails, report what broke — do **NOT** push. Follow `CLAUDE.md`'s decision protocol: stop and wait for the user to choose between debugging now or pushing anyway with a note added to `docs/gui_todo.md`.

## Reading the result

`tools/stress_test.py` fails on two independent things, and both matter:

- **A failed checkpoint** — an assertion about app state (a channel never settled, a thread reference was left behind, `close()` took too long, meaning a deadlock).
- **A Qt threading warning** — the script installs a Qt message handler, so messages like `QThread: Destroyed while thread is still running` and `Signal source has been deleted` fail the run instead of scrolling past in stderr. These raise no Python exception and are the main symptom of the crash class this whole protocol exists to catch. `faulthandler` is armed too, so a hard segfault still prints a Python traceback.

## If you extend the script

Four traps, each of which has already cost a debugging session here:

1. **Never `app.quit()` to end a wait loop.** It closes every window, and `MainWindow.closeEvent()` aborts a running flash — so every step after the first wait would run against a closed window and a cancelled flash. Use a local `QEventLoop` and quit *that*, as `pump_until()` does.
2. **The theme comes from `main.py`, not `MainWindow`.** Call `app.setStyleSheet(load_stylesheet(...))` if the visual state matters.
3. **Never connect a lambda to a cross-thread signal.** A lambda has no `QObject` thread affinity, so PySide6 cannot detect that the call needs queuing and it silently runs on the wrong thread. To close a modal dialog, pump the event loop (see `pumped_exec()`).
4. **Do not judge thread leaks with `threading.active_count()`.** Qt worker threads leave `_DummyThread` placeholders in Python's registry that linger until GC and always report `is_alive() == True`, so that count climbs to a plateau even when nothing leaked. Check what actually matters: no surviving `TesterPresent` keepalive thread (`CLAUDE.md`'s documented leak, which surfaces later as `Signal source has been deleted`), and no real non-Qt worker threads. Ground truth for a suspected leak is the OS thread count (`ps -M <pid>`), which should stay flat across many flashes.

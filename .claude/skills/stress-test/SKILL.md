# Stress Test

Full stress test before pushing session changes — catches crashes that isolated unit tests miss. This mirrors the mandatory pre-push protocol in `CLAUDE.md`'s "Rules" section (the authority if this ever drifts from that): required before any push that covers a whole session's changes, and worth running any time `gui/flash_tab.py` or other QThread-related code changed.

## Steps

1. Run the full test suite:
   ```bash
   python -m unittest discover -s tests -p "test_*.py" -v
   ```

2. Run threading tests explicitly (even if nothing there looks touched):
   ```bash
   python -m unittest tests.test_flash_threading -v
   ```

3. Run a headless end-to-end pass through the running app — real actions chained back-to-back in one process, not just constructing `MainWindow()` and closing it:
   ```bash
   QT_QPA_PLATFORM=offscreen python -c "
   import os
   import sys
   from PySide6.QtCore import QTimer
   from PySide6.QtWidgets import QApplication

   app = QApplication.instance() or QApplication(sys.argv)

   from gui.main_window import MainWindow
   from gui.test_connection_dialog import TestConnectionDialog
   from parsers.hex_parser import parse_hex_file

   w = MainWindow()
   w.show()

   def run_until(predicate, timeout_ms=15000, interval_ms=20):
       state = {'elapsed': 0, 'satisfied': False}
       def tick():
           if predicate():
               state['satisfied'] = True
               app.quit()
           elif state['elapsed'] >= timeout_ms:
               app.quit()
           else:
               state['elapsed'] += interval_ms
       timer = QTimer()
       timer.setInterval(interval_ms)
       timer.timeout.connect(tick)
       timer.start()
       app.exec()
       timer.stop()
       if not state['satisfied']:
           raise RuntimeError('timed out waiting for: ' + predicate.__doc__)
       return True

   sample_hex = os.path.join('tests', 'sample.hex')
   assert os.path.exists(sample_hex), 'tests/sample.hex not found'

   # Flash to completion via the Virtual ECU
   w._loaded_datablocks = [parse_hex_file(sample_hex)]
   w.flash_button_clicked()
   run_until(lambda: w.thread is None and w.worker is None)

   # Start a second flash and abort it mid-run
   w._loaded_datablocks = [parse_hex_file(sample_hex)]
   w.flash_button_clicked()
   QTimer.singleShot(80, w.flash_button_clicked)
   run_until(lambda: w.thread is None and w.worker is None)

   # Toggle Dark Mode (real slot, not a guessed method name)
   w.action_toggle_dark_mode(True)
   w.action_toggle_dark_mode(False)

   # Resize
   w.resize(1024, 768)
   w.resize(800, 600)

   # Open and close a dialog (Test Connection, against the Virtual ECU)
   dialog = TestConnectionDialog(w, True, None, False, w.get_can_config())
   QTimer.singleShot(500, dialog.close)
   dialog.exec()

   # Close the main window
   w.close()
   app.processEvents()
   print('Stress test PASSED')
   "
   ```

4. If all pass, report success. If anything fails, report what broke — do NOT push. Follow `CLAUDE.md`'s decision protocol: stop and wait for the user to choose between debugging now or pushing anyway with a note added to `docs/gui_todo.md`.

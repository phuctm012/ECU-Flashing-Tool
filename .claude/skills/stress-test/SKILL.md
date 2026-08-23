# Stress Test

Full stress test before pushing session changes — catches crashes that isolated unit tests miss.

## Steps

1. Run the full test suite:
   ```bash
   python -m unittest discover -s tests -p "test_*.py" -v
   ```

2. Run threading tests explicitly (even if nothing there looks touched):
   ```bash
   python -m unittest tests.test_flash_threading -v
   ```

3. Run a headless end-to-end pass through the running app:
   ```bash
   QT_QPA_PLATFORM=offscreen python -c "
   import sys
   from PySide6.QtWidgets import QApplication
   app = QApplication.instance() or QApplication(sys.argv)

   from gui.main_window import MainWindow
   w = MainWindow()
   w.show()

   # Load sample firmware
   from config.settings import DEFAULT_HEX_FILE
   import os
   hex_path = os.path.join('tests', 'sample.hex')
   if os.path.exists(hex_path):
       w._parse_firmware_file(hex_path)

   # Toggle dark mode
   if hasattr(w, 'toggle_dark_mode'):
       w.toggle_dark_mode()
       w.toggle_dark_mode()

   # Resize
   w.resize(1024, 768)
   w.resize(800, 600)

   # Close
   w.close()
   app.processEvents()
   print('Stress test PASSED')
   "
   ```

4. If all pass, report success. If anything fails, report what broke — do NOT push.

# Regenerate UI

Regenerate `gui/ui_main_window.py` from the Designer `.ui` file.

## Steps

1. Run pyside6-uic:
   ```bash
   pyside6-uic gui/main_window.ui -o gui/ui_main_window.py
   ```

2. Verify the generated file has no syntax errors:
   ```bash
   python -c "import gui.ui_main_window"
   ```

3. Report success or any errors.

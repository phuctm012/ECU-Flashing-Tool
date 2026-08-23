# Test

Run the full test suite for the ECU Flashing Tool.

## Steps

1. Run all tests:
   ```bash
   python -m unittest discover -s tests -p "test_*.py" -v
   ```

2. If any GUI or QThread-related code was changed, also run the threading tests explicitly:
   ```bash
   python -m unittest tests.test_flash_threading -v
   ```

3. Report results: which tests passed, which failed, and any tracebacks.

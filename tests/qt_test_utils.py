# ==================================================
# Shared QApplication for GUI tests
# ==================================================
#
# Qt only allows one QApplication instance per process.
# unittest runs every test module in the same process
# (via `python -m unittest discover`), so any test that
# needs a QApplication must reuse the same one instead of
# constructing its own.
# ==================================================

from PySide6.QtWidgets import QApplication

_app = None


def get_app():
    """Return the single shared QApplication for tests."""

    global _app

    existing = QApplication.instance()

    if existing is not None:
        _app = existing
    elif _app is None:
        _app = QApplication([])

    return _app

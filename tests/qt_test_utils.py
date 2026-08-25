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

import tempfile

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QSettings

_app = None


def get_app():
    """
    Return the single shared QApplication for tests.

    Also points QSettings(IniFormat, ...) at a fresh throwaway
    .ini file on every call. MainWindow persists a settings
    profile (Hardware/Radar Side/Security DLL/Flash Sequence —
    see gui/settings_profile.py, which explicitly constructs
    QSettings with IniFormat rather than the platform-native
    format specifically so this redirect works); left alone,
    QSettings.setPath() only affects QSettings objects
    constructed after the call, so leaving this unset would
    have every test share the SAME store for the whole
    process — leaking state between test methods (e.g. one
    test picking Radar Side S1 would make a later test's
    "defaults to S0" assertion fail). Every GUI test's setUp()
    calls get_app() immediately before constructing
    MainWindow(), so resetting the path here gives each test
    method its own clean slate.
    """

    global _app

    QSettings.setPath(
        QSettings.IniFormat,
        QSettings.UserScope,
        tempfile.mkdtemp(prefix="sflash_test_settings_"),
    )

    existing = QApplication.instance()

    if existing is not None:
        _app = existing
    elif _app is None:
        _app = QApplication([])

    return _app

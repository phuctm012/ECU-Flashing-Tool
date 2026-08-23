# ==================================================
# App-wide QSS Stylesheet & App Icon
# ==================================================
#
# load_stylesheet() returns the "Engineering Blue" theme
# (resources/style.qss) as text, applied once via
# app.setStyleSheet(...) in main.py. Never raises — if the
# file is missing for any reason, returns "" and the app
# just falls back to the default Qt Fusion look, exactly the
# same as before this theme existed.
# ==================================================

import os
import sys

# Running from source: resources/style.qss and
# resources/icons/ sit one level up from this file
# (gui/style.py -> gui/ -> project root -> resources/).
# Running as a PyInstaller --onefile .exe: bundled data files
# are extracted to sys._MEIPASS at startup instead (see
# build.bat's --add-data), so prefer that when it's set —
# same pattern as gui/menu_bar.py's _GUIDELINE_PATH.
_PROJECT_ROOT = (
    sys._MEIPASS if hasattr(sys, "_MEIPASS")
    else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
STYLE_PATH = os.path.join(_PROJECT_ROOT, "resources", "style.qss")
DARK_STYLE_PATH = os.path.join(_PROJECT_ROOT, "resources", "style_dark.qss")
ICON_PATH = os.path.join(
    _PROJECT_ROOT, "resources", "icons", "flash_bolt_blue.ico"
)


def load_stylesheet(dark=False):

    path = DARK_STYLE_PATH if dark else STYLE_PATH

    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def is_dark_mode_enabled():
    """
    Read the persisted Dark Mode preference (docs/gui_todo.md
    item #15) directly via QSettings, independent of MainWindow —
    main.py needs this *before* constructing MainWindow, so the
    very first paint already uses the right theme instead of
    flashing light-then-dark. Uses the same IniFormat/org/app
    QSettings identity as gui/settings_profile.py's self._settings,
    so both read/write the same on-disk store.

    Defaults to False (Light Mode) for a fresh install that has
    never explicitly saved a preference — once the user toggles
    View > Dark Mode either way, that explicit choice always
    wins on every future read.
    """

    from PySide6.QtCore import QSettings
    from config.settings import APP_AUTHOR, APP_NAME

    settings = QSettings(
        QSettings.IniFormat, QSettings.UserScope, APP_AUTHOR, APP_NAME,
    )
    return settings.value("appearance/darkMode", False, type=bool)

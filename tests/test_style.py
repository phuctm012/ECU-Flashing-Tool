# ==================================================
# Stylesheet Loader Tests
# ==================================================

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from gui.style import (
    load_stylesheet,
    is_dark_mode_enabled,
    ICON_PATH,
    DARK_STYLE_PATH,
)


class TestLoadStylesheet(unittest.TestCase):

    def test_reads_file_contents(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".qss", delete=False
        ) as f:
            f.write("QPushButton { color: red; }")
            path = f.name

        try:
            with mock.patch("gui.style.STYLE_PATH", path):
                css = load_stylesheet()
            self.assertEqual(css, "QPushButton { color: red; }")
        finally:
            os.unlink(path)

    def test_missing_file_returns_empty_string_not_raise(self):
        with mock.patch("gui.style.STYLE_PATH", "/no/such/file.qss"):
            css = load_stylesheet()
        self.assertEqual(css, "")

    def test_dark_true_reads_dark_style_path_not_light(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".qss", delete=False
        ) as f:
            f.write("QPushButton { color: white; }")
            dark_path = f.name

        try:
            with mock.patch("gui.style.DARK_STYLE_PATH", dark_path), \
                 mock.patch("gui.style.STYLE_PATH", "/no/such/light.qss"):
                css = load_stylesheet(dark=True)
            self.assertEqual(css, "QPushButton { color: white; }")
        finally:
            os.unlink(dark_path)


class TestShippedStylesheetContent(unittest.TestCase):
    """
    Regression guard for the actual shipped resources/style.qss —
    catches accidental deletion/typos of the Engineering Blue
    palette values the user approved in the preview artifacts
    (docs/gui_todo.md item #10).
    """

    def test_shipped_file_has_engineering_blue_palette(self):
        css = load_stylesheet()
        self.assertIn("#2b579a", css)  # accent
        self.assertIn("#4a7fd6", css)  # accent-soft
        self.assertIn("#eef3fa", css)  # accent-bg
        self.assertIn("#f4f6f9", css)  # bg

    def test_shipped_file_styles_flash_button_and_hover_states(self):
        css = load_stylesheet()
        self.assertIn("QPushButton#flashButton", css)
        self.assertIn("QPushButton:hover", css)
        self.assertIn("QPushButton:pressed", css)


class TestShippedDarkStylesheetContent(unittest.TestCase):
    """
    Regression guard for resources/style_dark.qss (docs/gui_todo.md
    item #15) — must exist and actually style the same key
    selectors as the light theme, just with a dark palette.
    """

    def test_shipped_dark_file_exists(self):
        self.assertTrue(
            os.path.isfile(DARK_STYLE_PATH),
            f"Dark stylesheet not found at {DARK_STYLE_PATH}",
        )

    def test_shipped_dark_file_styles_flash_button_and_hover_states(self):
        css = load_stylesheet(dark=True)
        self.assertIn("QPushButton#flashButton", css)
        self.assertIn("QPushButton:hover", css)
        self.assertIn("QPushButton:pressed", css)

    def test_both_themes_style_section_header_labels(self):
        # gui/main_window.ui's Datablocks/Details/Hardware/...
        # section labels used to hardcode a light-only
        # "background-color: #E0E0E0" inline styleSheet — unreadable
        # in dark mode (near-white QWidget text on a near-white
        # bar). Fixed by tagging them with a "sectionHeader"
        # dynamic property and letting each theme style it.
        self.assertIn(
            'QLabel[sectionHeader="true"]', load_stylesheet(dark=False)
        )
        self.assertIn(
            'QLabel[sectionHeader="true"]', load_stylesheet(dark=True)
        )

    def test_both_themes_set_alternate_row_background(self):
        # traceTable has alternatingRowColors=True (main_window.ui) —
        # without an explicit "alternate-background-color", Qt
        # falls back to the OS default palette's AlternateBase for
        # every other row (a fixed light gray, independent of
        # theme), making every other trace row unreadable against
        # Dark Mode's near-white default text (user screenshot:
        # SYSTEM rows fine, TX/RX data rows in between nearly
        # invisible).
        self.assertIn("alternate-background-color", load_stylesheet(dark=False))
        self.assertIn("alternate-background-color", load_stylesheet(dark=True))

    def test_both_themes_style_header_view_and_corner_button(self):
        # QHeaderView::section alone only styles the numbered
        # cells of a table's row-number gutter — the header
        # widget's own background beyond the last section (and
        # the tiny QTableCornerButton square above it) fell back
        # to the OS default white, an ugly bright column on a
        # short table in Dark Mode (reported by user screenshot).
        for css in (load_stylesheet(dark=False), load_stylesheet(dark=True)):
            self.assertIn("QHeaderView {", css)
            self.assertIn("QTableCornerButton::section", css)

    def test_both_themes_style_text_edit(self):
        # informationText (QTextEdit) had no QSS selector at all —
        # harmless in light mode (default white bg + near-black
        # QWidget text), but unreadable in dark mode (near-white
        # QWidget text on the default white QTextEdit background).
        self.assertIn("QTextEdit", load_stylesheet(dark=False))
        self.assertIn("QTextEdit", load_stylesheet(dark=True))

    def test_both_themes_style_plain_text_edit(self):
        # gui/test_connection_dialog.py's logText is a
        # QPlainTextEdit — a separate Qt class from QTextEdit
        # (both derive from QAbstractScrollArea, neither from
        # the other), so the "QTextEdit { ... }" rule above never
        # applied to it: same near-invisible dark-on-dark text
        # bug as informationText, reported by user screenshot in
        # the Test Connection dialog specifically.
        self.assertIn("QPlainTextEdit", load_stylesheet(dark=False))
        self.assertIn("QPlainTextEdit", load_stylesheet(dark=True))


class TestDarkModePreference(unittest.TestCase):
    """
    is_dark_mode_enabled() reads its own QSettings instance
    (same IniFormat/org/app identity as
    gui/settings_profile.py's self._settings) — redirect it to a
    throwaway .ini per test the same way tests/qt_test_utils.py
    does for GUI tests, so this doesn't touch the real user
    profile or leak state between test methods.
    """

    def setUp(self):
        from PySide6.QtCore import QSettings
        QSettings.setPath(
            QSettings.IniFormat, QSettings.UserScope,
            tempfile.mkdtemp(prefix="fflash_test_settings_"),
        )

    def test_defaults_to_false_when_never_set(self):
        # Fresh install, never toggled — defaults to Light Mode.
        self.assertFalse(is_dark_mode_enabled())

    def test_reflects_saved_value(self):
        from PySide6.QtCore import QSettings
        from config.settings import APP_AUTHOR, APP_NAME
        settings = QSettings(
            QSettings.IniFormat, QSettings.UserScope,
            APP_AUTHOR, APP_NAME,
        )
        settings.setValue("appearance/darkMode", True)
        settings.sync()
        self.assertTrue(is_dark_mode_enabled())


class TestIconPath(unittest.TestCase):
    """
    Regression guard for the app icon (docs/gui_todo.md item #12):
    the .ico shipped in resources/icons/ must actually exist at
    the path gui.main_window.MainWindow.__init__() feeds to
    QIcon() — a silent typo here would just show no icon at all,
    with no test ever failing.
    """

    def test_shipped_icon_file_exists(self):
        self.assertTrue(
            os.path.isfile(ICON_PATH),
            f"App icon not found at {ICON_PATH}",
        )


if __name__ == "__main__":
    unittest.main()

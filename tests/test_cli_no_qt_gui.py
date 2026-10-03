# ==================================================
# The CLI Must Stay QtCore-Only
# ==================================================
#
# cli.py drives QObject workers (they report progress through
# Signals), so PySide6.QtCore is unavoidable. QtGui and
# QtWidgets are not: the CLI creates no QApplication, because
# every command runs its worker on the thread that owns it —
# the sequential ones on the main thread, each parallel channel
# on the thread that built its own worker — so every emit is a
# direct call and no event loop is involved.
#
# Two things would quietly undo that, and both are easy to do by
# accident:
#
#   * re-adding `QApplication.instance() or QApplication(...)`
#     somewhere, "to be safe";
#   * importing anything from gui/ into cli.py or core/ (a
#     helper, a constant), which pulls the widget stack in
#     behind it.
#
# Either one triples the Qt payload a frozen CLI carries
# (QtCore ~23 MB against ~82 MB with QtGui + QtWidgets, measured
# in the dev env) and re-introduces the single-instance hazard
# the test suite used to trip over. Nothing fails visibly when
# it happens — the CLI keeps working, the .exe just gets fat —
# so it needs a test.
#
# Each check runs in a FRESH subprocess on purpose: under
# `unittest discover` the GUI tests have already imported
# QtWidgets into this process, so asserting on sys.modules here
# would always fail.
# ==================================================

import os
import subprocess
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

SAMPLE_HEX = os.path.join(REPO, "tests", "sample.hex")

GUI_QT_MODULES = ("PySide6.QtWidgets", "PySide6.QtGui")

PROBE = '''
import sys
sys.argv = ["cli.py"] + {argv!r}
import cli

rc = cli.main(sys.argv[1:])

from PySide6.QtCore import QCoreApplication

print("RC", rc)
print("APP", QCoreApplication.instance())
for name in ("PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets"):
    print("MOD", name, name in sys.modules)
'''


def _probe(argv):
    """
    Runs one CLI command in a fresh interpreter and reports
    which Qt modules it loaded and whether an application
    object was created.
    """

    result = subprocess.run(
        [sys.executable, "-c", PROBE.format(argv=argv)],
        cwd=REPO, capture_output=True, text=True,
    )

    loaded = {}
    rc = None
    app = None

    for line in result.stdout.splitlines():
        if line.startswith("MOD "):
            _, name, flag = line.split()
            loaded[name] = flag == "True"
        elif line.startswith("RC "):
            rc = int(line.split()[1])
        elif line.startswith("APP "):
            app = line[4:].strip()

    if rc is None:
        raise AssertionError(
            f"probe did not finish.\nstdout:\n{result.stdout}"
            f"\nstderr:\n{result.stderr}"
        )

    return rc, app, loaded


class TestCliLoadsNoWidgetStack(unittest.TestCase):

    def _assert_qtcore_only(self, argv, expected_rc=0):
        rc, app, loaded = _probe(argv)

        self.assertEqual(rc, expected_rc, f"{argv} returned {rc}")
        self.assertTrue(
            loaded["PySide6.QtCore"],
            "QtCore is expected — the workers are QObjects",
        )
        for name in GUI_QT_MODULES:
            self.assertFalse(
                loaded[name],
                f"{argv[0]} imported {name}. The CLI must stay "
                f"QtCore-only: see this file's header. Something "
                f"re-added a QApplication, or imported gui/.",
            )
        self.assertEqual(
            app, "None",
            f"{argv[0]} created an application object "
            f"({app}); the CLI needs none.",
        )

    def test_flash(self):
        self._assert_qtcore_only(
            ["flash", SAMPLE_HEX, "--quiet"]
        )

    def test_test_connection(self):
        self._assert_qtcore_only(["test-connection", "--quiet"])

    def test_batch(self):
        self._assert_qtcore_only(
            ["batch", SAMPLE_HEX, "--count", "2", "--quiet"]
        )

    def test_parallel(self):
        # The one command that really runs threads — and the one
        # where an event loop would most plausibly be thought
        # necessary. It is not: each worker is built on the
        # thread that runs it, so its signals are direct calls.
        self._assert_qtcore_only(
            ["parallel", SAMPLE_HEX, "--count", "3", "--quiet"]
        )

    def test_info_needs_no_qt_at_all_beyond_the_import(self):
        rc, app, loaded = _probe(["info", SAMPLE_HEX])
        self.assertEqual(rc, 0)
        self.assertEqual(app, "None")
        for name in GUI_QT_MODULES:
            self.assertFalse(loaded[name])


class TestCliSourceDoesNotReachIntoTheGui(unittest.TestCase):
    """
    Static backstop for the same rule: cli.py and core/ must not
    import gui/, which is also what keeps core/ usable
    headlessly at all (CLAUDE.md, "Layering").
    """

    def _sources(self):
        paths = [os.path.join(REPO, "cli.py"),
                 os.path.join(REPO, "cli_gitlab.py")]
        core = os.path.join(REPO, "core")
        paths += [
            os.path.join(core, name)
            for name in sorted(os.listdir(core))
            if name.endswith(".py")
        ]
        return paths

    def test_no_gui_imports(self):
        offenders = []

        for path in self._sources():
            with open(path, encoding="utf-8") as f:
                for number, line in enumerate(f, 1):
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        continue
                    if ("import gui" in stripped
                            or "from gui" in stripped):
                        offenders.append(
                            f"{os.path.relpath(path, REPO)}:{number}"
                        )

        self.assertEqual(
            offenders, [],
            "cli.py/core/ must not import gui/ — it drags the "
            "widget stack into the CLI and breaks headless use",
        )

    def test_cli_does_not_import_qtwidgets(self):
        with open(os.path.join(REPO, "cli.py"), encoding="utf-8") as f:
            source = f.read()

        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            self.assertNotIn(
                "QtWidgets", stripped,
                "cli.py must not import PySide6.QtWidgets",
            )


if __name__ == "__main__":
    unittest.main()

# ==================================================
# GitLab Fetch Dialog Threading Tests
# ==================================================
#
# Exercises GitLabFetchDialog's real QThread path, same discipline
# as tests/test_flash_threading.py: calling a worker's run() method
# directly (synchronously) cannot catch QThread lifecycle bugs (see
# CLAUDE.md's "Threading model" section) — these tests always go
# through moveToThread()+thread.start().
# ==================================================

import os
import sys
import unittest
from unittest.mock import patch
from PySide6.QtCore import QTimer

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from tests.qt_test_utils import get_app
from gui.main_window import MainWindow
from gui.gitlab_dialog import GitLabFetchDialog
from communication.gitlab_client import GitLabConnectionError


def _run_until(app, predicate, timeout_ms=5000, interval_ms=20):
    state = {"elapsed": 0, "satisfied": False}

    def tick():
        if predicate():
            state["satisfied"] = True
            app.quit()
        elif state["elapsed"] >= timeout_ms:
            app.quit()
        else:
            state["elapsed"] += interval_ms

    timer = QTimer()
    timer.setInterval(interval_ms)
    timer.timeout.connect(tick)
    timer.start()
    app.exec()
    timer.stop()
    if not state["satisfied"]:
        raise RuntimeError("timed out waiting for condition")


class TestFetchLatestArtifactRealThread(unittest.TestCase):

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()
        self.dialog = GitLabFetchDialog(self.window)
        self.dialog.urlEdit.setText("https://gitlab.com")
        self.dialog.projectEdit.setText("group/proj")
        self.dialog.tokenEdit.setText("tok")
        self.dialog.ciRefEdit.setText("main")
        self.dialog.ciJobEdit.setText("build_firmware")

    def test_fetch_latest_artifact_runs_and_cleans_up_thread(self):
        with patch(
            "gui.gitlab_dialog.gitlab_client.download_latest_artifact",
            return_value=b"PK\x03\x04fakezip",
        ):
            self.dialog.ciFetchButton.click()
            self.assertIsNotNone(self.dialog._thread)
            _run_until(self.app, lambda: self.dialog._thread is None)

        self.assertIsNone(self.dialog._thread)
        self.assertIsNone(self.dialog._worker)

    def test_connection_error_is_shown_and_does_not_crash(self):
        with patch(
            "gui.gitlab_dialog.gitlab_client.download_latest_artifact",
            side_effect=GitLabConnectionError("Could not reach https://gitlab.com: timeout"),
        ):
            self.dialog.ciFetchButton.click()
            _run_until(self.app, lambda: self.dialog._thread is None)

        self.assertIn(
            "Could not reach https://gitlab.com: timeout",
            self.dialog.statusLabel.text(),
        )

    def test_close_mid_fetch_does_not_crash(self):
        with patch(
            "gui.gitlab_dialog.gitlab_client.download_latest_artifact",
            return_value=b"PK\x03\x04fakezip",
        ):
            self.dialog.ciFetchButton.click()
            self.dialog.close()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()

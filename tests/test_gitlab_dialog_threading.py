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
import shutil
import sys
import tempfile
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
        self.dialog.ciProjectEdit.setText("group/proj")
        self.dialog.tokenEdit.setText("tok")
        self.dialog.ciRefEdit.setEditText("main")
        self.dialog.ciJobEdit.setEditText("build_firmware")

    def test_fetch_latest_artifact_runs_and_cleans_up_thread(self):
        with patch(
            "gui.gitlab_dialog.gitlab_client.download_latest_artifact",
            return_value=b"PK\x03\x04fakezip",
        ), patch.object(self.window, '_load_firmware_file', return_value=True):
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
        ), patch.object(self.window, '_load_firmware_file', return_value=True):
            self.dialog.ciFetchButton.click()
            self.dialog.close()
            self.app.processEvents()

    def test_close_mid_fetch_actually_cancels_pending_load(self):
        # Regression test for the final-review "cancel doesn't
        # actually cancel" finding: closeEvent() stops the QThread,
        # but a download_ready signal already queued on the main
        # thread's event loop still gets delivered after close()
        # returns — without the self._cancelled guard,
        # _on_download_ready() would run anyway and load an
        # unrequested firmware file after the user clicked Cancel.
        with patch(
            "gui.gitlab_dialog.gitlab_client.download_latest_artifact",
            return_value=b"PK\x03\x04fakezip",
        ), patch.object(self.window, '_load_firmware_file', return_value=True) as mock_load:
            self.dialog.ciFetchButton.click()
            self.dialog.close()
            self.app.processEvents()

        mock_load.assert_not_called()

    def test_browse_toggles_disabled_during_fetch(self):
        # Regression test for the final-review "browse toggles stay
        # enabled during an in-flight fetch" finding: clicking Browse
        # mid-fetch used to silently no-op (via _run_action()'s
        # `if self._thread is not None: return` guard) with no
        # indication why, since only the two Fetch buttons were
        # disabled — not the two Browse toggles.
        with patch(
            "gui.gitlab_dialog.gitlab_client.download_latest_artifact",
            return_value=b"PK\x03\x04fakezip",
        ), patch.object(self.window, '_load_firmware_file', return_value=True):
            self.dialog.ciFetchButton.click()
            self.assertFalse(self.dialog.ciBrowseToggle.isEnabled())
            self.assertFalse(self.dialog.pkgBrowseToggle.isEnabled())
            _run_until(self.app, lambda: self.dialog._thread is None)

        self.assertTrue(self.dialog.ciBrowseToggle.isEnabled())
        self.assertTrue(self.dialog.pkgBrowseToggle.isEnabled())


class TestBranchTagAndRefScopedJobsRealThread(unittest.TestCase):
    """
    Covers loading branches/tags into ciRefEdit and then browsing
    jobs scoped to the selected ref — both go through a real
    QThread, same discipline as every other action in this file.
    """

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()
        self.dialog = GitLabFetchDialog(self.window)
        self.dialog.urlEdit.setText("https://gitlab.com")
        self.dialog.ciProjectEdit.setText("group/proj")
        self.dialog.tokenEdit.setText("tok")

    def test_load_branches_and_tags_populates_ref_combo(self):
        with patch(
            "gui.gitlab_dialog.gitlab_client.list_branches_and_tags",
            return_value=[
                {"name": "main", "ref_type": "branch"},
                {"name": "v1.0.0", "ref_type": "tag"},
            ],
        ):
            self.dialog.ciLoadRefsButton.click()
            _run_until(self.app, lambda: self.dialog._thread is None)

        items = [
            self.dialog.ciRefEdit.itemText(i)
            for i in range(self.dialog.ciRefEdit.count())
        ]
        self.assertEqual(items, ["main", "v1.0.0"])

    def test_browsing_with_a_selected_ref_scopes_the_job_list(self):
        self.dialog.ciRefEdit.setEditText("Release_R_04_01_01")

        with patch(
            "gui.gitlab_dialog.gitlab_client.list_jobs_for_ref",
            return_value=[
                {
                    "pipeline_id": 500, "job_id": 4821,
                    "job_name": "create_ffi_3p5mb_no_HTSM",
                    "ref": "Release_R_04_01_01", "status": "success",
                    "created_at": "2026-08-27T09:14:00Z", "has_artifacts": True,
                },
            ],
        ) as mock_list:
            self.dialog.ciBrowseToggle.click()
            _run_until(self.app, lambda: self.dialog._thread is None)

        mock_list.assert_called_once_with(
            "https://gitlab.com", "group/proj", "tok",
            ref="Release_R_04_01_01", job_name=None, ssl_verify=True,
        )
        self.assertEqual(self.dialog.ciBrowseTable.rowCount(), 1)


class TestDownloadFolderRealThread(unittest.TestCase):
    """
    Covers copying the loaded firmware file into a user-configured
    "Download folder" (gui/gitlab_dialog.py's
    _copy_to_download_folder(), called from _load_and_close()) — an
    opt-in alternative to the default throwaway-temp-dir behavior, so
    Recent Files can reopen a GitLab-fetched file after closeEvent()
    deletes the dialog's temp dir. Goes through a real QThread, same
    discipline as every other action in this file.
    """

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()
        self.dialog = GitLabFetchDialog(self.window)
        self.dialog.urlEdit.setText("https://gitlab.com")
        self.dialog.ciProjectEdit.setText("group/proj")
        self.dialog.tokenEdit.setText("tok")
        self.dialog.ciRefEdit.setEditText("main")
        self.dialog.ciJobEdit.setEditText("build_firmware")
        self.output_dir = tempfile.mkdtemp(prefix="sflash_test_output_")

    def tearDown(self):
        shutil.rmtree(self.output_dir, ignore_errors=True)

    def test_downloaded_file_is_copied_to_configured_folder(self):
        self.dialog.downloadFolderEdit.setText(self.output_dir)

        with patch(
            "gui.gitlab_dialog.gitlab_client.download_latest_artifact",
            return_value=b"PK\x03\x04fakezip",
        ), patch.object(
            self.window, '_load_firmware_file', return_value=True
        ) as mock_load:
            self.dialog.ciFetchButton.click()
            _run_until(self.app, lambda: self.dialog._thread is None)

        mock_load.assert_called_once()
        loaded_path = mock_load.call_args[0][0]
        self.assertEqual(os.path.dirname(loaded_path), self.output_dir)

    def test_copied_file_survives_dialogs_own_temp_dir_cleanup(self):
        # _load_and_close() calls self.close() right after loading,
        # which runs closeEvent() -> shutil.rmtree() on the dialog's
        # temp dir. The copy into output_dir must be unaffected.
        self.dialog.downloadFolderEdit.setText(self.output_dir)

        with patch(
            "gui.gitlab_dialog.gitlab_client.download_latest_artifact",
            return_value=b"PK\x03\x04fakezip",
        ), patch.object(self.window, '_load_firmware_file', return_value=True):
            self.dialog.ciFetchButton.click()
            _run_until(self.app, lambda: self.dialog._thread is None)

        files = os.listdir(self.output_dir)
        self.assertEqual(len(files), 1)
        self.assertTrue(os.path.isfile(os.path.join(self.output_dir, files[0])))

    def test_name_collision_gets_a_timestamp_suffix_not_overwritten(self):
        # suggested_filename for fetch_latest_artifact is
        # "{job_name}-latest.zip" — pre-create that exact name so the
        # real download collides with an already-present file.
        self.dialog.downloadFolderEdit.setText(self.output_dir)
        existing_path = os.path.join(self.output_dir, "build_firmware-latest.zip")
        with open(existing_path, "wb") as f:
            f.write(b"pre-existing content")

        with patch(
            "gui.gitlab_dialog.gitlab_client.download_latest_artifact",
            return_value=b"PK\x03\x04fakezip",
        ), patch.object(self.window, '_load_firmware_file', return_value=True):
            self.dialog.ciFetchButton.click()
            _run_until(self.app, lambda: self.dialog._thread is None)

        files = os.listdir(self.output_dir)
        self.assertEqual(len(files), 2)
        with open(existing_path, "rb") as f:
            self.assertEqual(f.read(), b"pre-existing content")

    def test_empty_download_folder_keeps_using_temp_dir(self):
        # Regression guard: leaving the field empty must not attempt
        # any copy — the file loads straight from the dialog's own
        # temp dir, exactly like before this feature existed.
        self.dialog.downloadFolderEdit.setText("")

        with patch(
            "gui.gitlab_dialog.gitlab_client.download_latest_artifact",
            return_value=b"PK\x03\x04fakezip",
        ), patch.object(
            self.window, '_load_firmware_file', return_value=True
        ) as mock_load:
            self.dialog.ciFetchButton.click()
            _run_until(self.app, lambda: self.dialog._thread is None)

        loaded_path = mock_load.call_args[0][0]
        self.assertNotEqual(os.path.dirname(loaded_path), self.output_dir)
        self.assertEqual(os.listdir(self.output_dir), [])


class TestCiRowDownloadButtonRealThread(unittest.TestCase):
    """
    Covers the per-row "Download" button added to ciBrowseTable
    (final-review Fix 5) — must behave identically to the existing
    cellDoubleClicked wiring, including going through a real QThread.
    """

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()
        self.dialog = GitLabFetchDialog(self.window)
        self.dialog.urlEdit.setText("https://gitlab.com")
        self.dialog.ciProjectEdit.setText("group/proj")
        self.dialog.tokenEdit.setText("tok")
        self.dialog._populate_ci_browse_table([
            {
                "pipeline_id": 100, "job_id": 4821, "job_name": "build_firmware",
                "ref": "main", "status": "success",
                "created_at": "2026-08-27T09:14:00Z", "has_artifacts": True,
            },
        ])

    def test_clicking_download_button_triggers_same_path_as_double_click(self):
        with patch(
            "gui.gitlab_dialog.gitlab_client.download_job_artifact",
            return_value=b"PK\x03\x04fakezip",
        ) as mock_download, patch.object(
            self.window, '_load_firmware_file', return_value=True
        ):
            button = self.dialog.ciBrowseTable.cellWidget(0, 5)
            self.assertIsNotNone(button)
            self.assertTrue(button.isEnabled())
            button.click()
            _run_until(self.app, lambda: self.dialog._thread is None)

        mock_download.assert_called_once_with(
            "https://gitlab.com", "group/proj", "tok", job_id=4821,
            ssl_verify=True,
        )


class TestPkgRowDownloadButtonRealThread(unittest.TestCase):
    """
    Covers the per-row "Download" button added to pkgBrowseTable
    (final-review Fix 5) — must behave identically to the existing
    cellDoubleClicked wiring, including going through a real QThread.
    """

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()
        self.dialog = GitLabFetchDialog(self.window)
        self.dialog.urlEdit.setText("https://gitlab.com")
        self.dialog.pkgProjectEdit.setText("group/proj")
        self.dialog.tokenEdit.setText("tok")
        self.dialog.packageNameEdit.setText("suzuki-slp1-radar-firmware")
        # Simulates what _toggle_pkg_browse() would have stashed
        # (see Fix 9) — the fetch this row came from.
        self.dialog._pkg_browse_name = "suzuki-slp1-radar-firmware"
        self.dialog._populate_pkg_browse_table([
            {"package_id": 1, "version": "1.4.2", "created_at": "2026-08-27T09:00:00Z"},
        ])

    def test_download_button_exists_and_is_enabled(self):
        button = self.dialog.pkgBrowseTable.cellWidget(0, 2)
        self.assertIsNotNone(button)
        self.assertTrue(button.isEnabled())

    def test_clicking_download_button_triggers_download_package_version(self):
        with patch(
            "gui.gitlab_dialog.gitlab_client.download_package_version",
            return_value=b"PK\x03\x04fakezip",
        ) as mock_download, patch.object(
            self.window, '_load_firmware_file', return_value=True
        ):
            button = self.dialog.pkgBrowseTable.cellWidget(0, 2)
            button.click()
            _run_until(self.app, lambda: self.dialog._thread is None)

        mock_download.assert_called_once_with(
            "https://gitlab.com", "group/proj", "tok",
            package_name="suzuki-slp1-radar-firmware", version="1.4.2",
            ssl_verify=True,
        )


class TestFetchLatestPackageRealThread(unittest.TestCase):

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()
        self.dialog = GitLabFetchDialog(self.window)
        self.dialog.urlEdit.setText("https://gitlab.com")
        self.dialog.pkgProjectEdit.setText("group/proj")
        self.dialog.tokenEdit.setText("tok")
        self.dialog.packageNameEdit.setText("suzuki-slp1-radar-firmware")

    def test_fetch_latest_package_runs_and_cleans_up_thread(self):
        with patch(
            "gui.gitlab_dialog.gitlab_client.download_latest_package_file",
            return_value=b"PK\x03\x04fakezip",
        ), patch.object(self.window, '_load_firmware_file', return_value=True):
            self.dialog.pkgFetchButton.click()
            self.assertIsNotNone(self.dialog._thread)
            _run_until(self.app, lambda: self.dialog._thread is None)

        self.assertIsNone(self.dialog._thread)


if __name__ == "__main__":
    unittest.main()

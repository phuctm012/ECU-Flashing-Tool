# ==================================================
# Load from GitLab Dialog
# ==================================================
#
# Fetches a firmware file from a GitLab CI job artifact or the
# Package Registry, instead of only picking a local file. Reachable
# from two entry points (File > Load from GitLab..., and a button on
# Configure > Data) that both call MainWindow.open_gitlab_fetch_dialog()
# — see gui/menu_bar.py.
#
# Threading follows the exact same lifecycle rules as
# gui/test_connection_dialog.py (see CLAUDE.md "Threading model"):
# GitLabFetchWorker.finished is emitted from inside run() itself,
# while the worker thread is still executing, so the slot connected
# to it must never touch self._thread/self._worker directly — only
# _cleanup_thread(), wired to thread.finished, does that.
#
# Its own widgets are built in Python (not main_window.ui) — same
# precedent as TestConnectionDialog; only the two entry-point
# triggers live in the .ui (see gui/main_window.ui's
# actionLoadFromGitLab / buttonLoadFromGitLab).
# ==================================================

import os
import zipfile

from PySide6.QtCore import QObject, QSettings, QThread, Signal, Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from communication import gitlab_client
from config.settings import APP_AUTHOR, APP_NAME
from parsers.auto_parser import SREC_EXTENSIONS

RECOGNIZED_FIRMWARE_EXTENSIONS = (".hex", ".bin") + SREC_EXTENSIONS


class GitLabFetchWorker(QObject):
    """
    Runs exactly one GitLab operation (list or download) on a
    background QThread. A new instance is created per operation —
    same "fresh worker per action" pattern gui/test_connection_dialog.py
    uses, just parameterized by `action` since this dialog has
    several distinct operations instead of one fixed probe.
    """

    progress_message = Signal(str)
    list_ready = Signal(list)
    download_ready = Signal(bytes, str)  # (data, suggested_filename)
    error = Signal(str)
    finished = Signal()

    def __init__(self, action, url, project, token, **params):
        super().__init__()
        self._action = action
        self._url = url
        self._project = project
        self._token = token
        self._params = params

    def run(self):

        try:

            if self._action == "list_jobs":
                self.progress_message.emit("Loading recent jobs...")
                jobs = gitlab_client.list_recent_jobs(
                    self._url, self._project, self._token,
                    job_name=self._params.get("job_name"),
                )
                self.list_ready.emit(jobs)

            elif self._action == "fetch_latest_artifact":
                self.progress_message.emit("Downloading latest artifact...")
                data = gitlab_client.download_latest_artifact(
                    self._url, self._project, self._token,
                    ref=self._params["ref"], job_name=self._params["job_name"],
                )
                self.download_ready.emit(
                    data, f"{self._params['job_name']}-latest.zip"
                )

            elif self._action == "download_job_artifact":
                self.progress_message.emit("Downloading artifact...")
                data = gitlab_client.download_job_artifact(
                    self._url, self._project, self._token,
                    job_id=self._params["job_id"],
                )
                self.download_ready.emit(data, f"job-{self._params['job_id']}.zip")

            elif self._action == "list_packages":
                self.progress_message.emit("Loading package versions...")
                versions = gitlab_client.list_package_versions(
                    self._url, self._project, self._token,
                    package_name=self._params["package_name"],
                )
                self.list_ready.emit(versions)

            elif self._action == "fetch_latest_package":
                self.progress_message.emit("Downloading latest version...")
                data = gitlab_client.download_latest_package_file(
                    self._url, self._project, self._token,
                    package_name=self._params["package_name"],
                )
                self.download_ready.emit(
                    data, f"{self._params['package_name']}-latest.zip"
                )

            elif self._action == "download_package_version":
                self.progress_message.emit("Downloading version...")
                data = gitlab_client.download_package_version(
                    self._url, self._project, self._token,
                    package_name=self._params["package_name"],
                    version=self._params["version"],
                )
                self.download_ready.emit(
                    data,
                    f"{self._params['package_name']}-{self._params['version']}.zip",
                )

            else:
                self.error.emit(f"Unknown action: {self._action}")

        except gitlab_client.GitLabError as e:
            self.error.emit(str(e))
        except Exception as e:
            self.error.emit(f"Unexpected error: {e}")

        self.finished.emit()


class GitLabFetchDialog(QDialog):

    def __init__(self, parent):
        super().__init__(parent)

        self.setWindowTitle("Load from GitLab")
        self.resize(620, 460)

        self._main_window = parent
        self._thread = None
        self._worker = None

        self._settings = QSettings(
            QSettings.IniFormat, QSettings.UserScope, APP_AUTHOR, APP_NAME,
        )

        self._build_ui()
        self._load_settings()

    # ==================================================
    # UI construction
    # ==================================================

    def _build_ui(self):

        layout = QVBoxLayout(self)

        layout.addWidget(self._build_connection_card())

        self.tabs = QTabWidget(self)
        self.tabs.addTab(self._build_ci_tab(), "CI Artifact")
        self.tabs.addTab(self._build_package_tab(), "Package Registry")
        layout.addWidget(self.tabs)

        self.statusLabel = QLabel("")
        self.statusLabel.setWordWrap(True)
        layout.addWidget(self.statusLabel)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel, self)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)

    def _build_connection_card(self):

        box = QGroupBox("GitLab Connection", self)
        grid = QGridLayout(box)

        grid.addWidget(QLabel("Instance URL"), 0, 0)
        self.urlEdit = QLineEdit(box)
        self.urlEdit.textEdited.connect(self._save_settings)
        grid.addWidget(self.urlEdit, 0, 1)

        grid.addWidget(QLabel("Project"), 1, 0)
        self.projectEdit = QLineEdit(box)
        self.projectEdit.setPlaceholderText("group/firmware-repo")
        self.projectEdit.textEdited.connect(self._save_settings)
        grid.addWidget(self.projectEdit, 1, 1)

        grid.addWidget(QLabel("Access Token"), 2, 0)
        self.tokenEdit = QLineEdit(box)
        self.tokenEdit.setEchoMode(QLineEdit.EchoMode.Password)
        self.tokenEdit.textEdited.connect(self._save_settings)
        grid.addWidget(self.tokenEdit, 2, 1)

        return box

    def _build_ci_tab(self):

        page = QWidget(self)
        layout = QVBoxLayout(page)

        grid = QGridLayout()
        grid.addWidget(QLabel("Branch / ref"), 0, 0)
        self.ciRefEdit = QLineEdit(page)
        self.ciRefEdit.textEdited.connect(self._save_settings)
        grid.addWidget(self.ciRefEdit, 0, 1)

        grid.addWidget(QLabel("Job name"), 1, 0)
        self.ciJobEdit = QLineEdit(page)
        self.ciJobEdit.textEdited.connect(self._save_settings)
        grid.addWidget(self.ciJobEdit, 1, 1)
        layout.addLayout(grid)

        fetch_row = QHBoxLayout()
        self.ciFetchButton = QPushButton("Fetch Latest Artifact", page)
        self.ciFetchButton.clicked.connect(self._on_fetch_latest_artifact)
        fetch_row.addWidget(self.ciFetchButton)

        self.ciBrowseToggle = QPushButton("Browse recent jobs...", page)
        self.ciBrowseToggle.clicked.connect(self._toggle_ci_browse)
        fetch_row.addWidget(self.ciBrowseToggle)
        layout.addLayout(fetch_row)

        self.ciBrowseTable = QTableWidget(0, 5, page)
        self.ciBrowseTable.setHorizontalHeaderLabels(
            ["Pipeline", "Job", "Ref", "Status", "When"]
        )
        self.ciBrowseTable.horizontalHeader().setStretchLastSection(True)
        self.ciBrowseTable.setVisible(False)
        self.ciBrowseTable.cellDoubleClicked.connect(self._on_ci_row_activated)
        layout.addWidget(self.ciBrowseTable)

        layout.addStretch(1)
        return page

    def _build_package_tab(self):
        # Filled in by Task 5 — placeholder page so the tab exists
        # and index 1 is reachable; Task 5 replaces this body.
        page = QWidget(self)
        QVBoxLayout(page)
        return page

    # ==================================================
    # Settings persistence
    # ==================================================

    def _load_settings(self):

        s = self._settings
        self.urlEdit.setText(s.value("gitlab/instanceUrl", "https://gitlab.com", type=str))
        self.projectEdit.setText(s.value("gitlab/project", "", type=str))
        self.tokenEdit.setText(s.value("gitlab/token", "", type=str))
        self.ciRefEdit.setText(s.value("gitlab/ciRef", "main", type=str))
        self.ciJobEdit.setText(s.value("gitlab/ciJobName", "", type=str))

    def _save_settings(self, _text=None):

        s = self._settings
        s.setValue("gitlab/instanceUrl", self.urlEdit.text())
        s.setValue("gitlab/project", self.projectEdit.text())
        s.setValue("gitlab/token", self.tokenEdit.text())
        s.setValue("gitlab/ciRef", self.ciRefEdit.text())
        s.setValue("gitlab/ciJobName", self.ciJobEdit.text())
        s.sync()

    # ==================================================
    # CI Artifact tab actions
    # ==================================================

    def _toggle_ci_browse(self):

        opening = not self.ciBrowseTable.isVisible()
        self.ciBrowseTable.setVisible(opening)

        if opening:
            self._run_action(
                "list_jobs", {"job_name": self.ciJobEdit.text() or None},
                on_list=self._populate_ci_browse_table,
            )

    def _populate_ci_browse_table(self, jobs):

        self.ciBrowseTable.setRowCount(len(jobs))

        for row, job in enumerate(jobs):
            self.ciBrowseTable.setItem(row, 0, QTableWidgetItem(str(job["pipeline_id"])))
            self.ciBrowseTable.setItem(row, 1, QTableWidgetItem(job["job_name"]))
            self.ciBrowseTable.setItem(row, 2, QTableWidgetItem(job["ref"]))
            self.ciBrowseTable.setItem(row, 3, QTableWidgetItem(job["status"]))
            self.ciBrowseTable.setItem(row, 4, QTableWidgetItem(job["created_at"]))
            self.ciBrowseTable.item(row, 0).setData(Qt.UserRole, job)

    def _on_ci_row_activated(self, row, _col):

        job = self.ciBrowseTable.item(row, 0).data(Qt.UserRole)

        if not job["has_artifacts"]:
            self.statusLabel.setText(
                f"Job #{job['job_id']} ({job['status']}) has no artifact to download."
            )
            return

        self._run_action(
            "download_job_artifact", {"job_id": job["job_id"]},
            on_download=self._on_download_ready,
        )

    def _on_fetch_latest_artifact(self):

        self._run_action(
            "fetch_latest_artifact",
            {"ref": self.ciRefEdit.text(), "job_name": self.ciJobEdit.text()},
            on_download=self._on_download_ready,
        )

    def _on_download_ready(self, data, suggested_filename):
        # Zip extraction + file picker is added in Task 6 — for now
        # just confirm the bytes arrived.
        self.statusLabel.setText(
            f"Downloaded {suggested_filename} ({len(data)} bytes)."
        )

    # ==================================================
    # Threading helper (shared by every action)
    # ==================================================

    def _run_action(self, action, params, on_list=None, on_download=None):
        """
        Creates a fresh QThread + GitLabFetchWorker for one action
        and starts it. Only one action runs at a time — buttons stay
        enabled (multiple rapid clicks just queue more threads is
        avoided by disabling the primary fetch controls while
        self._thread is not None).
        """

        if self._thread is not None:
            return

        self.statusLabel.setText("")
        self.ciFetchButton.setEnabled(False)

        self._thread = QThread()
        self._worker = GitLabFetchWorker(
            action,
            self.urlEdit.text(), self.projectEdit.text(), self.tokenEdit.text(),
            **params,
        )
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.progress_message.connect(self.statusLabel.setText)

        if on_list is not None:
            self._worker.list_ready.connect(on_list)
        if on_download is not None:
            self._worker.download_ready.connect(on_download)
        self._worker.error.connect(self.statusLabel.setText)

        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)

        # NOTE: intentionally NOT connecting thread.finished ->
        # thread.deleteLater here — see module docstring.
        self._thread.finished.connect(self._cleanup_thread)

        self._thread.start()

    def _cleanup_thread(self):

        if self._thread is not None:
            self._thread.wait()

        self._thread = None
        self._worker = None
        self.ciFetchButton.setEnabled(True)

    def closeEvent(self, event):

        if self._thread is not None and self._thread.isRunning():
            # Same reasoning as gui/test_connection_dialog.py's
            # closeEvent(): worker.finished -> thread.quit is a
            # queued cross-thread connection, only delivered once
            # the main thread's event loop next runs — calling
            # wait() here would block that very event loop, so call
            # quit() directly first (thread-safe from any thread).
            self._thread.quit()
            self._thread.wait()

        event.accept()

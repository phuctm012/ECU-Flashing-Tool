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
import shutil
import tempfile
import zipfile

from PySide6.QtCore import QObject, QSettings, QThread, Signal, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from communication import gitlab_client
from config.settings import APP_AUTHOR, APP_NAME
from parsers.auto_parser import FIRMWARE_EXTENSIONS as RECOGNIZED_FIRMWARE_EXTENSIONS

# Which GitLabFetchWorker actions belong to the CI Artifact tab (use
# ciProjectEdit) vs the Package Registry tab (use pkgProjectEdit) —
# see _run_action()'s project selection.
_CI_ACTIONS = {
    "list_jobs", "fetch_latest_artifact", "download_job_artifact",
    "list_branches_and_tags", "list_jobs_for_ref",
}


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

    def __init__(self, action, url, project, token, ssl_verify=True, **params):
        super().__init__()
        self._action = action
        self._url = url
        self._project = project
        self._token = token
        self._ssl_verify = ssl_verify
        self._params = params

    def run(self):

        try:

            if self._action == "list_jobs":
                self.progress_message.emit("Loading recent jobs...")
                jobs = gitlab_client.list_recent_jobs(
                    self._url, self._project, self._token,
                    job_name=self._params.get("job_name"),
                    ssl_verify=self._ssl_verify,
                )
                self.list_ready.emit(jobs)

            elif self._action == "list_branches_and_tags":
                self.progress_message.emit("Loading branches and tags...")
                refs = gitlab_client.list_branches_and_tags(
                    self._url, self._project, self._token,
                    ssl_verify=self._ssl_verify,
                )
                self.list_ready.emit(refs)

            elif self._action == "list_jobs_for_ref":
                self.progress_message.emit(
                    f"Loading jobs for {self._params['ref']}..."
                )
                jobs = gitlab_client.list_jobs_for_ref(
                    self._url, self._project, self._token,
                    ref=self._params["ref"],
                    job_name=self._params.get("job_name"),
                    ssl_verify=self._ssl_verify,
                )
                self.list_ready.emit(jobs)

            elif self._action == "fetch_latest_artifact":
                self.progress_message.emit("Downloading latest artifact...")
                data = gitlab_client.download_latest_artifact(
                    self._url, self._project, self._token,
                    ref=self._params["ref"], job_name=self._params["job_name"],
                    ssl_verify=self._ssl_verify,
                )
                self.download_ready.emit(
                    data, f"{self._params['job_name']}-latest.zip"
                )

            elif self._action == "download_job_artifact":
                self.progress_message.emit("Downloading artifact...")
                data = gitlab_client.download_job_artifact(
                    self._url, self._project, self._token,
                    job_id=self._params["job_id"],
                    ssl_verify=self._ssl_verify,
                )
                self.download_ready.emit(data, f"job-{self._params['job_id']}.zip")

            elif self._action == "list_packages":
                self.progress_message.emit("Loading package versions...")
                versions = gitlab_client.list_package_versions(
                    self._url, self._project, self._token,
                    package_name=self._params["package_name"],
                    ssl_verify=self._ssl_verify,
                )
                self.list_ready.emit(versions)

            elif self._action == "fetch_latest_package":
                self.progress_message.emit("Downloading latest version...")
                data = gitlab_client.download_latest_package_file(
                    self._url, self._project, self._token,
                    package_name=self._params["package_name"],
                    ssl_verify=self._ssl_verify,
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
                    ssl_verify=self._ssl_verify,
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
        self.resize(620, 600)

        self._main_window = parent
        self._thread = None
        self._worker = None
        self._cancelled = False
        # Set for real by _toggle_pkg_browse() when a browse actually
        # runs; the empty default only matters if a row is ever
        # activated without going through that path first (not a
        # reachable path via the UI, but avoids an AttributeError).
        self._pkg_browse_name = ""

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

        self.pickerPanel = self._build_picker_panel()
        self.pickerPanel.setVisible(False)
        layout.addWidget(self.pickerPanel)

        self.statusLabel = QLabel("")
        self.statusLabel.setWordWrap(True)
        layout.addWidget(self.statusLabel)

        layout.addWidget(QLabel("GitLab log"))
        self.logView = QPlainTextEdit(self)
        self.logView.setReadOnly(True)
        self.logView.setMaximumBlockCount(500)
        self.logView.setMaximumHeight(120)
        layout.addWidget(self.logView)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel, self)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)

    def _append_log(self, message):
        # Keeps the full run history visible (statusLabel above only
        # ever shows the single most-recent message, overwritten on
        # every update) — same "never leave the user guessing what
        # happened" philosophy as this app's other trace/log panels
        # (Information tab, Trace tab).
        if message:
            self.logView.appendPlainText(message)

    def _build_connection_card(self):

        box = QGroupBox("GitLab Connection", self)
        grid = QGridLayout(box)

        grid.addWidget(QLabel("Instance URL"), 0, 0)
        self.urlEdit = QLineEdit(box)
        self.urlEdit.textEdited.connect(self._save_settings)
        grid.addWidget(self.urlEdit, 0, 1)

        grid.addWidget(QLabel("Access Token"), 1, 0)
        self.tokenEdit = QLineEdit(box)
        self.tokenEdit.setEchoMode(QLineEdit.EchoMode.Password)
        self.tokenEdit.textEdited.connect(self._save_settings)
        grid.addWidget(self.tokenEdit, 1, 1)

        self.verifyTlsCheckbox = QCheckBox("Verify TLS certificate (recommended)", box)
        self.verifyTlsCheckbox.setChecked(True)
        self.verifyTlsCheckbox.toggled.connect(self._save_settings)
        grid.addWidget(self.verifyTlsCheckbox, 2, 0, 1, 2)

        self.tokenHint = QLabel(
            "Required token permissions: read_api (branches/tags, pipelines, "
            "jobs, artifacts); read_registry (Package Registry); "
            "read_repository if required by project policy.",
            box,
        )
        self.tokenHint.setWordWrap(True)
        self.tokenHint.setStyleSheet("color: #808080; font-size: 11px;")
        grid.addWidget(self.tokenHint, 3, 0, 1, 2)

        return box

    def _build_ci_tab(self):

        page = QWidget(self)
        layout = QVBoxLayout(page)

        grid = QGridLayout()
        grid.addWidget(QLabel("Project"), 0, 0)
        self.ciProjectEdit = QLineEdit(page)
        self.ciProjectEdit.setPlaceholderText("group/ci-project")
        self.ciProjectEdit.textEdited.connect(self._save_settings)
        grid.addWidget(self.ciProjectEdit, 0, 1)

        grid.addWidget(QLabel("Branch / tag"), 1, 0)
        self.ciRefEdit = QComboBox(page)
        self.ciRefEdit.setEditable(True)
        # Not populated until "Load branches/tags" has run at least
        # once (see _populate_ci_ref_combo()) — typing a ref that's
        # never been loaded still works, same "combo as a shortcut,
        # not a requirement" convention as ciJobEdit.
        self.ciRefEdit.currentTextChanged.connect(self._save_settings)
        grid.addWidget(self.ciRefEdit, 1, 1)

        self.ciLoadRefsButton = QPushButton("Load branches/tags", page)
        self.ciLoadRefsButton.clicked.connect(self._load_ci_refs)
        grid.addWidget(self.ciLoadRefsButton, 1, 2)

        grid.addWidget(QLabel("Job name"), 2, 0)
        self.ciJobEdit = QComboBox(page)
        self.ciJobEdit.setEditable(True)
        # Not populated until "Browse recent jobs..." has run at
        # least once (see _populate_ci_job_combo()) — typing a job
        # name that's never been browsed still works, this is only
        # a convenience shortcut once real names are known.
        self.ciJobEdit.currentTextChanged.connect(self._save_settings)
        grid.addWidget(self.ciJobEdit, 2, 1)
        layout.addLayout(grid)

        fetch_row = QHBoxLayout()
        self.ciFetchButton = QPushButton("Fetch Latest Artifact", page)
        self.ciFetchButton.clicked.connect(self._on_fetch_latest_artifact)
        fetch_row.addWidget(self.ciFetchButton)

        self.ciBrowseToggle = QPushButton("Browse jobs...", page)
        self.ciBrowseToggle.clicked.connect(self._toggle_ci_browse)
        fetch_row.addWidget(self.ciBrowseToggle)
        layout.addLayout(fetch_row)

        self.ciBrowseTable = QTableWidget(0, 6, page)
        self.ciBrowseTable.setHorizontalHeaderLabels(
            ["Pipeline", "Job", "Ref", "Status", "When", "Download"]
        )
        self.ciBrowseTable.horizontalHeader().setStretchLastSection(True)
        self.ciBrowseTable.setVisible(False)
        self.ciBrowseTable.cellDoubleClicked.connect(self._on_ci_row_activated)
        layout.addWidget(self.ciBrowseTable)

        layout.addStretch(1)
        return page

    def _build_package_tab(self):

        page = QWidget(self)
        layout = QVBoxLayout(page)

        grid = QGridLayout()
        grid.addWidget(QLabel("Project"), 0, 0)
        self.pkgProjectEdit = QLineEdit(page)
        self.pkgProjectEdit.setPlaceholderText("group/firmware-packages")
        self.pkgProjectEdit.textEdited.connect(self._save_settings)
        grid.addWidget(self.pkgProjectEdit, 0, 1)

        grid.addWidget(QLabel("Package name"), 1, 0)
        self.packageNameEdit = QLineEdit(page)
        self.packageNameEdit.textEdited.connect(self._save_settings)
        grid.addWidget(self.packageNameEdit, 1, 1)
        layout.addLayout(grid)

        fetch_row = QHBoxLayout()
        self.pkgFetchButton = QPushButton("Fetch Latest Version", page)
        self.pkgFetchButton.clicked.connect(self._on_fetch_latest_package)
        fetch_row.addWidget(self.pkgFetchButton)

        self.pkgBrowseToggle = QPushButton("Browse versions...", page)
        self.pkgBrowseToggle.clicked.connect(self._toggle_pkg_browse)
        fetch_row.addWidget(self.pkgBrowseToggle)
        layout.addLayout(fetch_row)

        self.pkgBrowseTable = QTableWidget(0, 3, page)
        self.pkgBrowseTable.setHorizontalHeaderLabels(
            ["Version", "Uploaded", "Download"]
        )
        self.pkgBrowseTable.horizontalHeader().setStretchLastSection(True)
        self.pkgBrowseTable.setVisible(False)
        self.pkgBrowseTable.cellDoubleClicked.connect(self._on_pkg_row_activated)
        layout.addWidget(self.pkgBrowseTable)

        layout.addStretch(1)
        return page

    def _build_picker_panel(self):

        panel = QWidget(self)
        layout = QVBoxLayout(panel)

        layout.addWidget(QLabel("Select the firmware file:"))

        self.pickerList = QListWidget(panel)
        layout.addWidget(self.pickerList)

        row = QHBoxLayout()
        self.pickerLoadButton = QPushButton("Load Selected File", panel)
        self.pickerLoadButton.clicked.connect(self._on_load_selected_file)
        row.addWidget(self.pickerLoadButton)

        self.pickerBackButton = QPushButton("Back", panel)
        self.pickerBackButton.clicked.connect(self._show_tabs)
        row.addWidget(self.pickerBackButton)
        layout.addLayout(row)

        return panel

    def _show_tabs(self):
        self.pickerPanel.setVisible(False)
        self.tabs.setVisible(True)

    # ==================================================
    # Settings persistence
    # ==================================================

    def _load_settings(self):
        """
        Loading order matters here in a way that's easy to
        regress: ciRefEdit/ciJobEdit are editable QComboBoxes whose
        currentTextChanged is wired to _save_settings() (so picking
        or typing a value saves it immediately), and
        verifyTlsCheckbox's toggled fires on setChecked() too —
        unlike QLineEdit.setText(), which never fires textEdited.
        Setting any of these BEFORE every other field below it has
        been loaded would fire a premature _save_settings() that
        reads the not-yet-loaded fields' still-default widget values
        and overwrites their real saved settings with those defaults
        (hit for real: ciRefEdit's non-empty "main" default changing
        from "" fired a save that clobbered gitlab/packageProject and
        gitlab/packageName before they'd been loaded). Block signals
        on every settings-connected widget for the whole method
        rather than relying on load order staying safe forever.
        """

        for widget in (
            self.urlEdit, self.tokenEdit, self.verifyTlsCheckbox,
            self.ciProjectEdit, self.ciRefEdit, self.ciJobEdit,
            self.pkgProjectEdit, self.packageNameEdit,
        ):
            widget.blockSignals(True)

        try:
            s = self._settings
            self.urlEdit.setText(s.value("gitlab/instanceUrl", "https://gitlab.com", type=str))
            self.tokenEdit.setText(s.value("gitlab/token", "", type=str))
            self.verifyTlsCheckbox.setChecked(s.value("gitlab/verifyTls", True, type=bool))
            self.ciProjectEdit.setText(s.value("gitlab/ciProject", "", type=str))
            self.ciRefEdit.setEditText(s.value("gitlab/ciRef", "main", type=str))
            self.ciJobEdit.setEditText(s.value("gitlab/ciJobName", "", type=str))
            self.pkgProjectEdit.setText(s.value("gitlab/packageProject", "", type=str))
            self.packageNameEdit.setText(s.value("gitlab/packageName", "", type=str))
        finally:
            for widget in (
                self.urlEdit, self.tokenEdit, self.verifyTlsCheckbox,
                self.ciProjectEdit, self.ciRefEdit, self.ciJobEdit,
                self.pkgProjectEdit, self.packageNameEdit,
            ):
                widget.blockSignals(False)

    def _save_settings(self, _text=None):

        s = self._settings
        s.setValue("gitlab/instanceUrl", self.urlEdit.text())
        s.setValue("gitlab/token", self.tokenEdit.text())
        s.setValue("gitlab/verifyTls", self.verifyTlsCheckbox.isChecked())
        s.setValue("gitlab/ciProject", self.ciProjectEdit.text())
        s.setValue("gitlab/ciRef", self.ciRefEdit.currentText())
        s.setValue("gitlab/ciJobName", self.ciJobEdit.currentText())
        s.setValue("gitlab/packageProject", self.pkgProjectEdit.text())
        s.setValue("gitlab/packageName", self.packageNameEdit.text())
        s.sync()

    # ==================================================
    # CI Artifact tab actions
    # ==================================================

    def _load_ci_refs(self):
        self._run_action(
            "list_branches_and_tags", {},
            on_list=self._populate_ci_ref_combo,
        )

    def _populate_ci_ref_combo(self, refs):
        """
        Fills ciRefEdit's dropdown with branch/tag names (branches
        first, then tags, matching list_branches_and_tags()'s own
        order) after "Load branches/tags" runs. Same
        save/block/restore-current-text pattern as
        _populate_ci_job_combo() — a real branch and tag could share
        a name, so entries are deduped by name only, not by type.
        """

        if self._cancelled:
            return

        current_text = self.ciRefEdit.currentText()

        seen = set()
        names = []
        for ref in refs:
            name = ref["name"]
            if name not in seen:
                seen.add(name)
                names.append(name)

        self.ciRefEdit.blockSignals(True)
        self.ciRefEdit.clear()
        self.ciRefEdit.addItems(names)
        self.ciRefEdit.setEditText(current_text)
        self.ciRefEdit.blockSignals(False)

        self._append_log(f"Loaded {len(names)} branch/tag reference(s).")

    def _toggle_ci_browse(self):

        opening = not self.ciBrowseTable.isVisible()
        self.ciBrowseTable.setVisible(opening)

        if opening:
            ref = self.ciRefEdit.currentText()
            job_name = self.ciJobEdit.currentText() or None

            if ref:
                # A branch/tag is selected — scope the job list to
                # that ref's own pipeline(s), matching the reference
                # tool's "pick ref, then load jobs for it" flow,
                # instead of the project-wide recent-jobs list.
                self._run_action(
                    "list_jobs_for_ref", {"ref": ref, "job_name": job_name},
                    on_list=self._populate_ci_browse_table,
                )
            else:
                # No ref chosen — unchanged, original behavior:
                # recent jobs across the whole project.
                self._run_action(
                    "list_jobs", {"job_name": job_name},
                    on_list=self._populate_ci_browse_table,
                )

    def _populate_ci_job_combo(self, jobs):
        """
        Fills ciJobEdit's dropdown with the unique job names seen in
        the most recent Browse result (newest-first, matching the
        API's own order), without disturbing whatever text the user
        currently has typed/selected — editable QComboBox.addItem()
        can otherwise auto-select the first item added to a
        never-explicitly-set combo, so the current text is saved and
        restored around the repopulation, with signals blocked to
        avoid a spurious extra _save_settings() call.
        """

        current_text = self.ciJobEdit.currentText()

        seen = set()
        unique_names = []
        for job in jobs:
            name = job["job_name"]
            if name not in seen:
                seen.add(name)
                unique_names.append(name)

        self.ciJobEdit.blockSignals(True)
        self.ciJobEdit.clear()
        self.ciJobEdit.addItems(unique_names)
        self.ciJobEdit.setEditText(current_text)
        self.ciJobEdit.blockSignals(False)

    def _populate_ci_browse_table(self, jobs):

        if self._cancelled:
            return

        self._populate_ci_job_combo(jobs)

        self.ciBrowseTable.setRowCount(len(jobs))

        for row, job in enumerate(jobs):
            self.ciBrowseTable.setItem(row, 0, QTableWidgetItem(str(job["pipeline_id"])))
            self.ciBrowseTable.setItem(row, 1, QTableWidgetItem(job["job_name"]))
            self.ciBrowseTable.setItem(row, 2, QTableWidgetItem(job["ref"]))
            self.ciBrowseTable.setItem(row, 3, QTableWidgetItem(job["status"]))
            self.ciBrowseTable.setItem(row, 4, QTableWidgetItem(job["created_at"]))
            self.ciBrowseTable.item(row, 0).setData(Qt.UserRole, job)

            # Per-row Download button — matches the originally-
            # approved design mockup, in addition to (not instead
            # of) the existing double-click-to-download. Disabled
            # for artifact-less rows, same has_artifacts check
            # _on_ci_row_activated() already applies (not duplicated
            # here beyond this one condition — the handler itself
            # still guards defensively too).
            button = QPushButton("Download", self.ciBrowseTable)
            button.setEnabled(job["has_artifacts"])
            button.clicked.connect(
                lambda checked=False, r=row: self._on_ci_row_activated(r, 0)
            )
            self.ciBrowseTable.setCellWidget(row, 5, button)

        self._append_log(f"Loaded {len(jobs)} job(s).")

    def _on_ci_row_activated(self, row, _col):

        job = self.ciBrowseTable.item(row, 0).data(Qt.UserRole)

        if not job["has_artifacts"]:
            message = f"Job #{job['job_id']} ({job['status']}) has no artifact to download."
            self.statusLabel.setText(message)
            self._append_log(message)
            return

        self._run_action(
            "download_job_artifact", {"job_id": job["job_id"]},
            on_download=self._on_download_ready,
        )

    def _on_fetch_latest_artifact(self):

        self._run_action(
            "fetch_latest_artifact",
            {"ref": self.ciRefEdit.currentText(), "job_name": self.ciJobEdit.currentText()},
            on_download=self._on_download_ready,
        )

    def _on_download_ready(self, data, suggested_filename):

        if self._cancelled:
            # closeEvent() already ran (Cancel/close mid-fetch) — the
            # QThread was stopped, but this download_ready signal was
            # already queued on the main thread's event loop and
            # gets delivered anyway. Bail out before writing anything
            # to disk; nothing to clean up since nothing gets written.
            return

        self._download_dir = tempfile.mkdtemp(prefix="sflash_gitlab_")
        archive_path = os.path.join(self._download_dir, suggested_filename)

        with open(archive_path, "wb") as f:
            f.write(data)

        self._append_log(f"Downloaded {suggested_filename} ({len(data)} bytes).")

        if not zipfile.is_zipfile(archive_path):
            # Not a zip (current known sources always are, but don't
            # crash if that ever changes) — load it directly.
            self._load_and_close(archive_path)
            return

        extract_dir = os.path.join(self._download_dir, "extracted")
        with zipfile.ZipFile(archive_path) as zf:
            zf.extractall(extract_dir)
            names = zf.namelist()

        self._show_picker(extract_dir, names)

    def _show_picker(self, extract_dir, names):

        self.tabs.setVisible(False)
        self.pickerPanel.setVisible(True)
        self.pickerList.clear()

        preselect_row = None
        row = 0
        for name in names:
            if name.endswith("/"):
                # A directory entry within the zip (zipfile.namelist()
                # includes these), not a real file — don't list it as
                # a selectable candidate.
                continue

            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, os.path.join(extract_dir, name))
            self.pickerList.addItem(item)
            if (
                preselect_row is None
                and any(name.lower().endswith(ext) for ext in RECOGNIZED_FIRMWARE_EXTENSIONS)
            ):
                preselect_row = row
            row += 1

        if self.pickerList.count() > 0:
            self.pickerList.setCurrentRow(preselect_row if preselect_row is not None else 0)
        else:
            self.statusLabel.setText("Archive contains no files.")
            self._append_log("Archive contains no files.")

    def _on_load_selected_file(self):

        item = self.pickerList.currentItem()
        if item is None:
            return

        self._load_and_close(item.data(Qt.UserRole))

    def _load_and_close(self, path):

        if self._main_window._load_firmware_file(path):
            if self._main_window._loaded_datablocks:
                self._main_window._update_details_table(
                    self._main_window._loaded_datablocks[-1]
                )
            message = f"Loaded firmware from GitLab: {os.path.basename(path)}"
            self._main_window.log_information(message)
            self._append_log(f"Selected flashing input: {path}")

        self.close()

    # ==================================================
    # Package Registry tab actions
    # ==================================================

    def _toggle_pkg_browse(self):

        opening = not self.pkgBrowseTable.isVisible()
        self.pkgBrowseTable.setVisible(opening)

        if opening:
            # Stash the package name actually used for this browse,
            # so a later row activation uses the name the list was
            # fetched for — not whatever packageNameEdit says at
            # click time, which the user may have since edited (see
            # _on_pkg_row_activated()).
            self._pkg_browse_name = self.packageNameEdit.text()
            self._run_action(
                "list_packages", {"package_name": self._pkg_browse_name},
                on_list=self._populate_pkg_browse_table,
            )

    def _populate_pkg_browse_table(self, versions):

        if self._cancelled:
            return

        self.pkgBrowseTable.setRowCount(len(versions))

        for row, version in enumerate(versions):
            self.pkgBrowseTable.setItem(row, 0, QTableWidgetItem(version["version"]))
            self.pkgBrowseTable.setItem(row, 1, QTableWidgetItem(version["created_at"]))
            self.pkgBrowseTable.item(row, 0).setData(Qt.UserRole, version)

            # Per-row Download button — matches the originally-
            # approved design mockup, in addition to (not instead
            # of) the existing double-click-to-download. Always
            # enabled: unlike CI jobs, there's no has_artifacts-
            # equivalent signal available at listing time for
            # package versions.
            button = QPushButton("Download", self.pkgBrowseTable)
            button.clicked.connect(
                lambda checked=False, r=row: self._on_pkg_row_activated(r, 0)
            )
            self.pkgBrowseTable.setCellWidget(row, 2, button)

        self._append_log(f"Loaded {len(versions)} version(s).")

    def _on_pkg_row_activated(self, row, _col):

        version = self.pkgBrowseTable.item(row, 0).data(Qt.UserRole)

        self._run_action(
            "download_package_version",
            {"package_name": self._pkg_browse_name, "version": version["version"]},
            on_download=self._on_download_ready,
        )

    def _on_fetch_latest_package(self):

        self._run_action(
            "fetch_latest_package", {"package_name": self.packageNameEdit.text()},
            on_download=self._on_download_ready,
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
            # Defense in depth: the fetch/browse controls below are
            # disabled while a fetch is in flight, so this should be
            # unreachable via normal UI interaction — but if it's
            # ever hit some other way, say why nothing happened
            # instead of silently no-op'ing.
            self.statusLabel.setText("A fetch is already in progress. Please wait.")
            self._append_log("A fetch is already in progress. Please wait.")
            return

        self.statusLabel.setText("")
        self.ciFetchButton.setEnabled(False)
        self.pkgFetchButton.setEnabled(False)
        self.ciBrowseToggle.setEnabled(False)
        self.pkgBrowseToggle.setEnabled(False)

        # CI Artifact and Package Registry commonly live in two
        # different GitLab projects (e.g. one repo builds firmware
        # via CI, a separate one hosts the published packages) — the
        # Connection card only holds what both genuinely share
        # (Instance URL, Access Token); each tab has its own Project
        # field instead of one shared one.
        project = (
            self.ciProjectEdit.text()
            if action in _CI_ACTIONS
            else self.pkgProjectEdit.text()
        )

        self._thread = QThread()
        self._worker = GitLabFetchWorker(
            action,
            self.urlEdit.text(), project, self.tokenEdit.text(),
            ssl_verify=self.verifyTlsCheckbox.isChecked(),
            **params,
        )
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.progress_message.connect(self.statusLabel.setText)
        self._worker.progress_message.connect(self._append_log)

        if on_list is not None:
            self._worker.list_ready.connect(on_list)
        if on_download is not None:
            self._worker.download_ready.connect(on_download)
        self._worker.error.connect(self.statusLabel.setText)
        self._worker.error.connect(self._append_log)

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
        self.pkgFetchButton.setEnabled(True)
        self.ciBrowseToggle.setEnabled(True)
        self.pkgBrowseToggle.setEnabled(True)

    def closeEvent(self, event):

        # Must be set before anything else: a download_ready signal
        # already queued on the main thread's event loop (emitted by
        # the worker just before we get here) still gets delivered
        # after close() returns — _on_download_ready() etc. check
        # this flag and bail out rather than loading an unrequested
        # firmware file post-cancel.
        self._cancelled = True

        if self._thread is not None and self._thread.isRunning():
            # Same reasoning as gui/test_connection_dialog.py's
            # closeEvent(): worker.finished -> thread.quit is a
            # queued cross-thread connection, only delivered once
            # the main thread's event loop next runs — calling
            # wait() here would block that very event loop, so call
            # quit() directly first (thread-safe from any thread).
            self._thread.quit()
            self._thread.wait()

        # Clean up any download temp dir created by a completed
        # fetch. Safe ordering-wise: any file that was going to be
        # parsed (_load_and_close() -> _load_firmware_file()) has
        # already been read and returned before self.close() is ever
        # called, so this only ever runs after parsing is done.
        download_dir = getattr(self, "_download_dir", None)
        if download_dir and os.path.isdir(download_dir):
            shutil.rmtree(download_dir, ignore_errors=True)

        event.accept()

# Load Firmware from GitLab — Design

Status: approved by user (design + UI mockup), pending spec review before implementation planning.
Mockup: interactive HTML, reviewed and approved (both entry points).

## 1. Motivation

Today, SFlash only loads firmware from a local file via `QFileDialog`
(`ConfigureTabMixin.add_new_datablock()` → `_load_firmware_file()` →
`parsers/auto_parser.py`). The user's firmware is built by a GitLab CI
pipeline and also published to a GitLab Package Registry (Generic
packages); both already exist and are usable today. This feature adds
a third way to get a firmware file into SFlash: fetch it directly from
GitLab instead of the user manually downloading it out-of-band and
then picking it from disk.

## 2. Scope

**In scope:**
- Fetching from GitLab CI job artifacts (latest-by-ref+job, or browse
  a list of recent jobs and pick one).
- Fetching from GitLab Package Registry, Generic packages only
  (latest-by-package-name, or browse a list of versions and pick one).
- One fixed GitLab project/instance, configured once (not a
  multi-project switcher).
- Extracting a downloaded `.zip` and letting the user pick which file
  inside it is the actual firmware to load.
- A GitLab connection settings block (instance URL, project
  path/ID, access token) persisted via QSettings, same mechanism as
  every other Configure-page setting in this app.

**Explicitly out of scope (non-goals):**
- Multiple GitLab projects/instances or switching between them.
- Any GitLab *write* operation (uploading, triggering pipelines,
  publishing packages). Read-only fetch only.
- Package Registry types other than Generic packages (no npm/Maven/
  PyPI/conan support).
- A persistent local download cache/dedup — every fetch downloads
  fresh into a throwaway temp directory, cleaned up like any other
  temp file.
- OS keychain / secret-manager integration for the access token — the
  user explicitly chose QSettings (plain, same as Security DLL path)
  over an env-var-only or hybrid option, trading token-at-rest
  security for one-time setup convenience.

## 3. Architecture

Three new pieces, following this codebase's existing layering
(`gui/` → `core/` → `communication/`) and conventions:

### 3.1 `communication/gitlab_client.py` (new)

Thin wrapper around the `python-gitlab` library, mirroring
`communication/vector_can.py`'s style: plain functions/small class, a
dedicated exception hierarchy, lazy import so the rest of the app
works with zero changes when the library isn't installed.

```python
class GitLabError(Exception): ...
class GitLabAuthError(GitLabError): ...      # bad/expired token
class GitLabNotFoundError(GitLabError): ...  # project/job/package not found
class GitLabConnectionError(GitLabError): ...  # network/instance unreachable
```

Functions (exact `python-gitlab` call signatures to be confirmed
against the installed version during implementation — the behavior
below is the contract, not a specific API surface):

- `list_recent_jobs(url, project, token, job_name=None, limit=20)` →
  list of dicts (`pipeline_id`, `job_name`, `ref`, `status`,
  `created_at`, `has_artifacts`).
- `download_latest_artifact(url, project, token, ref, job_name)` →
  raw bytes of the artifact archive.
- `download_job_artifact(url, project, token, job_id)` → raw bytes,
  for a specific job picked from the browse list.
- `list_package_versions(url, project, token, package_name, limit=20)`
  → list of dicts (`version`, `created_at`).
- `download_latest_package_file(url, project, token, package_name)` →
  raw bytes.
- `download_package_version(url, project, token, package_name, version)`
  → raw bytes, for a specific version picked from the browse list.

Every function raises the specific `GitLabError` subclass for its
failure mode (401 → `GitLabAuthError`, 404 → `GitLabNotFoundError`,
network/DNS/timeout → `GitLabConnectionError`) with the real
underlying message included — same "never swallow the real reason"
philosophy already shipped for Vector hardware detection
(`detect_vector_channels_with_error()`, Phase 4.76).

### 3.2 `gui/gitlab_dialog.py` (new)

`GitLabFetchDialog(QDialog)`, structurally identical in its threading
lifecycle to `gui/test_connection_dialog.py`:

- A `GitLabFetchWorker(QObject)` does the network I/O (list/download
  calls into `gitlab_client.py`), moved to a `QThread` — never on the
  GUI thread, since these are blocking network calls.
- Same signal/lifecycle rules as documented in `CLAUDE.md`'s
  "Threading model" section: `worker.finished` connects to
  `thread.quit` + `worker.deleteLater`; `thread.finished` (not
  `worker.finished`) is the only place that clears the
  dialog's `_thread`/`_worker` references.
- Progress: determinate download progress if `python-gitlab`/the
  underlying HTTP layer exposes a byte-count callback; otherwise an
  indeterminate "Downloading…" state (confirmed at implementation
  time, doesn't change the design).

Dialog layout (see mockup):
- **GitLab Connection** card: Instance URL, Project (ID or path),
  Access Token (`QLineEdit.EchoMode.Password`). Persisted via
  QSettings (see §4).
- Two tabs, **CI Artifact** and **Package Registry**, each with:
  - A primary button ("Fetch Latest Artifact" / "Fetch Latest
    Version") using the tab's own ref+job-name / package-name fields.
  - A "Browse…" toggle that expands an in-dialog table (recent jobs /
    versions) with a per-row "Download" action. A row with no
    artifact (e.g. a failed CI job) has its Download button disabled.
- After any successful download: the dialog body switches to a
  **file picker** view — the archive is extracted (via stdlib
  `zipfile`) into the same temp directory, its contents listed, and
  the first entry whose extension matches
  `parsers/auto_parser.py`'s known firmware extensions
  (`.hex`/`.s19`/`.s3`/`.bin`/etc. — reusing that module's extension
  list as the single source of truth, not a duplicated one) is
  pre-selected and tagged "Recognized firmware". A "Back" link
  returns to the tabs without re-downloading.
- Confirming a file selection calls the *existing*
  `MainWindow._load_firmware_file(extracted_path)` — identical
  code path to picking a local file today, so it lands in the
  Datablocks table with the same parsing, error handling, and Recent
  Files behavior, no duplicated logic.
- If the downloaded file is *not* a zip (edge case — current known
  sources are always zips, but the client shouldn't crash if that
  changes): skip the picker, load the file directly.

### 3.3 Two entry points, one dialog

Both call the same `MainWindow.open_gitlab_fetch_dialog()`,
mirroring exactly how `open_test_connection_dialog()` is already
reachable from two places:

- **File → Load from GitLab…** — new `QAction` in `main_window.ui`'s
  `menuFile`, placed directly under `actionLoadFirmware` (before the
  Recent Files submenu). Wired in `gui/menu_bar.py`.
- **A button on Configure → Data**, placed directly below the
  Details table (`tableWidgetDetails`) — new `QPushButton` declared
  in `main_window.ui` (this is a static, Designer-expressible widget,
  not embedded in a table cell, so it belongs in the `.ui` per
  CLAUDE.md's ".ui first" rule, unlike Compression/Encryption
  Method's `setCellWidget()` case). Wired in `gui/configure_tab.py`,
  same pattern as `test_connection_button_clicked()`.

## 4. Settings & persistence

New QSettings keys (added to `gui/settings_profile.py`'s
`save_profile()`/`load_profile()`, same pattern as every existing
field there):

| Key | Field | Notes |
|---|---|---|
| `gitlab/instanceUrl` | Instance URL | default `https://gitlab.com` |
| `gitlab/project` | Project ID or path | e.g. `group/firmware-repo` |
| `gitlab/token` | Access Token | plain text in `SFlash.ini`, same tradeoff as Security DLL path — user's explicit choice |
| `gitlab/ciRef` | CI Artifact: default branch/ref | default `main` |
| `gitlab/ciJobName` | CI Artifact: default job name | |
| `gitlab/packageName` | Package Registry: default package name | |

`.sfproj` project files are **not** extended with GitLab fields —
these are connection/environment settings (like Security DLL path,
hardware channel), not part of a flashing session's saved state.

## 5. Error handling

Every `GitLabError` subclass raised by `gitlab_client.py` is caught in
`GitLabFetchWorker` and surfaced as a specific, readable message in
the dialog itself (a status/log area, not a silent failure) —
distinguishing "wrong/expired token", "project not found", "no
network/instance unreachable", and "no artifact/package matches"
rather than a generic failure. This directly continues the philosophy
established in Phase 4.76 for Vector hardware detection: a user
should never see an unexplained empty list or a bare "failed" with no
reason.

## 6. Dependency

`python-gitlab` becomes a new **optional** dependency, treated exactly
like `python-can`:
- Added to `requirements.txt` and `requirements_build.txt`, commented
  out by default, with the same explanatory comment style already
  used for `python-can` (why it's optional, that it can't be added to
  an already-built `.exe`).
- Lazily imported inside `gitlab_client.py` (inside functions/a
  try/except at call time, not a top-level `import gitlab`), so
  SFlash's core behavior (Virtual ECU, local file loading, Vector
  hardware) is entirely unaffected for users who never install it.
- Calling any `gitlab_client.py` function without the library
  installed raises `GitLabError("python-gitlab not installed. Run:
  pip install python-gitlab")`, surfaced the same way as every other
  `GitLabError`.

## 7. Testing (for the implementation plan)

- `communication/gitlab_client.py`: unit tests mocking `python-gitlab`
  objects, same style as `tests/test_vector_can.py`'s
  `_patched_canlib()` — one test per `GitLabError` subclass, plus
  happy-path list/download tests.
- `gui/gitlab_dialog.py`: GUI smoke tests (dialog opens from both
  entry points, tabs switch, Browse expands, zip picker pre-selects
  the recognized firmware file, Cancel/Back don't leave stray state).
- A real-`QThread` test for `GitLabFetchDialog`, mirroring
  `tests/test_flash_threading.py`'s discipline (this codebase has a
  documented history of `QThread` lifecycle crashes that only a real
  `QThread` run — not a synchronous worker call — can catch).
- Settings persistence round-trip test (`tests/test_gui_smoke.py`
  `TestSettingsProfile`, same pattern as existing fields).

## 8. Open items resolved during brainstorming

- CI Artifact vs Package Registry: **both**, from day one.
- Single fixed GitLab project vs multi-project: **single, fixed**.
- Token storage: **QSettings** (convenience over at-rest security —
  user's explicit choice, documented in §6/§4).
- Fetch mode: **both** — quick "Fetch Latest" plus an expandable
  "Browse…" list, not just one or the other.
- Entry point: **File menu** (`Load from GitLab...`) **and** a button
  on Configure → Data below the Details table — mirroring the
  existing Test Connection precedent (menu item + page button calling
  the same handler).
- Source file format: **always a `.zip`** needing extraction + a
  firmware-file-picker step (handled uniformly for both sources).
- Client library: **`python-gitlab`** (Approach B), accepting the new
  optional dependency in exchange for far less hand-written
  auth/pagination/error-handling code than a raw `urllib` client.

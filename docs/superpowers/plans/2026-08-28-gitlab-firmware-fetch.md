# Load Firmware from GitLab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user fetch a firmware file to flash directly from GitLab (CI job artifacts or the Package Registry) instead of only picking a local file, reachable from two entry points (File menu, and a button on Configure → Data) that both open the same dialog.

**Architecture:** A new `communication/gitlab_client.py` (thin `python-gitlab` wrapper, same style as `communication/vector_can.py`) does all network I/O. A new `gui/gitlab_dialog.py` (`GitLabFetchDialog` + `GitLabFetchWorker`, same `QThread` lifecycle pattern as `gui/test_connection_dialog.py`) drives it from the GUI. A successful fetch always yields a `.zip`; the dialog extracts it to a temp dir, lets the user pick the firmware file inside, then hands that local path to the *existing* `MainWindow._load_firmware_file()` — no new parsing logic anywhere.

**Tech Stack:** PySide6 (QThread/QObject/Signal), `python-gitlab` (new optional dependency), stdlib `zipfile`/`tempfile`.

**Spec:** `docs/superpowers/specs/2026-08-27-gitlab-firmware-fetch-design.md`

## Global Constraints

- `python-gitlab` is an **optional** dependency: commented out by default in `requirements.txt` and `requirements_build.txt` (same as `python-can`), lazily imported only inside `communication/gitlab_client.py` functions — nothing else in the app may import it at module load time.
- Every GitLab connection settings field (URL, project, token, CI ref/job, package name) is owned by `GitLabFetchDialog` itself via its own `QSettings(QSettings.IniFormat, QSettings.UserScope, APP_AUTHOR, APP_NAME)` instance — `gui/settings_profile.py` is **not** modified.
- The dialog's own widgets are built in Python inside `gui/gitlab_dialog.py`, following `gui/test_connection_dialog.py`'s precedent — only its two *entry-point triggers* (`actionLoadFromGitLab`, `buttonLoadFromGitLab`) go in `gui/main_window.ui`, per CLAUDE.md's ".ui first" rule (that rule governs `main_window.ui`; standalone dialogs in this codebase are already Python-built, e.g. `TestConnectionDialog`).
- `QThread` lifecycle must follow CLAUDE.md's "Threading model" rules exactly: a worker's `finished` signal is emitted while its thread is still executing, so any slot connected to it must never touch `self._thread`/`self._worker` — only a slot connected to `thread.finished` may do that. `closeEvent()` must call `thread.quit()` directly (not rely on a queued `worker.finished → thread.quit` connection) before `thread.wait()`.
- Every `GitLabError` subclass must produce a specific, real message to the user (never a silent empty result) — same philosophy as `detect_vector_channels_with_error()` (Phase 4.76).
- `docs/walkthrough.md` gets a new `## Phase X.Y` entry for this work (check its current last phase number first), following its existing Vietnamese format (intro paragraph, `### Thay đổi`, `### Đã kiểm tra`) — this is a standing project rule, not optional.
- Full test suite (`python -m unittest discover -s tests -p "test_*.py"`) plus `tests/test_flash_threading.py` must pass before this work is considered done, per CLAUDE.md.

---

### Task 1: `communication/gitlab_client.py` — exceptions + CI Artifact functions

**Files:**
- Create: `communication/gitlab_client.py`
- Modify: `requirements.txt`
- Modify: `requirements_build.txt`
- Test: `tests/test_gitlab_client.py`

**Interfaces:**
- Produces: `GitLabError`, `GitLabAuthError`, `GitLabNotFoundError`, `GitLabConnectionError` (all in `communication/gitlab_client.py`); `list_recent_jobs(url, project, token, job_name=None, limit=20) -> list[dict]` (each dict: `pipeline_id`, `job_id`, `job_name`, `ref`, `status`, `created_at`, `has_artifacts`); `download_latest_artifact(url, project, token, ref, job_name) -> bytes`; `download_job_artifact(url, project, token, job_id) -> bytes`.

- [ ] **Step 1: Add the optional dependency to both requirements files**

In `requirements.txt`, after the existing `python-can` block, add:

```
# Tùy chọn — chỉ cần khi dùng tính năng "Load from GitLab"
# (fetch firmware từ CI Artifact / Package Registry). Không cần
# nếu chỉ nạp firmware từ file local.
# Bỏ comment dòng dưới để cài, hoặc: pip install python-gitlab
# python-gitlab>=4.0
```

In `requirements_build.txt`, after the existing `python-can` block, add:

```
# Tùy chọn — chỉ bỏ comment dòng dưới nếu muốn bản .exe build ra
# hỗ trợ luôn tính năng "Load from GitLab". Nếu không cài lúc
# build, file .exe vẫn chạy tốt, chỉ thiếu tùy chọn đó — vì
# python-gitlab không được đóng gói bên trong .exe. Không thể cài
# thêm vào sau khi đã build.
# python-gitlab>=4.0
```

- [ ] **Step 2: Write the failing tests for the exception hierarchy and `list_recent_jobs`**

Create `tests/test_gitlab_client.py`:

```python
# ==================================================
# GitLab Client Tests
# ==================================================
#
# python-gitlab isn't installed in this dev/test env (it's an
# optional dependency, same treatment as python-can) — every test
# here mocks sys.modules["gitlab"], same technique
# tests/test_vector_can.py uses for python-can's "vector" backend.
# ==================================================

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from communication import gitlab_client
from communication.gitlab_client import (
    GitLabError,
    GitLabAuthError,
    GitLabNotFoundError,
    GitLabConnectionError,
)


def _fake_gitlab_module():
    """
    Builds a fake `gitlab` module good enough for gitlab_client.py's
    `import gitlab` + `gitlab.Gitlab(...)` + `gitlab.exceptions.*`
    usage. Returns (module, Gitlab_class_mock) so a test can shape
    what gl.auth()/gl.projects.get(...) do.
    """

    module = MagicMock()

    class FakeGitlabError(Exception):
        pass

    class FakeGitlabAuthenticationError(FakeGitlabError):
        pass

    class FakeGitlabGetError(FakeGitlabError):
        def __init__(self, message, response_code=None):
            super().__init__(message)
            self.response_code = response_code

    class FakeGitlabHttpError(FakeGitlabError):
        def __init__(self, message, response_code=None):
            super().__init__(message)
            self.response_code = response_code

    module.exceptions.GitlabError = FakeGitlabError
    module.exceptions.GitlabAuthenticationError = FakeGitlabAuthenticationError
    module.exceptions.GitlabGetError = FakeGitlabGetError
    module.exceptions.GitlabHttpError = FakeGitlabHttpError

    gl = MagicMock()
    module.Gitlab.return_value = gl

    return module, gl


def _patched_gitlab(module):
    return patch.dict(sys.modules, {"gitlab": module})


class TestExceptionHierarchy(unittest.TestCase):

    def test_all_subclass_gitlaberror(self):
        self.assertTrue(issubclass(GitLabAuthError, GitLabError))
        self.assertTrue(issubclass(GitLabNotFoundError, GitLabError))
        self.assertTrue(issubclass(GitLabConnectionError, GitLabError))


class TestConnect(unittest.TestCase):

    def test_missing_python_gitlab_raises_gitlaberror(self):
        # python-gitlab isn't installed in this dev/test env (same
        # as python-can — see tests/test_vector_can.py's
        # test_no_driver_returns_empty_list) — no mocking needed,
        # `import gitlab` genuinely fails here. Just guard against
        # a leftover fake "gitlab" module from another test's
        # sys.modules patch (which _patched_gitlab()'s
        # patch.dict(sys.modules, ...) already restores on exit, so
        # this is defensive, not expected to trigger).
        with patch.dict(sys.modules):
            for mod in list(sys.modules):
                if mod == "gitlab" or mod.startswith("gitlab."):
                    del sys.modules[mod]
            with self.assertRaises(GitLabError):
                gitlab_client.list_recent_jobs(
                    "https://gitlab.com", "group/proj", "tok"
                )

    def test_bad_token_raises_autherror(self):
        module, gl = _fake_gitlab_module()
        gl.auth.side_effect = module.exceptions.GitlabAuthenticationError("401")
        with _patched_gitlab(module):
            with self.assertRaises(GitLabAuthError):
                gitlab_client.list_recent_jobs(
                    "https://gitlab.com", "group/proj", "bad-token"
                )

    def test_unreachable_instance_raises_connectionerror(self):
        module, gl = _fake_gitlab_module()
        gl.auth.side_effect = OSError("Name or service not known")
        with _patched_gitlab(module):
            with self.assertRaises(GitLabConnectionError):
                gitlab_client.list_recent_jobs(
                    "https://unreachable.example", "group/proj", "tok"
                )

    def test_project_not_found_raises_notfounderror(self):
        module, gl = _fake_gitlab_module()
        gl.projects.get.side_effect = module.exceptions.GitlabGetError(
            "404 Project Not Found", response_code=404
        )
        with _patched_gitlab(module):
            with self.assertRaises(GitLabNotFoundError):
                gitlab_client.list_recent_jobs(
                    "https://gitlab.com", "group/missing-proj", "tok"
                )

    def test_project_fetch_network_error_raises_connectionerror(self):
        # A non-GitlabGetError failure (e.g. a network drop) while
        # fetching the project must still come out as a GitLabError
        # subclass, not leak through untyped — _get_project() needs
        # a broad except Exception fallback alongside its narrower
        # GitlabGetError handling, same as _connect()'s already has.
        module, gl = _fake_gitlab_module()
        gl.projects.get.side_effect = OSError("Connection reset by peer")
        with _patched_gitlab(module):
            with self.assertRaises(GitLabConnectionError):
                gitlab_client.list_recent_jobs(
                    "https://gitlab.com", "group/proj", "tok"
                )


class TestListRecentJobs(unittest.TestCase):

    def _make_job(self, job_id, name, ref, status, pipeline_id, artifacts_file=None):
        job = MagicMock()
        job.id = job_id
        job.name = name
        job.ref = ref
        job.status = status
        job.created_at = "2026-08-27T09:14:00Z"
        job.pipeline = {"id": pipeline_id}
        job.artifacts_file = artifacts_file
        return job

    def test_returns_dicts_with_expected_keys(self):
        module, gl = _fake_gitlab_module()
        proj = MagicMock()
        gl.projects.get.return_value = proj
        proj.jobs.list.return_value = [
            self._make_job(1, "build_firmware", "main", "success", 100, {"filename": "a.zip"}),
        ]

        with _patched_gitlab(module):
            jobs = gitlab_client.list_recent_jobs(
                "https://gitlab.com", "group/proj", "tok"
            )

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0], {
            "pipeline_id": 100,
            "job_id": 1,
            "job_name": "build_firmware",
            "ref": "main",
            "status": "success",
            "created_at": "2026-08-27T09:14:00Z",
            "has_artifacts": True,
        })

    def test_filters_by_job_name(self):
        module, gl = _fake_gitlab_module()
        proj = MagicMock()
        gl.projects.get.return_value = proj
        proj.jobs.list.return_value = [
            self._make_job(1, "build_firmware", "main", "success", 100),
            self._make_job(2, "lint", "main", "success", 100),
        ]

        with _patched_gitlab(module):
            jobs = gitlab_client.list_recent_jobs(
                "https://gitlab.com", "group/proj", "tok", job_name="build_firmware"
            )

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["job_name"], "build_firmware")

    def test_failed_job_has_artifacts_false(self):
        module, gl = _fake_gitlab_module()
        proj = MagicMock()
        gl.projects.get.return_value = proj
        proj.jobs.list.return_value = [
            self._make_job(1, "build_firmware", "main", "failed", 100, artifacts_file=None),
        ]

        with _patched_gitlab(module):
            jobs = gitlab_client.list_recent_jobs(
                "https://gitlab.com", "group/proj", "tok"
            )

        self.assertFalse(jobs[0]["has_artifacts"])

    def test_fetches_a_single_bounded_page_not_the_whole_history(self):
        # list_recent_jobs must NOT use _list_all()/get_all=True —
        # that walks every page of the project's entire job history
        # before any limit= truncation runs, defeating the point of
        # limit= for a feature meant to power a quick job picker.
        # per_page=limit alone (no all=/get_all=) is a single bounded
        # page, correct and identical across python-gitlab versions.
        module, gl = _fake_gitlab_module()
        proj = MagicMock()
        gl.projects.get.return_value = proj
        proj.jobs.list.return_value = [
            self._make_job(1, "build_firmware", "main", "success", 100),
        ]

        with _patched_gitlab(module):
            gitlab_client.list_recent_jobs(
                "https://gitlab.com", "group/proj", "tok", limit=5
            )

        proj.jobs.list.assert_called_once_with(per_page=5)

    def test_truncates_to_limit_when_more_are_returned(self):
        module, gl = _fake_gitlab_module()
        proj = MagicMock()
        gl.projects.get.return_value = proj
        proj.jobs.list.return_value = [
            self._make_job(i, "build_firmware", "main", "success", 100)
            for i in range(10)
        ]

        with _patched_gitlab(module):
            jobs = gitlab_client.list_recent_jobs(
                "https://gitlab.com", "group/proj", "tok", limit=3
            )

        self.assertEqual(len(jobs), 3)


class TestListAll(unittest.TestCase):
    """
    _list_all() itself — used for lists that are naturally small and
    unbounded (e.g. the files attached to one package version, Task 2),
    NOT for anything with a limit= — see its docstring.
    """

    def test_uses_get_all_by_default(self):
        manager = MagicMock()
        manager.list.return_value = ["a", "b"]

        result = gitlab_client._list_all(manager, per_page=20)

        self.assertEqual(result, ["a", "b"])
        manager.list.assert_called_once_with(get_all=True, per_page=20)

    def test_falls_back_to_all_kwarg_on_typeerror(self):
        # Simulates an older python-gitlab whose Manager.list()
        # doesn't accept get_all= at all (raises TypeError) —
        # _list_all() must retry with all= instead of crashing.
        manager = MagicMock()

        def fake_list(*args, **kwargs):
            if "get_all" in kwargs:
                raise TypeError("list() got an unexpected keyword argument 'get_all'")
            return ["a"]

        manager.list.side_effect = fake_list

        result = gitlab_client._list_all(manager, per_page=20)

        self.assertEqual(result, ["a"])


class TestDownloadArtifacts(unittest.TestCase):

    def test_download_latest_artifact_returns_bytes(self):
        module, gl = _fake_gitlab_module()
        proj = MagicMock()
        gl.projects.get.return_value = proj
        proj.artifacts.return_value = b"PK\x03\x04zipbytes"

        with _patched_gitlab(module):
            data = gitlab_client.download_latest_artifact(
                "https://gitlab.com", "group/proj", "tok",
                ref="main", job_name="build_firmware",
            )

        self.assertEqual(data, b"PK\x03\x04zipbytes")
        proj.artifacts.assert_called_once_with(ref_name="main", job="build_firmware")

    def test_download_latest_artifact_no_match_raises_notfounderror(self):
        module, gl = _fake_gitlab_module()
        proj = MagicMock()
        gl.projects.get.return_value = proj
        proj.artifacts.side_effect = module.exceptions.GitlabGetError(
            "404", response_code=404
        )

        with _patched_gitlab(module):
            with self.assertRaises(GitLabNotFoundError):
                gitlab_client.download_latest_artifact(
                    "https://gitlab.com", "group/proj", "tok",
                    ref="no-such-branch", job_name="build_firmware",
                )

    def test_download_job_artifact_returns_bytes(self):
        module, gl = _fake_gitlab_module()
        proj = MagicMock()
        gl.projects.get.return_value = proj
        job = MagicMock()
        job.artifacts.return_value = b"PK\x03\x04zipbytes"
        proj.jobs.get.return_value = job

        with _patched_gitlab(module):
            data = gitlab_client.download_job_artifact(
                "https://gitlab.com", "group/proj", "tok", job_id=4821,
            )

        self.assertEqual(data, b"PK\x03\x04zipbytes")
        proj.jobs.get.assert_called_once_with(4821)

    def test_download_job_artifact_missing_raises_notfounderror(self):
        module, gl = _fake_gitlab_module()
        proj = MagicMock()
        gl.projects.get.return_value = proj
        proj.jobs.get.side_effect = module.exceptions.GitlabGetError(
            "404", response_code=404
        )

        with _patched_gitlab(module):
            with self.assertRaises(GitLabNotFoundError):
                gitlab_client.download_job_artifact(
                    "https://gitlab.com", "group/proj", "tok", job_id=999999,
                )

    def test_download_latest_artifact_network_error_raises_connectionerror(self):
        # Same reasoning as test_project_fetch_network_error_raises_
        # connectionerror above — a non-GitlabGetError failure must
        # still come out typed, not leak through untyped.
        module, gl = _fake_gitlab_module()
        proj = MagicMock()
        gl.projects.get.return_value = proj
        proj.artifacts.side_effect = OSError("Connection reset by peer")

        with _patched_gitlab(module):
            with self.assertRaises(GitLabConnectionError):
                gitlab_client.download_latest_artifact(
                    "https://gitlab.com", "group/proj", "tok",
                    ref="main", job_name="build_firmware",
                )

    def test_download_job_artifact_network_error_raises_connectionerror(self):
        module, gl = _fake_gitlab_module()
        proj = MagicMock()
        gl.projects.get.return_value = proj
        proj.jobs.get.side_effect = OSError("Connection reset by peer")

        with _patched_gitlab(module):
            with self.assertRaises(GitLabConnectionError):
                gitlab_client.download_job_artifact(
                    "https://gitlab.com", "group/proj", "tok", job_id=4821,
                )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m unittest tests.test_gitlab_client -v`
Expected: `ModuleNotFoundError: No module named 'communication.gitlab_client'` (the module doesn't exist yet).

- [ ] **Step 4: Implement `communication/gitlab_client.py` (exceptions + connection helpers + CI artifact functions)**

```python
# ==================================================
# GitLab Client
# ==================================================
#
# Thin wrapper around python-gitlab for the "Load from GitLab"
# feature (fetch a firmware file from a CI job artifact or the
# Package Registry). Same treatment as communication/vector_can.py:
# python-gitlab is an optional dependency, lazily imported here so
# the rest of the app works with zero changes when it isn't
# installed — see requirements.txt.
# ==================================================


class GitLabError(Exception):
    """Base exception for GitLab client errors."""
    pass


class GitLabAuthError(GitLabError):
    """Raised when the access token is missing, invalid, or expired."""
    pass


class GitLabNotFoundError(GitLabError):
    """Raised when the project, job, or package doesn't exist."""
    pass


class GitLabConnectionError(GitLabError):
    """Raised when the GitLab instance can't be reached at all."""
    pass


def _connect(url, token):
    """
    Returns (gl, gitlab_module) — an authenticated gitlab.Gitlab
    client plus the gitlab module itself (callers need it for
    gitlab_module.exceptions.* type checks without importing gitlab
    at module load time). Raises GitLabError/a subclass on any
    failure — never returns a client that hasn't been verified to
    actually authenticate.
    """

    try:
        import gitlab
    except ImportError as e:
        raise GitLabError(
            f"python-gitlab not installed. Run: pip install python-gitlab ({e})"
        )

    try:
        gl = gitlab.Gitlab(url, private_token=token, timeout=15)
        gl.auth()
    except gitlab.exceptions.GitlabAuthenticationError as e:
        raise GitLabAuthError(f"Authentication failed: {e}")
    except Exception as e:
        raise GitLabConnectionError(f"Could not reach {url}: {e}")

    return gl, gitlab


def _get_project(gl, gitlab_module, project):

    try:
        return gl.projects.get(project)
    except gitlab_module.exceptions.GitlabGetError as e:
        if getattr(e, "response_code", None) == 404:
            raise GitLabNotFoundError(f"Project '{project}' not found: {e}")
        raise GitLabConnectionError(f"Could not load project '{project}': {e}")
    except Exception as e:
        raise GitLabConnectionError(f"Could not load project '{project}': {e}")


def _list_all(manager, **kwargs):
    """
    Fetches every page of a manager.list() call. python-gitlab
    renamed its "fetch every page" kwarg across major versions
    (all=True in <3.0, get_all=True in >=3.0) — try the current name
    first, fall back to the old one on TypeError, same defensive
    retry VectorCanInterface.connect() already uses for python-can's
    serial= kwarg (communication/vector_can.py). Only use this for
    lists that are naturally small and unbounded (e.g. the files
    attached to one package version) — for anything bounded by a
    `limit`, call manager.list(per_page=limit) directly instead
    (single-page mode, no all=/get_all=): get_all=True/all=True walks
    every page of the *entire* history before any limit= truncation
    in Python ever runs, defeating the bound and doing a potentially
    huge amount of needless network I/O.
    """

    try:
        return list(manager.list(get_all=True, **kwargs))
    except TypeError:
        return list(manager.list(all=True, **kwargs))


def list_recent_jobs(url, project, token, job_name=None, limit=20):
    """
    Returns up to `limit` most recent CI jobs for the project,
    newest first, as a list of dicts: pipeline_id, job_id, job_name,
    ref, status, created_at, has_artifacts. If job_name is given,
    only jobs with that exact name are returned.
    """

    gl, gitlab_module = _connect(url, token)
    proj = _get_project(gl, gitlab_module, project)

    try:
        # Deliberately NOT _list_all() — GitLab's jobs endpoint
        # returns newest-first, so a single per_page=limit page
        # already has everything this function needs; get_all=True
        # would walk the project's entire job history before the
        # len(results) >= limit break below ever gets a chance to
        # matter.
        jobs = proj.jobs.list(per_page=limit)
    except Exception as e:
        raise GitLabConnectionError(f"Could not list jobs: {e}")

    results = []

    for job in jobs:

        if job_name and job.name != job_name:
            continue

        results.append({
            "pipeline_id": job.pipeline["id"],
            "job_id": job.id,
            "job_name": job.name,
            "ref": job.ref,
            "status": job.status,
            "created_at": job.created_at,
            "has_artifacts": bool(getattr(job, "artifacts_file", None)),
        })

        if len(results) >= limit:
            break

    return results


def download_latest_artifact(url, project, token, ref, job_name):
    """
    Downloads the latest successful job artifact archive for the
    given ref+job name (GitLab's "download latest artifact" API).
    Returns raw bytes.
    """

    gl, gitlab_module = _connect(url, token)
    proj = _get_project(gl, gitlab_module, project)

    try:
        return proj.artifacts(ref_name=ref, job=job_name)
    except gitlab_module.exceptions.GitlabGetError as e:
        if getattr(e, "response_code", None) == 404:
            raise GitLabNotFoundError(
                f"No artifact found for ref '{ref}', job '{job_name}': {e}"
            )
        raise GitLabConnectionError(f"Download failed: {e}")
    except Exception as e:
        raise GitLabConnectionError(f"Download failed: {e}")


def download_job_artifact(url, project, token, job_id):
    """
    Downloads a specific job's artifact archive by job ID (picked
    from list_recent_jobs()). Returns raw bytes.
    """

    gl, gitlab_module = _connect(url, token)
    proj = _get_project(gl, gitlab_module, project)

    try:
        job = proj.jobs.get(job_id)
        return job.artifacts()
    except gitlab_module.exceptions.GitlabGetError as e:
        if getattr(e, "response_code", None) == 404:
            raise GitLabNotFoundError(f"Job {job_id} or its artifact not found: {e}")
        raise GitLabConnectionError(f"Download failed: {e}")
    except Exception as e:
        raise GitLabConnectionError(f"Download failed: {e}")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m unittest tests.test_gitlab_client -v`
Expected: all `TestExceptionHierarchy`, `TestConnect`, `TestListRecentJobs`, `TestListAll`, `TestDownloadArtifacts` tests PASS.

- [ ] **Step 6: Commit**

```bash
git add communication/gitlab_client.py tests/test_gitlab_client.py requirements.txt requirements_build.txt
git commit -m "Add GitLab client: exceptions + CI job artifact fetching (python-gitlab, optional dep)"
```

---

### Task 2: `communication/gitlab_client.py` — Package Registry functions

**Files:**
- Modify: `communication/gitlab_client.py`
- Modify: `tests/test_gitlab_client.py`

**Interfaces:**
- Consumes: `_connect(url, token)`, `_get_project(gl, gitlab_module, project)`, `_list_all(manager, **kwargs)`, `GitLabError`, `GitLabNotFoundError`, `GitLabConnectionError` (all from Task 1).
- Produces: `list_package_versions(url, project, token, package_name, limit=20) -> list[dict]` (each dict: `package_id`, `version`, `created_at`); `download_latest_package_file(url, project, token, package_name) -> bytes`; `download_package_version(url, project, token, package_name, version) -> bytes`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gitlab_client.py`:

```python
class TestListPackageVersions(unittest.TestCase):

    def _make_package(self, package_id, version, created_at="2026-08-27T00:00:00Z"):
        pkg = MagicMock()
        pkg.id = package_id
        pkg.version = version
        pkg.created_at = created_at
        return pkg

    def test_returns_dicts_with_expected_keys(self):
        module, gl = _fake_gitlab_module()
        proj = MagicMock()
        gl.projects.get.return_value = proj
        proj.packages.list.return_value = [
            self._make_package(1, "1.4.2", "2026-08-27T09:00:00Z"),
        ]

        with _patched_gitlab(module):
            versions = gitlab_client.list_package_versions(
                "https://gitlab.com", "group/proj", "tok",
                package_name="suzuki-slp1-radar-firmware",
            )

        self.assertEqual(versions, [{
            "package_id": 1,
            "version": "1.4.2",
            "created_at": "2026-08-27T09:00:00Z",
        }])

    def test_no_matching_package_raises_notfounderror(self):
        module, gl = _fake_gitlab_module()
        proj = MagicMock()
        gl.projects.get.return_value = proj
        proj.packages.list.return_value = []

        with _patched_gitlab(module):
            with self.assertRaises(GitLabNotFoundError):
                gitlab_client.list_package_versions(
                    "https://gitlab.com", "group/proj", "tok",
                    package_name="no-such-package",
                )

    def test_fetches_a_single_bounded_page_not_the_whole_history(self):
        # Same reasoning as TestListRecentJobs's equivalent test —
        # list_package_versions must NOT use _list_all()/get_all=True,
        # which would walk every version this package has ever had.
        module, gl = _fake_gitlab_module()
        proj = MagicMock()
        gl.projects.get.return_value = proj
        proj.packages.list.return_value = [
            self._make_package(1, "1.4.2"),
        ]

        with _patched_gitlab(module):
            gitlab_client.list_package_versions(
                "https://gitlab.com", "group/proj", "tok",
                package_name="suzuki-slp1-radar-firmware", limit=5,
            )

        proj.packages.list.assert_called_once_with(
            package_name="suzuki-slp1-radar-firmware",
            order_by="created_at", sort="desc", per_page=5,
        )


class TestDownloadPackageFile(unittest.TestCase):

    def _setup_project(self, gl, versions, files_by_package_id):
        proj = MagicMock()
        gl.projects.get.return_value = proj
        proj.id = 42

        pkg_objs = []
        for v in versions:
            pkg = MagicMock()
            pkg.id = v["package_id"]
            pkg.version = v["version"]
            pkg.created_at = v["created_at"]
            pkg_objs.append(pkg)
        proj.packages.list.return_value = pkg_objs

        def packages_get(package_id):
            pkg = MagicMock()
            file_mock = MagicMock()
            file_mock.file_name = files_by_package_id[package_id]
            pkg.package_files.list.return_value = [file_mock]
            return pkg

        proj.packages.get.side_effect = packages_get
        return proj

    def test_download_latest_package_file_returns_bytes(self):
        module, gl = _fake_gitlab_module()
        proj = self._setup_project(
            gl,
            versions=[
                {"package_id": 2, "version": "1.4.2", "created_at": "2026-08-27T09:00:00Z"},
                {"package_id": 1, "version": "1.4.1", "created_at": "2026-08-20T09:00:00Z"},
            ],
            files_by_package_id={2: "suzuki-slp1-radar-firmware-1.4.2.zip", 1: "suzuki-slp1-radar-firmware-1.4.1.zip"},
        )
        gl.http_get.return_value = MagicMock(content=b"PK\x03\x04pkgbytes")

        with _patched_gitlab(module):
            data = gitlab_client.download_latest_package_file(
                "https://gitlab.com", "group/proj", "tok",
                package_name="suzuki-slp1-radar-firmware",
            )

        self.assertEqual(data, b"PK\x03\x04pkgbytes")
        gl.http_get.assert_called_once_with(
            "/projects/42/packages/generic/suzuki-slp1-radar-firmware/1.4.2/"
            "suzuki-slp1-radar-firmware-1.4.2.zip",
            raw=True,
        )

    def test_download_package_version_picks_the_requested_one(self):
        module, gl = _fake_gitlab_module()
        proj = self._setup_project(
            gl,
            versions=[
                {"package_id": 2, "version": "1.4.2", "created_at": "2026-08-27T09:00:00Z"},
                {"package_id": 1, "version": "1.4.1", "created_at": "2026-08-20T09:00:00Z"},
            ],
            files_by_package_id={2: "suzuki-slp1-radar-firmware-1.4.2.zip", 1: "suzuki-slp1-radar-firmware-1.4.1.zip"},
        )
        gl.http_get.return_value = MagicMock(content=b"PK older bytes")

        with _patched_gitlab(module):
            data = gitlab_client.download_package_version(
                "https://gitlab.com", "group/proj", "tok",
                package_name="suzuki-slp1-radar-firmware", version="1.4.1",
            )

        self.assertEqual(data, b"PK older bytes")
        gl.http_get.assert_called_once_with(
            "/projects/42/packages/generic/suzuki-slp1-radar-firmware/1.4.1/"
            "suzuki-slp1-radar-firmware-1.4.1.zip",
            raw=True,
        )

    def test_download_package_version_missing_raises_notfounderror(self):
        module, gl = _fake_gitlab_module()
        self._setup_project(
            gl,
            versions=[{"package_id": 1, "version": "1.4.1", "created_at": "2026-08-20T09:00:00Z"}],
            files_by_package_id={1: "x.zip"},
        )

        with _patched_gitlab(module):
            with self.assertRaises(GitLabNotFoundError):
                gitlab_client.download_package_version(
                    "https://gitlab.com", "group/proj", "tok",
                    package_name="suzuki-slp1-radar-firmware", version="9.9.9",
                )

    def test_network_error_fetching_package_version_raises_connectionerror(self):
        # Same reasoning as Task 1's download-function network-error
        # tests — a non-GitlabGetError failure must still come out
        # typed, not leak through untyped.
        module, gl = _fake_gitlab_module()
        proj = self._setup_project(
            gl,
            versions=[{"package_id": 1, "version": "1.4.1", "created_at": "2026-08-20T09:00:00Z"}],
            files_by_package_id={1: "x.zip"},
        )
        proj.packages.get.side_effect = OSError("Connection reset by peer")

        with _patched_gitlab(module):
            with self.assertRaises(GitLabConnectionError):
                gitlab_client.download_package_version(
                    "https://gitlab.com", "group/proj", "tok",
                    package_name="suzuki-slp1-radar-firmware", version="1.4.1",
                )

    def test_non_404_error_fetching_package_version_is_not_misreported_as_notfound(self):
        # GitlabGetError is python-gitlab's generic "GET failed"
        # exception, not exclusively a 404 signal — a 403/500/429
        # while fetching package metadata must come out as
        # GitLabConnectionError, matching the response_code==404 gate
        # every other GitlabGetError catch in this file already uses
        # (_get_project, download_latest_artifact, download_job_artifact).
        module, gl = _fake_gitlab_module()
        proj = self._setup_project(
            gl,
            versions=[{"package_id": 1, "version": "1.4.1", "created_at": "2026-08-20T09:00:00Z"}],
            files_by_package_id={1: "x.zip"},
        )
        proj.packages.get.side_effect = module.exceptions.GitlabGetError(
            "500 Internal Server Error", response_code=500
        )

        with _patched_gitlab(module):
            with self.assertRaises(GitLabConnectionError):
                gitlab_client.download_package_version(
                    "https://gitlab.com", "group/proj", "tok",
                    package_name="suzuki-slp1-radar-firmware", version="1.4.1",
                )

    def test_version_with_no_files_raises_notfounderror(self):
        module, gl = _fake_gitlab_module()
        proj = MagicMock()
        gl.projects.get.return_value = proj
        proj.id = 42
        proj.packages.list.return_value = [
            self._make_package(1, "1.4.1", "2026-08-20T09:00:00Z"),
        ]
        pkg = MagicMock()
        pkg.package_files.list.return_value = []
        proj.packages.get.return_value = pkg

        with _patched_gitlab(module):
            with self.assertRaises(GitLabNotFoundError):
                gitlab_client.download_package_version(
                    "https://gitlab.com", "group/proj", "tok",
                    package_name="suzuki-slp1-radar-firmware", version="1.4.1",
                )

    def test_version_with_multiple_files_downloads_the_first(self):
        module, gl = _fake_gitlab_module()
        proj = MagicMock()
        gl.projects.get.return_value = proj
        proj.id = 42
        proj.packages.list.return_value = [
            self._make_package(1, "1.4.1", "2026-08-20T09:00:00Z"),
        ]
        pkg = MagicMock()
        first_file = MagicMock()
        first_file.file_name = "firmware.zip"
        second_file = MagicMock()
        second_file.file_name = "readme.txt"
        pkg.package_files.list.return_value = [first_file, second_file]
        proj.packages.get.return_value = pkg
        gl.http_get.return_value = MagicMock(content=b"PK\x03\x04firstfile")

        with _patched_gitlab(module):
            data = gitlab_client.download_package_version(
                "https://gitlab.com", "group/proj", "tok",
                package_name="suzuki-slp1-radar-firmware", version="1.4.1",
            )

        self.assertEqual(data, b"PK\x03\x04firstfile")
        gl.http_get.assert_called_once_with(
            "/projects/42/packages/generic/suzuki-slp1-radar-firmware/1.4.1/firmware.zip",
            raw=True,
        )

    def test_file_download_404_raises_notfounderror(self):
        module, gl = _fake_gitlab_module()
        proj = self._setup_project(
            gl,
            versions=[{"package_id": 1, "version": "1.4.1", "created_at": "2026-08-20T09:00:00Z"}],
            files_by_package_id={1: "x.zip"},
        )
        gl.http_get.side_effect = module.exceptions.GitlabHttpError(
            "404", response_code=404
        )

        with _patched_gitlab(module):
            with self.assertRaises(GitLabNotFoundError):
                gitlab_client.download_package_version(
                    "https://gitlab.com", "group/proj", "tok",
                    package_name="suzuki-slp1-radar-firmware", version="1.4.1",
                )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m unittest tests.test_gitlab_client -v`
Expected: `AttributeError: module 'communication.gitlab_client' has no attribute 'list_package_versions'` (and similar for the download functions).

- [ ] **Step 3: Implement the Package Registry functions**

Append to `communication/gitlab_client.py`:

```python
def list_package_versions(url, project, token, package_name, limit=20):
    """
    Returns up to `limit` most recent versions of the given Generic
    package name, newest first, as a list of dicts: package_id,
    version, created_at. Raises GitLabNotFoundError if no version of
    that package name exists in the project.
    """

    gl, gitlab_module = _connect(url, token)
    proj = _get_project(gl, gitlab_module, project)

    try:
        # Deliberately NOT _list_all() — same reasoning as
        # list_recent_jobs(): a single per_page=limit page (server-
        # sorted newest-first via order_by/sort) is exactly what
        # this function needs; get_all=True would walk every version
        # of this package the project has ever published before the
        # packages[:limit] slice below ever runs.
        packages = proj.packages.list(
            package_name=package_name,
            order_by="created_at", sort="desc", per_page=limit,
        )
    except Exception as e:
        raise GitLabConnectionError(f"Could not list packages: {e}")

    if not packages:
        raise GitLabNotFoundError(
            f"No package named '{package_name}' found in this project"
        )

    return [
        {
            "package_id": pkg.id,
            "version": pkg.version,
            "created_at": pkg.created_at,
        }
        for pkg in packages[:limit]
    ]


def _download_generic_package_file(gl, project_id, package_name, version, file_name):
    """
    Generic packages have no dedicated high-level python-gitlab
    download method as of this writing, so this hits GitLab's
    documented REST endpoint directly via the library's low-level
    (and version-stable) http_get(..., raw=True) escape hatch — the
    same primitive python-gitlab uses internally for every one of
    its own higher-level calls, so it doesn't drift across versions
    the way object-model wrappers can.
    """

    path = (
        f"/projects/{project_id}/packages/generic/"
        f"{package_name}/{version}/{file_name}"
    )
    response = gl.http_get(path, raw=True)
    return response.content


def _download_one_file_for_version(gl, gitlab_module, proj, package_name, version, package_id):
    """
    Downloads the file attached to one specific package version.
    Uses the first file if a version has more than one attached
    (not expected for this project's firmware packages, but not
    validated against — download_package_version()/
    download_latest_package_file() both go through this).
    """

    try:
        pkg = proj.packages.get(package_id)
        files = _list_all(pkg.package_files)
    except gitlab_module.exceptions.GitlabGetError as e:
        if getattr(e, "response_code", None) == 404:
            raise GitLabNotFoundError(f"Package version not found: {e}")
        raise GitLabConnectionError(f"Could not load package version: {e}")
    except Exception as e:
        raise GitLabConnectionError(f"Could not load package version: {e}")

    if not files:
        raise GitLabNotFoundError(
            f"Version '{version}' of package '{package_name}' has no files"
        )

    file_name = files[0].file_name

    try:
        return _download_generic_package_file(
            gl, proj.id, package_name, version, file_name
        )
    except gitlab_module.exceptions.GitlabHttpError as e:
        if getattr(e, "response_code", None) == 404:
            raise GitLabNotFoundError(f"File '{file_name}' not found: {e}")
        raise GitLabConnectionError(f"Download failed: {e}")
    except Exception as e:
        raise GitLabConnectionError(f"Download failed: {e}")


def download_latest_package_file(url, project, token, package_name):
    """
    Downloads the file attached to the newest version of the given
    package. Returns raw bytes.
    """

    gl, gitlab_module = _connect(url, token)
    proj = _get_project(gl, gitlab_module, project)

    latest = list_package_versions(url, project, token, package_name, limit=1)[0]

    return _download_one_file_for_version(
        gl, gitlab_module, proj, package_name, latest["version"], latest["package_id"]
    )


def download_package_version(url, project, token, package_name, version):
    """
    Downloads the file attached to a specific package version (as
    picked from list_package_versions()). Returns raw bytes. Raises
    GitLabNotFoundError if that exact version string isn't among the
    project's versions of this package.
    """

    gl, gitlab_module = _connect(url, token)
    proj = _get_project(gl, gitlab_module, project)

    versions = list_package_versions(url, project, token, package_name, limit=100)
    match = next((v for v in versions if v["version"] == version), None)

    if match is None:
        raise GitLabNotFoundError(
            f"Version '{version}' of package '{package_name}' not found"
        )

    return _download_one_file_for_version(
        gl, gitlab_module, proj, package_name, version, match["package_id"]
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m unittest tests.test_gitlab_client -v`
Expected: all tests PASS (this file now has ~31 tests total across both tasks).

- [ ] **Step 5: Commit**

```bash
git add communication/gitlab_client.py tests/test_gitlab_client.py
git commit -m "Add GitLab client: Package Registry (Generic packages) fetching"
```

---

### Task 3: `.ui` entry-point widgets (no behavior yet)

**Files:**
- Modify: `gui/main_window.ui`
- Modify: `gui/ui_main_window.py` (regenerated, not hand-edited)
- Test: `tests/test_gui_smoke.py`

**Interfaces:**
- Produces: `self.ui.actionLoadFromGitLab` (QAction), `self.ui.buttonLoadFromGitLab` (QPushButton) — both exist but are unwired until Task 7.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_gui_smoke.py` (near other menu/widget existence tests — a new small class is fine):

```python
class TestGitLabEntryPointWidgets(unittest.TestCase):
    """
    Covers the two static entry-point widgets for the "Load from
    GitLab" feature — declared in main_window.ui (unlike the
    dialog's own internal widgets, which are Python-built in
    gui/gitlab_dialog.py, same precedent as TestConnectionDialog).
    """

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()

    def test_action_load_from_gitlab_exists_in_file_menu(self):
        self.assertTrue(hasattr(self.window.ui, 'actionLoadFromGitLab'))
        self.assertIn(
            self.window.ui.actionLoadFromGitLab,
            self.window.ui.menuFile.actions(),
        )

    def test_button_load_from_gitlab_exists_on_data_page(self):
        self.assertTrue(hasattr(self.window.ui, 'buttonLoadFromGitLab'))
        self.assertTrue(self.window.ui.buttonLoadFromGitLab.isEnabled())
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen python -m unittest tests.test_gui_smoke.TestGitLabEntryPointWidgets -v`
Expected: FAIL — `AttributeError: 'Ui_MainWindow' object has no attribute 'actionLoadFromGitLab'`.

- [ ] **Step 3: Add `actionLoadFromGitLab` to `menuFile` in `main_window.ui`**

Find this exact block (the `menuFile` action list):

```xml
    <addaction name="actionLoadFirmware"/>
    <widget class="QMenu" name="menuRecentFiles">
```

Replace with:

```xml
    <addaction name="actionLoadFirmware"/>
    <addaction name="actionLoadFromGitLab"/>
    <widget class="QMenu" name="menuRecentFiles">
```

Find this exact block (the `<action>` declarations, right after `actionLoadFirmware`'s own declaration):

```xml
  <action name="actionLoadFirmware">
   <property name="text">
    <string>Load Firmware...</string>
   </property>
  </action>
```

Replace with:

```xml
  <action name="actionLoadFirmware">
   <property name="text">
    <string>Load Firmware...</string>
   </property>
  </action>
  <action name="actionLoadFromGitLab">
   <property name="text">
    <string>Load from GitLab...</string>
   </property>
  </action>
```

- [ ] **Step 4: Add `buttonLoadFromGitLab` below `tableWidgetDetails` on `pageData` in `main_window.ui`**

Find this exact block (end of the Details table, right before the page's trailing vertical spacer):

```xml
              </widget>
             </item>
             <item>
              <spacer name="verticalSpacer">
```

Replace with:

```xml
              </widget>
             </item>
             <item>
              <widget class="QPushButton" name="buttonLoadFromGitLab">
               <property name="text">
                <string>Load from GitLab...</string>
               </property>
              </widget>
             </item>
             <item>
              <spacer name="verticalSpacer">
```

(This is the `pageData` page's spacer — `tableWidgetDetails` only appears once in the file, so this match is unambiguous.)

- [ ] **Step 5: Regenerate `gui/ui_main_window.py`**

Run: `pyside6-uic gui/main_window.ui -o gui/ui_main_window.py`

- [ ] **Step 6: Run the test to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen python -m unittest tests.test_gui_smoke.TestGitLabEntryPointWidgets -v`
Expected: PASS.

- [ ] **Step 7: Run the full test suite to confirm nothing else broke**

Run: `QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -p "test_*.py"`
Expected: all tests PASS (no regressions from the `.ui`/generated-code change).

- [ ] **Step 8: Commit**

```bash
git add gui/main_window.ui gui/ui_main_window.py tests/test_gui_smoke.py
git commit -m "Add Load from GitLab entry-point widgets (File menu action + Data page button, unwired)"
```

---

### Task 4: `gui/gitlab_dialog.py` — Connection card, threading skeleton, CI Artifact tab

**Files:**
- Create: `gui/gitlab_dialog.py`
- Test: `tests/test_gui_smoke.py`
- Test: Create `tests/test_gitlab_dialog_threading.py`

**Interfaces:**
- Consumes: `communication.gitlab_client.list_recent_jobs`, `download_latest_artifact`, `download_job_artifact`, `GitLabError` (Task 1); `config.settings.APP_AUTHOR`, `APP_NAME`.
- Produces: `GitLabFetchWorker(QObject)` (signals: `progress_message(str)`, `list_ready(list)`, `download_ready(bytes, str)`, `error(str)`, `finished()`); `GitLabFetchDialog(QDialog)` — constructor `GitLabFetchDialog(parent)`; internal widgets `self.urlEdit`, `self.projectEdit`, `self.tokenEdit`, `self.tabs`, `self.ciRefEdit`, `self.ciJobEdit`, `self.ciFetchButton`, `self.ciBrowseToggle`, `self.ciBrowseTable`, `self.statusLabel`.

- [ ] **Step 1: Write the failing GUI smoke tests**

Add to `tests/test_gui_smoke.py`:

```python
class TestGitLabFetchDialogConnectionCard(unittest.TestCase):
    """
    Covers GitLabFetchDialog's Connection card (URL/project/token
    fields) and its own QSettings persistence — separate from
    gui/settings_profile.py, since this dialog only exists while
    open (see docs/superpowers/specs/2026-08-27-gitlab-firmware-
    fetch-design.md, section 4).
    """

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()

    def test_defaults_when_nothing_saved(self):
        from gui.gitlab_dialog import GitLabFetchDialog
        dialog = GitLabFetchDialog(self.window)
        self.assertEqual(dialog.urlEdit.text(), "https://gitlab.com")
        self.assertEqual(dialog.projectEdit.text(), "")
        self.assertEqual(dialog.tokenEdit.text(), "")
        self.assertEqual(
            dialog.tokenEdit.echoMode(), dialog.tokenEdit.EchoMode.Password
        )

    def test_fields_persist_across_dialog_instances(self):
        from gui.gitlab_dialog import GitLabFetchDialog

        dialog1 = GitLabFetchDialog(self.window)
        dialog1.urlEdit.setText("https://gitlab.example.com")
        dialog1.projectEdit.setText("radar-team/suzuki-slp1-firmware")
        dialog1.tokenEdit.setText("glpat-abc123")
        dialog1.urlEdit.textEdited.emit(dialog1.urlEdit.text())

        dialog2 = GitLabFetchDialog(self.window)
        self.assertEqual(dialog2.urlEdit.text(), "https://gitlab.example.com")
        self.assertEqual(dialog2.projectEdit.text(), "radar-team/suzuki-slp1-firmware")
        self.assertEqual(dialog2.tokenEdit.text(), "glpat-abc123")

    def test_ci_tab_is_selected_by_default(self):
        from gui.gitlab_dialog import GitLabFetchDialog
        dialog = GitLabFetchDialog(self.window)
        self.assertEqual(dialog.tabs.currentIndex(), 0)
        self.assertEqual(dialog.tabs.tabText(0), "CI Artifact")
        self.assertEqual(dialog.tabs.tabText(1), "Package Registry")

    def test_ci_browse_table_starts_hidden(self):
        from gui.gitlab_dialog import GitLabFetchDialog
        dialog = GitLabFetchDialog(self.window)
        self.assertFalse(dialog.ciBrowseTable.isVisible())
        # _run_action() is mocked out here — expanding Browse also
        # starts a real fetch (a real QThread), which this test has
        # no way to wait for/clean up; this test only cares about
        # the visibility toggle. The real fetch-and-populate
        # behavior (including full QThread lifecycle) is covered by
        # tests/test_gitlab_dialog_threading.py, which does wait.
        with unittest.mock.patch.object(dialog, '_run_action'):
            dialog.ciBrowseToggle.click()
        self.assertTrue(dialog.ciBrowseTable.isVisible())
```

Create `tests/test_gitlab_dialog_threading.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen python -m unittest tests.test_gui_smoke.TestGitLabFetchDialogConnectionCard tests.test_gitlab_dialog_threading -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gui.gitlab_dialog'`.

- [ ] **Step 3: Implement `gui/gitlab_dialog.py` (Connection card + threading + CI Artifact tab)**

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen python -m unittest tests.test_gui_smoke.TestGitLabFetchDialogConnectionCard tests.test_gitlab_dialog_threading -v`
Expected: all PASS.

- [ ] **Step 5: Run the full test suite**

Run: `QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -p "test_*.py"`
Expected: all PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add gui/gitlab_dialog.py tests/test_gui_smoke.py tests/test_gitlab_dialog_threading.py
git commit -m "Add GitLabFetchDialog: connection settings, threading, CI Artifact tab"
```

---

### Task 5: Package Registry tab

**Files:**
- Modify: `gui/gitlab_dialog.py`
- Modify: `tests/test_gui_smoke.py`
- Modify: `tests/test_gitlab_dialog_threading.py`

**Interfaces:**
- Consumes: `_run_action()`, `_on_download_ready()` (Task 4); `gitlab_client.list_package_versions`, `download_latest_package_file`, `download_package_version` (Task 2).
- Produces: `self.packageNameEdit`, `self.pkgFetchButton`, `self.pkgBrowseToggle`, `self.pkgBrowseTable`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_gui_smoke.py` (inside `TestGitLabFetchDialogConnectionCard` or a sibling class — add as a new class to keep each class focused):

```python
class TestGitLabFetchDialogPackageTab(unittest.TestCase):

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()

    def test_package_name_persists_across_dialog_instances(self):
        from gui.gitlab_dialog import GitLabFetchDialog

        dialog1 = GitLabFetchDialog(self.window)
        dialog1.packageNameEdit.setText("suzuki-slp1-radar-firmware")
        dialog1.packageNameEdit.textEdited.emit(dialog1.packageNameEdit.text())

        dialog2 = GitLabFetchDialog(self.window)
        self.assertEqual(dialog2.packageNameEdit.text(), "suzuki-slp1-radar-firmware")

    def test_package_browse_table_starts_hidden(self):
        from gui.gitlab_dialog import GitLabFetchDialog
        dialog = GitLabFetchDialog(self.window)
        self.assertFalse(dialog.pkgBrowseTable.isVisible())
        # Same reasoning as test_ci_browse_table_starts_hidden above
        # — _run_action() starts a real QThread this test can't wait
        # for, so it's mocked out; the real fetch path is covered by
        # tests/test_gitlab_dialog_threading.py.
        with unittest.mock.patch.object(dialog, '_run_action'):
            dialog.pkgBrowseToggle.click()
        self.assertTrue(dialog.pkgBrowseTable.isVisible())
```

Add to `tests/test_gitlab_dialog_threading.py`:

```python
class TestFetchLatestPackageRealThread(unittest.TestCase):

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()
        self.dialog = GitLabFetchDialog(self.window)
        self.dialog.urlEdit.setText("https://gitlab.com")
        self.dialog.projectEdit.setText("group/proj")
        self.dialog.tokenEdit.setText("tok")
        self.dialog.packageNameEdit.setText("suzuki-slp1-radar-firmware")

    def test_fetch_latest_package_runs_and_cleans_up_thread(self):
        with patch(
            "gui.gitlab_dialog.gitlab_client.download_latest_package_file",
            return_value=b"PK\x03\x04fakezip",
        ):
            self.dialog.pkgFetchButton.click()
            self.assertIsNotNone(self.dialog._thread)
            _run_until(self.app, lambda: self.dialog._thread is None)

        self.assertIsNone(self.dialog._thread)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen python -m unittest tests.test_gui_smoke.TestGitLabFetchDialogPackageTab tests.test_gitlab_dialog_threading.TestFetchLatestPackageRealThread -v`
Expected: FAIL — `AttributeError: 'GitLabFetchDialog' object has no attribute 'packageNameEdit'`.

- [ ] **Step 3: Implement the Package Registry tab**

In `gui/gitlab_dialog.py`, replace the placeholder `_build_package_tab()`:

```python
    def _build_package_tab(self):

        page = QWidget(self)
        layout = QVBoxLayout(page)

        grid = QGridLayout()
        grid.addWidget(QLabel("Package name"), 0, 0)
        self.packageNameEdit = QLineEdit(page)
        self.packageNameEdit.textEdited.connect(self._save_settings)
        grid.addWidget(self.packageNameEdit, 0, 1)
        layout.addLayout(grid)

        fetch_row = QHBoxLayout()
        self.pkgFetchButton = QPushButton("Fetch Latest Version", page)
        self.pkgFetchButton.clicked.connect(self._on_fetch_latest_package)
        fetch_row.addWidget(self.pkgFetchButton)

        self.pkgBrowseToggle = QPushButton("Browse versions...", page)
        self.pkgBrowseToggle.clicked.connect(self._toggle_pkg_browse)
        fetch_row.addWidget(self.pkgBrowseToggle)
        layout.addLayout(fetch_row)

        self.pkgBrowseTable = QTableWidget(0, 2, page)
        self.pkgBrowseTable.setHorizontalHeaderLabels(["Version", "Uploaded"])
        self.pkgBrowseTable.horizontalHeader().setStretchLastSection(True)
        self.pkgBrowseTable.setVisible(False)
        self.pkgBrowseTable.cellDoubleClicked.connect(self._on_pkg_row_activated)
        layout.addWidget(self.pkgBrowseTable)

        layout.addStretch(1)
        return page
```

Add the corresponding handler methods (next to the CI Artifact tab's equivalents):

```python
    def _toggle_pkg_browse(self):

        opening = not self.pkgBrowseTable.isVisible()
        self.pkgBrowseTable.setVisible(opening)

        if opening:
            self._run_action(
                "list_packages", {"package_name": self.packageNameEdit.text()},
                on_list=self._populate_pkg_browse_table,
            )

    def _populate_pkg_browse_table(self, versions):

        self.pkgBrowseTable.setRowCount(len(versions))

        for row, version in enumerate(versions):
            self.pkgBrowseTable.setItem(row, 0, QTableWidgetItem(version["version"]))
            self.pkgBrowseTable.setItem(row, 1, QTableWidgetItem(version["created_at"]))
            self.pkgBrowseTable.item(row, 0).setData(Qt.UserRole, version)

    def _on_pkg_row_activated(self, row, _col):

        version = self.pkgBrowseTable.item(row, 0).data(Qt.UserRole)

        self._run_action(
            "download_package_version",
            {"package_name": self.packageNameEdit.text(), "version": version["version"]},
            on_download=self._on_download_ready,
        )

    def _on_fetch_latest_package(self):

        self._run_action(
            "fetch_latest_package", {"package_name": self.packageNameEdit.text()},
            on_download=self._on_download_ready,
        )
```

Update `_save_settings()` to also persist the package name:

```python
    def _save_settings(self, _text=None):

        s = self._settings
        s.setValue("gitlab/instanceUrl", self.urlEdit.text())
        s.setValue("gitlab/project", self.projectEdit.text())
        s.setValue("gitlab/token", self.tokenEdit.text())
        s.setValue("gitlab/ciRef", self.ciRefEdit.text())
        s.setValue("gitlab/ciJobName", self.ciJobEdit.text())
        s.setValue("gitlab/packageName", self.packageNameEdit.text())
        s.sync()
```

Update `_load_settings()` to also restore it:

```python
    def _load_settings(self):

        s = self._settings
        self.urlEdit.setText(s.value("gitlab/instanceUrl", "https://gitlab.com", type=str))
        self.projectEdit.setText(s.value("gitlab/project", "", type=str))
        self.tokenEdit.setText(s.value("gitlab/token", "", type=str))
        self.ciRefEdit.setText(s.value("gitlab/ciRef", "main", type=str))
        self.ciJobEdit.setText(s.value("gitlab/ciJobName", "", type=str))
        self.packageNameEdit.setText(s.value("gitlab/packageName", "", type=str))
```

Update `_run_action()`'s button-disable/re-enable to cover the package button too (it currently only disables `ciFetchButton`):

```python
        self.statusLabel.setText("")
        self.ciFetchButton.setEnabled(False)
        self.pkgFetchButton.setEnabled(False)
```

and in `_cleanup_thread()`:

```python
        self._thread = None
        self._worker = None
        self.ciFetchButton.setEnabled(True)
        self.pkgFetchButton.setEnabled(True)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen python -m unittest tests.test_gui_smoke.TestGitLabFetchDialogPackageTab tests.test_gitlab_dialog_threading -v`
Expected: all PASS.

- [ ] **Step 5: Run the full test suite**

Run: `QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -p "test_*.py"`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add gui/gitlab_dialog.py tests/test_gui_smoke.py tests/test_gitlab_dialog_threading.py
git commit -m "Add GitLabFetchDialog: Package Registry tab"
```

---

### Task 6: Zip extraction, file picker, hand-off to the existing firmware loader

**Files:**
- Modify: `gui/gitlab_dialog.py`
- Modify: `tests/test_gui_smoke.py`

**Interfaces:**
- Consumes: `self._on_download_ready(data, suggested_filename)` (replaced); `MainWindow._load_firmware_file(path)`, `MainWindow._update_details_table(datablock)`, `MainWindow._loaded_datablocks`, `MainWindow.log_information(msg)` (all pre-existing, `gui/configure_tab.py` / `gui/main_window.py`).
- Produces: a picker view (`self.pickerList`, `self.pickerLoadButton`, `self.pickerBackButton`) shown after any successful download; `RECOGNIZED_FIRMWARE_EXTENSIONS` (module-level, already declared in Task 4).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_gui_smoke.py`:

```python
class TestGitLabFetchDialogZipPicker(unittest.TestCase):

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()
        from gui.gitlab_dialog import GitLabFetchDialog
        self.dialog = GitLabFetchDialog(self.window)

    def _make_zip_bytes(self, names_and_contents):
        import io
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name, content in names_and_contents:
                zf.writestr(name, content)
        return buf.getvalue()

    def test_download_ready_switches_to_picker_and_lists_files(self):
        data = self._make_zip_bytes([
            ("firmware/RAD_SUZ05_FFI_ForCanFlashing.s3", "S1..."),
            ("firmware/checksum.sha256", "abc123"),
        ])
        self.dialog._on_download_ready(data, "build_firmware-4821.zip")

        self.assertEqual(self.dialog.tabs.isVisible(), False)
        self.assertEqual(self.dialog.pickerList.count(), 2)

    def test_recognized_firmware_file_is_preselected(self):
        data = self._make_zip_bytes([
            ("build/manifest.json", "{}"),
            ("firmware/RAD_SUZ05_FFI_ForCanFlashing.s3", "S1..."),
        ])
        self.dialog._on_download_ready(data, "build_firmware-4821.zip")

        selected = self.dialog.pickerList.selectedItems()
        self.assertEqual(len(selected), 1)
        self.assertTrue(
            selected[0].text().endswith("RAD_SUZ05_FFI_ForCanFlashing.s3")
        )

    def test_load_selected_file_calls_existing_load_pipeline(self):
        data = self._make_zip_bytes([
            ("firmware/RAD_SUZ05_FFI_ForCanFlashing.s3", "S1130000100055555555555555555555555\n"),
        ])
        self.dialog._on_download_ready(data, "build_firmware-4821.zip")

        with unittest.mock.patch.object(
            self.window, '_load_firmware_file', return_value=True
        ) as mock_load:
            self.dialog.pickerLoadButton.click()

        mock_load.assert_called_once()
        loaded_path = mock_load.call_args[0][0]
        self.assertTrue(loaded_path.endswith("RAD_SUZ05_FFI_ForCanFlashing.s3"))

    def test_load_selected_file_closes_dialog(self):
        data = self._make_zip_bytes([
            ("firmware/RAD_SUZ05_FFI_ForCanFlashing.s3", "S1..."),
        ])
        self.dialog._on_download_ready(data, "build_firmware-4821.zip")

        with unittest.mock.patch.object(
            self.window, '_load_firmware_file', return_value=True
        ):
            self.dialog.pickerLoadButton.click()

        self.assertFalse(self.dialog.isVisible())

    def test_non_zip_download_loads_directly_without_picker(self):
        with unittest.mock.patch.object(
            self.window, '_load_firmware_file', return_value=True
        ) as mock_load:
            self.dialog._on_download_ready(b"not a zip file at all", "firmware.hex")

        mock_load.assert_called_once()
        self.assertTrue(mock_load.call_args[0][0].endswith("firmware.hex"))

    def test_back_button_returns_to_tabs(self):
        data = self._make_zip_bytes([
            ("firmware/RAD_SUZ05_FFI_ForCanFlashing.s3", "S1..."),
        ])
        self.dialog._on_download_ready(data, "build_firmware-4821.zip")
        self.dialog.pickerBackButton.click()

        self.assertTrue(self.dialog.tabs.isVisible())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen python -m unittest tests.test_gui_smoke.TestGitLabFetchDialogZipPicker -v`
Expected: FAIL — `AttributeError: 'GitLabFetchDialog' object has no attribute 'pickerList'`.

- [ ] **Step 3: Implement the picker panel**

In `gui/gitlab_dialog.py`, add one new stdlib import — `zipfile` is already imported (from Task 4); only `tempfile` is new — right after it:

```python
import tempfile
```

And add `QListWidget`/`QListWidgetItem` to the existing `PySide6.QtWidgets` import block:

```python
from PySide6.QtWidgets import (
    ...
    QListWidget,
    QListWidgetItem,
    ...
)
```

In `_build_ui()`, add the picker panel after the tabs (it starts hidden):

```python
        self.tabs = QTabWidget(self)
        self.tabs.addTab(self._build_ci_tab(), "CI Artifact")
        self.tabs.addTab(self._build_package_tab(), "Package Registry")
        layout.addWidget(self.tabs)

        self.pickerPanel = self._build_picker_panel()
        self.pickerPanel.setVisible(False)
        layout.addWidget(self.pickerPanel)
```

Add `_build_picker_panel()`:

```python
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
```

Replace `_on_download_ready()` with the real implementation:

```python
    def _on_download_ready(self, data, suggested_filename):

        self._download_dir = tempfile.mkdtemp(prefix="sflash_gitlab_")
        archive_path = os.path.join(self._download_dir, suggested_filename)

        with open(archive_path, "wb") as f:
            f.write(data)

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

        preselect_row = 0
        for i, name in enumerate(names):
            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, os.path.join(extract_dir, name))
            self.pickerList.addItem(item)
            if any(name.lower().endswith(ext) for ext in RECOGNIZED_FIRMWARE_EXTENSIONS):
                preselect_row = i

        if self.pickerList.count() > 0:
            self.pickerList.setCurrentRow(preselect_row)

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
            self._main_window.log_information(
                f"Loaded firmware from GitLab: {os.path.basename(path)}"
            )

        self.close()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen python -m unittest tests.test_gui_smoke.TestGitLabFetchDialogZipPicker -v`
Expected: all PASS.

- [ ] **Step 5: Run the full test suite**

Run: `QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -p "test_*.py"`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add gui/gitlab_dialog.py tests/test_gui_smoke.py
git commit -m "Add GitLabFetchDialog: zip extraction, firmware-file picker, hand-off to _load_firmware_file()"
```

---

### Task 7: Wire both entry points

**Files:**
- Modify: `gui/menu_bar.py`
- Modify: `gui/configure_tab.py`
- Modify: `tests/test_gui_smoke.py`

**Interfaces:**
- Consumes: `GitLabFetchDialog(parent)` (Task 4).
- Produces: `MainWindow.open_gitlab_fetch_dialog()`; `MainWindow.action_load_from_gitlab()`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_gui_smoke.py`'s `TestMenuBar` class (same class covering `actionAbout`/`actionOpenGuideline`/etc.):

```python
    def test_load_from_gitlab_menu_action_opens_dialog(self):
        with unittest.mock.patch(
            "gui.menu_bar.GitLabFetchDialog"
        ) as mock_dialog_cls:
            mock_dialog_cls.return_value.exec.return_value = None
            self.window.ui.actionLoadFromGitLab.trigger()
        mock_dialog_cls.assert_called_once_with(self.window)
        mock_dialog_cls.return_value.exec.assert_called_once()
```

Add a new small class for the Data-page button (mirroring `TestHardwareComboDetectionError`'s style of a focused class):

```python
class TestGitLabButtonOnDataPage(unittest.TestCase):

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()

    def test_button_opens_same_dialog_as_menu_action(self):
        with unittest.mock.patch(
            "gui.menu_bar.GitLabFetchDialog"
        ) as mock_dialog_cls:
            mock_dialog_cls.return_value.exec.return_value = None
            self.window.ui.buttonLoadFromGitLab.click()
        mock_dialog_cls.assert_called_once_with(self.window)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen python -m unittest tests.test_gui_smoke.TestMenuBar.test_load_from_gitlab_menu_action_opens_dialog tests.test_gui_smoke.TestGitLabButtonOnDataPage -v`
Expected: FAIL — clicking/triggering does nothing yet (no assertion match, `mock_dialog_cls` never called).

- [ ] **Step 3: Wire the menu action in `gui/menu_bar.py`**

Add the import at the top of `gui/menu_bar.py` (next to the existing `TestConnectionDialog` import):

```python
from gui.gitlab_dialog import GitLabFetchDialog
```

In `setup_menu_bar()`, add wiring next to `actionOpenGuideline`'s:

```python
        if hasattr(self.ui, 'actionLoadFromGitLab'):
            self.ui.actionLoadFromGitLab.triggered.connect(
                self.open_gitlab_fetch_dialog
            )
```

Add `open_gitlab_fetch_dialog()` next to `open_test_connection_dialog()`:

```python
    def open_gitlab_fetch_dialog(self):
        """
        Opens the "Load from GitLab" dialog — shared by the File >
        Load from GitLab... menu action above and the Configure >
        Data page's own button (gui/configure_tab.py's
        load_from_gitlab_button_clicked()), same "one handler, two
        entry points" pattern as open_test_connection_dialog().
        """

        dialog = GitLabFetchDialog(self)
        dialog.exec()
        return dialog
```

- [ ] **Step 4: Wire the Data-page button in `gui/configure_tab.py`**

In `setup_datablocks_table()`, add (right after the `self._setup_data_format_inputs()` call, inside the `if hasattr(self.ui, 'tableWidgetDetails'):` block or immediately after it — either is fine since `buttonLoadFromGitLab` doesn't depend on the Details table structure):

```python
        if hasattr(self.ui, 'buttonLoadFromGitLab'):
            self.ui.buttonLoadFromGitLab.clicked.connect(
                self.open_gitlab_fetch_dialog
            )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen python -m unittest tests.test_gui_smoke.TestMenuBar.test_load_from_gitlab_menu_action_opens_dialog tests.test_gui_smoke.TestGitLabButtonOnDataPage -v`
Expected: PASS.

- [ ] **Step 6: Run the full test suite plus threading tests**

Run: `QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -p "test_*.py"`
Run: `QT_QPA_PLATFORM=offscreen python -m unittest tests.test_flash_threading -v`
Expected: all PASS (this task doesn't touch flash threading, but it's a standing CLAUDE.md rule to re-run it whenever `gui/flash_tab.py` or QThread-related code changed anywhere in the session).

- [ ] **Step 7: Commit**

```bash
git add gui/menu_bar.py gui/configure_tab.py tests/test_gui_smoke.py
git commit -m "Wire both Load from GitLab entry points (File menu + Configure -> Data button)"
```

---

### Task 8: Docs

**Files:**
- Modify: `docs/walkthrough.md`
- Modify: `README.md`

**Interfaces:** None (docs only).

- [ ] **Step 1: Find the current last phase number**

Run: `grep -n "^## Phase" docs/walkthrough.md | tail -3`

- [ ] **Step 2: Add a new Phase entry to `docs/walkthrough.md`**

Following the file's existing format exactly (intro paragraph, `### Thay đổi` with bolded file names, `### Đã kiểm tra` with what was verified) — write the entry in Vietnamese, matching every other phase in this file, covering: the brainstorming process (spec + mockup approved before implementation, per `docs/superpowers/specs/2026-08-27-gitlab-firmware-fetch-design.md`), all new/modified files from Tasks 1–7, the `python-gitlab` optional-dependency decision, the two entry points, and the full test count added.

- [ ] **Step 3: Update `README.md`**

Add a short section (near the existing "D. Sử dụng trong app" numbered list, or as its own subsection) describing: how to enable the feature (`pip install python-gitlab`, uncomment in `requirements.txt`), where to find it (File → Load from GitLab..., or the button on Configure → Data), and that it's read-only (fetch only, never publishes/uploads).

- [ ] **Step 4: Commit**

```bash
git add docs/walkthrough.md README.md
git commit -m "Document Load from GitLab feature in walkthrough and README"
```

---

## Final verification (after all tasks)

Before considering this feature done, per CLAUDE.md:

1. Run: `QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -p "test_*.py"` — all PASS.
2. Run: `QT_QPA_PLATFORM=offscreen python -m unittest tests.test_flash_threading -v` — all PASS.
3. A real headless end-to-end pass (per the stronger "before pushing all of today's session" protocol, if/when the user asks to push): construct `MainWindow`, open the GitLab dialog from both entry points, fetch (mocked) a CI artifact, pick a file from the zip picker, confirm it lands in the Datablocks table, close the dialog, close the window — watching for any exception and a clean exit code.

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
        # subclass, not leak through untyped.
        module, gl = _fake_gitlab_module()
        gl.projects.get.side_effect = OSError("Connection reset by peer")
        with _patched_gitlab(module):
            with self.assertRaises(GitLabConnectionError):
                gitlab_client.list_recent_jobs(
                    "https://gitlab.com", "group/proj", "tok"
                )


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
        manager = MagicMock()

        def fake_list(*args, **kwargs):
            if "get_all" in kwargs:
                raise TypeError("list() got an unexpected keyword argument 'get_all'")
            return ["a"]

        manager.list.side_effect = fake_list

        result = gitlab_client._list_all(manager, per_page=20)

        self.assertEqual(result, ["a"])


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

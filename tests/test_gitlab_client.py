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

    def test_ssl_verify_defaults_to_true(self):
        module, gl = _fake_gitlab_module()
        gl.projects.get.return_value = MagicMock(jobs=MagicMock(list=MagicMock(return_value=[])))
        with _patched_gitlab(module):
            gitlab_client.list_recent_jobs("https://gitlab.com", "group/proj", "tok")

        module.Gitlab.assert_called_once_with(
            "https://gitlab.com", private_token="tok", ssl_verify=True, timeout=15,
        )

    def test_ssl_verify_false_is_passed_through_to_gitlab_client(self):
        # Needed for a self-hosted instance with a self-signed cert
        # — the GUI surfaces this as an opt-out checkbox, defaulting
        # to verifying (see test_ssl_verify_defaults_to_true).
        module, gl = _fake_gitlab_module()
        gl.projects.get.return_value = MagicMock(jobs=MagicMock(list=MagicMock(return_value=[])))
        with _patched_gitlab(module):
            gitlab_client.list_recent_jobs(
                "https://gitlab.internal", "group/proj", "tok", ssl_verify=False,
            )

        module.Gitlab.assert_called_once_with(
            "https://gitlab.internal", private_token="tok", ssl_verify=False, timeout=15,
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


class TestListBranchesAndTags(unittest.TestCase):

    def _make_ref(self, name):
        ref = MagicMock()
        ref.name = name
        return ref

    def test_returns_branches_then_tags(self):
        module, gl = _fake_gitlab_module()
        proj = MagicMock()
        gl.projects.get.return_value = proj
        proj.branches.list.return_value = [self._make_ref("main"), self._make_ref("dev")]
        proj.tags.list.return_value = [self._make_ref("v1.0.0")]

        with _patched_gitlab(module):
            refs = gitlab_client.list_branches_and_tags(
                "https://gitlab.com", "group/proj", "tok"
            )

        self.assertEqual(refs, [
            {"name": "main", "ref_type": "branch"},
            {"name": "dev", "ref_type": "branch"},
            {"name": "v1.0.0", "ref_type": "tag"},
        ])

    def test_fetches_a_single_bounded_page_not_the_whole_history(self):
        # Same reasoning as list_recent_jobs's equivalent test — must
        # NOT use _list_all()/get_all=True for either branches or tags.
        module, gl = _fake_gitlab_module()
        proj = MagicMock()
        gl.projects.get.return_value = proj
        proj.branches.list.return_value = []
        proj.tags.list.return_value = []

        with _patched_gitlab(module):
            gitlab_client.list_branches_and_tags(
                "https://gitlab.com", "group/proj", "tok", limit=10
            )

        proj.branches.list.assert_called_once_with(per_page=10)
        proj.tags.list.assert_called_once_with(per_page=10)

    def test_branches_network_error_raises_connectionerror(self):
        module, gl = _fake_gitlab_module()
        proj = MagicMock()
        gl.projects.get.return_value = proj
        proj.branches.list.side_effect = OSError("Connection reset by peer")

        with _patched_gitlab(module):
            with self.assertRaises(GitLabConnectionError):
                gitlab_client.list_branches_and_tags(
                    "https://gitlab.com", "group/proj", "tok"
                )

    def test_tags_network_error_raises_connectionerror(self):
        module, gl = _fake_gitlab_module()
        proj = MagicMock()
        gl.projects.get.return_value = proj
        proj.branches.list.return_value = []
        proj.tags.list.side_effect = OSError("Connection reset by peer")

        with _patched_gitlab(module):
            with self.assertRaises(GitLabConnectionError):
                gitlab_client.list_branches_and_tags(
                    "https://gitlab.com", "group/proj", "tok"
                )


class TestListJobsForRef(unittest.TestCase):

    def _make_job(self, job_id, name, status, artifacts_file=None):
        job = MagicMock()
        job.id = job_id
        job.name = name
        job.status = status
        job.created_at = "2026-08-27T09:14:00Z"
        job.artifacts_file = artifacts_file
        return job

    def _make_pipeline(self, pipeline_id, jobs):
        pipeline = MagicMock()
        pipeline.id = pipeline_id
        pipeline.jobs.list.return_value = jobs
        return pipeline

    def test_returns_dicts_with_expected_keys(self):
        module, gl = _fake_gitlab_module()
        proj = MagicMock()
        gl.projects.get.return_value = proj
        pipeline = self._make_pipeline(500, [
            self._make_job(1, "build_firmware", "success", {"filename": "a.zip"}),
        ])
        proj.pipelines.list.return_value = [pipeline]

        with _patched_gitlab(module):
            jobs = gitlab_client.list_jobs_for_ref(
                "https://gitlab.com", "group/proj", "tok", ref="Release_R_04_01_01",
            )

        self.assertEqual(jobs, [{
            "pipeline_id": 500,
            "job_id": 1,
            "job_name": "build_firmware",
            "ref": "Release_R_04_01_01",
            "status": "success",
            "created_at": "2026-08-27T09:14:00Z",
            "has_artifacts": True,
        }])

    def test_no_pipelines_for_ref_raises_notfounderror(self):
        module, gl = _fake_gitlab_module()
        proj = MagicMock()
        gl.projects.get.return_value = proj
        proj.pipelines.list.return_value = []

        with _patched_gitlab(module):
            with self.assertRaises(GitLabNotFoundError):
                gitlab_client.list_jobs_for_ref(
                    "https://gitlab.com", "group/proj", "tok", ref="no-such-ref",
                )

    def test_filters_by_job_name(self):
        module, gl = _fake_gitlab_module()
        proj = MagicMock()
        gl.projects.get.return_value = proj
        pipeline = self._make_pipeline(500, [
            self._make_job(1, "build_firmware", "success"),
            self._make_job(2, "lint", "success"),
        ])
        proj.pipelines.list.return_value = [pipeline]

        with _patched_gitlab(module):
            jobs = gitlab_client.list_jobs_for_ref(
                "https://gitlab.com", "group/proj", "tok", ref="main",
                job_name="lint",
            )

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["job_name"], "lint")

    def test_pipelines_fetched_as_a_single_bounded_page(self):
        module, gl = _fake_gitlab_module()
        proj = MagicMock()
        gl.projects.get.return_value = proj
        proj.pipelines.list.return_value = [self._make_pipeline(500, [])]

        with _patched_gitlab(module):
            gitlab_client.list_jobs_for_ref(
                "https://gitlab.com", "group/proj", "tok", ref="main",
            )

        proj.pipelines.list.assert_called_once_with(
            ref="main", order_by="id", sort="desc", per_page=5,
        )

    def test_pipeline_list_network_error_raises_connectionerror(self):
        module, gl = _fake_gitlab_module()
        proj = MagicMock()
        gl.projects.get.return_value = proj
        proj.pipelines.list.side_effect = OSError("Connection reset by peer")

        with _patched_gitlab(module):
            with self.assertRaises(GitLabConnectionError):
                gitlab_client.list_jobs_for_ref(
                    "https://gitlab.com", "group/proj", "tok", ref="main",
                )


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
        pkg_version = MagicMock()
        pkg_version.id = 1
        pkg_version.version = "1.4.1"
        pkg_version.created_at = "2026-08-20T09:00:00Z"
        proj.packages.list.return_value = [pkg_version]
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
        pkg_version = MagicMock()
        pkg_version.id = 1
        pkg_version.version = "1.4.1"
        pkg_version.created_at = "2026-08-20T09:00:00Z"
        proj.packages.list.return_value = [pkg_version]
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

    def test_download_latest_package_file_authenticates_only_once(self):
        # Regression test for the final-review "double auth" finding:
        # download_latest_package_file() used to call the PUBLIC
        # list_package_versions(), which internally did its own
        # _connect()+_get_project() on top of the caller's own — two
        # gl.auth()/gl.projects.get() round-trips for one download.
        # It must now reuse the gl/proj it already has via the
        # private _list_package_versions() helper instead.
        module, gl = _fake_gitlab_module()
        proj = self._setup_project(
            gl,
            versions=[
                {"package_id": 2, "version": "1.4.2", "created_at": "2026-08-27T09:00:00Z"},
            ],
            files_by_package_id={2: "suzuki-slp1-radar-firmware-1.4.2.zip"},
        )
        gl.http_get.return_value = MagicMock(content=b"PK\x03\x04pkgbytes")

        with _patched_gitlab(module):
            gitlab_client.download_latest_package_file(
                "https://gitlab.com", "group/proj", "tok",
                package_name="suzuki-slp1-radar-firmware",
            )

        self.assertEqual(gl.auth.call_count, 1)
        self.assertEqual(gl.projects.get.call_count, 1)
        self.assertEqual(proj.packages.list.call_count, 1)

    def test_download_package_version_authenticates_only_once(self):
        # Same regression coverage as the "latest" variant above, for
        # download_package_version().
        module, gl = _fake_gitlab_module()
        proj = self._setup_project(
            gl,
            versions=[
                {"package_id": 1, "version": "1.4.1", "created_at": "2026-08-20T09:00:00Z"},
            ],
            files_by_package_id={1: "suzuki-slp1-radar-firmware-1.4.1.zip"},
        )
        gl.http_get.return_value = MagicMock(content=b"PK older bytes")

        with _patched_gitlab(module):
            gitlab_client.download_package_version(
                "https://gitlab.com", "group/proj", "tok",
                package_name="suzuki-slp1-radar-firmware", version="1.4.1",
            )

        self.assertEqual(gl.auth.call_count, 1)
        self.assertEqual(gl.projects.get.call_count, 1)
        self.assertEqual(proj.packages.list.call_count, 1)


if __name__ == "__main__":
    unittest.main()

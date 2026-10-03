# ==================================================
# CLI `gitlab` Command Tests
# ==================================================
#
# cli_gitlab.py is the headless side of the GUI's Load from
# GitLab dialog. communication/gitlab_client.py (already covered
# by tests/test_gitlab_client.py) is mocked here — these tests
# are about the command layer: argument validation, the token
# resolution order, the success-only filter, where downloads
# land, and the zip-extraction/--print-firmware plumbing that
# lets a shell pipe an artifact straight into `cli.py flash`.
# ==================================================

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

import cli
import cli_gitlab
from communication.gitlab_client import (
    GitLabError,
    GitLabNotFoundError,
)

TOKEN_ARGS = ["--token", "glpat-test"]


def _run_cli(argv):

    out_buf = io.StringIO()
    err_buf = io.StringIO()
    with contextlib.redirect_stdout(out_buf), \
            contextlib.redirect_stderr(err_buf):
        code = cli.main(argv)
    return code, out_buf.getvalue() + err_buf.getvalue()


def _job(job_id, name="build", status="success", artifacts=True,
         ref="main"):
    return {
        "pipeline_id": 1000 + job_id,
        "job_id": job_id,
        "job_name": name,
        "ref": ref,
        "status": status,
        "created_at": "2026-10-01T09:00:00.000Z",
        "has_artifacts": artifacts,
    }


def _zip_bytes(names):

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name in names:
            zf.writestr(name, b"\x00" * 8)
    return buf.getvalue()


class TestTokenResolution(unittest.TestCase):

    def test_no_token_anywhere_exits_two_without_calling_gitlab(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch(
                "communication.gitlab_client.list_branches_and_tags"
            ) as listing:
                code, out = _run_cli(["gitlab", "refs"])

        self.assertEqual(code, 2)
        self.assertIn("No GitLab token", out)
        listing.assert_not_called()

    def test_environment_variable_is_used_when_no_flag_given(self):
        # Preferred over --token, which would land in the shell
        # history and in `ps`.
        with mock.patch.dict(
            os.environ,
            {cli_gitlab.TOKEN_ENV_VAR: "glpat-from-env"},
            clear=True,
        ):
            with mock.patch(
                "communication.gitlab_client.list_branches_and_tags",
                return_value=["main"],
            ) as listing:
                code, _ = _run_cli(["gitlab", "refs"])

        self.assertEqual(code, 0)
        self.assertEqual(listing.call_args[0][2], "glpat-from-env")

    def test_flag_beats_the_environment_variable(self):
        with mock.patch.dict(
            os.environ,
            {cli_gitlab.TOKEN_ENV_VAR: "glpat-from-env"},
            clear=True,
        ):
            with mock.patch(
                "communication.gitlab_client.list_branches_and_tags",
                return_value=["main"],
            ) as listing:
                code, _ = _run_cli(
                    ["gitlab", "refs"] + TOKEN_ARGS
                )

        self.assertEqual(code, 0)
        self.assertEqual(listing.call_args[0][2], "glpat-test")


class TestRefs(unittest.TestCase):

    def test_prints_every_ref_with_a_count(self):
        with mock.patch(
            "communication.gitlab_client.list_branches_and_tags",
            return_value=["main", "develop", "v1.0"],
        ):
            code, out = _run_cli(["gitlab", "refs"] + TOKEN_ARGS)

        self.assertEqual(code, 0)
        self.assertIn("3 ref(s)", out)
        self.assertIn("develop", out)

    def test_defaults_to_the_teams_ci_project(self):
        from config.settings import DEFAULT_GITLAB_CI_PROJECT

        with mock.patch(
            "communication.gitlab_client.list_branches_and_tags",
            return_value=[],
        ) as listing:
            _run_cli(["gitlab", "refs"] + TOKEN_ARGS)

        self.assertEqual(
            listing.call_args[0][1], DEFAULT_GITLAB_CI_PROJECT
        )

    def test_a_gitlab_error_exits_one(self):
        with mock.patch(
            "communication.gitlab_client.list_branches_and_tags",
            side_effect=GitLabError("python-gitlab not installed"),
        ):
            code, out = _run_cli(["gitlab", "refs"] + TOKEN_ARGS)

        self.assertEqual(code, 1)
        self.assertIn("python-gitlab not installed", out)


class TestJobs(unittest.TestCase):

    def test_lists_recent_jobs_across_refs_without_ref(self):
        with mock.patch(
            "communication.gitlab_client.list_recent_jobs",
            return_value=[_job(1), _job(2)],
        ) as listing:
            code, out = _run_cli(["gitlab", "jobs"] + TOKEN_ARGS)

        self.assertEqual(code, 0)
        self.assertIn("JOB NAME", out)
        listing.assert_called_once()

    def test_with_ref_uses_the_per_ref_listing(self):
        with mock.patch(
            "communication.gitlab_client.list_jobs_for_ref",
            return_value=[_job(3, ref="release")],
        ) as listing:
            code, out = _run_cli(
                ["gitlab", "jobs", "--ref", "release"] + TOKEN_ARGS
            )

        self.assertEqual(code, 0)
        self.assertEqual(listing.call_args[0][3], "release")

    def test_success_only_hides_the_rest_and_says_how_many(self):
        jobs = [
            _job(1, status="success"),
            _job(2, status="failed"),
            _job(3, status="running"),
        ]
        with mock.patch(
            "communication.gitlab_client.list_recent_jobs",
            return_value=jobs,
        ):
            code, out = _run_cli(
                ["gitlab", "jobs", "--success-only"] + TOKEN_ARGS
            )

        self.assertEqual(code, 0)
        self.assertIn("2 non-successful job(s) hidden", out)
        self.assertNotIn("failed", out)

    def test_empty_result_says_so(self):
        with mock.patch(
            "communication.gitlab_client.list_recent_jobs",
            return_value=[],
        ):
            code, out = _run_cli(["gitlab", "jobs"] + TOKEN_ARGS)

        self.assertEqual(code, 0)
        self.assertIn("No matching jobs", out)

    def test_limit_is_passed_through(self):
        with mock.patch(
            "communication.gitlab_client.list_recent_jobs",
            return_value=[],
        ) as listing:
            _run_cli(
                ["gitlab", "jobs", "--limit", "5"] + TOKEN_ARGS
            )

        self.assertEqual(listing.call_args[1]["limit"], 5)


class TestArtifactDownload(unittest.TestCase):

    def test_neither_job_id_nor_ref_and_job_exits_two(self):
        code, out = _run_cli(["gitlab", "artifact"] + TOKEN_ARGS)
        self.assertEqual(code, 2)
        self.assertIn("--job-id", out)

    def test_ref_and_job_download_lands_in_the_output_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch(
                "communication.gitlab_client"
                ".download_latest_artifact",
                return_value=_zip_bytes(["app.s19"]),
            ) as download:
                code, out = _run_cli([
                    "gitlab", "artifact", "--ref", "main",
                    "--job", "build", "-o", tmp,
                ] + TOKEN_ARGS)

            self.assertEqual(code, 0)
            self.assertTrue(
                os.path.isfile(
                    os.path.join(tmp, "artifact_build.zip")
                )
            )

        self.assertEqual(download.call_args[0][3], "main")
        self.assertEqual(download.call_args[0][4], "build")

    def test_job_id_uses_the_by_id_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch(
                "communication.gitlab_client.download_job_artifact",
                return_value=_zip_bytes(["app.s19"]),
            ) as download:
                code, _ = _run_cli([
                    "gitlab", "artifact", "--job-id", "4321",
                    "-o", tmp,
                ] + TOKEN_ARGS)

            self.assertEqual(code, 0)
            self.assertTrue(os.path.isfile(
                os.path.join(tmp, "artifact_job_4321.zip")
            ))

        self.assertEqual(download.call_args[0][3], 4321)

    def test_extract_lists_the_firmware_inside(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch(
                "communication.gitlab_client"
                ".download_latest_artifact",
                return_value=_zip_bytes([
                    "build/app.s19", "build/log.txt",
                ]),
            ):
                code, out = _run_cli([
                    "gitlab", "artifact", "--ref", "main",
                    "--job", "build", "-o", tmp, "--extract",
                ] + TOKEN_ARGS)

            self.assertEqual(code, 0)
            self.assertIn("Firmware file(s) found", out)
            self.assertIn("app.s19", out)
            # Everything is extracted, but only recognized
            # firmware extensions are offered as a flash target —
            # the dialog's picker highlights the same set.
            self.assertNotIn("log.txt", out)
            self.assertTrue(os.path.isfile(
                os.path.join(tmp, "extracted", "build", "log.txt")
            ))
            self.assertTrue(os.path.isfile(
                os.path.join(tmp, "extracted", "build", "app.s19")
            ))

    def test_print_firmware_prints_only_the_paths(self):
        # So a shell can do:
        #   FW=$(cli.py gitlab artifact ... --print-firmware)
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch(
                "communication.gitlab_client"
                ".download_latest_artifact",
                return_value=_zip_bytes([
                    "out/app.s19", "out/notes.md",
                ]),
            ):
                code, out = _run_cli([
                    "gitlab", "artifact", "--ref", "main",
                    "--job", "build", "-o", tmp,
                    "--print-firmware",
                ] + TOKEN_ARGS)

        self.assertEqual(code, 0)
        lines = [l for l in out.splitlines() if l.strip()]
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].endswith("app.s19"))

    def test_print_firmware_with_no_firmware_exits_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch(
                "communication.gitlab_client"
                ".download_latest_artifact",
                return_value=_zip_bytes(["out/notes.md"]),
            ):
                code, out = _run_cli([
                    "gitlab", "artifact", "--ref", "main",
                    "--job", "build", "-o", tmp,
                    "--print-firmware",
                ] + TOKEN_ARGS)

        self.assertEqual(code, 1)
        self.assertIn("No firmware file found", out)

    def test_a_non_zip_download_is_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch(
                "communication.gitlab_client"
                ".download_latest_artifact",
                return_value=b"not a zip at all",
            ):
                code, out = _run_cli([
                    "gitlab", "artifact", "--ref", "main",
                    "--job", "build", "-o", tmp, "--extract",
                ] + TOKEN_ARGS)

        self.assertEqual(code, 0)
        self.assertIn("not a zip archive", out)

    def test_a_not_found_error_exits_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch(
                "communication.gitlab_client"
                ".download_latest_artifact",
                side_effect=GitLabNotFoundError("no such job"),
            ):
                code, out = _run_cli([
                    "gitlab", "artifact", "--ref", "main",
                    "--job", "build", "-o", tmp,
                ] + TOKEN_ARGS)

        self.assertEqual(code, 1)
        self.assertIn("no such job", out)


class TestPackages(unittest.TestCase):

    def test_lists_versions(self):
        with mock.patch(
            "communication.gitlab_client.list_package_versions",
            return_value=[
                {"package_id": 7, "version": "1.2.3",
                 "created_at": "2026-09-30T10:00:00Z"},
            ],
        ):
            code, out = _run_cli([
                "gitlab", "packages", "--package-name", "fw",
            ] + TOKEN_ARGS)

        self.assertEqual(code, 0)
        self.assertIn("1.2.3", out)

    def test_defaults_to_the_teams_package_project(self):
        from config.settings import DEFAULT_GITLAB_PACKAGE_PROJECT

        with mock.patch(
            "communication.gitlab_client.list_package_versions",
            return_value=[],
        ) as listing:
            _run_cli([
                "gitlab", "packages", "--package-name", "fw",
            ] + TOKEN_ARGS)

        self.assertEqual(
            listing.call_args[0][1], DEFAULT_GITLAB_PACKAGE_PROJECT
        )

    def test_package_download_without_version_takes_the_latest(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch(
                "communication.gitlab_client"
                ".download_latest_package_file",
                return_value=_zip_bytes(["app.s19"]),
            ) as download:
                code, _ = _run_cli([
                    "gitlab", "package", "--package-name", "fw",
                    "-o", tmp,
                ] + TOKEN_ARGS)

            self.assertEqual(code, 0)
            self.assertTrue(os.path.isfile(
                os.path.join(tmp, "fw_latest.zip")
            ))

        download.assert_called_once()

    def test_package_download_with_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch(
                "communication.gitlab_client"
                ".download_package_version",
                return_value=_zip_bytes(["app.s19"]),
            ) as download:
                code, _ = _run_cli([
                    "gitlab", "package", "--package-name", "fw",
                    "--version", "2.0.0", "-o", tmp,
                ] + TOKEN_ARGS)

            self.assertEqual(code, 0)
            self.assertTrue(os.path.isfile(
                os.path.join(tmp, "fw_2.0.0.zip")
            ))

        self.assertEqual(download.call_args[0][4], "2.0.0")


class TestSslVerify(unittest.TestCase):

    def test_verification_is_on_by_default(self):
        with mock.patch(
            "communication.gitlab_client.list_branches_and_tags",
            return_value=[],
        ) as listing:
            _run_cli(["gitlab", "refs"] + TOKEN_ARGS)

        self.assertIs(listing.call_args[1]["ssl_verify"], True)

    def test_no_ssl_verify_turns_it_off(self):
        with mock.patch(
            "communication.gitlab_client.list_branches_and_tags",
            return_value=[],
        ) as listing:
            _run_cli(
                ["gitlab", "refs", "--no-ssl-verify"] + TOKEN_ARGS
            )

        self.assertIs(listing.call_args[1]["ssl_verify"], False)


if __name__ == "__main__":
    unittest.main()

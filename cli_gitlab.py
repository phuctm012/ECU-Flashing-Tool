#!/usr/bin/env python3
# ==================================================
# SFlash CLI — `gitlab` command group
# ==================================================
#
# The headless equivalent of the GUI's "Load from GitLab"
# dialog (gui/gitlab_dialog.py): list a project's branches/tags,
# list its CI jobs, download a job artifact or a Generic
# Package file, and (for an artifact zip) extract it and point
# at the firmware file inside — which is then just a path to
# hand to `cli.py flash`.
#
# Lives in its own module rather than in cli.py because it
# shares nothing with the flashing commands: no CAN options, no
# FlashWorker, no report. It drives communication/gitlab_client.py
# directly, which is already GUI-free (python-gitlab is an
# optional, lazily-imported dependency) — so unlike Batch and
# Parallel Flash there was no GUI logic to re-express here, only
# argument parsing and printing.
#
# The GUI dialog asks for the token in a password field and
# remembers it in QSettings. A CLI must not want an interactive
# prompt, so the token comes from --token or, preferably, the
# SFLASH_GITLAB_TOKEN environment variable — never a positional
# argument, which would land in the shell history and in `ps`.
# ==================================================

import os
import sys
import zipfile

from config.settings import (
    DEFAULT_GITLAB_CI_PROJECT,
    DEFAULT_GITLAB_PACKAGE_PROJECT,
    DEFAULT_GITLAB_URL,
)
from parsers.auto_parser import FIRMWARE_EXTENSIONS

TOKEN_ENV_VAR = "SFLASH_GITLAB_TOKEN"


# ==================================================
# Helpers
# ==================================================

def _resolve_token(args):

    token = args.token or os.environ.get(TOKEN_ENV_VAR, "")

    if not token:
        print(
            f"No GitLab token given. Pass --token, or set "
            f"{TOKEN_ENV_VAR} (preferred — keeps the token out "
            f"of your shell history).",
            file=sys.stderr,
        )
        return None

    return token


def _resolve_project(args, fallback):
    return args.gitlab_project or fallback


def _handle_error(e):
    print(f"GitLab error: {e}", file=sys.stderr)
    return 1


def _is_firmware(name):
    return any(
        name.lower().endswith(ext) for ext in FIRMWARE_EXTENSIONS
    )


def _write_bytes(output_dir, file_name, data):

    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, file_name)

    with open(path, "wb") as f:
        f.write(data)

    return path


def _extract_and_report(archive_path, output_dir, print_firmware):
    """
    Extracts a downloaded artifact zip next to itself and prints
    the firmware files found inside — the headless counterpart of
    the dialog's file picker, which highlights exactly these
    extensions. With print_firmware, prints nothing but the
    firmware paths, one per line, so a shell can capture them:

        FW=$(python cli.py gitlab artifact ... --print-firmware)
        python cli.py flash "$FW" ...
    """

    if not zipfile.is_zipfile(archive_path):
        if not print_firmware:
            print(
                "  Downloaded file is not a zip archive — "
                "leaving it as-is."
            )
        return [archive_path] if _is_firmware(archive_path) else []

    extract_dir = os.path.join(output_dir, "extracted")

    with zipfile.ZipFile(archive_path) as zf:
        zf.extractall(extract_dir)
        names = zf.namelist()

    firmware = [
        os.path.join(extract_dir, name)
        for name in names
        if not name.endswith("/") and _is_firmware(name)
    ]

    if not print_firmware:
        print(f"  Extracted {len(names)} entry(ies) to {extract_dir}")
        if firmware:
            print("  Firmware file(s) found:")
            for path in firmware:
                print(f"    {path}")
        else:
            print(
                "  No recognized firmware file "
                f"({', '.join(FIRMWARE_EXTENSIONS)}) in the archive."
            )

    return firmware


# ==================================================
# Commands
# ==================================================

def cmd_gitlab_refs(args):

    from communication import gitlab_client

    token = _resolve_token(args)
    if token is None:
        return 2

    try:
        refs = gitlab_client.list_branches_and_tags(
            args.gitlab_url,
            _resolve_project(args, DEFAULT_GITLAB_CI_PROJECT),
            token,
            ssl_verify=not args.no_ssl_verify,
        )
    except Exception as e:
        return _handle_error(e)

    print(f"{len(refs)} ref(s):")
    for ref in refs:
        print(f"  {ref}")

    return 0


def cmd_gitlab_jobs(args):

    from communication import gitlab_client

    token = _resolve_token(args)
    if token is None:
        return 2

    project = _resolve_project(args, DEFAULT_GITLAB_CI_PROJECT)

    try:
        if args.ref:
            jobs = gitlab_client.list_jobs_for_ref(
                args.gitlab_url, project, token, args.ref,
                job_name=args.job or None, limit=args.limit,
                ssl_verify=not args.no_ssl_verify,
            )
        else:
            jobs = gitlab_client.list_recent_jobs(
                args.gitlab_url, project, token,
                job_name=args.job or None, limit=args.limit,
                ssl_verify=not args.no_ssl_verify,
            )
    except Exception as e:
        return _handle_error(e)

    if args.success_only:
        # Same filter the dialog's Browse table applies — a job
        # that failed or is still running has no artifact worth
        # flashing (Phase 4.123).
        before = len(jobs)
        jobs = [j for j in jobs if j.get("status") == "success"]
        hidden = before - len(jobs)
        if hidden:
            print(f"({hidden} non-successful job(s) hidden)")

    if not jobs:
        print("No matching jobs.")
        return 0

    print(
        f"{'JOB ID':<10}{'STATUS':<10}{'ARTIFACT':<10}"
        f"{'REF':<34}{'CREATED':<22}JOB NAME"
    )
    for job in jobs:
        print(
            f"{job['job_id']:<10}{job['status']:<10}"
            f"{('yes' if job['has_artifacts'] else 'no'):<10}"
            f"{str(job['ref'])[:33]:<34}"
            f"{str(job['created_at'])[:21]:<22}{job['job_name']}"
        )

    return 0


def cmd_gitlab_artifact(args):

    from communication import gitlab_client

    token = _resolve_token(args)
    if token is None:
        return 2

    if not args.job_id and not (args.ref and args.job):
        print(
            "Give either --job-id, or both --ref and --job.",
            file=sys.stderr,
        )
        return 2

    project = _resolve_project(args, DEFAULT_GITLAB_CI_PROJECT)
    quiet = args.print_firmware

    try:
        if args.job_id:
            if not quiet:
                print(f"Downloading artifact of job {args.job_id}...")
            data = gitlab_client.download_job_artifact(
                args.gitlab_url, project, token, args.job_id,
                ssl_verify=not args.no_ssl_verify,
            )
            file_name = f"artifact_job_{args.job_id}.zip"
        else:
            if not quiet:
                print(
                    f"Downloading latest successful '{args.job}' "
                    f"artifact on {args.ref}..."
                )
            data = gitlab_client.download_latest_artifact(
                args.gitlab_url, project, token, args.ref, args.job,
                ssl_verify=not args.no_ssl_verify,
            )
            file_name = f"artifact_{args.job}.zip"
    except Exception as e:
        return _handle_error(e)

    try:
        path = _write_bytes(args.output_dir, file_name, data)
    except OSError as e:
        print(f"Could not write artifact: {e}", file=sys.stderr)
        return 1

    if not quiet:
        print(f"  Saved {len(data)} bytes to {path}")

    firmware = []
    if args.extract or args.print_firmware:
        firmware = _extract_and_report(
            path, args.output_dir, args.print_firmware
        )

    if args.print_firmware:
        if not firmware:
            print(
                "No firmware file found in the artifact.",
                file=sys.stderr,
            )
            return 1
        for fw_path in firmware:
            print(fw_path)

    return 0


def cmd_gitlab_packages(args):

    from communication import gitlab_client

    token = _resolve_token(args)
    if token is None:
        return 2

    try:
        versions = gitlab_client.list_package_versions(
            args.gitlab_url,
            _resolve_project(args, DEFAULT_GITLAB_PACKAGE_PROJECT),
            token,
            args.package_name,
            limit=args.limit,
            ssl_verify=not args.no_ssl_verify,
        )
    except Exception as e:
        return _handle_error(e)

    if not versions:
        print("No versions found.")
        return 0

    print(f"{'VERSION':<40}{'PACKAGE ID':<14}CREATED")
    for version in versions:
        print(
            f"{str(version['version'])[:39]:<40}"
            f"{version['package_id']:<14}{version['created_at']}"
        )

    return 0


def cmd_gitlab_package(args):

    from communication import gitlab_client

    token = _resolve_token(args)
    if token is None:
        return 2

    project = _resolve_project(args, DEFAULT_GITLAB_PACKAGE_PROJECT)
    quiet = args.print_firmware

    try:
        if args.version:
            if not quiet:
                print(
                    f"Downloading {args.package_name} "
                    f"{args.version}..."
                )
            data = gitlab_client.download_package_version(
                args.gitlab_url, project, token,
                args.package_name, args.version,
                ssl_verify=not args.no_ssl_verify,
            )
            file_name = f"{args.package_name}_{args.version}.zip"
        else:
            if not quiet:
                print(
                    f"Downloading latest {args.package_name}..."
                )
            data = gitlab_client.download_latest_package_file(
                args.gitlab_url, project, token, args.package_name,
                ssl_verify=not args.no_ssl_verify,
            )
            file_name = f"{args.package_name}_latest.zip"
    except Exception as e:
        return _handle_error(e)

    try:
        path = _write_bytes(args.output_dir, file_name, data)
    except OSError as e:
        print(f"Could not write package file: {e}", file=sys.stderr)
        return 1

    if not quiet:
        print(f"  Saved {len(data)} bytes to {path}")

    firmware = []
    if args.extract or args.print_firmware:
        firmware = _extract_and_report(
            path, args.output_dir, args.print_firmware
        )

    if args.print_firmware:
        if not firmware:
            print(
                "No firmware file found in the package.",
                file=sys.stderr,
            )
            return 1
        for fw_path in firmware:
            print(fw_path)

    return 0


# ==================================================
# Argument parser
# ==================================================

def _add_common_gitlab_args(parser):

    parser.add_argument(
        "--gitlab-url", default=DEFAULT_GITLAB_URL,
        help=f"GitLab instance URL (default {DEFAULT_GITLAB_URL})",
    )
    parser.add_argument(
        "--gitlab-project", default=None,
        help="Project path or numeric ID. Defaults to the team's "
             "CI project for artifact commands and package "
             "project for package commands (config/settings.py)",
    )
    parser.add_argument(
        "--token", default=None,
        help=f"Personal/project access token with api or "
             f"read_api scope. Prefer the {TOKEN_ENV_VAR} "
             f"environment variable instead",
    )
    parser.add_argument(
        "--no-ssl-verify", action="store_true",
        help="Skip TLS certificate verification (self-signed "
             "certificate on a self-hosted instance)",
    )


def _add_download_args(parser):

    parser.add_argument(
        "-o", "--output-dir", default=".",
        help="Directory to save the download into (default: "
             "current directory)",
    )
    parser.add_argument(
        "--extract", action="store_true",
        help="Extract the downloaded zip and list the firmware "
             "files found inside",
    )
    parser.add_argument(
        "--print-firmware", action="store_true",
        help="Extract, then print ONLY the firmware file paths "
             "(one per line) so a shell can capture them and "
             "feed them straight to `cli.py flash`",
    )


def add_gitlab_subparser(subparsers):
    """
    Registers the `gitlab` command group on cli.py's top-level
    subparsers. Returns the group's parser (tests use it).
    """

    p_gitlab = subparsers.add_parser(
        "gitlab",
        help="Fetch firmware from GitLab (CI job artifacts or "
             "the Package Registry) — the CLI side of the GUI's "
             "Load from GitLab dialog",
    )
    gitlab_subs = p_gitlab.add_subparsers(
        dest="gitlab_command", required=True
    )

    # --- refs ---
    p_refs = gitlab_subs.add_parser(
        "refs", help="List the project's branches and tags"
    )
    _add_common_gitlab_args(p_refs)
    p_refs.set_defaults(func=cmd_gitlab_refs)

    # --- jobs ---
    p_jobs = gitlab_subs.add_parser(
        "jobs", help="List CI jobs (newest first)"
    )
    _add_common_gitlab_args(p_jobs)
    p_jobs.add_argument(
        "--ref", default=None,
        help="Limit to one branch/tag. Without it, the project's "
             "most recent jobs across all refs are listed",
    )
    p_jobs.add_argument(
        "--job", default=None,
        help="Limit to jobs with this exact name",
    )
    p_jobs.add_argument(
        "--limit", type=int, default=20,
        help="Maximum number of jobs to list (default 20)",
    )
    p_jobs.add_argument(
        "--success-only", action="store_true",
        help="Only show successful jobs (what the GUI's Browse "
             "table shows)",
    )
    p_jobs.set_defaults(func=cmd_gitlab_jobs)

    # --- artifact ---
    p_artifact = gitlab_subs.add_parser(
        "artifact", help="Download a CI job's artifact archive"
    )
    _add_common_gitlab_args(p_artifact)
    _add_download_args(p_artifact)
    p_artifact.add_argument(
        "--ref", default=None,
        help="Branch/tag whose latest successful job to take "
             "(use with --job)",
    )
    p_artifact.add_argument(
        "--job", default=None,
        help="CI job name (use with --ref)",
    )
    p_artifact.add_argument(
        "--job-id", type=int, default=None,
        help="Exact job ID to download from (from `gitlab jobs`) "
             "— skips the ref/name lookup entirely",
    )
    p_artifact.set_defaults(func=cmd_gitlab_artifact)

    # --- packages ---
    p_packages = gitlab_subs.add_parser(
        "packages",
        help="List versions of a Generic package (newest first)",
    )
    _add_common_gitlab_args(p_packages)
    p_packages.add_argument(
        "--package-name", required=True,
        help="Generic package name",
    )
    p_packages.add_argument(
        "--limit", type=int, default=20,
        help="Maximum number of versions to list (default 20)",
    )
    p_packages.set_defaults(func=cmd_gitlab_packages)

    # --- package ---
    p_package = gitlab_subs.add_parser(
        "package",
        help="Download a Generic package file (latest version "
             "unless --version is given)",
    )
    _add_common_gitlab_args(p_package)
    _add_download_args(p_package)
    p_package.add_argument(
        "--package-name", required=True,
        help="Generic package name",
    )
    p_package.add_argument(
        "--version", default=None,
        help="Exact version to download (default: the newest)",
    )
    p_package.set_defaults(func=cmd_gitlab_package)

    return p_gitlab

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


def _connect(url, token, ssl_verify=True):
    """
    Returns (gl, gitlab_module) — an authenticated gitlab.Gitlab
    client plus the gitlab module itself (callers need it for
    gitlab_module.exceptions.* type checks without importing gitlab
    at module load time). Raises GitLabError/a subclass on any
    failure — never returns a client that hasn't been verified to
    actually authenticate.

    ssl_verify=False skips TLS certificate verification — needed for
    a self-hosted instance with a self-signed certificate. Defaults
    to True (verify) since that's the safe default; the GUI surfaces
    this as an opt-out checkbox, not a hidden setting.
    """

    try:
        import gitlab
    except ImportError as e:
        raise GitLabError(
            f"python-gitlab not installed. Run: pip install python-gitlab ({e})"
        )

    try:
        gl = gitlab.Gitlab(
            url, private_token=token, ssl_verify=ssl_verify, timeout=15,
        )
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


def list_recent_jobs(url, project, token, job_name=None, limit=20, ssl_verify=True):
    """
    Returns up to `limit` most recent CI jobs for the project,
    newest first, as a list of dicts: pipeline_id, job_id, job_name,
    ref, status, created_at, has_artifacts. If job_name is given,
    only jobs with that exact name are returned.
    """

    gl, gitlab_module = _connect(url, token, ssl_verify=ssl_verify)
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


def download_latest_artifact(url, project, token, ref, job_name, ssl_verify=True):
    """
    Downloads the latest successful job artifact archive for the
    given ref+job name (GitLab's "download latest artifact" API).
    Returns raw bytes.
    """

    gl, gitlab_module = _connect(url, token, ssl_verify=ssl_verify)
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


def download_job_artifact(url, project, token, job_id, ssl_verify=True):
    """
    Downloads a specific job's artifact archive by job ID (picked
    from list_recent_jobs()). Returns raw bytes.
    """

    gl, gitlab_module = _connect(url, token, ssl_verify=ssl_verify)
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


def list_package_versions(url, project, token, package_name, limit=20, ssl_verify=True):
    """
    Returns up to `limit` most recent versions of the given Generic
    package name, newest first, as a list of dicts: package_id,
    version, created_at. Raises GitLabNotFoundError if no version of
    that package name exists in the project.
    """

    gl, gitlab_module = _connect(url, token, ssl_verify=ssl_verify)
    proj = _get_project(gl, gitlab_module, project)

    return _list_package_versions(gl, gitlab_module, proj, package_name, limit)


def _list_package_versions(gl, gitlab_module, proj, package_name, limit=20):
    """
    Body of list_package_versions(), factored out to take an
    already-connected gl/proj so download_latest_package_file() and
    download_package_version() (which both already have their own
    gl/proj from a local _connect()+_get_project() call) can reuse it
    without a second, redundant gl.auth()+gl.projects.get() round-trip.
    The public list_package_versions() above is now a thin wrapper
    around this.
    """

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


def download_latest_package_file(url, project, token, package_name, ssl_verify=True):
    """
    Downloads the file attached to the newest version of the given
    package. Returns raw bytes.
    """

    gl, gitlab_module = _connect(url, token, ssl_verify=ssl_verify)
    proj = _get_project(gl, gitlab_module, project)

    latest = _list_package_versions(gl, gitlab_module, proj, package_name, limit=1)[0]

    return _download_one_file_for_version(
        gl, gitlab_module, proj, package_name, latest["version"], latest["package_id"]
    )


def download_package_version(url, project, token, package_name, version, ssl_verify=True):
    """
    Downloads the file attached to a specific package version (as
    picked from list_package_versions()). Returns raw bytes. Raises
    GitLabNotFoundError if that exact version string isn't among the
    project's versions of this package.
    """

    gl, gitlab_module = _connect(url, token, ssl_verify=ssl_verify)
    proj = _get_project(gl, gitlab_module, project)

    versions = _list_package_versions(gl, gitlab_module, proj, package_name, limit=100)
    match = next((v for v in versions if v["version"] == version), None)

    if match is None:
        raise GitLabNotFoundError(
            f"Version '{version}' of package '{package_name}' not found"
        )

    return _download_one_file_for_version(
        gl, gitlab_module, proj, package_name, version, match["package_id"]
    )

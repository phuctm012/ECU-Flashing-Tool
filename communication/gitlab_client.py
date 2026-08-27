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


def _list_all(manager, **kwargs):
    """
    python-gitlab renamed its "fetch every page" kwarg across major
    versions (all=True in <3.0, get_all=True in >=3.0). Try the
    current name first, fall back to the old one on TypeError — same
    defensive-retry pattern VectorCanInterface.connect() already
    uses for python-can's serial= kwarg (communication/vector_can.py).
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
        jobs = _list_all(proj.jobs, per_page=limit)
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

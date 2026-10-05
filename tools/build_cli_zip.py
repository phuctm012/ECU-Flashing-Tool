"""Package the CLI-only source tree into cli_package/SFlash_CLI_source.zip.

The zip holds what it takes to run ``cli.py`` and ``build_cli.bat`` and
nothing of the GUI: no ``gui/``, no ``main.py``, no ``resources/``. Apart
from code (and the two requirements files build_cli.bat installs from) the
only document in it is ``README_CLI_EN.md``.

Run by hand, or automatically by ``.githooks/post-merge`` whenever ``main``
moves (``git merge`` / ``git pull`` while on main); the hook then commits and
pushes the zip if it changed.

    python tools/build_cli_zip.py              # package the tip of main
    python tools/build_cli_zip.py --ref HEAD   # package the current commit
    python tools/build_cli_zip.py --no-smoke   # skip the run-from-the-zip check

The files come from the *commit* (``git archive``), never from the working
tree, so uncommitted edits and other branches' code can't leak in.

Before the zip is written, every CLI command is run from the unpacked
folder - where ``gui/`` does not exist - and the run must also prove that
neither ``gui`` nor ``PySide6.QtWidgets`` got imported. A CLI that started
depending on a new top-level package (or on ``gui/``) fails here with a
``ModuleNotFoundError``, and the previous zip is left untouched rather than
replaced by one that cannot run.

The zip file is only rewritten when its *contents* differ from the one
already at ``--out`` (compared entry by entry, name + CRC), so an unchanged
CLI never produces a new commit just because zip timestamps moved.
"""

import argparse
import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

ZIP_NAME = "SFlash_CLI_source.zip"
DEFAULT_OUT = REPO_ROOT / "cli_package" / ZIP_NAME
TOP_DIR = "SFlash_CLI"

# Files at the repo root that cli.py / build_cli.bat need.
ROOT_FILES = [
    "cli.py",
    "cli_gitlab.py",
    "build_cli.bat",
    "requirements.txt",
    "requirements_build.txt",
    "README_CLI_EN.md",
]

# Packages cli.py imports - every tracked .py in them is included, so a new
# module added to one of these is picked up without editing this list.
PACKAGES = ["core", "communication", "parsers", "config"]

# Smoke-test input. Read from the commit into a scratch folder *beside* the
# package - it is not shipped (the zip carries code + README_CLI_EN.md only).
SAMPLE_HEX = "tests/sample.hex"

# Must never appear in the zip. Checked explicitly, not just by omission.
FORBIDDEN_PREFIXES = ("gui/", "resources/", "main.py", "build.bat")

# Non-code files allowed in the zip; anything else that isn't .py is refused.
ALLOWED_NON_CODE = {"README_CLI_EN.md", "build_cli.bat",
                    "requirements.txt", "requirements_build.txt"}


def _git(*args):
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, check=True,
        capture_output=True,
    ).stdout


def list_package_files(ref):
    """Repo-relative paths that go into the zip, as they exist at ``ref``."""
    tracked = _git("ls-tree", "-r", "--name-only", ref).decode().splitlines()
    tracked_set = set(tracked)
    files = [f for f in ROOT_FILES if f in tracked_set]
    missing = [f for f in ROOT_FILES if f not in tracked_set]
    for pkg in PACKAGES:
        files += [f for f in tracked
                  if f.startswith(pkg + "/") and f.endswith(".py")]
    bad = [f for f in files if f.startswith(FORBIDDEN_PREFIXES)]
    if bad:
        raise RuntimeError(f"GUI files would be packaged: {bad}")
    stray = [f for f in files
             if not f.endswith(".py") and f not in ALLOWED_NON_CODE]
    if stray:
        raise RuntimeError(f"Non-code files would be packaged: {stray}")
    return sorted(files), missing


def extract(ref, files, dest):
    tar_bytes = _git("archive", "--format=tar", ref, "--", *files)
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
        if hasattr(tarfile, "data_filter"):
            tar.extractall(dest, filter="data")
        else:
            tar.extractall(dest)


GUI_PROBE = (
    "import sys, runpy\n"
    "sys.argv = ['cli.py', 'flash', sys.argv[1], '--dry-run']\n"
    "try:\n"
    "    runpy.run_path('cli.py', run_name='__main__')\n"
    "except SystemExit as e:\n"
    "    if e.code: raise\n"
    "leaked = [m for m in sys.modules\n"
    "          if m == 'gui' or m.startswith('gui.') or m in "
    "('PySide6.QtWidgets', 'PySide6.QtGui')]\n"
    "if leaked:\n"
    "    print('GUI modules imported:', leaked); sys.exit(1)\n"
)


def smoke_commands(hex_path):
    hex_path = str(hex_path)
    return [
        ["cli.py", "--version"],
        ["cli.py", "info", hex_path],
        ["cli.py", "flash", hex_path],
        ["cli.py", "test-connection", "--sequence", "suzuki"],
        ["cli.py", "parallel", hex_path, "--count", "2"],
        ["-c", GUI_PROBE, hex_path],
    ]


def smoke_test(folder, python, hex_path):
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen",
               PYTHONDONTWRITEBYTECODE="1")
    for cmd in smoke_commands(hex_path):
        label = ("gui-import probe" if cmd[0] == "-c"
                 else " ".join(cmd).replace(str(hex_path), "sample.hex"))
        proc = subprocess.run([python, *cmd], cwd=folder, env=env,
                              capture_output=True, text=True, timeout=180)
        if proc.returncode != 0:
            tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-15:])
            print(f"  FAIL  {label} (exit {proc.returncode})\n{tail}")
            return False
        print(f"  ok    {label}")
    return True


def _zip_signature(zip_path):
    """(name, CRC) of every entry - equal means same contents."""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            return sorted((i.filename, i.CRC) for i in zf.infolist())
    except (OSError, zipfile.BadZipFile):
        return None


def write_zip(folder, out_path):
    """Write the zip; return False (file untouched) if contents are equal."""
    tmp_out = out_path.with_suffix(".zip.tmp")
    with zipfile.ZipFile(tmp_out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(folder.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                # Fixed timestamp + mode: the zip depends only on content.
                info = zipfile.ZipInfo(
                    (Path(TOP_DIR) / path.relative_to(folder)).as_posix(),
                    date_time=(1980, 1, 1, 0, 0, 0))
                info.external_attr = 0o644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(info, path.read_bytes())
    if (out_path.exists()
            and _zip_signature(tmp_out) == _zip_signature(out_path)):
        tmp_out.unlink()
        return False
    os.replace(tmp_out, out_path)
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ref", default="main",
                        help="git ref to package (default: main)")
    parser.add_argument("--out", default=str(DEFAULT_OUT),
                        help="output zip path (default: cli_package/%s)"
                             % ZIP_NAME)
    parser.add_argument("--python", default=sys.executable,
                        help="interpreter used for the smoke test "
                             "(needs PySide6; default: this one)")
    parser.add_argument("--no-smoke", action="store_true",
                        help="skip running the CLI from the unpacked folder")
    args = parser.parse_args(argv)

    try:
        files, missing = list_package_files(args.ref)
    except subprocess.CalledProcessError as exc:
        print(f"[ERROR] git: {exc.stderr.decode().strip()}")
        return 2
    except RuntimeError as exc:
        print(f"[ERROR] {exc}")
        return 2
    for f in missing:
        print(f"[WARN] {f} is not in {args.ref} - left out of the zip")

    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="sflash_cli_zip_"))
    try:
        folder = work / TOP_DIR
        folder.mkdir()
        extract(args.ref, files, folder)
        sha = _git("rev-parse", args.ref).decode().strip()
        print(f"Packaging {len(files)} files from {args.ref} ({sha[:8]})")

        if not args.no_smoke:
            hex_path = work / "sample.hex"
            hex_path.write_bytes(_git("show", f"{args.ref}:{SAMPLE_HEX}"))
            print("Smoke test (run from the unpacked folder, no gui/):")
            if not smoke_test(folder, args.python, hex_path):
                print(f"[ERROR] Smoke test failed - {out_path.name} "
                      "was NOT updated.")
                return 1
        changed = write_zip(folder, out_path)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    if changed:
        print(f"Wrote {out_path}")
    else:
        print(f"Unchanged: {out_path} already has this content")
    return 0


if __name__ == "__main__":
    sys.exit(main())

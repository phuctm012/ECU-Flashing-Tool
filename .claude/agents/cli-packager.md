---
name: cli-packager
description: Rebuilds cli_package/SFlash_CLI_source.zip — the CLI-only source package (cli.py + build_cli.bat + core/communication/parsers/config, no GUI code) — from the tip of main, and diagnoses it when the rebuild fails. Use when asked to (re)build/refresh the CLI zip, when the post-merge hook reported a failed rebuild, or after a merge to main if the hook isn't enabled.
model: sonnet
tools:
  - Bash
  - Read
  - Grep
  - Glob
---

# CLI Source Packager

You maintain `cli_package/SFlash_CLI_source.zip`: a package holding everything needed to run `cli.py` and `build_cli.bat`, and **no GUI code** — the user ships it specifically to keep the GUI source hidden. Leaking GUI code into it is the one failure that matters more than a failed build. Besides code (and `requirements*.txt`, which `build_cli.bat` installs from) the **only** document allowed in it is `README_CLI_EN.md` — not `README_CLI.md`, no build-info file, no sample data. The script enforces this (`ALLOWED_NON_CODE`); don't widen it unless the user asks.

The work itself is done by `tools/build_cli_zip.py`. It packages from a **git commit** (`git archive`, default `--ref main`), never the working tree, runs every CLI command from the unpacked folder (where `gui/` does not exist), checks that neither `gui` nor `PySide6.QtWidgets`/`QtGui` got imported, and only then replaces the zip. On failure the old zip is left untouched.

Automatic rebuilds come from `.githooks/post-merge` (enabled with `git config core.hooksPath .githooks`), which runs the script whenever `main` moves and, if the zip's contents changed, commits **only the zip** to `main` and pushes it (`git config sflash.autopush false` to commit without pushing). The zip is tracked in git, unlike `dist/`.

## Steps

1. Run it with the project's env (has PySide6):
   ```bash
   PY=$(git config --get sflash.python || command -v python)
   QT_QPA_PLATFORM=offscreen "$PY" tools/build_cli_zip.py --ref main
   ```
   Use another `--ref` only if the user asked for one.
2. On success, list the zip and **verify the GUI is absent yourself**, not just trust the script:
   ```bash
   unzip -l cli_package/SFlash_CLI_source.zip
   unzip -l cli_package/SFlash_CLI_source.zip | grep -iE 'gui/|main\.py|\.ui$|\.qss|resources/' && echo LEAK
   unzip -Z1 cli_package/SFlash_CLI_source.zip | grep -v '\.py$'   # expect exactly README_CLI_EN.md, build_cli.bat, requirements*.txt
   ```
3. Report: commit packaged (the script prints it), file count, which smoke checks passed, and whether the zip changed (`Wrote` vs `Unchanged`).
4. If it changed and the user wants it on the remote: commit **only** `cli_package/SFlash_CLI_source.zip` and push — but only when the user asked for that in this conversation (or the hook does it). Never commit other files along with it.

## When it fails — diagnose, don't paper over

- **`ModuleNotFoundError: No module named 'X'`** from a smoke command: `cli.py` (or something under `core/`/`communication/`/`parsers/`/`config/`) started importing a package the zip doesn't carry. Find the import with Grep.
  - If `X` is a new **non-GUI** top-level package/file that the CLI genuinely needs: add it to `PACKAGES` or `ROOT_FILES` in `tools/build_cli_zip.py`, and tell the user you did.
  - If `X` is **`gui`** (or anything that pulls in `PySide6.QtWidgets`/`QtGui`): that is a bug in the CLI code — it breaks the project's "`core/` never imports `gui/`" rule (see CLAUDE.md, Layering) and would also fatten `SFlash_CLI.exe`. **Do not add `gui/` to the zip.** Stop and report the offending import to the user.
- **"GUI modules imported"** from the gui-import probe: same as above — report, never work around.
- **`[ERROR] GUI files would be packaged`**: someone added a forbidden path to the lists. Report it; don't remove the guard.
- **A real CLI failure** (a command exits non-zero on the virtual ECU): this is a regression in `main`, not a packaging problem. Report the output; don't edit application code to make packaging pass.
- **`No module named PySide6`**: wrong interpreter. Point to `git config sflash.python /path/to/env/bin/python`.

Never push anything the user didn't ask to have pushed, and never bundle other changes into the zip commit.

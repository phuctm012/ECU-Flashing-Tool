# PR Steward

Repo-specific guidance for driving PRs to green on this project.

## Before pushing a fix

- Run the full test suite: `python -m unittest discover -s tests -p "test_*.py" -v`
- Always run `tests/test_flash_threading.py` explicitly — thread lifecycle bugs are timing-dependent and easy to miss.
- If `gui/main_window.ui` was changed, regenerate with `pyside6-uic gui/main_window.ui -o gui/ui_main_window.py` and verify the import works.
- Re-read the diff: check for leftover debug prints, missing `self._cleanup()` on early returns in `FlashWorker.run()`, and any new widget with a generic Designer name.

## Conventions

- Merge base branch in (no rebase/force-push on others' branches).
- Regenerate `gui/ui_main_window.py` with `pyside6-uic` whenever `gui/main_window.ui` changes — never hand-edit the generated file.
- No linter/formatter is configured — don't add lint fix commits.

## Never

- Skip or disable a test to get CI green.
- Push without running the full test suite first.
- Hand-edit `gui/ui_main_window.py`.

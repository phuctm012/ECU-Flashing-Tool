# ==================================================
# Run Config (--config file.json)
# ==================================================
#
# One JSON file holding every setting a CLI command takes, so a
# pipeline step or a bench setup is a file under version control
# instead of a 12-flag command line.
#
# Distinct from the other two file formats, which it does not
# replace:
#
#   .sfproj (core/project_config.py) is a GUI session snapshot —
#   it carries the firmware list and the subset of settings the
#   Configure tab owns (11 of the 26 a `flash` accepts), and the
#   GUI is what writes it. A run config carries *all* of them,
#   including the ones that exist only on the command line
#   (--timeout, --bitrate, the report paths, the Security DLL
#   contract), and is written by hand.
#
#   --units-file (core/flash_unit.py) describes the ECUs of a
#   batch/parallel run. A run config can point at one, or carry
#   the same list inline under "unit".
#
# Keys are the command's own long options with the leading
# dashes dropped: "--json-summary" -> "json_summary" (or
# "json-summary", both are accepted). That mapping is checked
# against the *actual* argparse actions of the command being
# run, so this file format cannot drift from the flags: add a
# flag and the config supports it, with no change here.
#
# Precedence is left to argparse itself — cli.py applies the
# config through set_defaults(), so an explicit flag beats the
# file, the file beats a .sfproj, and that beats the built-in
# default, with no hand-written tie-breaking.
# ==================================================

import difflib
import json


class RunConfigError(Exception):
    """Raised for an unreadable, malformed or wrong-keyed config."""
    pass


def normalise_key(key):
    """
    "--json-summary" / "json-summary" / "json_summary" all mean
    the same option. Accepting all three is deliberate: the
    operator reads the key off `--help`, where it is spelled
    with dashes.
    """

    return str(key).strip().lstrip("-").replace("-", "_")


def load_run_config(path):
    """
    Reads the JSON file and returns {normalised_key: value}.
    Raises RunConfigError on anything unusable — a config that
    cannot be read is fatal, never a silent fallback to
    defaults: that would flash with settings nobody chose.
    """

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except OSError as e:
        raise RunConfigError(f"Could not read config file: {e}")
    except ValueError as e:
        raise RunConfigError(f"Config file is not valid JSON: {e}")

    if not isinstance(data, dict):
        raise RunConfigError(
            "Config file must contain a JSON object, got "
            f"{type(data).__name__}"
        )

    config = {}
    for key, value in data.items():
        normalised = normalise_key(key)
        if not normalised:
            continue
        if normalised in config:
            raise RunConfigError(
                f"Config key {normalised!r} appears twice "
                f"(two spellings of the same option)"
            )
        config[normalised] = value

    # A comment field is the one thing a JSON config always
    # wants and JSON itself has no syntax for.
    for ignored in ("comment", "_comment", "description"):
        config.pop(ignored, None)

    return config


def check_keys(config, allowed, command):
    """
    Rejects keys the command does not have, naming the closest
    match. A typo must never be ignored: "timeout_s" silently
    dropped means the run has no watchdog at all, which is
    exactly the failure the setting exists to prevent.
    """

    unknown = [key for key in config if key not in allowed]

    if not unknown:
        return

    lines = []
    for key in sorted(unknown):
        close = difflib.get_close_matches(key, sorted(allowed), n=1)
        hint = f" — did you mean {close[0]!r}?" if close else ""
        lines.append(f"  {key!r}{hint}")

    raise RunConfigError(
        f"Config file has {len(unknown)} setting(s) that "
        f"`{command}` does not accept:\n"
        + "\n".join(lines)
        + f"\n\nRun `{command} --help` for the full list."
    )

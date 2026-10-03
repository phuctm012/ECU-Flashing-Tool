# ==================================================
# Flash Unit (one ECU target)
# ==================================================
#
# The GUI expresses "which ECU am I talking to" through
# widgets: Configure > Communication's hardware combo and
# Radar Side for Single/Batch Flash, and Parallel Flash's
# per-panel channel combo + Channel Settings dialog
# (gui/parallel_channel_settings_dialog.py).
#
# cli.py needs the same thing without widgets, for commands
# that address more than one ECU (`batch`, `parallel`), so a
# unit is described either inline on the command line
#
#     --unit "name=Left,channel=0,serial=123456,side=s0"
#
# or in a JSON file for larger setups (--units-file). Every
# field is optional; anything left out falls back to the
# command's own global flags, which is what makes the simple
# single-config case (`batch firmware.s3 --count 5`) stay
# short.
# ==================================================

import json

from config.settings import SUZUKI_RADAR_CAN_IDS

# Keys accepted in a --unit spec / a units-file entry. Spelled
# out (rather than accepting anything) so a typo like
# "serial_number=..." is reported instead of silently ignored,
# which would flash the wrong ECU.
UNIT_KEYS = (
    "name",
    "hardware",
    "channel",
    "serial",
    "side",
    "tx_id",
    "rx_id",
    "functional_id",
)

_INT_KEYS = ("channel", "serial")
_HEX_KEYS = ("tx_id", "rx_id", "functional_id")


class UnitSpecError(Exception):
    """Raised for a malformed --unit spec or units file."""
    pass


class FlashUnit:
    """
    One ECU target: which hardware/channel to reach it on and
    which CAN IDs to use. Resolved against a command's global
    defaults so only the differences have to be spelled out.
    """

    def __init__(
        self,
        name=None,
        hardware="virtual",
        channel=0,
        serial=None,
        side="s0",
        tx_id=None,
        rx_id=None,
        functional_id=0x700,
    ):
        self.name = name
        self.hardware = hardware
        self.channel = channel
        self.serial = serial
        self.side = side
        self.functional_id = functional_id

        # Explicit IDs win over the Radar Side preset, exactly
        # like cli.py's _resolve_can_ids() does for --tx-id/
        # --rx-id vs --radar-side.
        preset = SUZUKI_RADAR_CAN_IDS[side.upper()]
        self.tx_id = (
            tx_id if tx_id is not None else int(preset["tx_id"], 16)
        )
        self.rx_id = (
            rx_id if rx_id is not None else int(preset["rx_id"], 16)
        )

    @property
    def use_virtual(self):
        return self.hardware == "virtual"

    @property
    def label(self):
        if self.name:
            return self.name
        if self.use_virtual:
            return f"Virtual (Tx=0x{self.tx_id:X})"
        return f"Channel {self.channel} (Tx=0x{self.tx_id:X})"

    def describe(self):
        target = (
            "Virtual ECU Simulator" if self.use_virtual
            else f"Vector channel {self.channel}"
            + (f" serial {self.serial}" if self.serial else "")
        )
        return (
            f"{target} | Tx=0x{self.tx_id:X} Rx=0x{self.rx_id:X}"
        )

    def __repr__(self):
        return f"<FlashUnit {self.label} {self.describe()}>"


# ==================================================
# Parsing
# ==================================================

def _coerce(key, raw):

    if key in _INT_KEYS:
        try:
            return int(str(raw), 10)
        except (TypeError, ValueError):
            raise UnitSpecError(
                f"unit field '{key}' must be an integer, got {raw!r}"
            )

    if key in _HEX_KEYS:
        try:
            return int(str(raw), 0) if isinstance(raw, str) else int(raw)
        except (TypeError, ValueError):
            raise UnitSpecError(
                f"unit field '{key}' must be an integer "
                f"(hex like 0x77B or decimal), got {raw!r}"
            )

    if key == "side":
        side = str(raw).strip().lower()
        if side.upper() not in SUZUKI_RADAR_CAN_IDS:
            valid = ", ".join(
                s.lower() for s in SUZUKI_RADAR_CAN_IDS
            )
            raise UnitSpecError(
                f"unit field 'side' must be one of {valid}, "
                f"got {raw!r}"
            )
        return side

    if key == "hardware":
        hardware = str(raw).strip().lower()
        if hardware not in ("virtual", "vector"):
            raise UnitSpecError(
                f"unit field 'hardware' must be 'virtual' or "
                f"'vector', got {raw!r}"
            )
        return hardware

    return str(raw)


def _fields_from_mapping(mapping):

    fields = {}

    for key, raw in mapping.items():
        key = str(key).strip().lower()
        if key not in UNIT_KEYS:
            raise UnitSpecError(
                f"unknown unit field {key!r} — valid fields are: "
                f"{', '.join(UNIT_KEYS)}"
            )
        if raw is None or raw == "":
            continue
        fields[key] = _coerce(key, raw)

    return fields


def parse_unit_spec(text):
    """
    Parses one `--unit "key=value,key=value"` spec into a dict
    of the fields it set. Unset fields are left out entirely so
    build_units() can fill them from the global flags.
    """

    mapping = {}

    for part in str(text).split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise UnitSpecError(
                f"unit spec part {part!r} is not key=value "
                f"(example: channel=0,side=s1)"
            )
        key, _, value = part.partition("=")
        mapping[key.strip()] = value.strip()

    return _fields_from_mapping(mapping)


def load_units_file(path):
    """
    Reads a units JSON file: either a bare list of unit objects,
    or an object with a "units" key holding that list (so the
    same file can carry comments/metadata alongside).
    Returns a list of field dicts, same shape parse_unit_spec()
    returns.
    """

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except OSError as e:
        raise UnitSpecError(f"Could not read units file: {e}")
    except ValueError as e:
        raise UnitSpecError(f"Units file is not valid JSON: {e}")

    if isinstance(data, dict):
        data = data.get("units", data.get("channels"))

    if not isinstance(data, list) or not data:
        raise UnitSpecError(
            "Units file must contain a non-empty list of unit "
            'objects, or an object with a "units" list'
        )

    units = []
    for i, entry in enumerate(data, 1):
        if not isinstance(entry, dict):
            raise UnitSpecError(
                f"units[{i}] must be an object, got "
                f"{type(entry).__name__}"
            )
        units.append(_fields_from_mapping(entry))

    return units


def build_units(field_dicts, defaults, count=1, name_prefix="Unit"):
    """
    Turns parsed field dicts into FlashUnit objects, filling
    every unset field from `defaults` (the command's global
    flags: hardware/channel/serial/side/tx_id/rx_id/
    functional_id).

    With no field dicts at all, produces `count` identical
    units from the defaults alone — the "same config, swap the
    ECU between runs" batch workflow. Units are auto-named
    "Unit 1".."Unit N" unless the spec named them.
    """

    field_dicts = list(field_dicts or [])

    if not field_dicts:
        field_dicts = [{} for _ in range(max(1, count))]

    units = []
    for i, fields in enumerate(field_dicts, 1):

        merged = {
            key: value for key, value in defaults.items()
            if key in UNIT_KEYS
        }

        # A unit that names its own Radar Side must not inherit
        # the *global* --tx-id/--rx-id, or every unit would end
        # up on the same CAN IDs and silently flash one ECU
        # twice. Within a unit, explicit IDs still win over its
        # side (same precedence as _resolve_can_ids()).
        if "side" in fields and "tx_id" not in fields:
            merged.pop("tx_id", None)
        if "side" in fields and "rx_id" not in fields:
            merged.pop("rx_id", None)

        merged.update(fields)

        if not merged.get("name"):
            merged["name"] = f"{name_prefix} {i}"

        units.append(FlashUnit(**merged))

    return units

# ==================================================
# Flash Unit (--unit / --units-file) Tests
# ==================================================
#
# core/flash_unit.py turns the `batch`/`parallel` commands' ECU
# descriptions into FlashUnit objects. The behaviour worth
# pinning is the *precedence*: a unit's own Radar Side must beat
# the command's global --tx-id/--rx-id, or every unit would
# inherit one pair of CAN IDs and the run would silently flash
# one ECU N times instead of N ECUs once.
# ==================================================

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from config.settings import SUZUKI_RADAR_CAN_IDS
from core.flash_unit import (
    FlashUnit,
    UnitSpecError,
    build_units,
    load_units_file,
    parse_unit_spec,
)

S0_TX = int(SUZUKI_RADAR_CAN_IDS["S0"]["tx_id"], 16)
S1_TX = int(SUZUKI_RADAR_CAN_IDS["S1"]["tx_id"], 16)
S1_RX = int(SUZUKI_RADAR_CAN_IDS["S1"]["rx_id"], 16)

DEFAULTS = {
    "hardware": "virtual",
    "channel": 0,
    "serial": None,
    "side": "s0",
    "functional_id": 0x700,
}


class TestFlashUnit(unittest.TestCase):

    def test_side_picks_the_can_ids(self):
        unit = FlashUnit(side="s1")
        self.assertEqual(unit.tx_id, S1_TX)
        self.assertEqual(unit.rx_id, S1_RX)

    def test_explicit_ids_win_over_side(self):
        unit = FlashUnit(side="s1", tx_id=0x123, rx_id=0x456)
        self.assertEqual(unit.tx_id, 0x123)
        self.assertEqual(unit.rx_id, 0x456)

    def test_label_falls_back_to_the_channel(self):
        self.assertIn("Channel 2", FlashUnit(
            hardware="vector", channel=2
        ).label)
        self.assertEqual(FlashUnit(name="Left").label, "Left")

    def test_describe_mentions_the_target_and_ids(self):
        text = FlashUnit(
            hardware="vector", channel=1, serial=99, side="s1"
        ).describe()
        self.assertIn("Vector channel 1", text)
        self.assertIn("serial 99", text)
        self.assertIn(f"0x{S1_TX:X}", text)

    def test_use_virtual(self):
        self.assertTrue(FlashUnit().use_virtual)
        self.assertFalse(FlashUnit(hardware="vector").use_virtual)


class TestParseUnitSpec(unittest.TestCase):

    def test_parses_every_field(self):
        fields = parse_unit_spec(
            "name=Left,hardware=vector,channel=1,serial=123456,"
            "side=s1,tx_id=0x77A,rx_id=0x78A,functional_id=0x700"
        )
        self.assertEqual(fields["name"], "Left")
        self.assertEqual(fields["hardware"], "vector")
        self.assertEqual(fields["channel"], 1)
        self.assertEqual(fields["serial"], 123456)
        self.assertEqual(fields["side"], "s1")
        self.assertEqual(fields["tx_id"], 0x77A)

    def test_omitted_fields_are_absent_not_defaulted(self):
        # build_units() has to be able to tell "not set" from
        # "set to the default" — that's what makes a global flag
        # fill it in.
        self.assertEqual(parse_unit_spec("channel=3"), {"channel": 3})

    def test_whitespace_and_empty_parts_are_tolerated(self):
        fields = parse_unit_spec(" channel = 2 , , side = s1 ")
        self.assertEqual(fields, {"channel": 2, "side": "s1"})

    def test_unknown_field_is_rejected(self):
        with self.assertRaises(UnitSpecError) as ctx:
            parse_unit_spec("serial_number=123")
        self.assertIn("unknown unit field", str(ctx.exception))

    def test_missing_equals_is_rejected(self):
        with self.assertRaises(UnitSpecError):
            parse_unit_spec("channel 0")

    def test_non_integer_channel_is_rejected(self):
        with self.assertRaises(UnitSpecError):
            parse_unit_spec("channel=left")

    def test_bad_side_is_rejected(self):
        with self.assertRaises(UnitSpecError):
            parse_unit_spec("side=s9")

    def test_bad_hardware_is_rejected(self):
        with self.assertRaises(UnitSpecError):
            parse_unit_spec("hardware=canoe")

    def test_hex_ids_accept_decimal_too(self):
        self.assertEqual(parse_unit_spec("tx_id=1915")["tx_id"], 1915)


class TestLoadUnitsFile(unittest.TestCase):

    def _write(self, payload):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "units.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        return path

    def test_bare_list(self):
        path = self._write([
            {"name": "Left", "channel": 0, "side": "s0"},
            {"name": "Right", "channel": 1, "side": "s1"},
        ])
        fields = load_units_file(path)
        self.assertEqual(len(fields), 2)
        self.assertEqual(fields[1]["name"], "Right")

    def test_object_with_units_key(self):
        path = self._write({
            "comment": "production line 1",
            "units": [{"channel": 0}],
        })
        self.assertEqual(load_units_file(path), [{"channel": 0}])

    def test_channels_key_is_accepted_too(self):
        # Parallel Flash's panels are "channels" in the GUI, so
        # a file written with that word reads the same.
        path = self._write({"channels": [{"channel": 3}]})
        self.assertEqual(load_units_file(path), [{"channel": 3}])

    def test_empty_list_is_rejected(self):
        with self.assertRaises(UnitSpecError):
            load_units_file(self._write([]))

    def test_non_object_entry_is_rejected(self):
        with self.assertRaises(UnitSpecError):
            load_units_file(self._write(["channel=0"]))

    def test_malformed_json_is_rejected(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "units.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{nope")
        with self.assertRaises(UnitSpecError):
            load_units_file(path)

    def test_missing_file_is_rejected(self):
        with self.assertRaises(UnitSpecError):
            load_units_file("/nonexistent/units.json")


class TestBuildUnits(unittest.TestCase):

    def test_no_specs_produces_count_identical_units(self):
        units = build_units([], DEFAULTS, count=3)
        self.assertEqual(len(units), 3)
        self.assertEqual(
            [u.name for u in units], ["Unit 1", "Unit 2", "Unit 3"]
        )
        self.assertTrue(all(u.tx_id == S0_TX for u in units))

    def test_count_zero_still_yields_one_unit(self):
        self.assertEqual(len(build_units([], DEFAULTS, count=0)), 1)

    def test_unset_fields_come_from_the_global_defaults(self):
        defaults = dict(DEFAULTS, hardware="vector", channel=7)
        unit = build_units([{"name": "A"}], defaults)[0]
        self.assertEqual(unit.hardware, "vector")
        self.assertEqual(unit.channel, 7)

    def test_unit_fields_override_the_defaults(self):
        unit = build_units([{"channel": 2}], DEFAULTS)[0]
        self.assertEqual(unit.channel, 2)

    def test_per_unit_side_beats_a_global_tx_rx(self):
        # The bug this guards: with --tx-id pinned globally,
        # inheriting it would give every unit the same CAN IDs,
        # so "flash Left and Right" would flash one ECU twice.
        defaults = dict(DEFAULTS, tx_id=S0_TX, rx_id=S0_TX + 0x10)
        units = build_units(
            [{"side": "s0"}, {"side": "s1"}], defaults
        )
        self.assertEqual(units[0].tx_id, S0_TX)
        self.assertEqual(units[1].tx_id, S1_TX)
        self.assertEqual(units[1].rx_id, S1_RX)

    def test_unit_level_tx_id_still_beats_its_own_side(self):
        defaults = dict(DEFAULTS, tx_id=S0_TX)
        unit = build_units(
            [{"side": "s1", "tx_id": 0x600}], defaults
        )[0]
        self.assertEqual(unit.tx_id, 0x600)

    def test_a_global_tx_id_applies_to_units_that_name_no_side(self):
        defaults = dict(DEFAULTS, tx_id=0x601, rx_id=0x602)
        unit = build_units([{"channel": 1}], defaults)[0]
        self.assertEqual(unit.tx_id, 0x601)
        self.assertEqual(unit.rx_id, 0x602)

    def test_name_prefix_is_used_for_auto_names(self):
        units = build_units([], DEFAULTS, count=2,
                            name_prefix="Channel")
        self.assertEqual(units[0].name, "Channel 1")

    def test_defaults_may_carry_unrelated_keys(self):
        # Callers pass a dict built from argparse; keys that
        # aren't unit fields must be ignored, not crash
        # FlashUnit's constructor.
        defaults = dict(DEFAULTS, bitrate=500000, verbose=True)
        self.assertEqual(len(build_units([], defaults, count=1)), 1)


if __name__ == "__main__":
    unittest.main()

# ==================================================
# Flash Sequence Tests
# ==================================================

import os
import sys
import unittest

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from parsers.hex_parser import Segment, Datablock
from core.flash_sequence import (
    FlashStep,
    build_flash_sequence,
    build_suzuki_slp1_flash_sequence,
)


def _make_datablock(address=0x1000, size=64):
    db = Datablock(file_path="synthetic.bin")
    db.segments.append(
        Segment(start_address=address, data=bytes([0xAA]) * size)
    )
    return db


class TestDefaultFlashSequence(unittest.TestCase):

    def test_inserts_one_download_step_per_segment(self):
        db = _make_datablock()
        steps = build_flash_sequence([db])
        downloads = [
            s for s in steps if s.step_type == FlashStep.TYPE_DOWNLOAD
        ]
        self.assertEqual(len(downloads), 1)

    def test_multiple_segments_multiple_downloads(self):
        db = Datablock(file_path="synthetic.bin")
        db.segments.append(Segment(0x1000, bytes([0xAA]) * 16))
        db.segments.append(Segment(0x2000, bytes([0xBB]) * 16))
        steps = build_flash_sequence([db])
        downloads = [
            s for s in steps if s.step_type == FlashStep.TYPE_DOWNLOAD
        ]
        self.assertEqual(len(downloads), 2)

    def test_no_datablocks_means_no_download_steps(self):
        steps = build_flash_sequence(None)
        downloads = [
            s for s in steps if s.step_type == FlashStep.TYPE_DOWNLOAD
        ]
        self.assertEqual(len(downloads), 0)
        self.assertGreater(len(steps), 0)  # sequence itself still built

    def test_default_addr_size_length_is_4(self):
        db = _make_datablock()
        steps = build_flash_sequence([db])
        dl = next(
            s for s in steps if s.step_type == FlashStep.TYPE_DOWNLOAD
        )
        self.assertEqual(dl.params["addr_length"], 4)
        self.assertEqual(dl.params["size_length"], 4)

    def test_has_read_did_before_and_after(self):
        db = _make_datablock()
        steps = build_flash_sequence([db])
        read_did_steps = [
            s for s in steps if s.step_type == FlashStep.TYPE_READ_DID
        ]
        phases = {s.params.get("phase") for s in read_did_steps}
        self.assertEqual(phases, {"before", "after"})


class TestSuzukiSlp1FlashSequence(unittest.TestCase):
    """
    Reverse-engineered from a real ECU trace log
    (docs/*_Report_Trace.csv) — these tests pin down the
    specific behaviors that differ from the generic
    sequence, confirmed against that log.
    """

    def test_three_pre_programming_steps_are_functional(self):
        db = _make_datablock(address=0x1AA000)
        steps = build_suzuki_slp1_flash_sequence([db])

        pre_programming = steps[:3]
        for step in pre_programming:
            self.assertTrue(
                step.params.get("functional"),
                f"{step.name} should be functional",
            )

    def test_final_session_confirm_is_functional(self):
        db = _make_datablock()
        steps = build_suzuki_slp1_flash_sequence([db])
        last = steps[-1]
        self.assertEqual(last.step_type, FlashStep.TYPE_SESSION)
        self.assertTrue(last.params.get("functional"))

    def test_erase_happens_once_no_precondition_check_step(self):
        db = _make_datablock()
        steps = build_suzuki_slp1_flash_sequence([db])
        routine_ff00_steps = [
            s for s in steps
            if s.step_type == FlashStep.TYPE_ROUTINE
            and s.params.get("routine_id") == 0xFF00
        ]
        self.assertEqual(len(routine_ff00_steps), 1)

    def test_no_read_did_steps(self):
        db = _make_datablock()
        steps = build_suzuki_slp1_flash_sequence([db])
        read_did_steps = [
            s for s in steps if s.step_type == FlashStep.TYPE_READ_DID
        ]
        self.assertEqual(len(read_did_steps), 0)

    def test_download_uses_5_byte_address(self):
        db = _make_datablock(address=0x1AA000)
        steps = build_suzuki_slp1_flash_sequence([db])
        dl = next(
            s for s in steps if s.step_type == FlashStep.TYPE_DOWNLOAD
        )
        self.assertEqual(dl.params["addr_length"], 5)
        self.assertEqual(dl.params["size_length"], 4)

    def test_erase_and_verify_have_option_record(self):
        db = _make_datablock()
        steps = build_suzuki_slp1_flash_sequence([db])
        for name in ("Erase Memory", "Verify Memory"):
            step = next(s for s in steps if s.name == name)
            self.assertEqual(
                step.params.get("option_record"), bytes([0x00])
            )

    def test_write_did_steps_target_f198_and_f199(self):
        db = _make_datablock()
        steps = build_suzuki_slp1_flash_sequence([db])
        write_did_steps = [
            s for s in steps if s.step_type == FlashStep.TYPE_WRITE_DID
        ]
        dids = {s.params["did"] for s in write_did_steps}
        self.assertEqual(dids, {0xF198, 0xF199})

    def test_programming_date_is_valid_packed_bcd(self):
        db = _make_datablock()
        steps = build_suzuki_slp1_flash_sequence([db])
        date_step = next(
            s for s in steps if s.name == "Write Programming Date"
        )
        data = date_step.params["data"]
        self.assertEqual(len(data), 4)
        for byte in data:
            self.assertLessEqual(byte >> 4, 9, "invalid BCD nibble")
            self.assertLessEqual(byte & 0x0F, 9, "invalid BCD nibble")

    def test_erase_before_download_before_verify(self):
        db = _make_datablock()
        steps = build_suzuki_slp1_flash_sequence([db])

        erase_idx = next(
            i for i, s in enumerate(steps) if s.name == "Erase Memory"
        )
        download_idx = next(
            i for i, s in enumerate(steps)
            if s.step_type == FlashStep.TYPE_DOWNLOAD
        )
        verify_idx = next(
            i for i, s in enumerate(steps) if s.name == "Verify Memory"
        )

        self.assertLess(erase_idx, download_idx)
        self.assertLess(download_idx, verify_idx)

    def test_write_tester_info_defaults_without_override(self):
        db = _make_datablock()
        steps = build_suzuki_slp1_flash_sequence([db])
        step = next(s for s in steps if s.name == "Write Tester Info")
        self.assertEqual(
            step.params["data"],
            bytes.fromhex("00112233445566778899"),
        )

    def test_tester_serial_number_override_is_applied(self):
        db = _make_datablock()
        override = bytes.fromhex("AABBCCDDEE0011223344")
        steps = build_suzuki_slp1_flash_sequence(
            [db], tester_serial_number=override
        )
        step = next(s for s in steps if s.name == "Write Tester Info")
        self.assertEqual(step.params["data"], override)

    def test_tester_serial_number_override_does_not_leak_into_template(self):
        # SUZUKI_SLP1_FLASH_SEQUENCE is a shared module-level
        # list reused by every call — the override must replace
        # the returned step with a new FlashStep rather than
        # mutating its params dict in place, or one flash's
        # override would silently apply to every later flash in
        # the same process, including ones that never touch it.
        db = _make_datablock()
        override = bytes.fromhex("AABBCCDDEE0011223344")
        build_suzuki_slp1_flash_sequence(
            [db], tester_serial_number=override
        )

        steps = build_suzuki_slp1_flash_sequence([db])
        step = next(s for s in steps if s.name == "Write Tester Info")
        self.assertEqual(
            step.params["data"],
            bytes.fromhex("00112233445566778899"),
        )


if __name__ == "__main__":
    unittest.main()

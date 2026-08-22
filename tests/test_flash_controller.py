# ==================================================
# FlashWorker End-to-End Tests (synchronous, no QThread)
# ==================================================
#
# Calls FlashWorker.run() directly to validate the flash
# sequence execution logic itself against the Virtual ECU
# Simulator. This does NOT exercise the QThread/GUI wiring
# in gui/flash_tab.py — see test_flash_threading.py for
# that (it's a different, thread-lifecycle-focused concern).
# ==================================================

import os
import sys
import unittest

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from parsers.hex_parser import Segment, Datablock, parse_hex_file
from core.flash_sequence import (
    build_flash_sequence,
    build_suzuki_slp1_flash_sequence,
)
from core.flash_controller import FlashWorker

SAMPLE_HEX = os.path.join(os.path.dirname(__file__), "sample.hex")


def _make_datablock(address=0x1AA000, size=64):
    db = Datablock(file_path="synthetic.bin")
    db.segments.append(
        Segment(start_address=address, data=bytes([0xAA]) * size)
    )
    return db


def _run_worker(worker):
    """Run a FlashWorker synchronously and collect outcome."""

    result = {"finished": False, "aborted": False}
    worker.flash_finished.connect(lambda: result.update(finished=True))
    worker.flash_aborted.connect(lambda: result.update(aborted=True))
    worker.run()
    return result


class TestDefaultSequenceFlash(unittest.TestCase):

    def test_flash_completes_successfully(self):
        db = parse_hex_file(SAMPLE_HEX)
        steps = build_flash_sequence([db])
        worker = FlashWorker(
            steps=steps, datablocks=[db], use_virtual=True
        )

        result = _run_worker(worker)

        self.assertTrue(result["finished"])
        self.assertFalse(result["aborted"])

    def test_step_started_emitted_for_every_step(self):
        db = parse_hex_file(SAMPLE_HEX)
        steps = build_flash_sequence([db])
        worker = FlashWorker(
            steps=steps, datablocks=[db], use_virtual=True
        )

        started = []
        worker.step_started.connect(started.append)
        _run_worker(worker)

        self.assertEqual(len(started), len(steps))

    def test_progress_reaches_100(self):
        db = parse_hex_file(SAMPLE_HEX)
        steps = build_flash_sequence([db])
        worker = FlashWorker(
            steps=steps, datablocks=[db], use_virtual=True
        )

        progress = []
        worker.progress_changed.connect(progress.append)
        _run_worker(worker)

        self.assertEqual(progress[-1], 100)

    def test_no_steps_finishes_immediately(self):
        worker = FlashWorker(steps=[], datablocks=[], use_virtual=True)
        result = _run_worker(worker)
        self.assertTrue(result["finished"])


class TestSuzukiSequenceFlash(unittest.TestCase):

    def test_flash_completes_with_left_side_can_ids(self):
        db = _make_datablock()
        steps = build_suzuki_slp1_flash_sequence([db])
        worker = FlashWorker(
            steps=steps,
            datablocks=[db],
            use_virtual=True,
            can_tx_id=0x77B,
            can_rx_id=0x78B,
            keepalive_functional=True,
        )

        result = _run_worker(worker)

        self.assertTrue(result["finished"])
        self.assertFalse(result["aborted"])

    def test_trace_rows_have_correct_functional_and_physical_targets(self):
        db = _make_datablock()
        steps = build_suzuki_slp1_flash_sequence([db])
        worker = FlashWorker(
            steps=steps,
            datablocks=[db],
            use_virtual=True,
            can_tx_id=0x77B,
            can_rx_id=0x78B,
            keepalive_functional=True,
        )

        rows = []
        worker.trace_row.connect(rows.append)
        _run_worker(worker)

        functional_rows = [
            r for r in rows if r.get("req_target") == "FuncGroup-0x700"
        ]
        physical_rows = [
            r for r in rows if r.get("req_target") == "0x77B"
        ]

        # 3 pre-programming steps (Extended Session, DTC off,
        # CommControl off) + the final post-reset session
        # confirmation — see SUZUKI_SLP1_FLASH_SEQUENCE.
        self.assertEqual(len(functional_rows), 4)
        self.assertGreater(len(physical_rows), 0)
        for r in physical_rows:
            self.assertEqual(r.get("resp_source"), "0x78B")


class TestAbort(unittest.TestCase):

    def test_abort_before_run_finishes_immediately(self):
        db = _make_datablock(size=200_000)  # big enough to have steps left
        steps = build_flash_sequence([db])
        worker = FlashWorker(
            steps=steps, datablocks=[db], use_virtual=True
        )
        worker.request_abort()

        result = _run_worker(worker)

        self.assertTrue(result["aborted"])
        self.assertFalse(result["finished"])


class TestSecurityDllFailureAbortsGracefully(unittest.TestCase):

    def test_missing_dll_aborts_without_raising(self):
        db = _make_datablock()
        steps = build_flash_sequence([db])
        worker = FlashWorker(
            steps=steps,
            datablocks=[db],
            use_virtual=False,
            security_dll_path="/nonexistent/path/to.dll",
        )

        # use_virtual=False without real hardware will fail to
        # connect (no python-can / no Vector driver) before it
        # even reaches the DLL — either way, run() must not
        # raise, it must abort cleanly via the signal.
        result = _run_worker(worker)

        self.assertTrue(result["aborted"])
        self.assertFalse(result["finished"])


if __name__ == "__main__":
    unittest.main()

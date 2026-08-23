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
import unittest.mock

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from parsers.hex_parser import Segment, Datablock, parse_hex_file
from core.flash_sequence import (
    FlashStep,
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

    def test_no_steps_still_cleans_up_keepalive_and_can(self):
        # Regression: run() used to emit flash_finished on the
        # empty-steps fast path WITHOUT calling _cleanup() first
        # — start_keepalive() runs unconditionally near the top
        # of run(), so this left a TesterPresentThread ticking
        # in the background forever (never stopped) and the CAN
        # interface never disconnected. That stray thread kept
        # calling back into this worker's _on_uds_trace() for
        # the rest of the test process's life, which could fire
        # "RuntimeError: Signal source has been deleted" at any
        # point later once the worker was torn down — an
        # intermittent, hard-to-reproduce crash symptom.
        worker = FlashWorker(steps=[], datablocks=[], use_virtual=True)
        _run_worker(worker)

        self.assertIsNone(worker._uds_client._tp_keepalive)
        self.assertFalse(worker._can_interface.is_connected)


class TestVerifyMemoryPassFail(unittest.TestCase):
    """
    Covers docs/gui_todo.md item #9 — _execute_routine() must
    emit an unambiguous PASS/FAIL line for the "Verify Memory"
    step specifically (params["action"] == "verify"), not just
    the generic "Routine 0x.... completed"/"Error: ..." text
    every other routine call gets.
    """

    def test_verify_memory_emits_pass_on_success(self):
        # Real end-to-end run through the Virtual ECU — Verify
        # Memory always succeeds there, so this exercises the
        # actual DEFAULT_FLASH_SEQUENCE step (params["action"]
        # == "verify" set in core/flash_sequence.py), not a
        # hand-built one.
        db = parse_hex_file(SAMPLE_HEX)
        steps = build_flash_sequence([db])
        worker = FlashWorker(
            steps=steps, datablocks=[db], use_virtual=True
        )

        messages = []
        worker.information_message.connect(messages.append)
        result = _run_worker(worker)

        self.assertTrue(result["finished"])
        self.assertIn("✓ Verify Memory: PASS", messages)

    def test_verify_memory_emits_failed_and_reraises_on_error(self):
        # Direct unit test of _execute_routine() — inject a
        # uds_client whose routine_control() raises, bypassing
        # the Virtual ECU (which never fails Verify Memory) so
        # the FAILED path can be exercised deterministically.
        mock_uds = unittest.mock.Mock()
        mock_uds.routine_control.side_effect = RuntimeError(
            "NRC 0x22: Conditions Not Correct"
        )

        worker = FlashWorker(uds_client=mock_uds)

        messages = []
        worker.information_message.connect(messages.append)

        step = FlashStep(
            name="Verify Memory",
            step_type=FlashStep.TYPE_ROUTINE,
            description="Verify Memory",
            params={"routine_id": 0xFF01, "action": "verify"},
        )

        with self.assertRaises(RuntimeError):
            worker._execute_routine(step)

        self.assertIn("✗ Verify Memory: FAILED", messages)

    def test_erase_memory_message_unaffected(self):
        # Regression: the "verify" branch must not change
        # behavior for any other action (erase, or no action).
        mock_uds = unittest.mock.Mock()
        worker = FlashWorker(uds_client=mock_uds)

        messages = []
        worker.information_message.connect(messages.append)

        step = FlashStep(
            name="Erase Memory",
            step_type=FlashStep.TYPE_ROUTINE,
            description="Erase Memory",
            params={"routine_id": 0xFF00, "action": "erase"},
        )
        worker._execute_routine(step)

        self.assertEqual(messages, ["Memory erased"])


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

# ==================================================
# TestConnectionWorker Tests (synchronous, no QThread)
# ==================================================
#
# Calls TestConnectionWorker.run() directly against the
# Virtual ECU Simulator to validate the probe logic itself —
# mirrors tests/test_flash_controller.py's approach for
# FlashWorker. Does NOT exercise the QThread/dialog wiring in
# gui/test_connection_dialog.py — that's covered separately
# in tests/test_gui_smoke.py (thread-lifecycle concern, same
# reasoning as test_flash_threading.py for the Flash button).
# ==================================================

import os
import sys
import unittest

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from core.test_connection import TestConnectionWorker


def _run_worker(worker):
    """Run a TestConnectionWorker synchronously, collect outcome."""

    result = {
        "passed": None, "message": None,
        "steps": [], "trace_rows": [], "ecu_info": None,
    }
    worker.finished.connect(
        lambda passed, message: result.update(
            passed=passed, message=message
        )
    )
    worker.step_message.connect(
        lambda msg: result["steps"].append(msg)
    )
    worker.trace_row.connect(
        lambda row: result["trace_rows"].append(row)
    )
    worker.ecu_info_message.connect(
        lambda info: result.update(ecu_info=info)
    )
    worker.run()
    return result


class TestGenericProbe(unittest.TestCase):

    def test_passes_and_restores_default_session(self):
        worker = TestConnectionWorker(use_virtual=True, functional=False)
        result = _run_worker(worker)

        self.assertTrue(result["passed"])
        self.assertIn("PASSED", result["message"])
        self.assertIn(
            "Read DID 0xF189: Vehicle Manufacturer ECU SW Version"
            " = V1.0.0",
            result["steps"],
        )
        self.assertIn("Restored Default session", result["steps"])

    def test_reads_ecu_identification(self):
        worker = TestConnectionWorker(use_virtual=True, functional=False)
        result = _run_worker(worker)

        self.assertIsNotNone(result["ecu_info"])
        self.assertIn(
            "Vehicle Manufacturer ECU SW Version", result["ecu_info"]
        )

    def test_never_sends_erase_or_download(self):
        # RequestDownload (0x34) / TransferData (0x36) must
        # never appear as the SID (first byte) of any request
        # this worker sends — it must stay read-only + session/
        # security only, same invariant as cli.py's
        # test-connection (tests/test_cli.py::test_never_touches_erase_or_download).
        worker = TestConnectionWorker(use_virtual=True, functional=False)
        result = _run_worker(worker)

        sids_sent = {
            row["req_data"].split(" ")[0]
            for row in result["trace_rows"]
            if row.get("req_data")
        }
        self.assertNotIn("34", sids_sent)
        self.assertNotIn("36", sids_sent)


class TestFunctionalProbe(unittest.TestCase):

    def test_pre_security_steps_use_functional_addressing(self):
        worker = TestConnectionWorker(use_virtual=True, functional=True)
        result = _run_worker(worker)

        self.assertTrue(result["passed"])

        pre_security_targets = [
            row["req_target"] for row in result["trace_rows"][:3]
        ]
        self.assertTrue(
            all(t == "FuncGroup-0x700" for t in pre_security_targets),
            pre_security_targets,
        )

    def test_restores_communication_and_dtc(self):
        worker = TestConnectionWorker(use_virtual=True, functional=True)
        result = _run_worker(worker)

        self.assertIn(
            "Disable DTC Settings (Network)", result["steps"]
        )
        self.assertIn("Restored Default session", result["steps"])

        req_data_sent = [
            row["req_data"] for row in result["trace_rows"]
            if row.get("req_data")
        ]
        self.assertIn("28 00 01", req_data_sent)  # Comm enable
        self.assertIn("85 01", req_data_sent)     # DTC ON


class TestConnectionFailure(unittest.TestCase):

    def test_vector_without_python_can_fails_gracefully(self):
        # python-can isn't installed in the dev/test env — this
        # must fail cleanly (passed=False), not crash.
        worker = TestConnectionWorker(use_virtual=False)
        result = _run_worker(worker)

        self.assertFalse(result["passed"])
        self.assertIn("Connection failed", result["message"])


if __name__ == "__main__":
    unittest.main()

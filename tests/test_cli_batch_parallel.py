# ==================================================
# Headless Multi-ECU Runner Tests (batch / parallel)
# ==================================================
#
# core/batch_runner.py and core/parallel_runner.py are the
# headless counterparts of gui/batch_flash.py and
# gui/parallel_flash.py. They drive the real FlashWorker /
# TestConnectionWorker against the Virtual ECU Simulator — no
# widgets, no Qt event loop — so these tests run the actual
# flash sequence end to end rather than mocking it.
#
# Two risk classes are specific to the headless runners and
# pinned below:
#
#  * Signal delivery. A Qt signal emitted from a thread the
#    QObject does not live on is queued, and with no event loop
#    running it is dropped silently — a probe of that exact
#    pattern received 0 of 3 emissions. ParallelRunner therefore
#    constructs each FlashWorker *inside* its own thread.
#    TestListenerActuallyHearsTheWorkers fails loudly if that is
#    ever "simplified" back, which would otherwise show up only
#    as a report with no steps in it.
#
#  * Security DLL serialisation. The lock is only reached on the
#    key_function/DLL paths of UdsClient._compute_security_key()
#    — which a virtual run does take, since FlashWorker passes
#    EcuSimulator.compute_key as key_function. So a virtual
#    parallel run is a real test of it.
# ==================================================

import os
import sys
import threading
import time
import unittest

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from PySide6.QtWidgets import QApplication

from unittest import mock

from core.batch_runner import BatchRunner
from core.flash_controller import FlashWorker
from core.flash_unit import FlashUnit
from core.parallel_runner import ParallelRunner
from core.report import RESULT_ABORTED, RESULT_FAIL, RESULT_PASS
from parsers.auto_parser import parse_firmware_file
from parsers.hex_parser import Datablock, Segment

SAMPLE_HEX = os.path.join(os.path.dirname(__file__), "sample.hex")


def get_app():
    return QApplication.instance() or QApplication(sys.argv)


def _datablocks():
    return [parse_firmware_file(SAMPLE_HEX)]


def _slow_datablocks(size=8000):
    """
    A datablock big enough that a flash is still in its Download
    steps when another thread calls abort_all(), the same trick
    tests/test_parallel_flash_threading.py uses — FlashWorker
    checks its abort flag at step boundaries, so the run has to
    still have boundaries left to reach.
    """

    datablock = Datablock(file_path="synthetic_parallel_cli.bin")
    datablock.segments.append(
        Segment(start_address=0x1000, data=bytes([0x5A]) * size)
    )
    return [datablock]


class _RecordingListener:
    """Collects every hook call, per unit, thread-safely."""

    def __init__(self):
        self.lock = threading.Lock()
        self.steps = []
        self.identifies = []
        self.finished = []
        self.trace_rows = []
        self.unit_starts = []

    def on_unit_started(self, index, total, unit):
        with self.lock:
            self.unit_starts.append((index, total, unit.label))

    def on_identify_result(self, unit, passed, message, info):
        with self.lock:
            self.identifies.append((unit.label, passed, info))

    def on_step_started(self, unit, index, total, description):
        with self.lock:
            self.steps.append((unit.label, description))

    def on_trace_row(self, unit, row):
        with self.lock:
            self.trace_rows.append(unit.label)

    def on_unit_finished(self, unit, result, duration, reason):
        with self.lock:
            self.finished.append((unit.label, result, reason))


class _CountingLock:
    """
    A lock that records how many times it was taken and whether
    two holders ever overlapped. Used in place of
    ParallelRunner's own security lock to prove serialisation
    through the real UdsClient code path.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._bookkeeping = threading.Lock()
        self.acquisitions = 0
        self.held = 0
        self.max_concurrent = 0

    def __enter__(self):
        self._lock.acquire()
        with self._bookkeeping:
            self.acquisitions += 1
            self.held += 1
            self.max_concurrent = max(self.max_concurrent, self.held)
        return self

    def __exit__(self, *exc):
        with self._bookkeeping:
            self.held -= 1
        self._lock.release()
        return False

    def acquire(self, *args, **kwargs):
        return self._lock.acquire(*args, **kwargs)

    def release(self):
        return self._lock.release()


# ==================================================
# BatchRunner
# ==================================================

class TestBatchRunnerSequential(unittest.TestCase):

    def setUp(self):
        self.app = get_app()

    def test_three_units_all_pass_and_report_their_serial(self):
        listener = _RecordingListener()
        units = [FlashUnit(name=f"U{i}") for i in range(1, 4)]

        results = BatchRunner(
            units, _datablocks(), listener=listener
        ).run()

        self.assertEqual(len(results), 3)
        self.assertTrue(
            all(r["result"] == RESULT_PASS for r in results)
        )
        # Identify ran per unit and captured the simulator's
        # serial — that's what lands in the report's Units table.
        self.assertEqual(len(listener.identifies), 3)
        self.assertTrue(
            all(r["serial"] for r in results),
            f"serials: {[r['serial'] for r in results]}",
        )

    def test_units_run_one_at_a_time_in_order(self):
        listener = _RecordingListener()
        units = [FlashUnit(name="first"), FlashUnit(name="second")]

        BatchRunner(units, _datablocks(), listener=listener).run()

        labels = [label for label, _ in listener.steps]
        # Every "first" step precedes every "second" step — the
        # defining difference from ParallelRunner.
        self.assertEqual(
            labels, sorted(labels, key=lambda l: l != "first")
        )
        self.assertEqual(
            [i for i, _, _ in listener.unit_starts], [1, 2]
        )

    def test_no_identify_skips_the_probe_but_still_flashes(self):
        listener = _RecordingListener()

        results = BatchRunner(
            [FlashUnit(name="U1")], _datablocks(),
            identify=False, listener=listener,
        ).run()

        self.assertEqual(listener.identifies, [])
        self.assertEqual(results[0]["result"], RESULT_PASS)

    def test_empty_datablocks_raises_instead_of_a_false_pass(self):
        # FlashWorker treats 0 steps as an instant
        # flash_finished, which would log PASS per unit with no
        # work done — the same trap _start_flash_for_current_ecu()
        # guards against in the GUI.
        with self.assertRaises(ValueError):
            BatchRunner([FlashUnit()], []).run()

    def test_generic_sequence_is_honoured(self):
        listener = _RecordingListener()

        BatchRunner(
            [FlashUnit(name="U1")], _datablocks(),
            sequence="generic", identify=False, listener=listener,
        ).run()

        descriptions = [d for _, d in listener.steps]
        # The generic sequence opens with a ReadDID step the
        # Suzuki one doesn't have at all.
        self.assertTrue(
            any("Identification" in d for d in descriptions),
            descriptions,
        )


class TestBatchRunnerFailureHandling(unittest.TestCase):

    # The Virtual ECU is wired straight to the in-memory bus, so
    # it answers on ANY CAN ID — there is no "wrong address" that
    # makes a virtual unit fail. To test the runner's failure
    # bookkeeping (not the CAN layer's), connection setup is made
    # to raise for one marked unit only, which is exactly what a
    # missing or mis-wired ECU does one layer down.
    BAD_TX = 0x7A0

    def setUp(self):
        self.app = get_app()

        real_setup = FlashWorker._setup_uds_client

        def setup_or_fail(worker_self):
            if worker_self._can_tx_id == self.BAD_TX:
                raise ConnectionError(
                    "simulated: ECU not reachable"
                )
            return real_setup(worker_self)

        patcher = mock.patch.object(
            FlashWorker, "_setup_uds_client", setup_or_fail
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _unreachable_unit(self, name):
        return FlashUnit(name=name, tx_id=self.BAD_TX, rx_id=0x7B0)

    def test_a_failing_unit_is_recorded_and_the_series_continues(self):
        listener = _RecordingListener()
        units = [
            self._unreachable_unit("bad"),
            FlashUnit(name="good"),
        ]

        results = BatchRunner(
            units, _datablocks(), listener=listener
        ).run()

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["result"], RESULT_FAIL)
        self.assertTrue(results[0]["reason"])
        self.assertEqual(results[1]["result"], RESULT_PASS)

    def test_stop_on_fail_ends_the_series_early(self):
        units = [
            self._unreachable_unit("bad"),
            FlashUnit(name="never-reached"),
        ]

        results = BatchRunner(
            units, _datablocks(), stop_on_fail=True
        ).run()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["result"], RESULT_FAIL)

    def test_a_failing_identify_does_not_start_a_flash(self):
        listener = _RecordingListener()

        BatchRunner(
            [self._unreachable_unit("bad")], _datablocks(),
            listener=listener,
        ).run()

        self.assertEqual(listener.steps, [])


class TestBatchRunnerPauseHook(unittest.TestCase):

    def setUp(self):
        self.app = get_app()

    def test_pause_hook_runs_between_units_only(self):
        calls = []

        BatchRunner(
            [FlashUnit(name="a"), FlashUnit(name="b"),
             FlashUnit(name="c")],
            _datablocks(), identify=False,
            pause_hook=lambda i, total, unit: calls.append(i) or True,
        ).run()

        # Not before the first unit — the operator already has
        # that ECU connected when they start the command.
        self.assertEqual(calls, [2, 3])

    def test_pause_hook_returning_false_stops_the_series(self):
        runner = BatchRunner(
            [FlashUnit(name="a"), FlashUnit(name="b")],
            _datablocks(), identify=False,
            pause_hook=lambda i, total, unit: False,
        )

        results = runner.run()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["result"], RESULT_PASS)


# ==================================================
# ParallelRunner
# ==================================================

class TestParallelRunnerConcurrency(unittest.TestCase):

    def setUp(self):
        self.app = get_app()

    def test_three_channels_overlap_instead_of_queueing(self):
        units = [FlashUnit(name=f"ch{i}") for i in range(3)]

        started = time.monotonic()
        results = ParallelRunner(units, _datablocks()).run()
        wall = time.monotonic() - started

        self.assertEqual(len(results), 3)
        self.assertTrue(
            all(r["result"] == RESULT_PASS for r in results)
        )

        # Each channel's own measured duration is ~1s (the
        # simulator's response delays). Run sequentially the wall
        # clock would be their sum; concurrently it is close to
        # the longest one. A generous 70% bound keeps this from
        # being flaky on a loaded machine while still failing
        # outright if the threads are serialised.
        total = sum(r["duration"] for r in results)
        self.assertLess(
            wall, total * 0.7,
            f"wall {wall:.2f}s vs summed {total:.2f}s — channels "
            f"do not look concurrent",
        )

    def test_results_come_back_in_the_units_order(self):
        # Threads finish in whatever order they like; the report
        # must still list Channel 1, 2, 3.
        units = [FlashUnit(name=f"ch{i}") for i in range(4)]

        results = ParallelRunner(units, _datablocks()).run()

        self.assertEqual(
            [r["name"] for r in results],
            [u.label for u in units],
        )

    def test_empty_datablocks_raises(self):
        with self.assertRaises(ValueError):
            ParallelRunner([FlashUnit()], []).run()


class TestListenerActuallyHearsTheWorkers(unittest.TestCase):
    """
    The regression guard described in this module's header: if a
    worker is ever constructed on the main thread and run on
    another, every one of these callbacks goes quiet and the
    reports silently lose their Steps and Trace sections.
    """

    def setUp(self):
        self.app = get_app()

    def test_step_and_trace_callbacks_arrive_from_each_channel(self):
        listener = _RecordingListener()
        units = [FlashUnit(name="ch0"), FlashUnit(name="ch1")]

        ParallelRunner(
            units, _datablocks(), listener=listener
        ).run()

        labels = {label for label, _ in listener.steps}
        self.assertEqual(labels, {"ch0", "ch1"})
        # 13 Suzuki steps per channel, both channels heard.
        self.assertGreaterEqual(len(listener.steps), 20)
        self.assertEqual(set(listener.trace_rows), {"ch0", "ch1"})

    def test_every_channel_reports_a_final_result(self):
        listener = _RecordingListener()

        ParallelRunner(
            [FlashUnit(name="ch0"), FlashUnit(name="ch1")],
            _datablocks(), listener=listener,
        ).run()

        self.assertEqual(
            sorted(label for label, _, _ in listener.finished),
            ["ch0", "ch1"],
        )


class TestParallelSecurityLockSerialises(unittest.TestCase):

    def setUp(self):
        self.app = get_app()

    def test_key_computations_never_overlap(self):
        units = [FlashUnit(name=f"ch{i}") for i in range(4)]
        runner = ParallelRunner(units, _datablocks())

        counting = _CountingLock()
        runner.security_lock = counting

        results = runner.run()

        self.assertTrue(
            all(r["result"] == RESULT_PASS for r in results)
        )
        # One Security Access step per channel reached the lock...
        self.assertEqual(counting.acquisitions, 4)
        # ...and no two held it at once.
        self.assertEqual(counting.max_concurrent, 1)

    def test_all_workers_share_one_lock_object(self):
        runner = ParallelRunner(
            [FlashUnit(), FlashUnit()], _datablocks()
        )
        self.assertIsNotNone(runner.security_lock)
        # Shared by construction: _run_unit passes
        # self.security_lock to every FlashWorker.
        seen = []
        original = runner._build_steps

        def spy():
            seen.append(runner.security_lock)
            return original()

        runner._build_steps = spy
        runner.run()
        self.assertEqual(len(set(id(l) for l in seen)), 1)


class TestParallelAbort(unittest.TestCase):

    def setUp(self):
        self.app = get_app()

    def test_abort_all_mid_flash_settles_every_channel(self):
        units = [FlashUnit(name="ch0"), FlashUnit(name="ch1")]
        runner = ParallelRunner(units, _slow_datablocks())

        class AbortingListener(_RecordingListener):
            def on_step_started(self, unit, index, total, description):
                super().on_step_started(unit, index, total, description)
                if "Erase Memory" in description:
                    runner.abort_all()

        listener = AbortingListener()
        runner.listener = listener

        results = runner.run()

        self.assertEqual(len(results), 2)
        for record in results:
            self.assertEqual(record["result"], RESULT_ABORTED)
        # No channel is left behind without a result row.
        self.assertEqual(len(listener.finished), 2)

    def test_abort_before_start_is_honoured(self):
        runner = ParallelRunner(
            [FlashUnit(name="ch0")], _slow_datablocks()
        )
        runner.abort_all()

        results = runner.run()

        self.assertEqual(results[0]["result"], RESULT_ABORTED)


if __name__ == "__main__":
    unittest.main()

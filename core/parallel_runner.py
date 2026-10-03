# ==================================================
# Parallel Runner (headless, concurrent)
# ==================================================
#
# The headless half of gui/parallel_flash.py: flash several
# ECUs at the same time, one per physical CAN channel, sharing
# one firmware and one set of protocol settings — with the
# Security DLL's key computation serialised across the
# concurrent workers by a single shared lock
# (FlashWorker(security_lock=...)), exactly as the GUI does.
#
# Threading, and why it looks different from the GUI's:
#
# * The GUI runs each FlashWorker on a real QThread because it
#   has a Qt event loop on the main thread that must stay
#   responsive, and it needs worker signals delivered *there*
#   to paint progress bars. That machinery is also what made
#   the GIL <-> Qt pooled-mutex lock-order inversion reachable
#   (CLAUDE.md's fifth failure mode), hence gui/worker_teardown.py.
#
# * Headless, there is no event loop and nothing to paint, so
#   plain threading.Thread is used and each worker is
#   constructed INSIDE the thread that runs it. That matters:
#   a signal emitted from a thread the QObject does not live on
#   is delivered *queued*, and with no event loop ever running
#   it is silently dropped — a probe of exactly this pattern
#   received 0 of 3 emissions. Constructing the worker on its
#   own thread makes every emission a direct call on that same
#   thread, so no progress is lost and no cross-thread QObject
#   destruction (the other half of that inversion) ever
#   happens: each worker is born, connected, run and dropped by
#   one thread, under the GIL.
#
# * Listener callbacks therefore run on the worker threads. The
#   runner holds them inside an output lock so two channels can
#   never interleave half a line of stdout.
# ==================================================

import threading

from core.flash_controller import FlashWorker
from core.flash_sequence import (
    build_flash_sequence,
    build_suzuki_slp1_flash_sequence,
)
from core.report import RESULT_ABORTED, RESULT_FAIL, RESULT_PASS


class ParallelRunner:
    """
    Flashes every unit concurrently. run() blocks until all
    channels have finished and returns a list of result dicts
    in the units' original order.
    """

    def __init__(
        self,
        units,
        datablocks,
        sequence="suzuki",
        tester_serial=None,
        security_dll_path=None,
        security_dll_signature="auto",
        security_dll_variant="",
        run_timeout=None,
        bitrate=500000,
        can_fd=False,
        data_bitrate=2000000,
        compression=0x00,
        encryption=0x00,
        listener=None,
        clock=None,
    ):
        self.units = list(units)
        self.datablocks = list(datablocks)
        self.sequence = sequence
        self.tester_serial = tester_serial
        self.security_dll_path = security_dll_path
        self.security_dll_signature = security_dll_signature
        self.security_dll_variant = security_dll_variant
        # Each channel gets the full budget — they run at the
        # same time, so the wall clock is one unit's worth.
        self.run_timeout = run_timeout
        self.bitrate = bitrate
        self.can_fd = can_fd
        self.data_bitrate = data_bitrate
        self.compression = compression
        self.encryption = encryption
        self.listener = listener

        self._clock = clock or self._default_clock

        # The one deliberate piece of cross-worker coordination,
        # same as gui/parallel_flash.py's
        # _parallel_security_lock: an external Security DLL is
        # not guaranteed thread-safe, so all concurrent
        # SecurityAccess key computations queue through this.
        self.security_lock = threading.Lock()

        # Serialises listener callbacks (stdout) across threads.
        self.output_lock = threading.Lock()

        self._workers = {}
        self._workers_lock = threading.Lock()
        self._results = {}
        self._aborting = False

    @staticmethod
    def _default_clock():
        import time
        return time.monotonic()

    # ==========================================
    # Listener plumbing
    # ==========================================

    def _call(self, name, *args):
        """
        Invokes a listener hook while holding the output lock —
        callbacks come from several worker threads at once and
        almost all of them print.
        """

        if self.listener is None:
            return

        hook = getattr(self.listener, name, None)
        if hook is None:
            return

        with self.output_lock:
            hook(*args)

    # ==========================================
    # Sequence
    # ==========================================

    def _build_steps(self):

        if self.sequence == "suzuki":
            return build_suzuki_slp1_flash_sequence(
                self.datablocks,
                tester_serial_number=self.tester_serial,
            )

        return build_flash_sequence(self.datablocks)

    # ==========================================
    # One channel
    # ==========================================

    def _run_unit(self, index, unit):

        started = self._clock()
        steps = self._build_steps()
        total = len(steps)

        worker = FlashWorker(
            steps=steps,
            datablocks=self.datablocks,
            use_virtual=unit.use_virtual,
            security_dll_path=self.security_dll_path,
            security_dll_signature=self.security_dll_signature,
            security_dll_variant=self.security_dll_variant,
            run_timeout=self.run_timeout,
            security_lock=self.security_lock,
            keepalive_functional=(self.sequence == "suzuki"),
            can_channel=unit.channel,
            can_serial=unit.serial,
            can_tx_id=unit.tx_id,
            can_rx_id=unit.rx_id,
            can_bitrate=self.bitrate,
            can_fd=self.can_fd,
            can_data_bitrate=self.data_bitrate,
            functional_id=unit.functional_id,
            download_compression=self.compression,
            download_encrypting=self.encryption,
        )

        with self._workers_lock:
            self._workers[index] = worker
            # abort_all() may have been called while this thread
            # was still building its worker — honour it rather
            # than starting a flash nobody is waiting for.
            already_aborting = self._aborting

        if already_aborting:
            worker.request_abort()

        state = {
            "finished": False,
            "aborted": False,
            "last_message": "",
            "step": 0,
        }

        def on_step_started(description):
            state["step"] += 1
            self._call(
                "on_step_started", unit, state["step"], total,
                description,
            )

        def on_information(message):
            state["last_message"] = message
            self._call("on_information", unit, message)

        worker.step_started.connect(on_step_started)
        worker.information_message.connect(on_information)
        worker.trace_message.connect(
            lambda message: self._call(
                "on_trace_message", unit, message
            )
        )
        worker.trace_row.connect(
            lambda row: self._call("on_trace_row", unit, row)
        )
        worker.segment_progress.connect(
            lambda seg, sent, size: self._call(
                "on_segment_progress", unit, seg, sent, size
            )
        )
        worker.flash_finished.connect(
            lambda: state.update(finished=True)
        )
        worker.flash_aborted.connect(
            lambda: state.update(aborted=True)
        )

        self._call("on_unit_started", index + 1, len(self.units), unit)

        try:
            worker.run()
        except Exception as e:
            # A worker thread must never die with a bare
            # traceback: that would leave this channel without a
            # result row and the run without an exit code to
            # trust. Record it as a failure instead.
            result = RESULT_FAIL
            reason = f"Worker crashed: {e}"
        else:
            if state["finished"]:
                result, reason = RESULT_PASS, ""
            elif self._aborting:
                result = RESULT_ABORTED
                reason = state["last_message"] or "Aborted"
            else:
                result = RESULT_FAIL
                reason = state["last_message"] or "Flash aborted"

        duration = round(self._clock() - started, 1)

        self._results[index] = {
            "unit": unit,
            "name": unit.label,
            "result": result,
            "duration": duration,
            "serial": None,
            "reason": reason,
            "ecu_info": {},
        }

        self._call("on_unit_finished", unit, result, duration, reason)

        # Drop the worker on the thread that created it, so
        # PySide deletes it synchronously here instead of
        # deferring to a thread that will never run an event
        # loop again (see gui/worker_teardown.py for the
        # observed semantics).
        with self._workers_lock:
            self._workers.pop(index, None)
        del worker

    # ==========================================
    # Abort
    # ==========================================

    def abort_all(self):
        """
        Thread-safe: FlashWorker.request_abort() only sets a
        flag, which run() checks between steps. Called from the
        main thread on Ctrl+C, same as Parallel Flash's "Abort
        All" button.
        """

        with self._workers_lock:
            self._aborting = True
            workers = list(self._workers.values())

        for worker in workers:
            worker.request_abort()

    # ==========================================
    # Run every channel
    # ==========================================

    def run(self):

        if not self.datablocks:
            raise ValueError(
                "No firmware loaded — nothing to flash"
            )

        threads = []
        for index, unit in enumerate(self.units):
            thread = threading.Thread(
                target=self._run_unit,
                args=(index, unit),
                name=f"sflash-parallel-{index}",
                daemon=True,
            )
            threads.append(thread)

        for thread in threads:
            thread.start()

        try:
            for thread in threads:
                # Join in bounded slices so a Ctrl+C on the main
                # thread is actually noticed: a bare join()
                # blocks the interpreter's signal handling until
                # the thread ends, which for a stuck flash can
                # be minutes.
                while thread.is_alive():
                    thread.join(timeout=0.2)
        except KeyboardInterrupt:
            self.abort_all()
            for thread in threads:
                thread.join(timeout=30)

        return [
            self._results[i]
            for i in sorted(self._results)
        ]

# ==================================================
# Batch Runner (headless, sequential)
# ==================================================
#
# The headless half of gui/batch_flash.py's BatchFlashMixin:
# flash a series of ECUs one after another on the same tester,
# each one Identified first (so its serial number lands in the
# log/report), recording PASS/FAIL/ABORTED per unit and never
# stopping the series because one unit failed — unless asked to.
#
# Why this isn't BatchFlashMixin reused: that mixin *is* the UI
# state machine. Its flow is driven by button clicks and by
# queued Qt signals arriving back on the GUI thread between
# units (Identify finishes -> slot auto-starts the flash), and
# every step of it reads or writes widgets. Headless, there is
# no event loop to deliver those signals and no widgets to
# read, so the same sequence is expressed here as a plain
# blocking loop. Both drive the identical workers
# (TestConnectionWorker for Identify, FlashWorker for the
# flash) built with the identical arguments, which is what
# keeps the two honest about flashing the same way.
#
# Threading: nothing is moved to another thread. Each worker is
# constructed and run() on the calling thread, so its signals
# reach plain-Python listener callbacks directly (an emit from
# a thread the QObject doesn't live on would be *queued* and,
# with no event loop running, silently dropped — verified
# behaviour, and the reason core/parallel_runner.py constructs
# each worker inside its own thread instead).
# ==================================================

from core.flash_controller import FlashWorker
from core.flash_sequence import (
    build_flash_sequence,
    build_suzuki_slp1_flash_sequence,
)
from core.report import RESULT_ABORTED, RESULT_FAIL, RESULT_PASS
from core.test_connection import TestConnectionWorker

SERIAL_NUMBER_KEY = "ECU Serial Number"


class RunListener:
    """
    No-op listener — the runners call these as they go and
    cli.py overrides the ones it wants to print. Every method is
    optional in practice (the runners use getattr), but having
    them all here documents the full set in one place.
    """

    def on_unit_started(self, index, total, unit): pass

    def on_identify_step(self, unit, message): pass

    def on_identify_result(self, unit, passed, message, info): pass

    def on_step_started(self, unit, index, total, description): pass

    def on_information(self, unit, message): pass

    def on_trace_message(self, unit, message): pass

    def on_trace_row(self, unit, row): pass

    def on_segment_progress(self, unit, seg_index, sent, total): pass

    def on_unit_finished(self, unit, result, duration, reason): pass

    def on_pause(self, next_index, total, unit): pass


def _call(listener, name, *args):
    if listener is None:
        return
    hook = getattr(listener, name, None)
    if hook is not None:
        hook(*args)


class BatchRunner:
    """
    Flashes `units` sequentially. run() blocks until the whole
    series is done and returns a list of result dicts:
    {unit, result, duration, serial, reason}.
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
        identify=True,
        stop_on_fail=False,
        listener=None,
        pause_hook=None,
        clock=None,
    ):
        self.units = list(units)
        self.datablocks = list(datablocks)
        self.sequence = sequence
        self.tester_serial = tester_serial
        self.security_dll_path = security_dll_path
        self.security_dll_signature = security_dll_signature
        self.security_dll_variant = security_dll_variant
        # Per-unit watchdog: each unit gets the full budget, so a
        # 5-unit batch with --timeout 600 allows 600s each, not
        # 120s each. A pipeline sets the limit it expects one ECU
        # to need.
        self.run_timeout = run_timeout
        self.bitrate = bitrate
        self.can_fd = can_fd
        self.data_bitrate = data_bitrate
        self.compression = compression
        self.encryption = encryption
        self.identify = identify
        self.stop_on_fail = stop_on_fail
        self.listener = listener

        # Called between units (cli.py's --pause waits for the
        # operator to swap the ECU). Returning False stops the
        # series — that's how Ctrl+C at the prompt bows out
        # without losing the units already recorded.
        self.pause_hook = pause_hook

        # Injectable so tests don't depend on real elapsed time.
        self._clock = clock or self._default_clock

        self.results = []

    @staticmethod
    def _default_clock():
        import time
        return time.monotonic()

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
    # Identify
    # ==========================================

    def _identify(self, unit):
        """
        Runs the same read-only probe as Batch Flash's Identify
        step / `cli.py test-connection`. Returns
        (passed, message, serial_or_None, ecu_info).
        """

        worker = TestConnectionWorker(
            use_virtual=unit.use_virtual,
            security_dll_path=self.security_dll_path,
            security_dll_signature=self.security_dll_signature,
            security_dll_variant=self.security_dll_variant,
            run_timeout=self.run_timeout,
            functional=(self.sequence == "suzuki"),
            can_channel=unit.channel,
            can_serial=unit.serial,
            can_tx_id=unit.tx_id,
            can_rx_id=unit.rx_id,
            can_bitrate=self.bitrate,
            can_fd=self.can_fd,
            can_data_bitrate=self.data_bitrate,
            functional_id=unit.functional_id,
        )

        outcome = {"passed": False, "message": "", "info": {}}

        worker.step_message.connect(
            lambda message: _call(
                self.listener, "on_identify_step", unit, message
            )
        )
        worker.trace_message.connect(
            lambda message: _call(
                self.listener, "on_trace_message", unit, message
            )
        )
        worker.trace_row.connect(
            lambda row: _call(self.listener, "on_trace_row", unit, row)
        )

        def on_info(info):
            outcome["info"] = dict(info)

        def on_finished(passed, message):
            outcome["passed"] = passed
            outcome["message"] = message

        worker.ecu_info_message.connect(on_info)
        worker.finished.connect(on_finished)

        worker.run()

        serial = outcome["info"].get(SERIAL_NUMBER_KEY) or None
        if serial and serial.startswith("N/A"):
            serial = None

        _call(
            self.listener, "on_identify_result", unit,
            outcome["passed"], outcome["message"], outcome["info"],
        )

        return (
            outcome["passed"], outcome["message"], serial,
            outcome["info"],
        )

    # ==========================================
    # Flash one unit
    # ==========================================

    def _flash_unit(self, unit, steps):
        """
        Returns (result, reason) where result is one of
        RESULT_PASS / RESULT_FAIL / RESULT_ABORTED.
        """

        worker = FlashWorker(
            steps=steps,
            datablocks=self.datablocks,
            use_virtual=unit.use_virtual,
            security_dll_path=self.security_dll_path,
            security_dll_signature=self.security_dll_signature,
            security_dll_variant=self.security_dll_variant,
            run_timeout=self.run_timeout,
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

        state = {
            "finished": False,
            "aborted": False,
            "last_message": "",
            "step": 0,
        }
        total = len(steps)

        def on_step_started(description):
            state["step"] += 1
            _call(
                self.listener, "on_step_started", unit,
                state["step"], total, description,
            )

        def on_information(message):
            state["last_message"] = message
            _call(self.listener, "on_information", unit, message)

        worker.step_started.connect(on_step_started)
        worker.information_message.connect(on_information)
        worker.trace_message.connect(
            lambda message: _call(
                self.listener, "on_trace_message", unit, message
            )
        )
        worker.trace_row.connect(
            lambda row: _call(self.listener, "on_trace_row", unit, row)
        )
        worker.segment_progress.connect(
            lambda seg, sent, size: _call(
                self.listener, "on_segment_progress",
                unit, seg, sent, size,
            )
        )
        worker.flash_finished.connect(
            lambda: state.update(finished=True)
        )
        worker.flash_aborted.connect(
            lambda: state.update(aborted=True)
        )

        try:
            worker.run()
        except KeyboardInterrupt:
            # FlashWorker.run()'s own finally already ran
            # _cleanup(); record the unit as aborted rather than
            # letting the exception lose the units before it.
            return RESULT_ABORTED, "Interrupted by operator"

        if state["finished"]:
            return RESULT_PASS, ""

        reason = state["last_message"] or "Flash aborted"
        return RESULT_FAIL, reason

    # ==========================================
    # Run the series
    # ==========================================

    def run(self):

        total = len(self.units)

        # One empty-steps guard for the whole series, mirroring
        # _start_flash_for_current_ecu()'s: FlashWorker treats 0
        # steps as an instant flash_finished, which would log a
        # PASS row per unit with no work done at all.
        if not self.datablocks:
            raise ValueError(
                "No firmware loaded — nothing to flash"
            )

        for index, unit in enumerate(self.units, 1):

            if index > 1 and self.pause_hook is not None:
                _call(self.listener, "on_pause", index, total, unit)
                if self.pause_hook(index, total, unit) is False:
                    break

            _call(self.listener, "on_unit_started", index, total, unit)

            started = self._clock()
            serial = None
            result = None
            reason = ""

            ecu_info = {}

            if self.identify:
                passed, message, serial, ecu_info = self._identify(unit)
                if not passed:
                    result = RESULT_FAIL
                    reason = message or "Identify failed"

            if result is None:
                # Steps are rebuilt per unit so no two units
                # ever share FlashStep objects, matching how
                # Parallel Flash builds a fresh sequence per
                # panel.
                result, reason = self._flash_unit(
                    unit, self._build_steps()
                )

            duration = round(self._clock() - started, 1)

            record = {
                "unit": unit,
                "name": unit.label,
                "result": result,
                "duration": duration,
                "serial": serial,
                "reason": reason,
                "ecu_info": ecu_info,
            }
            self.results.append(record)

            _call(
                self.listener, "on_unit_finished",
                unit, result, duration, reason,
            )

            if self.stop_on_fail and result != RESULT_PASS:
                break

        return self.results

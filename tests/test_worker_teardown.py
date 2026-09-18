"""
Tests for gui/worker_teardown.py — the main-thread, synchronous
teardown of a finished QThread + its worker that replaced
`worker.finished -> worker.deleteLater` everywhere in gui/
(docs/walkthrough.md Phase 4.116).

Background, in one paragraph: PySide deletes a QObject synchronously
only when the calling thread is the object's own thread. A worker
that still lives on its finished QThread is therefore never freed by
dropping the Python reference (deferred to a dead thread — a leak),
and deleteLater() freed it on the *worker* thread with the GIL
released — where a Python-subclassed QObject's destructor blocks for
the GIL while holding one of Qt's pooled signal/slot mutexes. The
main thread, holding the GIL in moveToThread()/connect() for the
next worker, could hit that same pooled mutex: a lock-order
inversion that showed up as a random deadlock or SIGSEGV/SIGBUS in
Parallel Flash. These tests pin the observed PySide behaviour the
helper relies on, and the guarantee the helper gives.
"""

import gc
import os
import sys
import threading
import unittest
import weakref

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shiboken6
from PySide6.QtCore import QEventLoop, QObject, QThread, QTimer

from tests.qt_test_utils import get_app
from gui.worker_teardown import dispose_worker_thread
from gui.main_window import MainWindow
from parsers.hex_parser import parse_hex_file

SAMPLE_HEX = os.path.join(os.path.dirname(__file__), "sample.hex")


class _Probe(QObject):
    """A Python-subclassed QObject, like every worker in this app."""


def _finished_thread_hosting(obj):
    """Move `obj` to a fresh QThread, run that thread to completion,
    and return the (finished, still-referenced) QThread."""
    thread = QThread()
    obj.moveToThread(thread)
    loop = QEventLoop()
    thread.finished.connect(loop.quit)
    thread.started.connect(thread.quit)
    thread.start()
    loop.exec()
    thread.wait()
    return thread


class TestObservedPySideBehaviour(unittest.TestCase):
    """The facts the helper is built on. If PySide ever changes them,
    these fail first and point at the right file."""

    def setUp(self):
        self.app = get_app()
        gc.collect()

    def _probe(self):
        flag = {"destroyed": False}
        obj = _Probe()
        obj.destroyed.connect(lambda *_: flag.__setitem__("destroyed", True))
        return obj, flag

    def test_dropping_a_worker_that_lives_on_a_finished_thread_leaks(self):
        obj, flag = self._probe()
        thread = _finished_thread_hosting(obj)
        del obj
        self.app.processEvents()
        self.assertFalse(
            flag["destroyed"],
            "PySide now deletes foreign-thread QObjects synchronously — "
            "re-examine gui/worker_teardown.py",
        )
        del thread

    def test_worker_cannot_be_pulled_back_while_its_qthread_exists(self):
        obj, _ = self._probe()
        thread = _finished_thread_hosting(obj)
        obj.moveToThread(self.app.thread())   # Qt refuses + warns
        self.assertIsNot(obj.thread(), self.app.thread())
        del thread

    def test_pull_back_after_qthread_deleted_then_delete_is_synchronous(self):
        obj, flag = self._probe()
        thread = _finished_thread_hosting(obj)
        shiboken6.delete(thread)
        obj.moveToThread(self.app.thread())
        self.assertIs(obj.thread(), self.app.thread())
        del obj
        self.assertTrue(flag["destroyed"], "not deleted synchronously")


class TestDisposeWorkerThread(unittest.TestCase):

    def setUp(self):
        self.app = get_app()

    def test_destroys_both_synchronously_on_the_calling_thread(self):
        flags = {"worker": False, "thread": False}
        worker = _Probe()
        worker.destroyed.connect(lambda *_: flags.__setitem__("worker", True))
        thread = _finished_thread_hosting(worker)
        thread.destroyed.connect(lambda *_: flags.__setitem__("thread", True))

        dispose_worker_thread(thread, worker)

        self.assertTrue(flags["thread"])
        self.assertTrue(flags["worker"], "worker was deferred, not deleted")
        self.assertFalse(shiboken6.isValid(thread))
        self.assertFalse(shiboken6.isValid(worker))

    def test_tolerates_none_and_already_deleted_arguments(self):
        dispose_worker_thread(None, None)
        worker = _Probe()
        thread = _finished_thread_hosting(worker)
        dispose_worker_thread(thread, worker)
        dispose_worker_thread(thread, worker)   # second call is a no-op
        dispose_worker_thread(None, _Probe())   # worker already on main thread


class TestRealFlashWorkerIsDestroyedByCleanup(unittest.TestCase):
    """End to end through flash_button_clicked(): once _cleanup_thread()
    has run, the FlashWorker's C++ object must be gone (no leak) and
    must have died on the main thread (this test thread)."""

    def setUp(self):
        self.app = get_app()
        self.window = MainWindow()
        self.window._loaded_datablocks = [parse_hex_file(SAMPLE_HEX)]

    def test_worker_destroyed_on_main_thread_after_cleanup(self):
        seen = {}
        self.window.flash_button_clicked()
        worker = self.window.worker
        worker.destroyed.connect(
            lambda *_: seen.__setitem__("thread", threading.get_ident())
        )
        wref = weakref.ref(worker)
        del worker

        loop = QEventLoop()
        state = {"elapsed": 0}
        timer = QTimer()
        timer.setInterval(20)

        def tick():
            if self.window.thread is None and self.window.worker is None:
                loop.quit()
            elif state["elapsed"] > 15000:
                loop.quit()
            state["elapsed"] += 20

        timer.timeout.connect(tick)
        timer.start()
        loop.exec()
        timer.stop()

        self.assertIsNone(self.window.thread)
        self.assertEqual(
            seen.get("thread"), threading.get_ident(),
            "worker destroyed on a thread other than the main thread "
            "(or not destroyed at all)",
        )
        gc.collect()
        self.assertIsNone(wref(), "worker wrapper leaked")


if __name__ == "__main__":
    unittest.main()

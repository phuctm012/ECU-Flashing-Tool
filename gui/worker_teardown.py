"""
Deterministic, main-thread teardown of a finished QThread and the
Python-subclassed worker (FlashWorker / TestConnectionWorker /
GitLab worker) that ran on it.

Why this exists — Phase 4.116 (docs/walkthrough.md), a real
intermittent deadlock + SIGSEGV/SIGBUS in Parallel Flash:

PySide only deletes a QObject synchronously when the calling thread
is the object's own thread. Dropping the last Python reference to a
worker that still "lives" on its (already finished) QThread instead
schedules a deferred delete on that dead thread, which never runs —
a silent leak. The previous answer, `worker.finished ->
worker.deleteLater`, ran the C++ destructor *on the worker thread,
inside its event loop, with the GIL released*. Destroying a
Python-subclassed QObject there means `~QObject` holds one of Qt's
pooled signal/slot mutexes while `disconnectNotify` (a virtual
shiboken must look up in Python) blocks waiting for the GIL. Any
thread holding the GIL and touching the same pooled mutex (a
`connect()`, a `moveToThread()`, another synchronous QObject
delete) is then deadlocked against it — Qt's pool is indexed by
object-address hash, so unrelated objects collide at random. That
is the mutex <-> GIL lock-order inversion this module avoids.

The rule that follows: the main thread is the only thread that ever
destroys a QObject, and it does so while it holds the GIL, so the
GIL is never *waited for* under a Qt mutex. Concretely
(observed with PySide 6, tests in tests/test_worker_teardown.py):

1. `thread.wait()` — proves the OS thread is gone.
2. `shiboken6.delete(thread)` — the QThread lives on the main thread,
   so this is a synchronous C++ delete. It also nulls the thread
   pointer in the worker's QThreadData, which is what lets step 3
   happen at all (Qt refuses to move an object that still belongs
   to a live QThread from any thread but that one).
3. `worker.moveToThread(main)` — "pull" the now-orphaned worker
   into the main thread.
4. `shiboken6.delete(worker)` — it lives on the main thread now, so
   this too is synchronous: `~QObject` -> disconnectNotify -> the
   GIL is already ours, no wait, no inversion, no leak.

Corollary for worker code (core/): never call `connect()`,
`disconnect()` or `moveToThread()` from inside `run()` — those take
the pooled mutex while holding the GIL, i.e. they are the other half
of the same inversion. TestConnectionWorker builds its inner
FlashWorker and wires its signals in __init__ (main thread) for
exactly this reason.
"""

import shiboken6
from PySide6.QtCore import QCoreApplication


def dispose_worker_thread(thread, worker):
    """
    Destroy a finished QThread and its worker, synchronously, on
    the calling (main) thread. Call it from the slot connected to
    `thread.finished` — never from a slot connected to the worker's
    own finished/aborted signal (those fire while the worker
    thread is still running; see CLAUDE.md "Threading model").

    Either argument may be None or already invalidated; both are
    safe to pass. After the call the caller must drop its own
    references (they are now invalid wrappers).
    """

    if thread is not None and shiboken6.isValid(thread):
        thread.wait()
        shiboken6.delete(thread)

    if worker is None or not shiboken6.isValid(worker):
        return

    app = QCoreApplication.instance()
    if app is not None and worker.thread() is not app.thread():
        worker.moveToThread(app.thread())

    shiboken6.delete(worker)

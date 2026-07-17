"""Qt-free Signal / QThread shims (used when PySide6 is absent).

PySide6 is present in this test environment, so these exercise the shim classes
directly — the fallback path the orchestrator takes on a headless server.
"""
import threading
import time

from app.eventbus import QThread, Signal, _BoundSignal


# ---- Signal descriptor ------------------------------------------------------

class _Emitter:
    ready = Signal(dict)
    done = Signal(str, int)


def test_signal_connect_and_emit():
    e = _Emitter()
    seen = []
    e.ready.connect(lambda p: seen.append(p))
    e.ready.emit({"x": 1})
    e.ready.emit({"x": 2})
    assert seen == [{"x": 1}, {"x": 2}]


def test_signal_multiple_callbacks_and_args():
    e = _Emitter()
    a, b = [], []
    e.done.connect(lambda s, n: a.append((s, n)))
    e.done.connect(lambda s, n: b.append(s))
    e.done.emit("ok", 5)
    assert a == [("ok", 5)] and b == ["ok"]


def test_signal_is_per_instance():
    e1, e2 = _Emitter(), _Emitter()
    hits = []
    e1.ready.connect(lambda p: hits.append(p))
    e2.ready.emit({"other": True})          # must not reach e1's callback
    assert hits == []
    e1.ready.emit({"mine": True})
    assert hits == [{"mine": True}]


def test_signal_ignores_connection_type_arg():
    # Qt callers pass a connection-type constant as a 2nd arg; the shim ignores it
    e = _Emitter()
    seen = []
    e.ready.connect(lambda p: seen.append(p), "DirectConnection-sentinel")
    e.ready.emit({"ok": 1})
    assert seen == [{"ok": 1}]


def test_signal_disconnect():
    sig = _BoundSignal()
    seen = []
    cb = lambda v: seen.append(v)
    sig.connect(cb)
    sig.emit(1)
    sig.disconnect(cb)
    sig.emit(2)
    assert seen == [1]
    sig.connect(lambda v: seen.append(v * 10))
    sig.disconnect()                        # drop all
    sig.emit(3)
    assert seen == [1]


def test_class_attribute_access_returns_descriptor():
    assert isinstance(_Emitter.ready, Signal)   # class access, not an instance


# ---- QThread shim -----------------------------------------------------------

class _Worker(QThread):
    finished = Signal(str)

    def __init__(self):
        super().__init__()
        self.ran = False

    def run(self):
        time.sleep(0.05)
        self.ran = True
        self.finished.emit("done")


def test_qthread_runs_in_background_and_waits():
    w = _Worker()
    got = []
    w.finished.connect(lambda s: got.append(s))
    assert w.isRunning() is False
    w.start()
    assert w.wait(5000) is True             # joins within the timeout
    assert w.ran is True
    assert got == ["done"]
    assert w.isRunning() is False


def test_qthread_wait_before_start_is_noop():
    w = _Worker()
    assert w.wait(100) is True              # nothing started → returns True


def test_qthread_isrunning_true_while_busy():
    started = threading.Event()

    class Slow(QThread):
        def run(self):
            started.set()
            time.sleep(0.3)

    s = Slow()
    s.start()
    assert started.wait(2.0)
    assert s.isRunning() is True
    assert s.wait(5000) is True
    assert s.isRunning() is False


def test_qthread_run_directly_synchronous():
    # calling run() directly (as headless `dispatcher run` does) executes inline
    w = _Worker()
    got = []
    w.finished.connect(lambda s: got.append(s))
    w.run()
    assert w.ran and got == ["done"]
    assert w.isRunning() is False           # never spawned a thread

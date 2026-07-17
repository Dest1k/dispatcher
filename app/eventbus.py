"""Qt-free Signal / QThread shims so the orchestration engine runs without
PySide6 (e.g. on a headless server).

The orchestrator imports `QThread` and `Signal` from PySide6 when it is
available — the GUI relies on Qt's queued cross-thread signal delivery — and
falls back to these plain-Python equivalents otherwise. The shims provide the
small subset the orchestrator and its callers use:

  * `Signal(...)` — a descriptor that yields a per-instance `_BoundSignal`
    with `.connect(cb, *ignored)` and `.emit(*args)` (synchronous, like Qt's
    DirectConnection).
  * `QThread` — `start()` (background `threading.Thread`), `run()` (override),
    `wait(msecs=None)`, `isRunning()`.

These are intentionally minimal: they are correct for headless, single-process
use where signal callbacks run synchronously in the emitting thread. The GUI
must use the real PySide6 classes.
"""
from __future__ import annotations

import threading


class _BoundSignal:
    """Per-instance signal: synchronous callbacks, thread-safe registration."""

    def __init__(self) -> None:
        self._callbacks: list = []
        self._lock = threading.Lock()

    def connect(self, callback, *_ignored) -> None:
        # extra positional args (e.g. a Qt connection-type constant) are ignored
        with self._lock:
            self._callbacks.append(callback)

    def disconnect(self, callback=None) -> None:
        with self._lock:
            if callback is None:
                self._callbacks.clear()
            elif callback in self._callbacks:
                self._callbacks.remove(callback)

    def emit(self, *args) -> None:
        with self._lock:
            callbacks = list(self._callbacks)
        for cb in callbacks:
            cb(*args)


class Signal:
    """Descriptor mirroring PySide6's `Signal` for class-attribute use.

    `x = Signal(dict)` on a class yields a distinct `_BoundSignal` per instance,
    created lazily and cached on the instance.
    """

    def __init__(self, *types) -> None:
        self._types = types
        self._name = f"_sig_{id(self)}"

    def __set_name__(self, owner, name) -> None:
        self._name = f"__signal_{name}"

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        store = obj.__dict__
        sig = store.get(self._name)
        if sig is None:
            sig = _BoundSignal()
            store[self._name] = sig
        return sig


class QThread:
    """Minimal QThread-compatible runner backed by threading.Thread."""

    def __init__(self, *args, **kwargs) -> None:
        self.__thread: threading.Thread | None = None

    def start(self) -> None:
        self.__thread = threading.Thread(target=self.run, daemon=True)
        self.__thread.start()

    def run(self) -> None:  # pragma: no cover - overridden by subclasses
        raise NotImplementedError

    def wait(self, msecs: int | None = None) -> bool:
        t = self.__thread
        if t is not None:
            t.join(None if msecs is None else msecs / 1000.0)
            return not t.is_alive()
        return True

    def isRunning(self) -> bool:      # noqa: N802 (Qt-compatible name)
        t = self.__thread
        return bool(t and t.is_alive())

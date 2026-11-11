"""Optional timing trace of the live loop, for measuring the running app on a real game.

Set TILELENS_TRACE=/path/to/trace.jsonl and every capture, read, stable position, published
result and overlay paint is appended as one JSON line with a monotonic timestamp. Without the
variable emit() is a single attribute test. benchmarks/analyze_trace.py turns a trace into
latency statistics.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path


class Trace:
    def __init__(self, path: Path):
        self._file = path.open("a", buffering=1)
        self._lock = threading.Lock()

    def emit(self, ev: str, **fields) -> None:
        line = json.dumps({"ev": ev, "t": round(time.monotonic(), 6), **fields}, default=str)
        with self._lock:
            self._file.write(line + "\n")

    def close(self) -> None:
        with self._lock:
            self._file.close()


_trace: Trace | None = None
_checked = False


def _open() -> Trace | None:
    global _trace, _checked
    if not _checked:
        _checked = True
        path = os.environ.get("TILELENS_TRACE")
        if path:
            try:
                _trace = Trace(Path(path))
                _trace.emit("start", pid=os.getpid(), wall=time.time())
            except OSError:
                _trace = None
    return _trace


def emit(ev: str, **fields) -> None:
    trace = _trace if _checked else _open()
    if trace is not None:
        trace.emit(ev, **fields)


def enabled() -> bool:
    return (_trace if _checked else _open()) is not None


__all__ = ["Trace", "emit", "enabled"]

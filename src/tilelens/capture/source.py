from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

import numpy as np

from tilelens.platforms.base import CaptureError, PlatformAdapter, Region


class FrameSource(Protocol):
    def grab(self) -> np.ndarray | None: ...


class ScreenSource:
    """Captures a screen region. Frames stay in memory only."""

    def __init__(self, adapter: PlatformAdapter, region: Region):
        self.adapter = adapter
        self.region = region
        self.last_error: str | None = None

    def grab(self) -> np.ndarray | None:
        try:
            frame = self.adapter.capture(self.region)
        except CaptureError as exc:
            self.last_error = str(exc)
            return None
        self.last_error = None
        return frame


class CallableSource:
    """Frames from a function, for replays and the built-in fake game."""

    def __init__(self, fn: Callable[[], np.ndarray | None]):
        self.fn = fn

    def grab(self) -> np.ndarray | None:
        return self.fn()


class ReplaySource:
    """Plays back a fixed list of frames, holding the last one."""

    def __init__(self, frames: list[np.ndarray | None]):
        self.frames = list(frames)
        self.index = 0

    def grab(self) -> np.ndarray | None:
        if not self.frames:
            return None
        frame = self.frames[min(self.index, len(self.frames) - 1)]
        self.index += 1
        return frame


class PermissionGate:
    """Stops capturing, with a clear reason, while the OS denies screen capture."""

    MISSING = "Screen Recording permission is missing"

    def __init__(self, inner, adapter: PlatformAdapter, clock=None, recheck: float = 2.0):
        import time

        self.inner = inner
        self.adapter = adapter
        self.clock = clock or time.monotonic
        self.recheck = recheck
        self._checked_at = -1e9
        self._allowed: bool | None = None
        self.last_error: str | None = None

    def allowed(self) -> bool:
        now = self.clock()
        if now - self._checked_at >= self.recheck:
            self._checked_at = now
            self._allowed = self.adapter.screen_permission()
        return self._allowed is not False

    def grab(self) -> np.ndarray | None:
        if not self.allowed():
            self.last_error = self.MISSING
            return None
        frame = self.inner.grab()
        self.last_error = getattr(self.inner, "last_error", None)
        return frame

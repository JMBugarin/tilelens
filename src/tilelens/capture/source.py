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

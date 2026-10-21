from __future__ import annotations

import threading

import numpy as np

from tilelens.platforms.base import CaptureError, Region


class MssGrabber:
    """mss handles are per-thread, so keep one per thread."""

    def __init__(self):
        self._local = threading.local()

    def _handle(self):
        h = getattr(self._local, "handle", None)
        if h is None:
            import mss

            h = mss.MSS()
            self._local.handle = h
        return h

    def grab(self, region: Region) -> np.ndarray:
        if region.width <= 0 or region.height <= 0:
            raise CaptureError("empty capture region")
        try:
            shot = self._handle().grab(
                {
                    "left": region.x,
                    "top": region.y,
                    "width": region.width,
                    "height": region.height,
                }
            )
        except Exception as exc:
            raise CaptureError(str(exc)) from exc
        return np.ascontiguousarray(np.asarray(shot)[:, :, :3])

    def close(self) -> None:
        h = getattr(self._local, "handle", None)
        if h is not None:
            h.close()
            self._local.handle = None

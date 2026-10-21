from __future__ import annotations

import cv2
import numpy as np

Rect = tuple[int, int, int, int]


class ChangeDetector:
    """Cheap pixel-change test on the board and rack, ignoring masked areas.

    Only decides whether a frame is worth reading again; what counts as a meaningful change
    is decided later from the recognized content.
    """

    def __init__(self, regions: list[Rect], masks: list[Rect] | None = None, threshold=6.0):
        self.regions = list(regions)
        self.masks = list(masks or [])
        self.threshold = threshold
        self._prev: np.ndarray | None = None
        self._shape: tuple[int, int] | None = None
        self._weight: np.ndarray | None = None

    def reset(self) -> None:
        self._prev = None

    def _build_weight(self, h: int, w: int) -> np.ndarray:
        weight = np.zeros((h, w), np.float32)
        for x, y, rw, rh in self.regions:
            weight[max(0, y) : max(0, y + rh), max(0, x) : max(0, x + rw)] = 1
        for x, y, rw, rh in self.masks:
            weight[max(0, y) : max(0, y + rh), max(0, x) : max(0, x + rw)] = 0
        return cv2.resize(weight, (w // 4 or 1, h // 4 or 1), interpolation=cv2.INTER_AREA) > 0.5

    def update(self, frame: np.ndarray) -> bool:
        """Return True if the watched pixels changed since the previous call."""
        h, w = frame.shape[:2]
        if self._shape != (h, w):
            self._shape = (h, w)
            self._weight = self._build_weight(h, w)
            self._prev = None
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (w // 4 or 1, h // 4 or 1), interpolation=cv2.INTER_AREA)
        small = small.astype(np.int16)
        prev, self._prev = self._prev, small
        if prev is None:
            return True
        if not self._weight.any():
            return False
        diff = np.abs(small - prev)[self._weight]
        return bool(diff.max() > self.threshold * 3 or diff.mean() > self.threshold * 0.05)

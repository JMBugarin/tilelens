from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np


class UnsupportedPlatform(RuntimeError):
    pass


class CaptureError(RuntimeError):
    pass


@dataclass(frozen=True)
class Region:
    """A rectangle in screen points (logical pixels), origin at the top-left of the main display."""

    x: int
    y: int
    width: int
    height: int

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}

    @classmethod
    def from_dict(cls, d: dict) -> Region:
        return cls(int(d["x"]), int(d["y"]), int(d["width"]), int(d["height"]))


class PlatformAdapter(ABC):
    """Everything that touches the operating system lives behind this interface."""

    name = "base"

    @abstractmethod
    def capture(self, region: Region) -> np.ndarray:
        """Return the region as a BGR image at the display's physical resolution."""

    @abstractmethod
    def screen_permission(self) -> bool | None:
        """True if screen capture is allowed, False if denied, None if unknown."""

    def request_permission(self) -> None:  # noqa: B027
        """Ask the OS to show its permission prompt, where one exists."""

    def permission_help(self) -> str:
        return ""

    def exclude_from_capture(self, win_id: int) -> bool:
        """Hide a native window from screen capture. Returns False if not possible."""
        return False

    def include_in_capture(self, win_id: int) -> None:  # noqa: B027
        """Capture one of this process's own windows anyway (the built-in fake game)."""

    def show_on_all_spaces(self, win_id: int) -> bool:
        """Keep a window visible over full-screen apps, where the OS supports it."""
        return False

    def close(self) -> None:  # noqa: B027
        pass

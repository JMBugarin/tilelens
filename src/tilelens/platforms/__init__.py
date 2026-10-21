from __future__ import annotations

import sys

from tilelens.platforms.base import (
    CaptureError,
    PlatformAdapter,
    Region,
    UnsupportedPlatform,
)


class GenericAdapter(PlatformAdapter):
    """Plain mss capture for other desktops. Untested; no permission or overlay handling."""

    name = "generic"

    def __init__(self):
        from tilelens.platforms.mss_capture import MssGrabber

        self._grabber = MssGrabber()

    def capture(self, region):
        return self._grabber.grab(region)

    def screen_permission(self):
        return None

    def close(self):
        self._grabber.close()


def get_adapter(platform: str | None = None) -> PlatformAdapter:
    platform = platform or sys.platform
    if platform == "darwin":
        from tilelens.platforms.macos import MacAdapter

        return MacAdapter()
    if platform.startswith("win"):
        from tilelens.platforms.windows import WindowsAdapter

        return WindowsAdapter()
    return GenericAdapter()


__all__ = [
    "CaptureError",
    "GenericAdapter",
    "PlatformAdapter",
    "Region",
    "UnsupportedPlatform",
    "get_adapter",
]

from __future__ import annotations

from tilelens.platforms.base import PlatformAdapter, UnsupportedPlatform

MESSAGE = (
    "Windows is not supported yet. TileLens has only been built and tested on macOS; "
    "capture, overlay and permission handling for Windows have not been written."
)


class WindowsAdapter(PlatformAdapter):
    name = "windows"

    def __init__(self):
        raise UnsupportedPlatform(MESSAGE)

    def capture(self, region):
        raise UnsupportedPlatform(MESSAGE)

    def screen_permission(self):
        raise UnsupportedPlatform(MESSAGE)

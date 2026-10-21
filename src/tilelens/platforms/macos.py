from __future__ import annotations

import numpy as np

from tilelens.platforms.base import PlatformAdapter, Region
from tilelens.platforms.mss_capture import MssGrabber

PERMISSION_HELP = (
    "TileLens needs the Screen Recording permission to read the board.\n"
    "Open System Settings > Privacy & Security > Screen & System Audio Recording, turn on the "
    "app that launched TileLens (for example Terminal, iTerm or TileLens.app), then quit and "
    "reopen that app. Without it, captures contain only the desktop wallpaper."
)


class MacAdapter(PlatformAdapter):
    name = "macos"

    def __init__(self):
        self._grabber = MssGrabber()

    def capture(self, region: Region) -> np.ndarray:
        return self._grabber.grab(region)

    def screen_permission(self) -> bool | None:
        try:
            import Quartz

            return bool(Quartz.CGPreflightScreenCaptureAccess())
        except Exception:
            return None

    def request_permission(self) -> None:
        try:
            import Quartz

            Quartz.CGRequestScreenCaptureAccess()
        except Exception:
            pass

    def permission_help(self) -> str:
        return PERMISSION_HELP

    def exclude_from_capture(self, win_id: int) -> bool:
        """Set NSWindowSharingNone on the NSWindow behind a Qt widget's winId()."""
        try:
            import objc
            from AppKit import NSWindowSharingNone

            view = objc.objc_object(c_void_p=int(win_id))
            window = view.window()
            if window is None:
                return False
            window.setSharingType_(NSWindowSharingNone)
            return window.sharingType() == NSWindowSharingNone
        except Exception:
            return False

    def close(self) -> None:
        self._grabber.close()

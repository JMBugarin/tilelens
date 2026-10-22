from __future__ import annotations

import numpy as np

from tilelens.platforms.base import CaptureError, PlatformAdapter, Region
from tilelens.platforms.mss_capture import MssGrabber

PERMISSION_HELP = (
    "TileLens needs the Screen Recording permission to read the board.\n"
    "Open System Settings > Privacy & Security > Screen & System Audio Recording, turn on the "
    "app that launched TileLens (for example Terminal, iTerm or TileLens.app), then quit and "
    "reopen that app. Without it, captures contain only the desktop wallpaper."
)


def quartz_capture(region: Region) -> np.ndarray:
    import Quartz

    if region.width <= 0 or region.height <= 0:
        raise CaptureError("empty capture region")
    rect = Quartz.CGRectMake(region.x, region.y, region.width, region.height)
    options = Quartz.kCGWindowImageBoundsIgnoreFraming | Quartz.kCGWindowImageShouldBeOpaque
    image = Quartz.CGWindowListCreateImage(
        rect, Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID, options
    )
    if image is None:
        raise CaptureError("screen capture failed")
    width, height = Quartz.CGImageGetWidth(image), Quartz.CGImageGetHeight(image)
    row = Quartz.CGImageGetBytesPerRow(image)
    data = Quartz.CGDataProviderCopyData(Quartz.CGImageGetDataProvider(image))
    pixels = np.frombuffer(data, np.uint8).reshape(height, row // 4, 4)[:, :width, :3]
    return np.ascontiguousarray(pixels)


class MacAdapter(PlatformAdapter):
    name = "macos"

    def __init__(self):
        self._grabber = MssGrabber()

    def capture(self, region: Region) -> np.ndarray:
        """Capture at the display's native resolution (2x on Retina) when Quartz is available."""
        try:
            return quartz_capture(region)
        except (ImportError, CaptureError):
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

    def show_on_all_spaces(self, win_id: int) -> bool:
        """Let a window appear over full-screen apps and on every Space."""
        try:
            import objc
            from AppKit import (
                NSStatusWindowLevel,
                NSWindowCollectionBehaviorCanJoinAllSpaces,
                NSWindowCollectionBehaviorFullScreenAuxiliary,
                NSWindowCollectionBehaviorStationary,
            )

            view = objc.objc_object(c_void_p=int(win_id))
            window = view.window()
            if window is None:
                return False
            window.setCollectionBehavior_(
                NSWindowCollectionBehaviorCanJoinAllSpaces
                | NSWindowCollectionBehaviorFullScreenAuxiliary
                | NSWindowCollectionBehaviorStationary
            )
            window.setLevel_(NSStatusWindowLevel)
            return True
        except Exception:
            return False

    def close(self) -> None:
        self._grabber.close()

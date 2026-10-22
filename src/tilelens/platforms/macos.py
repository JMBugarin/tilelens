from __future__ import annotations

import os

import numpy as np

from tilelens.platforms.base import CaptureError, PlatformAdapter, Region
from tilelens.platforms.mss_capture import MssGrabber

PERMISSION_HELP = (
    "TileLens needs the Screen Recording permission to read the board.\n"
    "Open System Settings > Privacy & Security > Screen & System Audio Recording, turn on the "
    "app that launched TileLens (for example Terminal, iTerm or TileLens.app), then quit and "
    "reopen that app. Without it, captures contain only the desktop wallpaper."
)


def other_windows(keep: set[int] = frozenset()) -> list[int]:
    """On-screen windows, front to back, that do not belong to this process (or are kept)."""
    import Quartz

    info = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID
    )
    own = os.getpid()
    return [
        int(w["kCGWindowNumber"])
        for w in info or ()
        if int(w["kCGWindowOwnerPID"]) != own or int(w["kCGWindowNumber"]) in keep
    ]


def quartz_capture(region: Region, keep: set[int] = frozenset()) -> np.ndarray:
    """Capture what is on screen without any TileLens window.

    The overlay, panel and dialogs are left out by building the image from every other
    window, which does not depend on a window's sharing setting being honoured.
    """
    import Quartz

    if region.width <= 0 or region.height <= 0:
        raise CaptureError("empty capture region")
    rect = Quartz.CGRectMake(region.x, region.y, region.width, region.height)
    options = Quartz.kCGWindowImageBoundsIgnoreFraming | Quartz.kCGWindowImageShouldBeOpaque
    ids = other_windows(keep)
    windows = Quartz.CFArrayCreate(None, ids, len(ids), None)
    image = Quartz.CGWindowListCreateImageFromArray(rect, windows, options)
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
        self._keep: set[int] = set()

    def capture(self, region: Region) -> np.ndarray:
        """Capture at the display's native resolution (2x on Retina) when Quartz is available."""
        try:
            return quartz_capture(region, self._keep)
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

    def include_in_capture(self, win_id: int) -> None:
        try:
            import objc

            window = objc.objc_object(c_void_p=int(win_id)).window()
            if window is not None:
                self._keep.add(int(window.windowNumber()))
        except Exception:
            pass

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

from __future__ import annotations

import contextlib
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


BACKDROP_ID = "tilelens.backdrop"
MATERIAL = "HUDWindow"
BLUR_ALPHA = 0.55
_MIN_POINTER = 0x10000  # Qt's offscreen platform hands out small counters, not NSView pointers


def _native_window(win_id: int):
    """The NSWindow behind a Qt winId(), or None for an id that cannot be an NSView pointer."""
    win_id = int(win_id)
    if win_id < _MIN_POINTER or win_id % 16:
        return None
    import objc

    return objc.objc_object(c_void_p=win_id).window()


def _frame_view(window):
    content = window.contentView()
    return content, (content.superview() if content is not None else None)


def _find_backdrop(frame_view):
    for view in frame_view.subviews() or ():
        if view.identifier() == BACKDROP_ID:
            return view
    return None


def _remove(frame_view) -> bool:
    effect = _find_backdrop(frame_view) if frame_view is not None else None
    if effect is None:
        return False
    effect.removeFromSuperview()
    return True


def apply_backdrop(win_id: int, radius: float, dark: bool, inset: float = 0.0) -> bool:
    """Put an NSVisualEffectView behind the Qt view so the window blurs what is behind it.

    The effect view is a sibling below the content view in the window's frame view; a
    subview of the Qt view would be drawn over Qt's own painting. Its layer carries the
    corner radius, so the blur takes the rounded shape. A False return leaves no effect view
    behind, so the caller's solid fallback is not drawn over a stale blur.
    """
    frame_view = None
    try:
        import AppKit
        from AppKit import (
            NSAppearance,
            NSAppearanceNameAqua,
            NSAppearanceNameDarkAqua,
            NSColor,
            NSInsetRect,
            NSViewHeightSizable,
            NSViewWidthSizable,
            NSVisualEffectBlendingModeBehindWindow,
            NSVisualEffectStateActive,
            NSVisualEffectView,
            NSWindowBelow,
        )

        window = _native_window(win_id)
        if window is None:
            return False
        content, frame_view = _frame_view(window)
        if frame_view is None:
            return False
        frame = NSInsetRect(content.frame(), max(0.0, inset), max(0.0, inset))
        if frame.size.width <= 0 or frame.size.height <= 0:
            _remove(frame_view)
            return False
        radius = max(0.0, min(float(radius), frame.size.width / 2, frame.size.height / 2))

        window.setOpaque_(False)
        window.setBackgroundColor_(NSColor.clearColor())
        effect = _find_backdrop(frame_view)
        fresh = effect is None
        if fresh:
            effect = NSVisualEffectView.alloc().initWithFrame_(frame)
            effect.setIdentifier_(BACKDROP_ID)
            effect.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
            effect.setState_(NSVisualEffectStateActive)
            if hasattr(effect, "setAlphaValue_"):
                effect.setAlphaValue_(BLUR_ALPHA)
            effect.setMaterial_(getattr(AppKit, f"NSVisualEffectMaterial{MATERIAL}"))
            effect.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
            effect.setWantsLayer_(True)
        else:
            effect.setFrame_(frame)
        effect.setAppearance_(
            NSAppearance.appearanceNamed_(
                NSAppearanceNameDarkAqua if dark else NSAppearanceNameAqua
            )
        )
        layer = effect.layer()
        layer.setCornerRadius_(radius)
        layer.setMasksToBounds_(True)
        with contextlib.suppress(Exception):
            layer.setCornerCurve_("continuous")
        if fresh:
            frame_view.addSubview_positioned_relativeTo_(effect, NSWindowBelow, content)
        return True
    except Exception:
        with contextlib.suppress(Exception):
            _remove(frame_view)
        return False


def clear_backdrop(win_id: int) -> bool:
    try:
        window = _native_window(win_id)
        if window is None:
            return False
        return _remove(_frame_view(window)[1])
    except Exception:
        return False


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
            from AppKit import NSWindowSharingNone

            window = _native_window(win_id)
            if window is None:
                return False
            window.setSharingType_(NSWindowSharingNone)
            return window.sharingType() == NSWindowSharingNone
        except Exception:
            return False

    def include_in_capture(self, win_id: int) -> None:
        try:
            window = _native_window(win_id)
            if window is not None:
                self._keep.add(int(window.windowNumber()))
        except Exception:
            pass

    def show_on_all_spaces(self, win_id: int) -> bool:
        """Let a window appear over another app's full-screen Space, on every Space.

        Qt's tool windows are NSPanels without the non-activating style, and macOS keeps those
        out of other apps' full-screen Spaces however their collection behavior is set.
        """
        try:
            from AppKit import (
                NSStatusWindowLevel,
                NSWindowCollectionBehaviorCanJoinAllSpaces,
                NSWindowCollectionBehaviorFullScreenAuxiliary,
                NSWindowStyleMaskNonactivatingPanel,
            )

            window = _native_window(win_id)
            if window is None:
                return False
            if hasattr(window, "setFloatingPanel_"):
                window.setStyleMask_(window.styleMask() | NSWindowStyleMaskNonactivatingPanel)
                window.setFloatingPanel_(True)
            window.setCollectionBehavior_(
                NSWindowCollectionBehaviorCanJoinAllSpaces
                | NSWindowCollectionBehaviorFullScreenAuxiliary
            )
            window.setLevel_(NSStatusWindowLevel)
            window.setHidesOnDeactivate_(False)
            return True
        except Exception:
            return False

    def apply_backdrop(self, win_id: int, radius: float, dark: bool, inset: float = 0.0) -> bool:
        return apply_backdrop(win_id, radius, dark, inset)

    def clear_backdrop(self, win_id: int) -> bool:
        return clear_backdrop(win_id)

    def close(self) -> None:
        self._grabber.close()

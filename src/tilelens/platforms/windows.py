from __future__ import annotations

import ctypes
import functools
import math
import sys
from ctypes import addressof, byref, c_int, c_long, c_size_t, c_uint, c_void_p, sizeof

from tilelens.platforms.base import PlatformAdapter, UnsupportedPlatform

MESSAGE = (
    "Windows is not supported yet. TileLens has only been built and tested on macOS; "
    "capture, overlay and permission handling for Windows have not been written."
)

BUILD_WINDOWS_10 = 10240
BUILD_ACRYLIC = 17134  # Windows 10 1803: acrylic blur-behind exists
BUILD_SYSTEM_BACKDROP = 22621  # Windows 11 22H2: DWMWA_SYSTEMBACKDROP_TYPE exists

DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_SYSTEMBACKDROP_TYPE = 38
DWMWCP_DONOTROUND = 1
DWMWCP_ROUND = 2
DWMSBT_NONE = 1
DWMSBT_TRANSIENTWINDOW = 3  # the acrylic material
DWM_CORNER_RADIUS = 8  # what DWMWCP_ROUND draws, in device-independent pixels

WCA_ACCENT_POLICY = 19
ACCENT_DISABLED = 0
ACCENT_ENABLE_BLURBEHIND = 3
ACCENT_ENABLE_ACRYLICBLURBEHIND = 4

_applied: set[int] = set()


class _AccentPolicy(ctypes.Structure):
    _fields_ = [
        ("AccentState", c_int),
        ("AccentFlags", c_int),
        ("GradientColor", c_uint),
        ("AnimationId", c_int),
    ]


class _CompositionData(ctypes.Structure):
    _fields_ = [("Attribute", c_int), ("Data", c_void_p), ("SizeOfData", c_size_t)]


class _Margins(ctypes.Structure):
    _fields_ = [("left", c_int), ("right", c_int), ("top", c_int), ("bottom", c_int)]


class _Rect(ctypes.Structure):
    _fields_ = [("left", c_long), ("top", c_long), ("right", c_long), ("bottom", c_long)]


class _Api:
    """The few user32/dwmapi/gdi32 calls the backdrop needs, with 64-bit safe signatures."""

    def __init__(self):
        self.dwm = ctypes.WinDLL("dwmapi")
        self.user32 = ctypes.WinDLL("user32")
        self.gdi32 = ctypes.WinDLL("gdi32")
        self.dwm.DwmSetWindowAttribute.argtypes = [c_void_p, c_uint, c_void_p, c_uint]
        self.dwm.DwmExtendFrameIntoClientArea.argtypes = [c_void_p, ctypes.POINTER(_Margins)]
        self.user32.SetWindowCompositionAttribute.argtypes = [
            c_void_p,
            ctypes.POINTER(_CompositionData),
        ]
        self.user32.GetClientRect.argtypes = [c_void_p, ctypes.POINTER(_Rect)]
        self.user32.SetWindowRgn.argtypes = [c_void_p, c_void_p, c_int]
        self.user32.GetDpiForWindow.argtypes = [c_void_p]
        self.user32.GetDpiForWindow.restype = c_uint
        self.gdi32.CreateRoundRectRgn.argtypes = [c_int] * 6
        self.gdi32.CreateRoundRectRgn.restype = c_void_p
        self.gdi32.DeleteObject.argtypes = [c_void_p]

    def dwm_attribute(self, hwnd: int, attribute: int, value: int) -> bool:
        data = c_int(value)
        return self.dwm.DwmSetWindowAttribute(hwnd, attribute, byref(data), sizeof(data)) == 0

    def extend_frame(self, hwnd: int, margin: int) -> bool:
        margins = _Margins(margin, margin, margin, margin)
        return self.dwm.DwmExtendFrameIntoClientArea(hwnd, byref(margins)) == 0

    def accent(self, hwnd: int, state: int, flags: int, color: int) -> bool:
        policy = _AccentPolicy(state, flags, color, 0)
        data = _CompositionData(WCA_ACCENT_POLICY, addressof(policy), sizeof(policy))
        return bool(self.user32.SetWindowCompositionAttribute(hwnd, byref(data)))

    def client_size(self, hwnd: int) -> tuple[int, int] | None:
        rect = _Rect()
        if not self.user32.GetClientRect(hwnd, byref(rect)):
            return None
        return int(rect.right - rect.left), int(rect.bottom - rect.top)

    def dpi(self, hwnd: int) -> int:
        return int(self.user32.GetDpiForWindow(hwnd) or 96)

    def round_region(self, hwnd: int, box: tuple[int, int, int, int], diameter: int) -> bool:
        region = self.gdi32.CreateRoundRectRgn(*box, diameter, diameter)
        if not region:
            return False
        if self.user32.SetWindowRgn(hwnd, region, True):
            return True
        self.gdi32.DeleteObject(region)  # the system owns it only after a successful call
        return False

    def clear_region(self, hwnd: int) -> bool:
        return bool(self.user32.SetWindowRgn(hwnd, None, True))


@functools.cache
def _load() -> _Api:
    return _Api()


def _build() -> int:
    return int(sys.getwindowsversion().build)


def _tint(dark: bool) -> int:
    alpha, (r, g, b) = (0x38, (18, 22, 36)) if dark else (0x38, (245, 247, 252))
    return alpha << 24 | b << 16 | g << 8 | r


def _clip(api: _Api, hwnd: int, radius: float, inset: float, native_corners: bool) -> bool:
    """Cut the window to the rounded glass shape when the system's own corners are not it."""
    exact = native_corners and inset <= 0 and abs(radius - DWM_CORNER_RADIUS) <= 1
    if exact or (radius <= 0 and inset <= 0):
        api.clear_region(hwnd)
        return True
    size = api.client_size(hwnd)
    if size is None:
        return False
    scale = api.dpi(hwnd) / 96
    margin = round(inset * scale)
    width, height = size
    if width - 2 * margin <= 0 or height - 2 * margin <= 0:
        return False
    box = (margin, margin, width - margin + 1, height - margin + 1)
    diameter = min(max(0, round(2 * radius * scale)), width - 2 * margin, height - 2 * margin)
    return api.round_region(hwnd, box, diameter)


def _enable(api: _Api, hwnd: int, radius: float, dark: bool, inset: float, build: int) -> bool:
    modern = build >= BUILD_SYSTEM_BACKDROP
    if modern:
        api.dwm_attribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, int(dark))
        corners = DWMWCP_ROUND if radius > 0 else DWMWCP_DONOTROUND
        api.dwm_attribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, corners)
        api.extend_frame(hwnd, -1)
        ok = api.dwm_attribute(hwnd, DWMWA_SYSTEMBACKDROP_TYPE, DWMSBT_TRANSIENTWINDOW)
    else:
        acrylic = build >= BUILD_ACRYLIC
        state = ACCENT_ENABLE_ACRYLICBLURBEHIND if acrylic else ACCENT_ENABLE_BLURBEHIND
        ok = api.accent(hwnd, state, 2 if acrylic else 0, _tint(dark))
    return bool(ok) and _clip(api, hwnd, max(0.0, radius), max(0.0, inset), modern)


def apply_backdrop(
    hwnd: int, radius: float, dark: bool, inset: float = 0.0, *, api=None, build: int | None = None
) -> bool:
    """Acrylic behind a frameless translucent window, clipped to a rounded rectangle.

    Windows 11 22H2+ uses the system acrylic backdrop; earlier builds use the acrylic (1803+)
    or plain blur-behind accent. Call again after a resize: the clip is a fixed region. Any
    failure undoes what was already set, so False always means the window is as it was.
    """
    try:
        hwnd = int(hwnd)
        radius, inset = float(radius), float(inset)
        if hwnd <= 0 or not (math.isfinite(radius) and math.isfinite(inset)):
            return False
        api = api or _load()
        build = _build() if build is None else build
    except Exception:
        return False
    if build < BUILD_WINDOWS_10:
        return False
    _applied.add(hwnd)
    try:
        if _enable(api, hwnd, radius, dark, inset, build):
            return True
    except Exception:
        pass
    clear_backdrop(hwnd, api=api, build=build)
    return False


def clear_backdrop(hwnd: int, *, api=None, build: int | None = None) -> bool:
    try:
        hwnd = int(hwnd)
        if hwnd not in _applied:
            return False
        _applied.discard(hwnd)
        api = api or _load()
        build = _build() if build is None else build
        if build >= BUILD_SYSTEM_BACKDROP:
            api.dwm_attribute(hwnd, DWMWA_SYSTEMBACKDROP_TYPE, DWMSBT_NONE)
            api.extend_frame(hwnd, 0)
        else:
            api.accent(hwnd, ACCENT_DISABLED, 0, 0)
        api.clear_region(hwnd)
        return True
    except Exception:
        return False


class WindowsAdapter(PlatformAdapter):
    name = "windows"

    def __init__(self):
        raise UnsupportedPlatform(MESSAGE)

    def capture(self, region):
        raise UnsupportedPlatform(MESSAGE)

    def screen_permission(self):
        raise UnsupportedPlatform(MESSAGE)

    def apply_backdrop(self, win_id: int, radius: float, dark: bool, inset: float = 0.0) -> bool:
        return apply_backdrop(win_id, radius, dark, inset)

    def clear_backdrop(self, win_id: int) -> bool:
        return clear_backdrop(win_id)

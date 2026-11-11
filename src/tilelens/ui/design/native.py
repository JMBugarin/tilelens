"""The operating system's blur-behind as a callable for GlassWindow.backdrop.

The platform modules expose apply_backdrop(win_id, radius, dark, inset=0.0) with the radius and
inset in points. Older hooks took only (win_id, radius, dark), and an adapter object may be handed
over instead of a function; call_backdrop accepts all of them.
"""

from __future__ import annotations

import importlib
import inspect
import sys
from collections.abc import Callable
from typing import Any

from PySide6.QtGui import QGuiApplication

Backdrop = Callable[..., bool]

_NATIVE = {
    "darwin": ("cocoa", "tilelens.platforms.macos"),
    "win32": ("windows", "tilelens.platforms.windows"),
}


class NativeBackdrop:
    """apply_backdrop of the running OS; False wherever there is no real native window.

    An object with __call__ does not bind to the window when it is assigned to the class.
    """

    def __init__(self, platform: str | None = None):
        key = sys.platform if platform is None else platform
        self._qt, self._module = _NATIVE.get("win32" if key.startswith("win") else key, ("", ""))

    @property
    def available(self) -> bool:
        return bool(self._module)

    def __call__(self, win_id: int, radius: float, dark: bool, inset: float = 0.0) -> bool:
        # offscreen and other Qt platforms hand out counters, not native handles
        if not self._module or int(win_id) <= 0 or QGuiApplication.platformName() != self._qt:
            return False
        try:
            apply = importlib.import_module(self._module).apply_backdrop
        except Exception:
            return False
        return bool(apply(int(win_id), float(radius), bool(dark), float(inset)))


def native_backdrop(platform: str | None = None) -> NativeBackdrop:
    return NativeBackdrop(platform)


def resolve_backdrop(source: Any) -> Backdrop | None:
    """A callable from None, a function, or an object with apply_backdrop (adapter or module)."""
    if source is None:
        return None
    apply = getattr(source, "apply_backdrop", None)
    if callable(apply):
        return apply
    return source if callable(source) else None


def takes_inset(fn: Backdrop) -> bool:
    try:
        inspect.signature(fn).bind(0, 0.0, False, 0.0)
    except TypeError:
        return False
    except ValueError:
        return True
    return True


def call_backdrop(source: Any, win_id: int, radius: float, dark: bool, inset: float) -> bool:
    """Ask `source` (any form resolve_backdrop takes) for a blur behind a window."""
    fn = resolve_backdrop(source)
    if fn is None:
        return False
    if takes_inset(fn):
        return bool(fn(win_id, radius, dark, inset))
    return bool(fn(win_id, radius, dark))


__all__ = [
    "NativeBackdrop",
    "call_backdrop",
    "native_backdrop",
    "resolve_backdrop",
    "takes_inset",
]

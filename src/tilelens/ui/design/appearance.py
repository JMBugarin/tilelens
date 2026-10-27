"""Which material is in force: user mode, backdrop luminance with hysteresis, adaptive strength.

The logic (LumaSwitch, luma_of, strength_for) is plain Python and numpy; Appearance only wraps it
in a QObject that emits signals, so all of it runs without a display.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QGuiApplication

from tilelens.ui.design.tokens import DARK, LIGHT, MATERIALS, Material

MODES = ("auto", "dark", "light")
PIVOT = 0.55
BAND = 0.12
SAMPLES = 3
STRENGTH_MIN = 0.82
STRENGTH_MAX = 1.15
_GRID = 24
_LUMA_BGR = np.array([0.114, 0.587, 0.299], np.float32) / 255.0

_default = {"material": DARK}


def default_material() -> Material:
    return _default["material"]


def set_default_material(m: Material) -> None:
    """Material of widgets that sit under no glass window (dialogs, tests)."""
    _default["material"] = m


def material_of(widget) -> Material:
    """The material a widget should paint with: the nearest ancestor's glass_material."""
    w = widget
    while w is not None:
        m = getattr(w, "glass_material", None)
        if isinstance(m, Material):
            return m
        w = w.parentWidget()
    return _default["material"]


def system_material() -> Material:
    if QGuiApplication.instance() is None:
        return DARK
    try:
        scheme = QGuiApplication.styleHints().colorScheme()
    except (AttributeError, RuntimeError):
        return DARK
    return LIGHT if scheme == Qt.ColorScheme.Light else DARK


def _rect_of(rect) -> tuple[int, int, int, int]:
    if isinstance(rect, tuple | list):
        x, y, w, h = rect
        return int(x), int(y), int(w), int(h)
    vals = []
    for name in ("x", "y", "width", "height"):
        v = getattr(rect, name)
        vals.append(int(v() if callable(v) else v))
    return vals[0], vals[1], vals[2], vals[3]


def backdrop_stats(bgr: np.ndarray, rect) -> tuple[float, float]:
    """Mean luminance (0..1) and its spread over rect, from at most 24 x 24 samples.

    rect is (x, y, w, h) or anything with x, y, width, height, in the image's pixels. An empty
    or off-image rect reads as the pivot, which never moves a LumaSwitch.
    """
    x, y, w, h = _rect_of(rect)
    ih, iw = bgr.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(iw, x + w), min(ih, y + h)
    if x1 <= x0 or y1 <= y0:
        return PIVOT, 0.0
    sub = bgr[y0 : y1 : max(1, (y1 - y0) // _GRID), x0 : x1 : max(1, (x1 - x0) // _GRID)]
    if sub.ndim == 2:
        luma = sub.astype(np.float32) / 255.0
    else:
        luma = sub[..., :3].astype(np.float32) @ _LUMA_BGR
    return float(luma.mean()), float(luma.std())


def luma_of(bgr: np.ndarray, rect) -> float:
    return backdrop_stats(bgr, rect)[0]


def strength_for(luma: float, busy: float, dark: bool) -> float:
    """Fill opacity factor: higher where the backdrop fights the glass (bright under dark glass,
    dark under light glass) or is busy with detail.
    """
    natural = 0.22 if dark else 0.82
    mismatch = min(1.0, abs(luma - natural) / 0.55)
    texture = min(1.0, busy / 0.22)
    pressure = 0.65 * mismatch + 0.35 * texture
    return STRENGTH_MIN + (STRENGTH_MAX - STRENGTH_MIN) * pressure


class LumaSwitch:
    """Light/dark flip that needs `samples` consecutive readings beyond pivot +/- band."""

    def __init__(
        self,
        light: bool = False,
        pivot: float = PIVOT,
        band: float = BAND,
        samples: int = SAMPLES,
    ):
        self.light = light
        self.pivot = pivot
        self.band = band
        self.samples = samples
        self.streak = 0

    def reset(self, light: bool | None = None) -> None:
        if light is not None:
            self.light = light
        self.streak = 0

    def update(self, luma: float) -> bool:
        """Feed one reading; True when it flipped the state."""
        beyond = luma < self.pivot - self.band if self.light else luma > self.pivot + self.band
        self.streak = self.streak + 1 if beyond else 0
        if self.streak < self.samples:
            return False
        self.light = not self.light
        self.streak = 0
        return True


class Appearance(QObject):
    """Mode ("auto", "dark", "light"), the material it resolves to and the adaptive strength."""

    appearance_changed = Signal(object)  # Material
    strength_changed = Signal(float)

    def __init__(self, mode: str = "auto", parent: QObject | None = None, *, initial=DARK):
        super().__init__(parent)
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        self._mode = mode
        self._switch = LumaSwitch(light=not initial.dark)
        self._material = {"dark": DARK, "light": LIGHT}.get(mode, initial)
        self._switch.reset(light=not self._material.dark)
        self._strength = 1.0
        self._last_stats = (0.2 if self._material.dark else 0.8, 0.0)

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def material(self) -> Material:
        return self._material

    @property
    def strength(self) -> float:
        return self._strength

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        self._mode = mode
        if mode == "auto":
            self._switch.reset(light=not self._material.dark)
        else:
            self._set_material(MATERIALS[mode])
        self._refresh_strength()

    def feed(self, luma: float, busy: float = 0.0) -> None:
        """One backdrop reading: luma 0..1 under the window and how much detail it holds."""
        self._last_stats = (luma, busy)
        if self._mode == "auto" and self._switch.update(luma):
            self._set_material(LIGHT if self._switch.light else DARK)
        self._refresh_strength()

    def sample(self, bgr: np.ndarray, rect) -> float:
        luma, busy = backdrop_stats(bgr, rect)
        self.feed(luma, busy)
        return luma

    def _set_material(self, m: Material) -> None:
        if m.name == self._material.name:
            return
        self._material = m
        self._switch.reset(light=not m.dark)
        self.appearance_changed.emit(m)

    def _refresh_strength(self) -> None:
        luma, busy = self._last_stats
        target = strength_for(luma, busy, self._material.dark)
        new = 0.5 * self._strength + 0.5 * target
        if abs(new - self._strength) >= 0.02:
            self._strength = new
            self.strength_changed.emit(new)


__all__ = [
    "BAND",
    "MODES",
    "PIVOT",
    "SAMPLES",
    "STRENGTH_MAX",
    "STRENGTH_MIN",
    "Appearance",
    "LumaSwitch",
    "backdrop_stats",
    "default_material",
    "luma_of",
    "material_of",
    "set_default_material",
    "strength_for",
    "system_material",
]

"""Design tokens of the glass interface: spacing, radii, motion, type and two materials."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass

from PySide6.QtGui import QColor, QFont


def rgba(r: int, g: int, b: int, a: float = 1.0) -> QColor:
    return QColor(r, g, b, round(max(0.0, min(1.0, a)) * 255))


def with_alpha(color: QColor, a: float) -> QColor:
    c = QColor(color)
    c.setAlphaF(max(0.0, min(1.0, a)))
    return c


def mix(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
        round(a.alpha() + (b.alpha() - a.alpha()) * t),
    )


class Space:
    """4 pt grid."""

    XS = 4
    S = 8
    M = 12
    L = 16
    XL = 20
    XXL = 24


class Radius:
    """Concentric: a child's radius is its parent's radius minus the gap between them."""

    CONTROL = 12
    CARD = 18
    SHEET = 22
    PANEL = 26


class Motion:
    """Milliseconds. Short and quiet: nothing here runs while a round is being played."""

    FAST = 120
    BASE = 180
    SLOW = 280


@dataclass(frozen=True)
class Material:
    """Everything that changes between the dark and the light glass."""

    name: str
    dark: bool
    tint_top: QColor  # over a native blur
    tint_bottom: QColor
    solid_top: QColor  # without one: more opaque so the text stays readable
    solid_bottom: QColor
    rim_top: QColor  # specular edge, brightest where light would enter
    rim_mid: QColor
    rim_bottom: QColor
    edge: QColor  # hairline that separates the glass from what is behind it
    shadow: QColor
    text: QColor
    text2: QColor
    text3: QColor
    fill: QColor  # resting control
    fill_hover: QColor
    fill_press: QColor
    divider: QColor
    accent: QColor
    on_accent: QColor
    success: QColor
    warning: QColor
    danger: QColor


DARK = Material(
    name="dark",
    dark=True,
    tint_top=rgba(52, 58, 76, 0.50),
    tint_bottom=rgba(20, 24, 38, 0.62),
    solid_top=rgba(40, 46, 64, 0.92),
    solid_bottom=rgba(18, 22, 36, 0.95),
    rim_top=rgba(255, 255, 255, 0.46),
    rim_mid=rgba(255, 255, 255, 0.07),
    rim_bottom=rgba(255, 255, 255, 0.20),
    edge=rgba(0, 0, 0, 0.34),
    shadow=rgba(2, 6, 18, 1.0),
    text=rgba(255, 255, 255, 0.96),
    text2=rgba(235, 240, 255, 0.68),
    text3=rgba(235, 240, 255, 0.44),
    fill=rgba(255, 255, 255, 0.07),
    fill_hover=rgba(255, 255, 255, 0.13),
    fill_press=rgba(255, 255, 255, 0.19),
    divider=rgba(255, 255, 255, 0.10),
    accent=rgba(90, 200, 250),
    on_accent=rgba(4, 22, 34),
    success=rgba(48, 209, 88),
    warning=rgba(255, 214, 10),
    danger=rgba(255, 105, 97),
)

LIGHT = Material(
    name="light",
    dark=False,
    tint_top=rgba(255, 255, 255, 0.62),
    tint_bottom=rgba(240, 244, 252, 0.52),
    solid_top=rgba(252, 253, 255, 0.94),
    solid_bottom=rgba(236, 240, 248, 0.95),
    rim_top=rgba(255, 255, 255, 0.95),
    rim_mid=rgba(255, 255, 255, 0.30),
    rim_bottom=rgba(255, 255, 255, 0.70),
    edge=rgba(20, 30, 60, 0.16),
    shadow=rgba(20, 30, 60, 1.0),
    text=rgba(18, 22, 32, 0.94),
    text2=rgba(28, 36, 56, 0.64),
    text3=rgba(28, 36, 56, 0.42),
    fill=rgba(20, 30, 60, 0.05),
    fill_hover=rgba(20, 30, 60, 0.09),
    fill_press=rgba(20, 30, 60, 0.14),
    divider=rgba(20, 30, 60, 0.10),
    accent=rgba(0, 113, 227),
    on_accent=rgba(255, 255, 255),
    success=rgba(26, 150, 66),
    warning=rgba(176, 108, 0),
    danger=rgba(210, 30, 40),
)

MATERIALS = {"dark": DARK, "light": LIGHT}

# (pixel size, weight) per role; the family is the platform's own UI font
TYPE_ROLES = {
    "caption": (11, QFont.Weight.Medium),
    "footnote": (12, QFont.Weight.Normal),
    "body": (13, QFont.Weight.Normal),
    "label": (13, QFont.Weight.DemiBold),
    "headline": (15, QFont.Weight.DemiBold),
    "title": (20, QFont.Weight.Bold),
    "word": (19, QFont.Weight.Bold),
    "score": (21, QFont.Weight.Bold),
}


def type_font(role: str, scale: float = 1.0, tabular: bool = False) -> QFont:
    size, weight = TYPE_ROLES[role]
    f = QFont()
    f.setPixelSize(max(6, round(size * scale)))
    f.setWeight(weight)
    f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    if tabular:
        with contextlib.suppress(AttributeError, TypeError):
            f.setFeature(QFont.Tag("tnum"), 1)
    return f


__all__ = [
    "DARK",
    "LIGHT",
    "MATERIALS",
    "TYPE_ROLES",
    "Material",
    "Motion",
    "Radius",
    "Space",
    "mix",
    "rgba",
    "type_font",
    "with_alpha",
]

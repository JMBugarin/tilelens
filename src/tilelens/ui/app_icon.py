"""The TileLens mark: a glass lens ring around a tile, drawn as vectors at any size.

paint_app_icon draws into any rectangle; render_app_icon and the QIcon builders rasterize it.
The tray version is a single-colour template on macOS so the menu bar can tint it.
"""

from __future__ import annotations

import math
import sys

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)

from tilelens.ui.design import DARK, LIGHT, mix, rgba, with_alpha

SIZES = (16, 20, 24, 32, 48, 64, 128, 256, 512)
TRAY_SIZES = (16, 18, 22, 24, 32, 44, 64)
UNIT = 100.0
_WHITE = rgba(255, 255, 255)
_BLACK = rgba(0, 0, 0)
_SQUIRCLE_POWER = 5.0


def _opaque(c: QColor) -> QColor:
    return with_alpha(c, 1.0)


def _squircle(side: float = UNIT) -> QPainterPath:
    """The continuous-corner rounded square of app icons (a superellipse)."""
    steps = 160
    half = side / 2
    path = QPainterPath()
    for i in range(steps):
        t = math.tau * i / steps
        c, s = math.cos(t), math.sin(t)
        x = half + half * math.copysign(abs(c) ** (2 / _SQUIRCLE_POWER), c)
        y = half + half * math.copysign(abs(s) ** (2 / _SQUIRCLE_POWER), s)
        if i == 0:
            path.moveTo(x, y)
        else:
            path.lineTo(x, y)
    path.closeSubpath()
    return path


def _tile_mark(cx: float, cy: float, size: float) -> tuple[QPainterPath, QPainterPath]:
    """The tile and the letter T inside it, both in unit coordinates."""
    k = size / 24.0
    tile = QPainterPath()
    tile.addRoundedRect(QRectF(cx - size / 2, cy - size / 2, size, size), 6.2 * k, 6.2 * k)
    letter = QPainterPath()
    letter.addRoundedRect(QRectF(cx - 7 * k, cy - 8.5 * k, 14 * k, 3.6 * k), 1.1 * k, 1.1 * k)
    letter.addRoundedRect(QRectF(cx - 1.8 * k, cy - 6 * k, 3.6 * k, 14.5 * k), 1.1 * k, 1.1 * k)
    return tile, letter.simplified()


def _body(p: QPainter, dark: bool, simple: bool) -> None:
    m = DARK if dark else LIGHT
    top = mix(_opaque(m.solid_top), m.accent, 0.20 if dark else 0.10)
    bottom = _opaque(m.solid_bottom)
    shape = _squircle()
    grad = QLinearGradient(QPointF(0, 0), QPointF(0, UNIT))
    grad.setColorAt(0, top)
    grad.setColorAt(1, bottom)
    p.fillPath(shape, grad)
    if simple:
        return
    sheen = QLinearGradient(QPointF(0, 0), QPointF(0, UNIT * 0.45))
    sheen.setColorAt(0, with_alpha(_WHITE, 0.16 if dark else 0.55))
    sheen.setColorAt(1, with_alpha(_WHITE, 0.0))
    p.fillPath(shape, sheen)
    rim = QLinearGradient(QPointF(0, 0), QPointF(UNIT, UNIT))
    rim.setColorAt(0, with_alpha(_WHITE, 0.55 if dark else 0.95))
    rim.setColorAt(0.4, with_alpha(_WHITE, 0.06))
    rim.setColorAt(0.65, with_alpha(_WHITE, 0.06))
    rim.setColorAt(1, with_alpha(_WHITE, 0.26 if dark else 0.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QBrush(rim), 1.1))
    p.drawPath(_squircle(UNIT - 1.1).translated(0.55, 0.55))


def _lens(p: QPainter, dark: bool, simple: bool) -> None:
    m = DARK if dark else LIGHT
    centre = QPointF(UNIT / 2, UNIT / 2)
    radius, width = (28.0, 8.0) if simple else (27.5, 6.6)
    if not simple:
        fill = QRadialGradient(QPointF(42, 36), 34)
        fill.setColorAt(0, with_alpha(_WHITE, 0.20 if dark else 0.34))
        fill.setColorAt(1, with_alpha(_WHITE, 0.02))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fill)
        p.drawEllipse(centre, radius, radius)
    ring = QLinearGradient(QPointF(18, 18), QPointF(82, 82))
    ring.setColorAt(0, with_alpha(_WHITE, 0.98))
    ring.setColorAt(0.5, with_alpha(mix(_WHITE, m.accent, 0.35), 0.80))
    ring.setColorAt(1, with_alpha(m.accent, 0.92))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QBrush(ring), width))
    p.drawEllipse(centre, radius, radius)
    if simple:
        return
    p.setPen(QPen(with_alpha(_BLACK, 0.22), 0.9))
    p.drawEllipse(centre, radius - width / 2 - 0.4, radius - width / 2 - 0.4)
    arc_box = QRectF(centre.x() - radius, centre.y() - radius, 2 * radius, 2 * radius)
    spec = QPen(with_alpha(_WHITE, 0.95), 2.0)
    spec.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(spec)
    p.drawArc(arc_box, 112 * 16, 46 * 16)


def _tile(p: QPainter, dark: bool, simple: bool) -> None:
    m = DARK if dark else LIGHT
    tile, letter = _tile_mark(UNIT / 2, UNIT / 2, 25.0 if not simple else 27.0)
    if not simple:
        p.fillPath(tile.translated(0, 1.4), with_alpha(_BLACK, 0.30))
    box = tile.boundingRect()
    face = QLinearGradient(box.topLeft(), box.bottomLeft())
    face.setColorAt(0, mix(m.accent, _WHITE, 0.42))
    face.setColorAt(1, m.accent)
    p.fillPath(tile, face)
    if not simple:
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(with_alpha(_WHITE, 0.55), 0.9))
        p.drawPath(_inset_rounded(box.adjusted(0.5, 0.5, -0.5, -0.5), 5.7 * box.width() / 24.0))
    p.fillPath(letter, m.on_accent)


def _inset_rounded(box: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(box, radius, radius)
    return path


def paint_app_icon(
    p: QPainter,
    rect: QRectF,
    *,
    mono: bool = False,
    background: bool = True,
    dark: bool = True,
    simple: bool | None = None,
) -> None:
    """Draw the mark into rect.

    background: the glass body behind the lens (off for a transparent mark). mono: one colour,
    black on transparent, for a template icon. simple: fewer details for small sizes; by default
    it follows the pixel size of rect.
    """
    side = min(rect.width(), rect.height())
    if side <= 0:
        return
    if simple is None:
        ratio = p.device().devicePixelRatioF() if p.device() is not None else 1.0
        simple = side * ratio < 40
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    p.translate(rect.center().x() - side / 2, rect.center().y() - side / 2)
    p.scale(side / UNIT, side / UNIT)
    if mono:
        _paint_mono(p)
    else:
        if background:
            _body(p, dark, simple)
        _lens(p, dark, simple)
        _tile(p, dark, simple)
    p.restore()


def _paint_mono(p: QPainter) -> None:
    centre = QPointF(UNIT / 2, UNIT / 2)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(_BLACK, 9.0))
    p.drawEllipse(centre, 42.0, 42.0)
    tile, letter = _tile_mark(UNIT / 2, UNIT / 2, 34.0)
    p.fillPath(tile.subtracted(letter), _BLACK)


def render_app_icon(
    size: int,
    dpr: float = 1.0,
    *,
    mono: bool = False,
    background: bool = True,
    dark: bool = True,
    margin: float | None = None,
) -> QPixmap:
    """The mark as a pixmap of size x size points. margin is the empty border as a fraction of
    the side (default: a little for the large sizes, so the Dock shows it like other icons)."""
    px = max(1, round(size * dpr))
    pm = QPixmap(px, px)
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    if margin is None:
        margin = 0.0 if mono else (0.08 if size >= 64 else 0.02)
    inset = size * margin
    p = QPainter(pm)
    paint_app_icon(
        p,
        QRectF(inset, inset, size - 2 * inset, size - 2 * inset),
        mono=mono,
        background=background,
        dark=dark,
    )
    p.end()
    return pm


def app_icon(dark: bool = True) -> QIcon:
    """Window and application icon: the glass mark in a rounded square."""
    icon = QIcon()
    for size in SIZES:
        icon.addPixmap(render_app_icon(size, dark=dark))
    return icon


def tray_icon(template: bool | None = None) -> QIcon:
    """Menu bar and notification area icon.

    On macOS it is a template (black on transparent) that the system tints for light and dark
    menu bars; elsewhere it is the coloured mark, which reads on both.
    """
    template = sys.platform == "darwin" if template is None else template
    icon = QIcon()
    for size in TRAY_SIZES:
        if template:
            icon.addPixmap(render_app_icon(size, mono=True, background=False))
        else:
            icon.addPixmap(render_app_icon(size, margin=0.0))
    if template:
        icon.setIsMask(True)
    return icon


__all__ = [
    "SIZES",
    "TRAY_SIZES",
    "app_icon",
    "paint_app_icon",
    "render_app_icon",
    "tray_icon",
]

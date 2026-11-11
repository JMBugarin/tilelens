"""Painting of the glass material: tinted body, specular rim, hairline edge and soft shadows.

The body is drawn over a native blur when the platform gives one and over a more opaque fill
when it does not, so the text is readable either way. Shadows are rendered once per shape and
reused; a repaint is a few blits and one gradient fill.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import cv2
import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)

from tilelens.ui.design.tokens import Material, with_alpha

SHADOW_BLEED = 36  # points of room a window leaves around its glass for the shadow


@dataclass(frozen=True)
class Elevation:
    """Two stacked shadows: a wide ambient one and a tight contact one."""

    ambient_blur: float
    ambient_dy: float
    ambient_alpha: float
    contact_blur: float
    contact_dy: float
    contact_alpha: float


FLAT = Elevation(0, 0, 0, 0, 0, 0)
RAISED = Elevation(10, 3, 0.18, 2.5, 1, 0.22)
FLOATING = Elevation(26, 12, 0.34, 5, 2, 0.28)


def _rounded(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def paint_glass(
    p: QPainter,
    rect: QRectF,
    radius: float,
    m: Material,
    *,
    blur: bool = True,
    strength: float = 1.0,
    tint: QColor | None = None,
    rim: bool = True,
    sheen: bool = True,
) -> None:
    """The glass body. strength scales the fill (adaptive contrast); tint adds a color wash."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    top, bottom = (m.tint_top, m.tint_bottom) if blur else (m.solid_top, m.solid_bottom)
    body = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    body.setColorAt(0, with_alpha(top, top.alphaF() * strength))
    body.setColorAt(1, with_alpha(bottom, bottom.alphaF() * strength))
    path = _rounded(rect, radius)
    p.fillPath(path, body)
    if tint is not None:
        p.fillPath(path, tint)
    if sheen:
        # the lit upper part of a thick pane: bright at the top, gone by a third of the way down
        lit = QLinearGradient(
            rect.topLeft(), QPointF(rect.left(), rect.top() + rect.height() * 0.42)
        )
        lit.setColorAt(0, with_alpha(m.rim_top, 0.13 if m.dark else 0.34))
        lit.setColorAt(1, with_alpha(m.rim_top, 0.0))
        p.fillPath(path, lit)
    if rim:
        paint_rim(p, rect, radius, m)
    p.restore()


def paint_rim(p: QPainter, rect: QRectF, radius: float, m: Material) -> None:
    """Specular edge (light on the upper left, a softer return on the lower right) + hairline."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(m.edge, 1.0))
    p.drawRoundedRect(rect.adjusted(-0.5, -0.5, 0.5, 0.5), radius + 0.5, radius + 0.5)
    inset = rect.adjusted(0.75, 0.75, -0.75, -0.75)
    grad = QLinearGradient(inset.topLeft(), inset.bottomRight())
    grad.setColorAt(0.0, m.rim_top)
    grad.setColorAt(0.38, m.rim_mid)
    grad.setColorAt(0.62, m.rim_mid)
    grad.setColorAt(1.0, m.rim_bottom)
    p.setPen(QPen(grad, 1.5))
    p.drawRoundedRect(inset, max(0.0, radius - 0.75), max(0.0, radius - 0.75))
    p.restore()


def paint_wash(
    p: QPainter, rect: QRectF, radius: float, color: QColor, rim: QColor | None = None
) -> None:
    """A flat tinted pane for controls inside glass: a selected card, a pressed button."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawRoundedRect(rect, radius, radius)
    if rim is not None:
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(rim, 1.2))
        p.drawRoundedRect(rect.adjusted(0.6, 0.6, -0.6, -0.6), radius - 0.6, radius - 0.6)
    p.restore()


def _blurred_mask(width: int, height: int, radius: float, blur: float, scale: float) -> np.ndarray:
    """Alpha (0..1) of a rounded rectangle blurred by a Gaussian, at device pixels."""
    img = QImage(width, height, QImage.Format.Format_Grayscale8)
    img.fill(0)
    q = QPainter(img)
    q.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    q.setPen(Qt.PenStyle.NoPen)
    q.setBrush(QColor(255, 255, 255))
    margin = blur * scale * 2.2
    q.drawRoundedRect(
        QRectF(margin, margin, width - 2 * margin, height - 2 * margin),
        radius * scale,
        radius * scale,
    )
    q.end()
    ptr = img.constBits()
    arr = np.frombuffer(ptr, np.uint8).reshape(height, img.bytesPerLine())[:, :width].copy()
    sigma = max(0.1, blur * scale / 2.0)
    out = cv2.GaussianBlur(arr, (0, 0), sigma)
    return out.astype(np.float32) / 255.0


@lru_cache(maxsize=64)
def _shadow_tile(
    radius: float, blur: float, alpha: float, rgb: tuple[int, int, int], dpr: float
) -> QPixmap:
    """One square holding all four corners of a shadow; the edges stretch from its middle."""
    if blur <= 0 or alpha <= 0:
        return QPixmap()
    reach = blur * 2.2
    half = int(np.ceil((radius + reach) * dpr))
    side = half * 2 + 2
    mask = _blurred_mask(side, side, radius, blur, dpr) * alpha
    rgba_arr = np.zeros((side, side, 4), np.uint8)
    rgba_arr[..., 0] = rgb[2]
    rgba_arr[..., 1] = rgb[1]
    rgba_arr[..., 2] = rgb[0]
    # premultiplied BGRA
    rgba_arr[..., :3] = (rgba_arr[..., :3] * mask[..., None]).astype(np.uint8)
    rgba_arr[..., 3] = (mask * 255).astype(np.uint8)
    img = QImage(
        rgba_arr.data, side, side, side * 4, QImage.Format.Format_ARGB32_Premultiplied
    ).copy()
    pix = QPixmap.fromImage(img)
    pix.setDevicePixelRatio(dpr)
    return pix


def paint_shadow(
    p: QPainter,
    rect: QRectF,
    radius: float,
    elevation: Elevation,
    m: Material,
    dpr: float = 1.0,
) -> None:
    """Soft layered shadow under rect; drawn outside the glass only."""
    if elevation is FLAT:
        return
    rgb = (m.shadow.red(), m.shadow.green(), m.shadow.blue())
    p.save()
    clip = QPainterPath()
    clip.addRect(QRectF(rect).adjusted(-200, -200, 200, 200))
    clip.addPath(_rounded(rect, radius))
    clip.setFillRule(Qt.FillRule.OddEvenFill)
    p.setClipPath(clip)
    for blur, dy, alpha in (
        (elevation.ambient_blur, elevation.ambient_dy, elevation.ambient_alpha),
        (elevation.contact_blur, elevation.contact_dy, elevation.contact_alpha),
    ):
        tile = _shadow_tile(float(radius), float(blur), float(alpha), rgb, float(dpr))
        if tile.isNull():
            continue
        r = rect.translated(0, dy)
        reach = blur * 2.2
        _blit_nine(p, tile, r.adjusted(-reach, -reach, reach, reach), (radius + reach), dpr)
    p.restore()


def _blit_nine(p: QPainter, tile: QPixmap, target: QRectF, corner: float, dpr: float) -> None:
    """Draw a 9-slice: corners as they are, edges and middle stretched from the tile's centre."""
    side = tile.width()
    half = side // 2
    c = corner * dpr
    c = min(c, half)
    cs = c / dpr
    w, h = target.width(), target.height()
    if w < 2 * cs or h < 2 * cs:
        cs = min(w, h) / 2
        c = cs * dpr
    x0, y0 = target.left(), target.top()
    x1, y1 = target.right() - cs, target.bottom() - cs
    src_mid = QRectF(half - 0.5, half - 0.5, 1, 1)
    # corners
    for (tx, ty), (sx, sy) in (
        ((x0, y0), (0, 0)),
        ((x1, y0), (side - c, 0)),
        ((x0, y1), (0, side - c)),
        ((x1, y1), (side - c, side - c)),
    ):
        p.drawPixmap(QRectF(tx, ty, cs, cs), tile, QRectF(sx, sy, c, c))
    # edges
    p.drawPixmap(QRectF(x0 + cs, y0, w - 2 * cs, cs), tile, QRectF(c, 0, 1, c))
    p.drawPixmap(QRectF(x0 + cs, y1, w - 2 * cs, cs), tile, QRectF(c, side - c, 1, c))
    p.drawPixmap(QRectF(x0, y0 + cs, cs, h - 2 * cs), tile, QRectF(0, c, c, 1))
    p.drawPixmap(QRectF(x1, y0 + cs, cs, h - 2 * cs), tile, QRectF(side - c, c, c, 1))
    p.drawPixmap(QRectF(x0 + cs, y0 + cs, w - 2 * cs, h - 2 * cs), tile, src_mid)


__all__ = [
    "FLAT",
    "FLOATING",
    "RAISED",
    "SHADOW_BLEED",
    "Elevation",
    "paint_glass",
    "paint_rim",
    "paint_shadow",
    "paint_wash",
]

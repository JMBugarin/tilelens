"""Grabbing widgets and laying them over a backdrop."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import cv2
import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from ui_gallery import backdrops

PAD = 48  # points of backdrop around a window


@dataclass
class Layer:
    """A grabbed widget or an image at a position on the stage, in points."""

    image: QImage
    pos: QPointF
    blur: bool = True


@dataclass
class Stage:
    """Several things laid out on one stage. size is in points; items are
    (widget | QImage | QPixmap, (x, y)) or (widget, (x, y), (w, h)) tuples drawn in order.
    Click-through widgets (the board preview) never sit over a native blur."""

    size: tuple[int, int]
    items: list = field(default_factory=list)


def settle(ms: int) -> None:
    app = QApplication.instance()
    end = time.monotonic() + ms / 1000.0
    app.processEvents()
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


def to_image(obj) -> QImage:
    if isinstance(obj, QImage):
        return obj
    if isinstance(obj, QPixmap):
        return obj.toImage()
    return obj.grab().toImage()


def has_alpha(img: QImage) -> bool:
    """True when any pixel is not fully opaque."""
    if not img.hasAlphaChannel():
        return False
    argb = img.convertToFormat(QImage.Format.Format_ARGB32)
    h = argb.height()
    arr = np.frombuffer(argb.constBits(), np.uint8, count=argb.bytesPerLine() * h)
    arr = arr.reshape(h, argb.bytesPerLine())[:, : argb.width() * 4]
    return bool((arr[:, 3::4] < 255).any())


def present(widget: QWidget, size: tuple[int, int] | None = None, wait: int = 0) -> None:
    widget.ensurePolished()
    if size is not None:
        widget.setGeometry(0, 0, size[0], size[1])
    widget.show()
    settle(wait)


def _blur_under(base: QImage, layer: Layer, scale: float) -> None:
    """Approximates the native backdrop blur under the glass of a layer: blur and saturate the
    backdrop where the layer is mostly opaque, which is the glass body and not its shadow."""
    img = layer.image
    ratio = img.devicePixelRatio() or 1.0
    argb = img.convertToFormat(QImage.Format.Format_ARGB32)
    lw, lh = argb.width(), argb.height()
    alpha = np.frombuffer(argb.constBits(), np.uint8, count=argb.bytesPerLine() * lh)
    alpha = alpha.reshape(lh, argb.bytesPerLine())[:, : lw * 4][:, 3::4].astype(np.float32) / 255
    ox, oy = round(layer.pos.x() * scale), round(layer.pos.y() * scale)
    if abs(ratio - scale) > 0.01:
        alpha = cv2.resize(alpha, (round(lw * scale / ratio), round(lh * scale / ratio)))
        lh, lw = alpha.shape
    x0, y0 = max(0, ox), max(0, oy)
    x1, y1 = min(base.width(), ox + lw), min(base.height(), oy + lh)
    if x1 <= x0 or y1 <= y0:
        return
    w, h = x1 - x0, y1 - y0
    rgb = base.copy(x0, y0, w, h).convertToFormat(QImage.Format.Format_RGB888)
    region = np.frombuffer(rgb.constBits(), np.uint8, count=rgb.bytesPerLine() * h)
    region = region.reshape(h, rgb.bytesPerLine())[:, : w * 3].reshape(h, w, 3).astype(np.float32)
    blurred = cv2.GaussianBlur(region, (0, 0), 11.0 * scale)
    lum = (blurred * np.array([0.299, 0.587, 0.114], np.float32)).sum(axis=2, keepdims=True)
    blurred = np.clip(lum + (blurred - lum) * 1.45, 0, 255)
    mask = alpha[y0 - oy : y1 - oy, x0 - ox : x1 - ox]
    mask = cv2.GaussianBlur((mask > 0.4).astype(np.float32), (0, 0), 0.8 * scale)[..., None]
    out = np.ascontiguousarray((region * (1 - mask) + blurred * mask).astype(np.uint8))
    patched = QImage(out.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
    patched = patched.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    p = QPainter(base)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
    p.drawImage(x0, y0, patched)
    p.end()


def composite(
    stage: tuple[int, int],
    layers: list[Layer],
    backdrop: str | None,
    scale: float,
    simulate_blur: bool = False,
) -> QImage:
    """backdrop None leaves the stage transparent."""
    w, h = stage
    if backdrop is None:
        base = backdrops.new_image(w, h, scale)
        base.fill(Qt.GlobalColor.transparent)
    else:
        base = backdrops.render(backdrop, w, h, scale).copy()
    for layer in layers:
        if simulate_blur and layer.blur and backdrop is not None:
            base.setDevicePixelRatio(1.0)
            _blur_under(base, layer, scale)
            base.setDevicePixelRatio(scale)
        p = QPainter(base)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.drawImage(layer.pos, layer.image)
        p.end()
    return base


def pad_image(img: QImage, pad_pt: int) -> QImage:
    """The image centered on a larger transparent canvas."""
    ratio = img.devicePixelRatio() or 1.0
    px = round(pad_pt * ratio)
    out = QImage(
        img.width() + 2 * px, img.height() + 2 * px, QImage.Format.Format_ARGB32_Premultiplied
    )
    out.setDevicePixelRatio(ratio)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.drawImage(QPointF(pad_pt, pad_pt), img)
    p.end()
    return out

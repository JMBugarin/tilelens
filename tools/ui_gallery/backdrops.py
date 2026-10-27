"""The three things a translucent window is judged against.

    real      the Letter League frame the board fixtures come from
    discord   Discord's dark UI with chat rows
    gradient  saturated gradient with high-frequency detail: the worst case for readability

Every backdrop renders at the stage size in points and the device scale, as an image whose
devicePixelRatio is the scale, so painting it needs no further math.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from functools import lru_cache

import cv2
import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter

NAMES = ("real", "discord", "gradient")
FOCUS = QPointF(525.0, 310.0)  # the played tiles in the real frame, in points


def new_image(width_pt: float, height_pt: float, scale: float) -> QImage:
    img = QImage(
        max(1, round(width_pt * scale)),
        max(1, round(height_pt * scale)),
        QImage.Format.Format_ARGB32_Premultiplied,
    )
    img.setDevicePixelRatio(scale)
    img.fill(QColor(0, 0, 0))
    return img


def bgr_to_qimage(arr: np.ndarray, scale: float = 1.0) -> QImage:
    rgb = np.ascontiguousarray(arr[:, :, ::-1])
    h, w = rgb.shape[:2]
    img = QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
    img = img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(scale)
    return img


def qimage_to_bgr(img: QImage) -> np.ndarray:
    img = img.convertToFormat(QImage.Format.Format_RGB888)
    w, h = img.width(), img.height()
    arr = np.frombuffer(img.constBits(), np.uint8, count=img.bytesPerLine() * h)
    return arr.reshape(h, img.bytesPerLine())[:, : w * 3].reshape(h, w, 3)[:, :, ::-1].copy()


def real(width: float, height: float, scale: float) -> QImage:
    from ui_gallery import ctx

    base = ctx.scenario()
    wide = round(width - base.size_pt[0]) if abs(height - base.size_pt[1]) < 1 else 0
    frame, native = ctx.scenario(max(0, wide)).frame, ctx.FRAME_SCALE
    fh, fw = frame.shape[:2]
    # the frame is a Retina capture: native device pixels per point
    want_w, want_h = round(width * native), round(height * native)
    if want_w > fw or want_h > fh:
        s = max(want_w / fw, want_h / fh)
        frame = cv2.resize(frame, (round(fw * s), round(fh * s)), interpolation=cv2.INTER_CUBIC)
        fh, fw = frame.shape[:2]
        x0, y0 = 0, 0
    else:
        x0 = round(FOCUS.x() * native - want_w / 2)
        y0 = round(FOCUS.y() * native - want_h / 2)
        x0 = max(0, min(fw - want_w, x0))
        y0 = max(0, min(fh - want_h, y0))
    crop = frame[y0 : y0 + want_h, x0 : x0 + want_w]
    target = (max(1, round(width * scale)), max(1, round(height * scale)))
    if crop.shape[1] != target[0] or crop.shape[0] != target[1]:
        interp = cv2.INTER_AREA if target[0] < crop.shape[1] else cv2.INTER_CUBIC
        crop = cv2.resize(crop, target, interpolation=interp)
    return bgr_to_qimage(crop, scale)


def _font(px: float, bold: bool = False) -> QFont:
    f = QFont()
    f.setPixelSize(round(px))
    f.setBold(bold)
    return f


def discord(width: float, height: float, scale: float) -> QImage:
    rnd = random.Random(7)
    img = new_image(width, height, scale)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    p.fillRect(QRectF(0, 0, width, height), QColor("#313338"))
    wide = width >= 700
    left = 312.0 if wide else 0.0
    if wide:
        p.fillRect(QRectF(0, 0, 72, height), QColor("#1e1f22"))
        palette = ["#5865f2", "#3ba55d", "#eb459e", "#faa61a", "#ed4245", "#4f545c", "#57f287"]
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(max(1, int(height // 56))):
            p.setBrush(QColor(palette[i % len(palette)]))
            p.drawRoundedRect(QRectF(12, 12 + i * 56, 48, 48), 16 if i else 24, 16 if i else 24)
        p.fillRect(QRectF(72, 0, 240, height), QColor("#2b2d31"))
        p.setPen(QColor("#f2f3f5"))
        p.setFont(_font(15, True))
        p.drawText(QRectF(88, 0, 200, 48), Qt.AlignmentFlag.AlignVCenter, "Letter League")
        p.fillRect(QRectF(72, 47, 240, 1), QColor("#1f2023"))
        p.setFont(_font(15))
        for i, name in enumerate(["general", "games", "word-nerds", "screenshots", "voice-chat"]):
            y = 64 + i * 34
            if i == 1:
                p.setBrush(QColor("#404249"))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(QRectF(80, y - 4, 224, 30), 6, 6)
            p.setPen(QColor("#f2f3f5" if i == 1 else "#949ba4"))
            p.drawText(QRectF(96, y - 4, 200, 30), Qt.AlignmentFlag.AlignVCenter, f"#  {name}")
        p.fillRect(QRectF(72, height - 52, 240, 52), QColor("#232428"))
    p.fillRect(QRectF(left, 0, width - left, 48), QColor("#313338"))
    p.fillRect(QRectF(left, 47, width - left, 1), QColor("#1f2023"))
    p.setPen(QColor("#f2f3f5"))
    p.setFont(_font(15, True))
    p.drawText(QRectF(left + 16, 0, 300, 48), Qt.AlignmentFlag.AlignVCenter, "#  games")
    names = [("maya", "#f47fff"), ("dev_ops", "#5dade2"), ("josh", "#57f287"), ("tori", "#faa61a")]
    lines = [
        "that three letter word was brutal",
        "anyone up for another round?",
        "I had a Z the whole game and nowhere to put it",
        "good game, rematch in ten",
        "the double word on the left is open again",
        "wait is QI valid here",
        "lol yes it is",
        "starting a new lobby",
    ]
    y = 64.0
    while y < height - 96:
        name, color = names[rnd.randrange(len(names))]
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        p.drawEllipse(QRectF(left + 16, y + 2, 40, 40))
        p.setPen(QColor(color))
        p.setFont(_font(16, True))
        p.drawText(QPointF(left + 68, y + 16), name)
        p.setPen(QColor("#949ba4"))
        p.setFont(_font(12))
        p.drawText(
            QPointF(left + 68 + 12 + len(name) * 9, y + 16), f"Today at 9:4{rnd.randrange(10)} PM"
        )
        p.setPen(QColor("#dbdee1"))
        p.setFont(_font(16))
        for k in range(rnd.choice((1, 1, 2))):
            p.drawText(QPointF(left + 68, y + 38 + k * 22), rnd.choice(lines))
        y += 70 + 22 * (k)
    p.setBrush(QColor("#383a40"))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(QRectF(left + 16, height - 68, width - left - 32, 44), 8, 8)
    p.setPen(QColor("#6d6f78"))
    p.setFont(_font(16))
    p.drawText(
        QRectF(left + 32, height - 68, 300, 44), Qt.AlignmentFlag.AlignVCenter, "Message #games"
    )
    p.end()
    return img


def gradient(width: float, height: float, scale: float) -> QImage:
    w, h = max(1, round(width * scale)), max(1, round(height * scale))
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    u, v = xx / w, yy / h
    t = np.clip(0.62 * u + 0.38 * v + 0.07 * np.sin(v * 11.0 + u * 5.0), 0, 1)
    stops = np.array(
        [(236, 48, 140), (255, 168, 28), (30, 210, 190), (60, 80, 235), (160, 40, 220)], np.float32
    )
    pos = t * (len(stops) - 1)
    lo = np.clip(np.floor(pos).astype(int), 0, len(stops) - 2)
    frac = (pos - lo)[..., None]
    rgb = stops[lo] * (1 - frac) + stops[lo + 1] * frac
    period = 3.5 * scale
    stripes = np.sin((xx + yy) * (2 * np.pi / period)) * 34.0
    cell = 3.0 * scale
    checker = np.where(((xx // cell).astype(int) + (yy // cell).astype(int)) % 2 == 0, 26.0, -26.0)
    band = ((u > 0.42) & (u < 0.78)).astype(np.float32)
    rgb += (stripes * (1 - band * 0.4))[..., None] + (checker * band)[..., None]
    dots = ((xx % (9 * scale) < 1.6 * scale) & (yy % (9 * scale) < 1.6 * scale)).astype(np.float32)
    rgb = rgb * (1 - dots[..., None] * 0.55) + 255.0 * dots[..., None] * 0.55
    bars = ((yy // (16 * scale)).astype(int) % 5 == 0) & (u < 0.3)
    rgb[bars] = np.where((((xx[bars] // (5 * scale)).astype(int)) % 2 == 0)[:, None], 12.0, 247.0)
    bgr = np.clip(rgb[..., ::-1], 0, 255).astype(np.uint8)
    return bgr_to_qimage(bgr, scale)


RENDERERS: dict[str, Callable[[float, float, float], QImage]] = {
    "real": real,
    "discord": discord,
    "gradient": gradient,
}


@lru_cache(maxsize=8)
def render(name: str, width: float, height: float, scale: float) -> QImage:
    """Shared between callers: paint on a copy."""
    return RENDERERS[name](width, height, scale)

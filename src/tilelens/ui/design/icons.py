"""Vector icons drawn with QPainterPath on a 24 unit grid. No fonts, no image files."""

from __future__ import annotations

import math
from collections.abc import Callable
from functools import cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen

GRID = 24.0
Shape = tuple[QPainterPath, bool]  # (path, filled)


def _line(*pts: tuple[float, float], close: bool = False) -> QPainterPath:
    path = QPainterPath(QPointF(*pts[0]))
    for pt in pts[1:]:
        path.lineTo(QPointF(*pt))
    if close:
        path.closeSubpath()
    return path


def _dot(x: float, y: float, r: float = 1.1) -> QPainterPath:
    path = QPainterPath()
    path.addEllipse(QPointF(x, y), r, r)
    return path


def _circle(x: float, y: float, r: float) -> QPainterPath:
    return _dot(x, y, r)


def _round_rect(x: float, y: float, w: float, h: float, r: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(QRectF(x, y, w, h), r, r)
    return path


def _arc(cx: float, cy: float, r: float, start: float, span: float) -> QPainterPath:
    """Qt angles: degrees, counter-clockwise from 3 o'clock; a negative span runs clockwise."""
    box = QRectF(cx - r, cy - r, 2 * r, 2 * r)
    path = QPainterPath()
    path.arcMoveTo(box, start)
    path.arcTo(box, start, span)
    return path


def _star4(cx: float, cy: float, r: float, pinch: float = 0.2) -> QPainterPath:
    c = r * pinch
    path = QPainterPath(QPointF(cx, cy - r))
    path.quadTo(cx + c, cy - c, cx + r, cy)
    path.quadTo(cx + c, cy + c, cx, cy + r)
    path.quadTo(cx - c, cy + c, cx - r, cy)
    path.quadTo(cx - c, cy - c, cx, cy - r)
    path.closeSubpath()
    return path


def _gear() -> list[Shape]:
    teeth = 8
    r_in, r_out = 7.3, 9.6
    pts = []
    for k in range(teeth):
        a = math.tau * k / teeth
        for da, r in ((-0.30, r_in), (-0.17, r_out), (0.17, r_out), (0.30, r_in)):
            ang = a + da
            pts.append((12 + r * math.cos(ang), 12 + r * math.sin(ang)))
    return [(_line(*pts, close=True), False), (_circle(12, 12, 3.1), False)]


def _refresh() -> list[Shape]:
    start, span = 40.0, -290.0
    arc = _arc(12, 12, 8, start, span)
    end = arc.currentPosition()
    # direction of travel at the end of a clockwise arc
    ang = math.radians(start + span)
    tx, ty = math.sin(ang), math.cos(ang)
    px, py = -ty, tx
    tip = QPointF(end.x() + tx * 1.4, end.y() + ty * 1.4)
    head = QPainterPath(QPointF(tip.x() - tx * 4.2 + px * 3.4, tip.y() - ty * 4.2 + py * 3.4))
    head.lineTo(tip)
    head.lineTo(QPointF(tip.x() - tx * 4.2 - px * 3.4, tip.y() - ty * 4.2 - py * 3.4))
    return [(arc, False), (head, False)]


def _folder() -> list[Shape]:
    path = QPainterPath(QPointF(5, 19.5))
    path.lineTo(19, 19.5)
    path.quadTo(21, 19.5, 21, 17.5)
    path.lineTo(21, 9.5)
    path.quadTo(21, 7.5, 19, 7.5)
    path.lineTo(12, 7.5)
    path.lineTo(10.3, 5.2)
    path.lineTo(5, 5.2)
    path.quadTo(3, 5.2, 3, 7.2)
    path.lineTo(3, 17.5)
    path.quadTo(3, 19.5, 5, 19.5)
    return [(path, False)]


def _shield() -> list[Shape]:
    path = QPainterPath(QPointF(12, 3.2))
    path.lineTo(19.2, 5.9)
    path.lineTo(19.2, 11.6)
    path.cubicTo(19.2, 16.2, 16.2, 19.4, 12, 21)
    path.cubicTo(7.8, 19.4, 4.8, 16.2, 4.8, 11.6)
    path.lineTo(4.8, 5.9)
    path.closeSubpath()
    return [(path, False), (_line((8.8, 12.2), (11.1, 14.5), (15.3, 9.6)), False)]


def _lock() -> list[Shape]:
    shackle = QPainterPath(QPointF(8.2, 10.4))
    shackle.lineTo(8.2, 8)
    shackle.arcTo(QRectF(8.2, 4, 7.6, 7.6), 180, -180)
    shackle.lineTo(15.8, 10.4)
    return [
        (_round_rect(5, 10.4, 14, 9.8, 2.6), False),
        (shackle, False),
        (_dot(12, 15.3, 1.15), True),
    ]


def _keyboard() -> list[Shape]:
    dots = [(_dot(x, 10.2, 0.85), True) for x in (6.6, 10.2, 13.8, 17.4)]
    return [
        (_round_rect(2.5, 6.2, 19, 11.8, 3), False),
        *dots,
        (_line((8, 14.7), (16, 14.7)), False),
    ]


def _play() -> list[Shape]:
    return [(_line((8, 5.6), (18.6, 12), (8, 18.4), close=True), True)]


def _pause() -> list[Shape]:
    return [
        (_round_rect(6.6, 5.6, 3.8, 12.8, 1.4), True),
        (_round_rect(13.6, 5.6, 3.8, 12.8, 1.4), True),
    ]


@cache
def _shapes(name: str) -> tuple[Shape, ...]:
    return tuple(_BUILDERS[name]())


_BUILDERS: dict[str, Callable[[], list[Shape]]] = {
    "gear": _gear,
    "pause": _pause,
    "play": _play,
    "refresh": _refresh,
    "target": lambda: [
        (_circle(12, 12, 6.4), False),
        (_line((12, 2.8), (12, 8.2)), False),
        (_line((12, 15.8), (12, 21.2)), False),
        (_line((2.8, 12), (8.2, 12)), False),
        (_line((15.8, 12), (21.2, 12)), False),
        (_dot(12, 12, 1.3), True),
    ],
    "check": lambda: [(_line((5.2, 12.6), (10, 17.2), (18.8, 7.4)), False)],
    "chevron-down": lambda: [(_line((6, 9.4), (12, 15.4), (18, 9.4)), False)],
    "chevron-up": lambda: [(_line((6, 14.6), (12, 8.6), (18, 14.6)), False)],
    "chevron-right": lambda: [(_line((9.4, 6), (15.4, 12), (9.4, 18)), False)],
    "close": lambda: [
        (_line((6.6, 6.6), (17.4, 17.4)), False),
        (_line((17.4, 6.6), (6.6, 17.4)), False),
    ],
    "minus": lambda: [(_line((6, 12), (18, 12)), False)],
    "plus": lambda: [(_line((6, 12), (18, 12)), False), (_line((12, 6), (12, 18)), False)],
    "ellipsis": lambda: [
        (_dot(5.8, 12, 1.5), True),
        (_dot(12, 12, 1.5), True),
        (_dot(18.2, 12, 1.5), True),
    ],
    "shield": _shield,
    "lock": _lock,
    "alert-triangle": lambda: [
        (_line((12, 4.2), (21, 19.6), (3, 19.6), close=True), False),
        (_line((12, 9.8), (12, 14.2)), False),
        (_dot(12, 16.9, 1.0), True),
    ],
    "info": lambda: [
        (_circle(12, 12, 9), False),
        (_line((12, 11), (12, 16.4)), False),
        (_dot(12, 7.7, 1.05), True),
    ],
    "keyboard": _keyboard,
    "folder": _folder,
    "sparkle": lambda: [(_star4(10.5, 13.5, 7.6), False), (_star4(18.4, 5.8, 3.2), False)],
    "wand": lambda: [
        (_line((4.6, 19.4), (13.4, 10.6)), False),
        (_star4(17.4, 6.6, 3.8), False),
        (_dot(20.2, 13.2, 0.85), True),
        (_dot(10.8, 4.4, 0.85), True),
    ],
    "spinner": lambda: [(_arc(12, 12, 8.6, 90, -270), False)],
}

ALIASES = {
    "settings": "gear",
    "crosshair": "target",
    "collapse": "minus",
    "x": "close",
    "warning": "alert-triangle",
    "alert": "alert-triangle",
    "magic": "wand",
    "key": "keyboard",
    "reload": "refresh",
}

ICON_NAMES = tuple(_BUILDERS)


def has_icon(name: str) -> bool:
    return ALIASES.get(name, name) in _BUILDERS


def check_icon(name: str) -> str:
    """The name itself, or ValueError: a typo should fail where it is written, not on paint."""
    if not has_icon(name):
        raise ValueError(f"unknown icon {name!r}")
    return name


def paint_icon(p: QPainter, name: str, rect: QRectF, color: QColor, weight: float = 1.6) -> None:
    """Draw an icon centred in rect. weight is the stroke width in 24 unit grid steps."""
    key = ALIASES.get(name, name)
    if key not in _BUILDERS:
        raise ValueError(f"unknown icon {name!r}")
    r = QRectF(rect)
    side = min(r.width(), r.height())
    if side <= 0:
        return
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.translate(r.center().x() - side / 2, r.center().y() - side / 2)
    p.scale(side / GRID, side / GRID)
    stroke = QPen(
        color, weight, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin
    )
    fill_pen = QPen(
        color, 1.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin
    )
    for path, filled in _shapes(key):
        if filled:
            p.setBrush(color)
            p.setPen(fill_pen)
        else:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(stroke)
        p.drawPath(path)
    p.restore()


__all__ = ["ALIASES", "GRID", "ICON_NAMES", "check_icon", "has_icon", "paint_icon"]

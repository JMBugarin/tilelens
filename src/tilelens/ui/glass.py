"""Glass look for the overlay: a frame on the board, ghost tiles, the rack outline and pills."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen

from tilelens.solver.movegen import Move
from tilelens.state.memory import Coverage
from tilelens.state.names import square_name

CYAN = QColor(34, 211, 238)
GREEN = QColor(52, 211, 153)
AMBER = QColor(251, 191, 36)
MAGENTA = QColor(255, 20, 147)  # a board tile TileLens has read
INK = QColor(240, 246, 255)
GLASS = QColor(13, 18, 36, 215)
GHOST_FILL = QColor(8, 28, 44, 185)


def alpha(color: QColor, a: int) -> QColor:
    c = QColor(color)
    c.setAlpha(a)
    return c


@dataclass
class OverlayScene:
    """Everything the overlay draws, in overlay coordinates."""

    move: Move | None = None
    rect_of: Callable[[int, int], QRectF] | None = None
    board: QRectF | None = None
    coverage: Coverage | None = None
    rack: QRectF | None = None
    rack_read: tuple[int, int] | None = None
    value_of: Callable[[str, bool], int] | None = None
    framed: bool = False
    seen: list[QRectF] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return self.move is None and self.board is None and self.rack is None and not self.seen


def _font(px: float, bold: bool = True) -> QFont:
    f = QFont()
    f.setPixelSize(max(6, int(px)))
    f.setBold(bold)
    return f


def paint_pill(
    p: QPainter,
    anchor: QPointF,
    text: str,
    color: QColor,
    size: float = 13,
    align: str = "left",
    tick: bool = False,
) -> QRectF:
    """A rounded label on dark glass. align says which side of the pill sits on the anchor."""
    font = _font(size)
    fm = QFontMetricsF(font)
    pad = size * 0.75
    mark = size * 1.1 if tick else 0
    w = fm.horizontalAdvance(text) + 2 * pad + mark
    h = size * 2.0
    x = {"left": anchor.x(), "right": anchor.x() - w, "center": anchor.x() - w / 2}[align]
    rect = QRectF(x, anchor.y() - h / 2, w, h)
    p.setPen(QPen(color, 1.6))
    p.setBrush(alpha(GLASS, 225))
    p.drawRoundedRect(rect, h / 2, h / 2)
    if tick:
        c = QPointF(rect.left() + pad + size * 0.4, rect.center().y())
        p.setBrush(color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(c, size * 0.45, size * 0.45)
        p.setPen(QPen(QColor(10, 20, 30), max(1.2, size * 0.13)))
        path = QPainterPath(QPointF(c.x() - size * 0.2, c.y()))
        path.lineTo(c.x() - size * 0.05, c.y() + size * 0.17)
        path.lineTo(c.x() + size * 0.22, c.y() - size * 0.15)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
    p.setFont(font)
    p.setPen(color)
    p.drawText(rect.adjusted(pad + mark, 0, -pad, 0), Qt.AlignmentFlag.AlignCenter, text)
    return rect


def paint_board_frame(
    p: QPainter, rect: QRectF, coverage: Coverage | None, framed: bool = False
) -> None:
    if coverage in (Coverage.TRACKING, Coverage.PARTIAL):
        pen = QPen(alpha(AMBER, 220), 2)
        pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 10, 10)
        if framed:
            text = "Whole board in view · some squares behind game panels"
        elif coverage == Coverage.TRACKING:
            text = "Partial view · tracking"
        else:
            text = "Partial view · zoom out to check"
        paint_pill(p, QPointF(rect.left() + 12, rect.top() + 4), text, AMBER)
        return
    arm = max(18.0, min(48.0, min(rect.width(), rect.height()) * 0.08))
    p.setPen(QPen(CYAN, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    for cx, cy, dx, dy in (
        (rect.left(), rect.top(), 1, 1),
        (rect.right(), rect.top(), -1, 1),
        (rect.left(), rect.bottom(), 1, -1),
        (rect.right(), rect.bottom(), -1, -1),
    ):
        p.drawLine(QPointF(cx, cy), QPointF(cx + dx * arm, cy))
        p.drawLine(QPointF(cx, cy), QPointF(cx, cy + dy * arm))
    if coverage == Coverage.FULL:
        paint_pill(p, QPointF(rect.left() + 12, rect.top() + 4), "Board aligned", CYAN, tick=True)


def paint_ghosts(
    p: QPainter,
    move: Move,
    rect_of: Callable[[int, int], QRectF],
    value_of: Callable[[str, bool], int] | None = None,
) -> None:
    """Cyan ghost tiles for the new tiles; existing letters of the words get a glow ring."""
    existing = {
        (cell.row, cell.col) for word in move.breakdown.words for cell in word.cells if not cell.new
    }
    for r, c in existing:
        rect = rect_of(r, c)
        p.setPen(QPen(alpha(CYAN, 200), max(1.5, rect.width() * 0.05)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, rect.width() * 0.16, rect.width() * 0.16)
    rects = []
    for t in move.tiles:
        rect = rect_of(t.row, t.col)
        rects.append(rect)
        radius = rect.width() * 0.16
        for i, a in enumerate((26, 18, 10)):
            grow = rect.width() * 0.05 * (i + 1)
            p.setPen(QPen(alpha(CYAN, a), rect.width() * 0.06))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rect.adjusted(-grow, -grow, grow, grow), radius + grow, radius + grow)
        pen = QPen(CYAN, max(1.5, rect.width() * 0.055))
        pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(GHOST_FILL)
        p.drawRoundedRect(rect, radius, radius)
        p.setFont(_font(rect.height() * 0.52))
        p.setPen(alpha(CYAN, 255) if t.blank else INK)
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, t.letter)
        if value_of is not None:
            small = rect.adjusted(0, 0, -rect.width() * 0.1, -rect.height() * 0.06)
            p.setFont(_font(rect.height() * 0.2))
            p.setPen(alpha(CYAN, 230))
            p.drawText(
                small,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom,
                str(value_of(t.letter, t.blank)),
            )
    if not rects:
        return
    box = rects[0]
    for r in rects[1:]:
        box = box.united(r)
    size = max(10.0, min(16.0, rects[0].height() * 0.3))
    paint_pill(
        p,
        QPointF(box.center().x(), box.top() - size * 1.4),
        f"+{move.score} pts",
        CYAN,
        size=size,
        align="center",
    )


def paint_seen(p: QPainter, rects: list[QRectF]) -> None:
    """Outline every board tile TileLens has read, so a tile left out shows as a gap."""
    if not rects:
        return
    side = min(rects[0].width(), rects[0].height())
    radius = side * 0.18
    p.setPen(QPen(alpha(MAGENTA, 245), max(3.0, side * 0.07)))
    p.setBrush(Qt.BrushStyle.NoBrush)
    for rect in rects:
        p.drawRoundedRect(rect.adjusted(-1, -1, 1, 1), radius, radius)


def paint_rack(p: QPainter, rect: QRectF, read: tuple[int, int] | None) -> None:
    known, total = read or (0, 0)
    ok = total > 0 and known == total
    color = GREEN if ok else AMBER
    p.setPen(QPen(color, 3))
    p.setBrush(Qt.BrushStyle.NoBrush)
    outer = rect.adjusted(-6, -6, 6, 6)
    p.drawRoundedRect(outer, 10, 10)
    text = f"Rack confirmed · {known}/{total}" if ok else f"Rack · {known}/{total} read"
    paint_pill(p, QPointF(outer.right() + 12, outer.center().y()), text, color, tick=ok)


def paint_scene(p: QPainter, scene: OverlayScene) -> None:
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    if scene.board is not None:
        paint_board_frame(p, scene.board, scene.coverage, scene.framed)
    if scene.rack is not None:
        paint_rack(p, scene.rack, scene.rack_read)
    if scene.move is not None and scene.rect_of is not None:
        paint_ghosts(p, scene.move, scene.rect_of, scene.value_of)
    paint_seen(p, scene.seen)


__all__ = [
    "AMBER",
    "CYAN",
    "GREEN",
    "MAGENTA",
    "OverlayScene",
    "paint_pill",
    "paint_scene",
    "square_name",
]

"""What the click-through overlay paints over the game.

The suggested play is a row of smoked-glass ghost tiles, a soft bar under the letters the word
runs through and one glass score tag. Alignment guides and the rack outline are transient and
live in the window's fade; nothing here marks tiles that were read.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Hashable
from dataclasses import dataclass
from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, QSizeF, Qt
from PySide6.QtGui import (
    QColor,
    QFontMetricsF,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
    QTransform,
)
from PySide6.QtWidgets import QWidget

from tilelens.solver.movegen import Move
from tilelens.state.memory import Coverage
from tilelens.state.names import square_name
from tilelens.ui.design.material import Elevation, paint_glass, paint_shadow
from tilelens.ui.design.tokens import DARK, MATERIALS, Material, type_font, with_alpha

GUIDE_MODES = ("auto", "always", "off")
GUIDE_HOLD_MS = 1400

CYAN = DARK.accent
GREEN = DARK.success
AMBER = DARK.warning
INK = DARK.text

TILE_FILL = 0.92
TILE_RADIUS = 0.2
MIN_LETTER_PX = 9.0
MIN_VALUE_PX = 7.0
VALUE_MIN_TILE = 20.0
EDGE_MARGIN = 4.0
NEAR_SQUARES = 3


@dataclass(eq=False)
class OverlayScene:
    """Everything the overlay draws, in overlay coordinates (points).

    rank and total label the score tag ("1 of 3"); asking holds the rects of tiles TileLens needs
    an answer for; guides_mode is the user's setting (auto, always, off); guide_alpha pins the
    guides to a fixed opacity instead of letting the window fade them; lock extends the lock-state
    signature that decides when the guides flash; occupied says whether a board square holds a
    tile, so the score tag keeps off the game's own tiles.
    """

    move: Move | None = None
    rect_of: Callable[[int, int], QRectF] | None = None
    board: QRectF | None = None
    coverage: Coverage | None = None
    rack: QRectF | None = None
    rack_read: tuple[int, int] | None = None
    value_of: Callable[[str, bool], int] | None = None
    framed: bool = False
    rank: int | None = None
    total: int | None = None
    asking: tuple[QRectF, ...] = ()
    guides_mode: str = "auto"
    guide_alpha: float | None = None
    lock: Hashable = None
    occupied: Callable[[int, int], bool] | None = None

    @property
    def has_guides(self) -> bool:
        return self.board is not None or self.rack is not None

    @property
    def empty(self) -> bool:
        if self.move is not None or self.asking:
            return False
        return self.guides_mode == "off" or not self.has_guides

    @property
    def rack_ok(self) -> bool:
        known, total = self.rack_read or (0, 0)
        return total > 0 and known == total

    @property
    def needs_attention(self) -> bool:
        if self.coverage in (Coverage.TRACKING, Coverage.PARTIAL):
            return True
        if self.rack is not None and self.rack_read is not None:
            return self.rack_read[1] > 0 and not self.rack_ok
        return False

    @property
    def lock_key(self) -> tuple:
        return (
            self.board is not None,
            self.coverage,
            self.framed,
            self.rack is not None,
            self.rack_ok,
            self.lock,
        )

    def key(self) -> tuple:
        """What paints: two scenes with the same key draw the same pixels."""
        move = self.move
        play = None
        if move is not None:
            geo = ()
            if self.rect_of is not None:
                geo = tuple(_q(self.rect_of(r, c)) for r, c in _play_cells(move))
            values = ()
            if self.value_of is not None:
                values = tuple(self.value_of(t.letter, t.blank) for t in move.tiles)
            play = (move.tiles, move.score, move.word, move.row, move.col, move.direction)
            play = (play, geo, values, tuple(near_taken(move, self.occupied)))
        return (
            play,
            self.rank,
            self.total,
            _q(self.board),
            self.coverage,
            _q(self.rack),
            self.rack_read,
            self.framed,
            tuple(_q(r) for r in self.asking),
            self.guides_mode,
            self.guide_alpha,
            self.lock,
        )

    def __eq__(self, other: object) -> bool:
        return isinstance(other, OverlayScene) and self.key() == other.key()

    __hash__ = None  # type: ignore[assignment]


def _q(rect: QRectF | None) -> tuple[int, int, int, int] | None:
    if rect is None:
        return None
    return (
        round(rect.x() * 4),
        round(rect.y() * 4),
        round(rect.width() * 4),
        round(rect.height() * 4),
    )


def snap(value: float, dpr: float) -> float:
    return round(value * dpr) / dpr


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _main_word(move: Move):
    words = getattr(getattr(move, "breakdown", None), "words", ()) or ()
    for w in words:
        if (w.row, w.col, w.direction) == (move.row, move.col, move.direction):
            return w
    return words[0] if words else None


def _existing_cells(move: Move) -> list[tuple[int, int]]:
    word = _main_word(move)
    if word is None:
        return []
    return [(c.row, c.col) for c in word.cells if not c.new]


def _play_cells(move: Move) -> list[tuple[int, int]]:
    return [(t.row, t.col) for t in move.tiles] + _existing_cells(move)


def _crossing_existing(move: Move) -> list[tuple[int, int]]:
    words = getattr(getattr(move, "breakdown", None), "words", ()) or ()
    main = _main_word(move)
    out = []
    for w in words:
        if w is main:
            continue
        out.extend((c.row, c.col) for c in w.cells if not c.new)
    return out


def near_taken(
    move: Move, occupied: Callable[[int, int], bool] | None, reach: int = NEAR_SQUARES
) -> list[tuple[int, int]]:
    """Squares within reach of the play that hold a tile, apart from the play's own."""
    if occupied is None or not move.tiles:
        return []
    own = {(t.row, t.col) for t in move.tiles}
    rows = [t.row for t in move.tiles]
    cols = [t.col for t in move.tiles]
    out = []
    for r in range(min(rows) - reach, max(rows) + reach + 1):
        for c in range(min(cols) - reach, max(cols) + reach + 1):
            if (r, c) in own or r < 0 or c < 0:
                continue
            try:
                if occupied(r, c):
                    out.append((r, c))
            except IndexError:
                continue
    return out


def square_of(rect_of: Callable[[int, int], QRectF], row: int, col: int) -> QRectF:
    """The whole square at full pitch around what rect_of returns, whatever inset it applies."""
    base = rect_of(row, col)
    bw, bh = base.width(), base.height()
    px = rect_of(row, col + 1).x() - base.x()
    py = rect_of(row + 1, col).y() - base.y()
    if not (0.9 * bw <= px <= 1.5 * bw):
        px = bw
    if not (0.9 * bh <= py <= 1.5 * bh):
        py = bh
    c = base.center()
    return QRectF(c.x() - px / 2, c.y() - py / 2, px, py)


def tile_rect(square: QRectF, dpr: float) -> QRectF:
    w = max(1 / dpr, snap(square.width() * TILE_FILL, dpr))
    h = max(1 / dpr, snap(square.height() * TILE_FILL, dpr))
    c = square.center()
    return QRectF(snap(c.x() - w / 2, dpr), snap(c.y() - h / 2, dpr), w, h)


def _dpr(p: QPainter) -> float:
    try:
        return max(1.0, float(p.device().devicePixelRatioF()))
    except (AttributeError, RuntimeError):
        return 1.0


def _bounds(p: QPainter) -> QRectF:
    dev = p.device()
    if isinstance(dev, QWidget):
        return QRectF(0, 0, dev.width(), dev.height())
    dpr = _dpr(p)
    return QRectF(0, 0, dev.width() / dpr, dev.height() / dpr)


# glass sprites: rendered once per size and device ratio, then blitted


def _px(v: float, dpr: float) -> int:
    return max(1, round(v * dpr))


@lru_cache(maxsize=32)
def _tile_glass(w_px: int, h_px: int, dpr: float, radius_q: int) -> QImage:
    """A smoked pane: dark body, upper sheen, specular rim, and a lens that brightens its
    middle and gathers a little light along the lower edge."""
    pad = 1.0
    pad_px = math.ceil(pad * dpr)
    img = QImage(w_px + 2 * pad_px, h_px + 2 * pad_px, QImage.Format.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(dpr)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    w, h, radius = w_px / dpr, h_px / dpr, radius_q / 16
    off = pad_px / dpr
    rect = QRectF(off, off, w, h)
    paint_glass(p, rect, radius, DARK, blur=False, strength=1.0)
    clip = QPainterPath()
    clip.addRoundedRect(rect, radius, radius)
    p.setClipPath(clip)
    lens = QRadialGradient(QPointF(rect.center().x(), rect.top() + h * 0.18), w * 0.85)
    lens.setColorAt(0.0, with_alpha(DARK.rim_top, 0.20))
    lens.setColorAt(0.55, with_alpha(DARK.rim_top, 0.05))
    lens.setColorAt(1.0, with_alpha(DARK.rim_top, 0.0))
    p.fillRect(rect, lens)
    pool = QLinearGradient(rect.bottomLeft(), QPointF(rect.left(), rect.bottom() - h * 0.34))
    pool.setColorAt(0.0, with_alpha(DARK.accent, 0.26))
    pool.setColorAt(1.0, with_alpha(DARK.accent, 0.0))
    p.fillRect(rect, pool)
    p.end()
    return img


def _glass_off(dpr: float) -> float:
    return math.ceil(1.0 * dpr) / dpr


@lru_cache(maxsize=32)
def _tile_shadow(w_px: int, h_px: int, dpr: float, radius_q: int, k_q: int) -> tuple[QImage, float]:
    k = k_q / 8
    elev = Elevation(7.5 * k, 2.5 * k, 0.36, 1.5 * k, 0.8 * k, 0.32)
    pad = math.ceil(elev.ambient_blur * 2.2 + elev.ambient_dy + 2)
    w, h = w_px / dpr, h_px / dpr
    img = QImage(
        w_px + 2 * math.ceil(pad * dpr),
        h_px + 2 * math.ceil(pad * dpr),
        QImage.Format.Format_ARGB32_Premultiplied,
    )
    img.setDevicePixelRatio(dpr)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    paint_shadow(p, QRectF(pad, pad, w, h), radius_q / 16, elev, DARK, dpr)
    p.end()
    return img, float(pad)


@lru_cache(maxsize=16)
def _bar_glass(w_px: int, h_px: int, dpr: float) -> QImage:
    pad_px = math.ceil(1.5 * dpr)
    img = QImage(w_px + 2 * pad_px, h_px + 2 * pad_px, QImage.Format.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(dpr)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    w, h = w_px / dpr, h_px / dpr
    off = pad_px / dpr
    rect = QRectF(off, off, w, h)
    halo = QPainterPath()
    halo.addRoundedRect(rect.adjusted(-1, -1, 1, 1), h / 2 + 1, h / 2 + 1)
    p.fillPath(halo, with_alpha(DARK.rim_top, 0.30))
    paint_glass(p, rect, h / 2, DARK, blur=False, strength=1.0, rim=False, sheen=False)
    p.end()
    return img


@dataclass(frozen=True)
class PlayLayout:
    """Where a play was painted, for placing what goes beside it."""

    tiles: tuple[QRectF, ...]
    existing: tuple[QRectF, ...]
    word: QRectF
    cell: float
    others: tuple[QRectF, ...] = ()


def _font_px(role: str, px: float, tabular: bool = False):
    from tilelens.ui.design.tokens import TYPE_ROLES

    return type_font(role, px / TYPE_ROLES[role][0], tabular=tabular)


def _baseline(fm: QFontMetricsF, cy: float) -> float:
    return cy + fm.capHeight() / 2


def paint_ghosts(
    p: QPainter,
    move: Move,
    rect_of: Callable[[int, int], QRectF],
    value_of: Callable[[str, bool], int] | None = None,
    material: Material | None = None,
    occupied: Callable[[int, int], bool] | None = None,
) -> PlayLayout:
    """Smoked-glass tiles for the new letters, soft bars under the existing ones the word uses."""
    dpr = _dpr(p)
    m = material or DARK
    squares = [square_of(rect_of, t.row, t.col) for t in move.tiles]
    held_cells = _existing_cells(move)
    held = [square_of(rect_of, r, c) for r, c in held_cells]
    near = dict.fromkeys(_crossing_existing(move) + near_taken(move, occupied))
    others = [square_of(rect_of, r, c) for r, c in near if (r, c) not in held_cells]
    tiles = [tile_rect(s, dpr) for s in squares]
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    for sq in held:
        _paint_bar(p, sq, dpr)
    if tiles:
        w_px, h_px = _px(tiles[0].width(), dpr), _px(tiles[0].height(), dpr)
        side = min(tiles[0].width(), tiles[0].height())
        radius_q = round(side * TILE_RADIUS * 16)
        k_q = round(_clamp(side / 24, 0.6, 2.4) * 8)
        shadow, spad = _tile_shadow(w_px, h_px, dpr, radius_q, k_q)
        glass = _tile_glass(w_px, h_px, dpr, radius_q)
        goff = _glass_off(dpr)
        for r in tiles:
            p.drawImage(QPointF(r.x() - spad, r.y() - spad), shadow)
        for r in tiles:
            p.drawImage(QPointF(r.x() - goff, r.y() - goff), glass)
        for r, t in zip(tiles, move.tiles, strict=True):
            value = value_of(t.letter, t.blank) if value_of is not None else None
            _paint_letter(p, r, t.letter, t.blank, value, m)
    p.restore()
    cells = squares + held
    if not cells:
        return PlayLayout((), (), QRectF(), 0.0)
    word = cells[0]
    for c in cells[1:]:
        word = word.united(c)
    cell = min(squares[0].width(), squares[0].height()) if squares else 0.0
    return PlayLayout(tuple(tiles), tuple(held), word, cell, tuple(others))


def _paint_bar(p: QPainter, square: QRectF, dpr: float) -> None:
    pitch = min(square.width(), square.height())
    h = max(2.5, snap(pitch * 0.085, dpr))
    w = max(h * 2, snap(pitch * 0.5, dpr))
    img = _bar_glass(_px(w, dpr), _px(h, dpr), dpr)
    off = math.ceil(1.5 * dpr) / dpr
    x = snap(square.center().x() - w / 2, dpr)
    y = snap(square.top() + square.height() * 0.84 - h / 2, dpr)
    p.drawImage(QPointF(x - off, y - off), img)


def _paint_letter(
    p: QPainter, r: QRectF, letter: str, blank: bool, value: int | None, m: Material
) -> None:
    side = min(r.width(), r.height())
    show_value = value is not None and side >= VALUE_MIN_TILE
    px = min(max(MIN_LETTER_PX, side * 0.52), side * 0.84)
    font = _font_px("word", px)
    p.setFont(font)
    fm = QFontMetricsF(font)
    ink = DARK.accent if blank else DARK.text
    cx = r.center().x() - (side * 0.03 if show_value else 0)
    x = cx - fm.horizontalAdvance(letter) / 2
    y = _baseline(fm, r.center().y() - (side * 0.02 if show_value else 0))
    p.setPen(with_alpha(DARK.shadow, 0.45))
    p.drawText(QPointF(x, y + max(0.8, side * 0.03)), letter)
    p.setPen(ink)
    p.drawText(QPointF(x, y), letter)
    if blank and side >= VALUE_MIN_TILE:
        d = side * 0.16
        ring = QRectF(r.left() + side * 0.11, r.top() + side * 0.11, d, d)
        p.setPen(QPen(with_alpha(DARK.accent, 0.9), max(1.0, side * 0.04)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(ring)
    if show_value:
        vf = _font_px("caption", max(MIN_VALUE_PX, side * 0.21), tabular=True)
        p.setFont(vf)
        vm = QFontMetricsF(vf)
        text = str(value)
        vx = r.right() - side * 0.1 - vm.horizontalAdvance(text)
        vy = r.bottom() - side * 0.1
        p.setPen(DARK.accent if blank else DARK.text2)
        p.drawText(QPointF(vx, vy), text)


# score tag


@dataclass(frozen=True)
class _Sprite:
    image: QImage
    size: QSizeF
    pad: float


@lru_cache(maxsize=24)
def _tag_sprite(score: str, rank: str, h_q: int, dpr: float, mat: str, strength_q: int) -> _Sprite:
    m = MATERIALS.get(mat, DARK)
    h = h_q / 4
    main_px = round(h * 0.54)
    small_px = max(9.0, main_px * 0.74)
    f_main = _font_px("score", main_px, tabular=True)
    f_small = _font_px("label", small_px)
    fm_main, fm_small = QFontMetricsF(f_main), QFontMetricsF(f_small)
    pad_x = h * 0.46
    gap = main_px * 0.26
    split = h * 0.34
    w_score = fm_main.horizontalAdvance(score)
    w_unit = fm_small.horizontalAdvance("pts")
    w_rank = fm_small.horizontalAdvance(rank) if rank else 0.0
    width = pad_x * 2 + w_score + gap + w_unit
    if rank:
        width += split * 2 + 1 + w_rank
    elev = Elevation(9, 3, 0.26, 2, 1, 0.26)
    pad = math.ceil(elev.ambient_blur * 2.2 + elev.ambient_dy + 2)
    img = QImage(
        _px(width + 2 * pad, dpr),
        _px(h + 2 * pad, dpr),
        QImage.Format.Format_ARGB32_Premultiplied,
    )
    img.setDevicePixelRatio(dpr)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    rect = QRectF(pad, pad, width, h)
    paint_shadow(p, rect, h / 2, elev, m, dpr)
    paint_glass(p, rect, h / 2, m, blur=False, strength=strength_q / 20)
    cy = rect.center().y()
    x = rect.left() + pad_x
    p.setFont(f_main)
    p.setPen(m.text)
    p.drawText(QPointF(x, _baseline(fm_main, cy)), score)
    x += w_score + gap
    p.setFont(f_small)
    p.setPen(m.text)
    p.drawText(QPointF(x, _baseline(fm_small, cy)), "pts")
    if rank:
        x += w_unit + split
        p.fillRect(QRectF(x, cy - h * 0.2, 1, h * 0.4), m.divider)
        x += 1 + split
        p.setPen(m.text)
        p.drawText(QPointF(x, _baseline(fm_small, cy)), rank)
    p.end()
    return _Sprite(img, QSizeF(width, h), float(pad))


def tag_text(move: Move, rank: int | None, total: int | None) -> tuple[str, str]:
    rank_text = ""
    if rank is not None and (total is None or total > 1):
        rank_text = f"{rank} of {total}" if total else f"#{rank}"
    return f"+{move.score}", rank_text


def tag_height(cell: float) -> float:
    return _clamp(round(cell * 0.9), 20.0, 34.0)


def place_tag(
    size: QSizeF,
    layout: PlayLayout,
    vertical: bool,
    bounds: QRectF,
    avoid: tuple[QRectF, ...] = (),
) -> QRectF:
    """The slot beside the word that stays on screen, off the rack and off the letters."""
    w, h = size.width(), size.height()
    gap = _clamp(layout.cell * 0.28, 5.0, 12.0)
    far = layout.cell
    box = layout.word
    first = layout.tiles[0] if layout.tiles else box
    c = box.center()
    if vertical:
        spots = [
            (c.x() - w / 2, box.top() - gap - h),
            (box.right() + gap, box.top()),
            (box.left() - gap - w, box.top()),
            (c.x() - w / 2, box.bottom() + gap),
            (box.right() + gap, box.bottom() - h),
            (box.left() - gap - w, box.bottom() - h),
            (box.right() + gap + far, box.top()),
            (box.left() - gap - far - w, box.top()),
        ]
    else:
        spots = [
            (box.left(), box.top() - gap - h),
            (box.left(), box.bottom() + gap),
            (box.right() - w, box.top() - gap - h),
            (box.right() - w, box.bottom() + gap),
            (box.left() - gap - w, first.center().y() - h / 2),
            (box.right() + gap, first.center().y() - h / 2),
            (c.x() - w / 2, box.top() - gap - h),
            (c.x() - w / 2, box.bottom() + gap),
            (box.left(), box.top() - gap - far - h),
            (box.left(), box.bottom() + gap + far),
            (box.right() - w, box.top() - gap - far - h),
            (box.right() - w, box.bottom() + gap + far),
        ]
    lim = bounds.adjusted(EDGE_MARGIN, EDGE_MARGIN, -EDGE_MARGIN, -EDGE_MARGIN)
    best, best_cost = None, math.inf
    for i, (x, y) in enumerate(spots):
        x = _clamp(x, lim.left(), max(lim.left(), lim.right() - w))
        y = _clamp(y, lim.top(), max(lim.top(), lim.bottom() - h))
        slot = QRectF(x, y, w, h)
        cost = i * 2.0 + math.hypot(x - spots[i][0], y - spots[i][1]) * 0.05
        for t in layout.tiles:
            cost += _overlap(slot, t.adjusted(-2, -2, 2, 2)) * 40
        for e in layout.existing:
            cost += _overlap(slot, e) * 6
        for o in layout.others:
            cost += _overlap(slot, o) * 12
        for a in avoid:
            cost += _overlap(slot, a) * 40
        if cost < best_cost:
            best, best_cost = slot, cost
    return best if best is not None else QRectF(lim.topLeft(), size)


def _overlap(a: QRectF, b: QRectF) -> float:
    i = a.intersected(b)
    return max(0.0, i.width()) * max(0.0, i.height())


def paint_tag(
    p: QPainter,
    layout: PlayLayout,
    move: Move,
    rank: int | None,
    total: int | None,
    bounds: QRectF,
    material: Material,
    strength: float = 1.0,
    avoid: tuple[QRectF, ...] = (),
) -> QRectF | None:
    if not layout.tiles:
        return None
    dpr = _dpr(p)
    score, rank_text = tag_text(move, rank, total)
    h = tag_height(layout.cell)
    sprite = _tag_sprite(
        score, rank_text, round(h * 4), dpr, material.name, round(_clamp(strength, 0.6, 1.3) * 20)
    )
    vertical = move.direction != "H"
    slot = place_tag(sprite.size, layout, vertical, bounds, avoid)
    x, y = snap(slot.x(), dpr), snap(slot.y(), dpr)
    p.drawImage(QPointF(x - sprite.pad, y - sprite.pad), sprite.image)
    return QRectF(x, y, slot.width(), slot.height())


# guides


def _underlay(m: Material) -> QColor:
    return with_alpha(m.solid_top if not m.dark else m.solid_bottom, 0.62)


def _stroke(p: QPainter, path: QPainterPath, color: QColor, width: float, m: Material) -> None:
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(
        QPen(
            _underlay(m),
            width + 2.4,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
    )
    p.drawPath(path)
    p.setPen(
        QPen(
            color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin
        )
    )
    p.drawPath(path)


def _corner(arm: float, radius: float) -> QPainterPath:
    path = QPainterPath(QPointF(0, arm))
    path.lineTo(0, radius)
    path.arcTo(QRectF(0, 0, 2 * radius, 2 * radius), 180, -90)
    path.lineTo(arm, 0)
    return path


def paint_board_guide(
    p: QPainter, rect: QRectF, color: QColor, m: Material, bounds: QRectF, cell: float
) -> None:
    """Four corner brackets just outside the board."""
    width = _clamp(cell * 0.1, 2.0, 3.5)
    arm = _clamp(min(rect.width(), rect.height()) * 0.05, 14.0, 44.0)
    radius = _clamp(arm * 0.4, 5.0, 14.0)
    lim = bounds.adjusted(width, width, -width, -width)
    out = rect.adjusted(-width, -width, width, width)
    out = QRectF(
        QPointF(max(out.left(), lim.left()), max(out.top(), lim.top())),
        QPointF(min(out.right(), lim.right()), min(out.bottom(), lim.bottom())),
    )
    base = _corner(arm, radius)
    for pos, sx, sy in (
        (out.topLeft(), 1, 1),
        (out.topRight(), -1, 1),
        (out.bottomLeft(), 1, -1),
        (out.bottomRight(), -1, -1),
    ):
        tf = QTransform().translate(pos.x(), pos.y()).scale(sx, sy)
        _stroke(p, tf.map(base), color, width, m)


def rack_ring(rack: QRectF) -> tuple[QRectF, float]:
    pad = _clamp(rack.height() * 0.1, 3.0, 8.0)
    return rack.adjusted(-pad, -pad, pad, pad), rack.height() * 0.2 + pad


def paint_rack_guide(p: QPainter, rack: QRectF, color: QColor, m: Material) -> QRectF:
    ring, radius = rack_ring(rack)
    width = _clamp(rack.height() * 0.045, 2.0, 3.0)
    path = QPainterPath()
    path.addRoundedRect(ring, radius, radius)
    _stroke(p, path, color, width, m)
    return ring


@lru_cache(maxsize=16)
def _label_sprite(text: str, rgba: int, size_q: int, dpr: float, mat: str) -> _Sprite:
    m = MATERIALS.get(mat, DARK)
    px = size_q / 4
    font = _font_px("label", px)
    fm = QFontMetricsF(font)
    h = round(px * 1.9)
    dot = px * 0.52
    pad = h * 0.42
    width = pad + dot + px * 0.5 + fm.horizontalAdvance(text) + pad
    elev = Elevation(8, 2, 0.22, 1.6, 1, 0.22)
    spad = math.ceil(elev.ambient_blur * 2.2 + elev.ambient_dy + 2)
    img = QImage(
        _px(width + 2 * spad, dpr),
        _px(h + 2 * spad, dpr),
        QImage.Format.Format_ARGB32_Premultiplied,
    )
    img.setDevicePixelRatio(dpr)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    rect = QRectF(spad, spad, width, h)
    paint_shadow(p, rect, h / 2, elev, m, dpr)
    paint_glass(p, rect, h / 2, m, blur=False)
    color = QColor.fromRgba(rgba)
    cy = rect.center().y()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawEllipse(QPointF(rect.left() + pad + dot / 2, cy), dot / 2, dot / 2)
    p.setFont(font)
    p.setPen(m.text)
    p.drawText(QPointF(rect.left() + pad + dot + px * 0.5, _baseline(fm, cy)), text)
    p.end()
    return _Sprite(img, QSizeF(width, h), float(spad))


def label_size(text: str, color: QColor, m: Material, dpr: float, px: float = 12.0) -> QSizeF:
    return _label_sprite(text, color.rgba(), round(px * 4), dpr, m.name).size


def paint_label(
    p: QPainter, topleft: QPointF, text: str, color: QColor, m: Material, px: float = 12.0
) -> QRectF:
    dpr = _dpr(p)
    s = _label_sprite(text, color.rgba(), round(px * 4), dpr, m.name)
    x, y = snap(topleft.x(), dpr), snap(topleft.y(), dpr)
    p.drawImage(QPointF(x - s.pad, y - s.pad), s.image)
    return QRectF(x, y, s.size.width(), s.size.height())


def _free_slot(
    size: QSizeF, spots: list[tuple[float, float]], bounds: QRectF, avoid: list[QRectF]
) -> QRectF | None:
    lim = bounds.adjusted(EDGE_MARGIN, EDGE_MARGIN, -EDGE_MARGIN, -EDGE_MARGIN)
    for x, y in spots:
        slot = QRectF(x, y, size.width(), size.height())
        if not lim.contains(slot):
            continue
        if all(_overlap(slot, a) <= 0 for a in avoid):
            return slot
    return None


def board_note(scene: OverlayScene) -> str:
    if scene.coverage == Coverage.TRACKING:
        return "Partial view · tracking"
    if scene.coverage == Coverage.PARTIAL:
        return "Some squares hidden" if scene.framed else "Partial view · zoom out"
    return ""


def rack_note(scene: OverlayScene) -> str:
    if scene.rack is None or scene.rack_read is None or scene.rack_ok:
        return ""
    known, total = scene.rack_read
    return f"Rack {known}/{total}" if total > 0 else ""


def paint_guides(
    p: QPainter, scene: OverlayScene, m: Material, bounds: QRectF, cell: float
) -> list[QRectF]:
    """The brackets, the rack outline and their short notes; returns the rects it used."""
    used: list[QRectF] = []
    taken: list[QRectF] = []
    board = scene.board
    ring = None
    if scene.rack is not None:
        if scene.rack_read is None or scene.rack_read[1] == 0:
            tone = m.accent
        else:
            tone = m.success if scene.rack_ok else m.warning
        ring = paint_rack_guide(p, scene.rack, tone, m)
        used.append(ring)
        taken.append(ring.adjusted(-4, -4, 4, 4))
    if board is not None:
        attention = scene.coverage in (Coverage.TRACKING, Coverage.PARTIAL)
        paint_board_guide(p, board, m.warning if attention else m.accent, m, bounds, cell)
        used.append(board)
    dpr = _dpr(p)
    avoid = list(taken)
    if board is not None:
        avoid.append(board)
    note = board_note(scene)
    if note and board is not None:
        size = label_size(note, m.warning, m, dpr)
        gap = 6.0
        slot = _free_slot(
            size,
            [
                (board.right() - size.width(), board.bottom() + gap),
                (board.left(), board.bottom() + gap),
                (board.right() - size.width(), board.top() - gap - size.height()),
                (board.left(), board.top() - gap - size.height()),
            ],
            bounds,
            avoid,
        )
        if slot is not None:
            used.append(paint_label(p, slot.topLeft(), note, m.warning, m))
            avoid.append(used[-1].adjusted(-4, -4, 4, 4))
    note = rack_note(scene)
    if note and ring is not None:
        size = label_size(note, m.warning, m, dpr)
        gap = 8.0
        slot = _free_slot(
            size,
            [
                (ring.right() + gap, ring.center().y() - size.height() / 2),
                (ring.left() - gap - size.width(), ring.center().y() - size.height() / 2),
                (ring.right() - size.width(), ring.bottom() + gap),
                (ring.left(), ring.bottom() + gap),
            ],
            bounds,
            avoid,
        )
        if slot is not None:
            used.append(paint_label(p, slot.topLeft(), note, m.warning, m))
    return used


def paint_asking(p: QPainter, rects: tuple[QRectF, ...], m: Material) -> None:
    """One soft ring around the tile that needs an answer, no label."""
    for r in rects:
        gap = _clamp(min(r.width(), r.height()) * 0.1, 2.0, 5.0)
        ring = r.adjusted(-gap, -gap, gap, gap)
        radius = min(r.width(), r.height()) * TILE_RADIUS + gap
        width = _clamp(min(r.width(), r.height()) * 0.06, 2.0, 3.0)
        path = QPainterPath()
        path.addRoundedRect(ring, radius, radius)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(with_alpha(m.warning, 0.20), width * 3.4))
        p.drawPath(path)
        _stroke(p, path, m.warning, width, m)


def paint_scene(
    p: QPainter,
    scene: OverlayScene,
    guides: float | None = None,
    material: Material | None = None,
    strength: float = 1.0,
    bounds: QRectF | None = None,
) -> None:
    """guides is the opacity of the transient guides; None takes scene.guide_alpha, else 1."""
    m = material or DARK
    bounds = bounds if bounds is not None else _bounds(p)
    fade = scene.guide_alpha if guides is None else guides
    fade = 1.0 if fade is None else _clamp(fade, 0.0, 1.0)
    if scene.guides_mode == "off":
        fade = 0.0
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    cell = _cell_hint(scene)
    if fade > 0.004 and scene.has_guides:
        p.setOpacity(fade)
        paint_guides(p, scene, m, bounds, cell)
        p.setOpacity(1.0)
    if scene.asking:
        paint_asking(p, scene.asking, m)
    if scene.move is not None and scene.rect_of is not None:
        layout = paint_ghosts(p, scene.move, scene.rect_of, scene.value_of, m, scene.occupied)
        keep = []
        if scene.rack is not None:
            keep.append(rack_ring(scene.rack)[0].adjusted(-4, -4, 4, 4))
        keep.extend(r.adjusted(-4, -4, 4, 4) for r in scene.asking)
        paint_tag(p, layout, scene.move, scene.rank, scene.total, bounds, m, strength, tuple(keep))
    p.restore()


def _cell_hint(scene: OverlayScene) -> float:
    if scene.move is not None and scene.rect_of is not None and scene.move.tiles:
        t = scene.move.tiles[0]
        sq = square_of(scene.rect_of, t.row, t.col)
        return min(sq.width(), sq.height())
    if scene.rack is not None:
        return scene.rack.height() * 0.5
    return 24.0


__all__ = [
    "AMBER",
    "CYAN",
    "GREEN",
    "GUIDE_HOLD_MS",
    "GUIDE_MODES",
    "INK",
    "OverlayScene",
    "PlayLayout",
    "paint_asking",
    "paint_ghosts",
    "paint_guides",
    "paint_scene",
    "paint_tag",
    "place_tag",
    "snap",
    "square_name",
    "square_of",
    "tile_rect",
]

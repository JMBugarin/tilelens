"""The click-through overlay drawn on the game: ghost tiles, one score tag, transient guides."""

from __future__ import annotations

import dataclasses
from functools import lru_cache

import cv2
import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QWidget

from tilelens.rules.board import WALL_CELL, Board, Tile
from tilelens.rules.scoring import score_placement
from tilelens.solver.movegen import Move
from tilelens.state.memory import Coverage
from tilelens.ui.design.tokens import MATERIALS
from tilelens.ui.glass import paint_scene
from tilelens.ui.preview import CellMapper
from ui_gallery import backdrops, ctx, scheme
from ui_gallery.registry import register

STEADY = 0.0  # guides after they faded
SHOWN = 1.0  # guides while they flash or while something needs attention


def _scene(guides: float | None = STEADY, rank: int | None = 1, total: int | None = 3, **kw):
    """The scene the session would build: the gallery's real board plus the new fields."""
    scene = ctx.overlay_scene(**kw)
    return dataclasses.replace(
        scene, rank=rank, total=total, guide_alpha=guides, occupied=_occupied
    )


def _occupied(row: int, col: int) -> bool:
    board = ctx.scenario().board
    if not (0 <= row < board.height and 0 <= col < board.width):
        return False
    return board.get(row, col) not in (None, WALL_CELL)


def _material_name() -> str | None:
    name = scheme.current()
    return name if name in MATERIALS else None


def _window(scene):
    overlay = ctx.make_overlay(scene)
    name = _material_name()
    if name is not None:
        overlay.set_material(MATERIALS[name])
    return overlay


def _overlay(name: str, doc: str, guides: float | None = STEADY, **scene) -> None:
    def build():
        return _window(_scene(guides, **scene))

    build.__doc__ = doc
    register(f"overlay/{name}", kind="overlay")(build)


# the suggested play

_overlay("ghosts", "The best play: smoked ghost tiles, soft bars under its letters, score tag")
_overlay("ghosts-blank", "A play that uses a blank tile and forms a crossing word", move=1, rank=2)
_overlay("ghosts-crossing", "A play that forms two crossing words", move=2, rank=3)
_overlay("play-only", "Ghost tiles with no board or rack found", board=False, rack=False)

# the three plays in turn

for _i, _word in enumerate(("first", "second", "third")):
    _overlay(f"rank-{_word}", f"Play {_i + 1} of 3 selected", move=_i, rank=_i + 1)
_overlay("rank-only", "A rank without a total", rank=2, total=None, move=1)
_overlay("single-play", "The only play found: the tag carries no rank", rank=1, total=1)

# guides: the flash after the lock changes, and what stays while something needs attention

_overlay("guides-flash", "Brackets and rack outline right after the lock changed", guides=SHOWN)
_overlay("guides-fading", "The same guides halfway through the fade", guides=0.5)
_overlay("frame-only", "Aligned board and no play yet: the brackets flash", guides=SHOWN, move=None)
_overlay("no-rack", "Board and play, rack not found", guides=SHOWN, rack=False)
_overlay("rack-unsure", "Only part of the rack was read", SHOWN, rack_read=(5, 7))
_overlay("tracking", "Partial view that is tracked", SHOWN, coverage=Coverage.TRACKING)
_overlay("partial", "Partial view, zoom out to check", SHOWN, coverage=Coverage.PARTIAL)
_overlay(
    "partial-framed",
    "Squares hidden by the game's panels",
    SHOWN,
    coverage=Coverage.PARTIAL,
    framed=True,
)


def _with(guides_mode: str, **kw):
    return dataclasses.replace(_scene(SHOWN, **kw), guides_mode=guides_mode)


@register("overlay/guides-off", kind="overlay")
def guides_off():
    """Guides turned off: only the play is drawn, whatever the lock"""
    return _window(_with("off", coverage=Coverage.PARTIAL))


@register("overlay/guides-always", kind="overlay")
def guides_always():
    """Guides always on: shown with the play on a good lock"""
    return _window(_with("always"))


# the tile that needs an answer


def _asking(rects):
    return dataclasses.replace(_scene(STEADY), asking=tuple(rects))


@register("overlay/asking-tile", kind="overlay")
def asking_tile():
    """One square TileLens needs an answer for: a soft amber ring, no label"""
    sc = ctx.scenario()
    return _window(_asking([sc.mapper.rect(22, 31, 0.03)]))


@register("overlay/asking-rack", kind="overlay")
def asking_rack():
    """A rack tile that needs an answer, with the rack otherwise confirmed"""
    sc = ctx.scenario()
    r = sc.rack_rect
    tile = QRectF(r.left() + 5 * 56.0, r.top(), 50.0, r.height())
    return _window(_asking([tile]))


# a move built for edges and long words


@lru_cache(maxsize=8)
def _play(word: str, row: int, col: int, direction: str, blank_at: int = -1) -> Move:
    sc = ctx.scenario()
    dr, dc = (0, 1) if direction == "H" else (1, 0)
    tiles = tuple(Tile(row + dr * i, col + dc * i, ch, i == blank_at) for i, ch in enumerate(word))
    scored = score_placement(Board.empty_like(sc.profile), tiles, sc.profile)
    return Move(tiles, scored.total, word, row, col, direction, scored)


def _edges() -> tuple[int, int, int, int]:
    """First and last row and column of the real board that are in view and playable."""
    sc = ctx.scenario()
    board = sc.board
    free = np.array(
        [[board.get(r, c) != WALL_CELL for c in range(board.width)] for r in range(board.height)]
    )
    free &= sc.reading.view.visible
    rows = np.nonzero(free.sum(axis=1) >= 15)[0]
    cols = np.nonzero(free[rows.min() : rows.max() + 1].all(axis=0))[0]
    return int(rows.min()), int(rows.max()), int(cols.min()), int(cols.max())


def _custom(move: Move, **kw):
    sc = ctx.scenario()
    return dataclasses.replace(
        _scene(STEADY, **kw),
        move=move,
        rect_of=sc.mapper.rect,
        value_of=sc.profile.tile_value,
    )


@register("overlay/long-word-top", kind="overlay")
def long_word_top():
    """A 15-letter word along the top edge of the board, ending at the right edge"""
    top, _, _, right = _edges()
    return _window(_custom(_play("INTERNATIONALLY", top, right - 14, "H", 4), rank=1))


@register("overlay/long-word-right", kind="overlay")
def long_word_right():
    """A 15-letter word down the right edge: the tag stays inside the overlay"""
    top, _, _, right = _edges()
    return _window(_custom(_play("INTERNATIONALLY", top, right, "V"), rank=2))


@register("overlay/long-word-bottom", kind="overlay")
def long_word_bottom():
    """A 15-letter word along the bottom edge, just above the rack: the tag keeps off the rack"""
    _, bottom, left, _ = _edges()
    return _window(_custom(_play("INTERNATIONALLY", bottom, left + 8, "H"), rank=1))


@register("overlay/three-digit-score", kind="overlay")
def three_digit_score():
    """A score of 128 on an 8-letter word with a blank"""
    top, _, left, _ = _edges()
    move = dataclasses.replace(_play("QUARTZES", top + 6, left + 4, "H", 7), score=128)
    return _window(_custom(move, rank=2, total=3))


# the same play at other zoom levels: the whole frame is scaled, the overlay follows it


def _zoomed(factor: float, view: tuple[int, int], focus: QPointF):
    """(stage, game image layer, overlay window) with the board scaled by factor."""
    sc = ctx.scenario()
    frame = cv2.resize(sc.frame, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
    img = backdrops.bgr_to_qimage(frame, ctx.FRAME_SCALE)
    w, h = view
    ox = min(0.0, max(w - img.width() / ctx.FRAME_SCALE, w / 2 - focus.x() * factor))
    oy = min(0.0, max(h - img.height() / ctx.FRAME_SCALE, h / 2 - focus.y() * factor))
    if img.width() / ctx.FRAME_SCALE < w:
        ox = (w - img.width() / ctx.FRAME_SCALE) / 2
    if img.height() / ctx.FRAME_SCALE < h:
        oy = (h - img.height() / ctx.FRAME_SCALE) / 2
    m = sc.mapper
    mapper = CellMapper(
        m.x0 * factor + ox, m.y0 * factor + oy, m.pitch_x * factor, m.pitch_y * factor
    )

    def moved(r: QRectF) -> QRectF:
        return QRectF(
            r.x() * factor + ox, r.y() * factor + oy, r.width() * factor, r.height() * factor
        )

    scene = dataclasses.replace(
        _scene(STEADY),
        rect_of=mapper.rect,
        board=moved(sc.board_rect),
        rack=moved(sc.rack_rect),
    )
    return img, QPointF(ox, oy), scene


def _zoom_state(factor: float, view: tuple[int, int], guides: float):
    img, at, scene = _zoomed(factor, view, QPointF(525.0, 310.0))
    scene = dataclasses.replace(scene, guide_alpha=guides)
    return ctx.stage(view, (img, (at.x(), at.y())), (_window(scene), (0, 0), view))


@register("overlay/zoomed-out", kind="overlay", backdrops=("real",))
def zoomed_out():
    """Discord zoomed out: 11 pt squares, the tiles and the tag stay legible"""
    return _zoom_state(0.44, (420, 280), STEADY)


@register("overlay/zoomed-out-guides", kind="overlay", backdrops=("real",))
def zoomed_out_guides():
    """Zoomed out with the guides showing"""
    return _zoom_state(0.44, (420, 280), SHOWN)


@register("overlay/zoomed-in", kind="overlay", backdrops=("real",))
def zoomed_in():
    """Discord zoomed in: 62 pt squares, the tag stops growing"""
    return _zoom_state(2.4, (640, 420), STEADY)


# one device pixel per point: what a 100 percent display shows


class _Pixels(QWidget):
    """Shows an image of a dpr 1 render with every pixel enlarged, over the stage."""

    def __init__(self, image: QImage):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._image = image

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        p.drawImage(QRectF(self.rect()), self._image)
        p.end()


@register("overlay/dpr-1", kind="overlay")
def dpr_one():
    """Rendered at 100 percent scale, pixels enlarged: edges stay crisp on a plain display"""
    scene = _scene(SHOWN)
    w, h = ctx.scenario().size_pt
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    name = _material_name()
    paint_scene(p, scene, material=MATERIALS.get(name) if name else None, bounds=QRectF(0, 0, w, h))
    p.end()
    view = _Pixels(img)
    return ctx.stage((w, h), (view, (0, 0), (w, h)))

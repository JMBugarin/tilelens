import dataclasses

import numpy as np
import pytest

pytestmark = pytest.mark.gui
pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tilelens import telemetry  # noqa: E402
from tilelens.rules.board import WALL_CELL, Board, Tile  # noqa: E402
from tilelens.rules.scoring import CellScore, MoveScore, WordScore  # noqa: E402
from tilelens.solver.movegen import Move  # noqa: E402
from tilelens.state.memory import Coverage  # noqa: E402
from tilelens.ui import glass  # noqa: E402
from tilelens.ui.design import DARK, LIGHT, set_reduce_motion  # noqa: E402
from tilelens.ui.glass import OverlayScene, paint_ghosts, paint_scene, paint_tag, rack_ring  # noqa: E402
from tilelens.ui.overlay import OverlayWindow  # noqa: E402
from tilelens.ui.preview import CellMapper, paint_board, paint_move  # noqa: E402

GAME_COLORS = [
    (251, 230, 202),
    (190, 244, 178),
    (242, 163, 127),
    (190, 226, 250),
    (248, 210, 114),
    (150, 222, 133),
    (255, 255, 255),
    (240, 128, 96),
]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def calm(qapp):
    set_reduce_motion(False)
    yield
    set_reduce_motion(False)


def make_move(word, row, col, direction="H", through=(), blank_at=-1, score=34) -> Move:
    dr, dc = (0, 1) if direction == "H" else (1, 0)
    tiles, cells = [], []
    for i, ch in enumerate(word):
        r, c = row + dr * i, col + dc * i
        new = i not in through
        if new:
            tiles.append(Tile(r, c, ch, i == blank_at))
        cells.append(CellScore(r, c, ch, i == blank_at, new, 1, 1))
    main = WordScore(word, row, col, direction, tuple(cells), 10, 1, score)
    breakdown = MoveScore((main,), len(tiles), 0, score)
    return Move(tuple(tiles), score, word, row, col, direction, breakdown)


def mapper_for(cell: float, x0: float = 40.0, y0: float = 20.0) -> CellMapper:
    return CellMapper(x0, y0, cell, cell)


def render(draw, size: tuple[float, float], dpr: float = 1.0) -> np.ndarray:
    w, h = size
    img = QImage(round(w * dpr), round(h * dpr), QImage.Format.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(dpr)
    img.fill(0)
    p = QPainter(img)
    draw(p)
    p.end()
    straight = img.convertToFormat(QImage.Format.Format_ARGB32)
    arr = np.frombuffer(straight.constBits(), np.uint8, count=straight.sizeInBytes())
    arr = arr.reshape(straight.height(), straight.bytesPerLine())[:, : straight.width() * 4]
    return arr.reshape(straight.height(), straight.width(), 4).copy()


def luminance(rgb) -> float:
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(rgb[0]) + 0.7152 * lin(rgb[1]) + 0.0722 * lin(rgb[2])


def contrast(a, b) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def busy_scene(cell: float = 24.0, **kw) -> OverlayScene:
    m = mapper_for(cell)
    taken = {(r, c) for r in range(2, 13) for c in range(1, 14) if (r * 7 + c * 3) % 5 == 0}
    move = make_move("TORNADOS", 3, 6, "V", through=(5,))
    taken -= {(t.row, t.col) for t in move.tiles}
    board = QRectF(m.x0, m.y0, 15 * cell, 15 * cell)
    rack = QRectF(board.center().x() - 100, board.bottom() + 24, 200, 36)
    base = {
        "move": move,
        "rect_of": m.rect,
        "board": board,
        "coverage": Coverage.FULL,
        "rack": rack,
        "rack_read": (7, 7),
        "value_of": lambda ch, blank: 0 if blank else 1,
        "framed": True,
        "rank": 1,
        "total": 3,
        "guide_alpha": 1.0,
        "occupied": lambda r, c: (r, c) in taken,
        "asking": (m.rect(12, 12, 0.03),),
    }
    base.update(kw)
    return OverlayScene(**base)


def test_scene_has_no_recognition_highlight():
    names = {f.name for f in dataclasses.fields(OverlayScene)}
    assert "seen" not in names
    assert not hasattr(glass, "MAGENTA")
    assert not hasattr(glass, "paint_seen")
    assert not hasattr(glass, "paint_pill")


@pytest.mark.parametrize("dpr", [1.0, 2.0])
def test_busy_scene_has_no_hot_pink(dpr):
    scene = busy_scene(guides_mode="always", coverage=Coverage.PARTIAL, rack_read=(5, 7))
    arr = render(lambda p: paint_scene(p, scene, bounds=QRectF(0, 0, 440, 480)), (440, 480), dpr)
    b, g, r, a = (arr[..., i].astype(int) for i in range(4))
    pink = (a > 0) & (abs(r - 255) < 50) & (abs(g - 20) < 50) & (abs(b - 147) < 50)
    assert not pink.any()
    assert (a > 0).any()


def test_squares_the_play_does_not_use_stay_transparent():
    scene = busy_scene(24.0)
    bounds = QRectF(0, 0, 440, 480)
    arr = render(lambda p: paint_scene(p, scene, bounds=bounds), (440, 480), 1.0)
    layout, tag, _ = layout_of(scene, bounds)
    alpha = arr[..., 3]
    zones = [layout.word.adjusted(-30, -30, 30, 30), tag.adjusted(-30, -30, 30, 30)]
    zones.append(scene.asking[0].adjusted(-12, -12, 12, 12))
    checked = 0
    for r in range(15):
        for c in range(15):
            if not scene.occupied(r, c):
                continue
            rect = scene.rect_of(r, c)
            if any(rect.intersects(z) for z in zones):
                continue
            x0, y0 = int(rect.left()) + 1, int(rect.top()) + 1
            x1, y1 = int(rect.right()) - 1, int(rect.bottom()) - 1
            assert alpha[y0:y1, x0:x1].max() <= 8, (r, c)
            checked += 1
    assert checked > 10


def test_nothing_is_drawn_without_a_play_or_guides():
    assert OverlayScene().empty
    scene = busy_scene(move=None, asking=(), guide_alpha=0.0, guides_mode="off")
    assert scene.empty
    arr = render(lambda p: paint_scene(p, scene, bounds=QRectF(0, 0, 440, 480)), (440, 480))
    assert not arr[..., 3].any()
    assert not OverlayScene(board=QRectF(0, 0, 10, 10)).empty
    assert not OverlayScene(asking=(QRectF(0, 0, 10, 10),)).empty


def test_scene_key_follows_what_paints():
    a, b = busy_scene(), busy_scene()
    assert a == b
    assert a.key() == b.key()
    assert busy_scene(rank=2) != a
    assert busy_scene(total=None) != a
    assert busy_scene(guides_mode="always") != a
    assert busy_scene(rack_read=(6, 7)) != a
    assert busy_scene(asking=()) != a
    assert busy_scene(lock="x") != a
    moved = busy_scene()
    moved.rect_of = mapper_for(24.0, 41.0).rect
    assert moved != a
    other = dataclasses.replace(a, move=make_move("TORNADOS", 3, 6, "V", through=(5,), score=40))
    assert other != a
    with pytest.raises(TypeError):
        hash(a)


@pytest.mark.parametrize("cell", [9.0, 14.0, 24.0, 51.0, 90.0])
@pytest.mark.parametrize("dpr", [1.0, 1.25, 2.0])
def test_ghost_tiles_sit_on_their_squares(cell, dpr):
    m = mapper_for(cell, 31.3, 17.7)
    move = make_move("HOH", 2, 3, "H")
    size = (31.3 + cell * 8, 17.7 + cell * 6)

    def draw(p):
        paint_ghosts(p, move, m.rect, lambda ch, blank: 1)

    arr = render(draw, size, dpr)
    body = arr[..., 3] > 230
    ys, xs = np.nonzero(body)
    assert len(xs)
    tol = 1.5 / dpr + 0.02 * cell
    left = (m.x0 + 3 * cell) * dpr
    top = (m.y0 + 2 * cell) * dpr
    right = (m.x0 + 6 * cell) * dpr
    bottom = (m.y0 + 3 * cell) * dpr
    assert abs((xs.min() + xs.max() + 1) / 2 - (left + right) / 2) <= tol * dpr
    assert abs((ys.min() + ys.max() + 1) / 2 - (top + bottom) / 2) <= tol * dpr
    assert xs.min() >= left - tol * dpr and xs.max() <= right + tol * dpr
    assert ys.min() >= top - tol * dpr and ys.max() <= bottom + tol * dpr
    width_ratio = (xs.max() - xs.min() + 1) / (right - left)
    assert 0.84 <= width_ratio <= 1.0


@pytest.mark.parametrize("cell", [24.0, 51.0, 90.0])
def test_letter_is_centered_on_its_tile(cell):
    m = mapper_for(cell, 20.0, 20.0)
    move = make_move("O", 1, 1)
    arr = render(lambda p: paint_ghosts(p, move, m.rect, None), (20 + cell * 4, 20 + cell * 4), 2.0)
    r, g, b, a = (arr[..., i].astype(int) for i in (2, 1, 0, 3))
    ink = (a > 230) & (r > 215) & (g > 215) & (b > 215)
    ys, xs = np.nonzero(ink)
    assert len(xs)
    cx, cy = (xs.min() + xs.max() + 1) / 4, (ys.min() + ys.max() + 1) / 4
    tile_cx, tile_cy = 20 + cell * 1.5, 20 + cell * 1.5
    assert abs(cx - tile_cx) <= cell * 0.06
    assert abs(cy - tile_cy) <= cell * 0.08


def test_ghost_tile_is_distinct_from_every_game_color():
    m = mapper_for(51.0)
    move = make_move("HOH", 2, 3, "H")
    arr = render(lambda p: paint_ghosts(p, move, m.rect, None), (320, 200), 1.0)
    cx, cy = int(m.x0 + 3.1 * 51), int(m.y0 + 2.12 * 51)
    patch = arr[cy : cy + 6, cx : cx + 6].reshape(-1, 4).astype(float).mean(axis=0)
    body = (patch[2], patch[1], patch[0])
    assert luminance(body) < 0.1
    for color in GAME_COLORS:
        assert contrast(body, color) >= 3.0
    assert contrast(DARK.text.getRgb()[:3], body) >= 7.0


def test_blank_letter_is_marked():
    m = mapper_for(51.0)
    plain = render(
        lambda p: paint_ghosts(p, make_move("A", 1, 1), m.rect, lambda ch, bl: 1), (200, 160)
    )
    blank = render(
        lambda p: paint_ghosts(p, make_move("A", 1, 1, blank_at=0), m.rect, lambda ch, bl: 0),
        (200, 160),
    )
    assert np.any(plain != blank)


def layout_of(scene: OverlayScene, bounds: QRectF, dpr: float = 1.0):
    out = {}
    keep = []

    def draw(p):
        material = DARK
        layout = paint_ghosts(
            p, scene.move, scene.rect_of, scene.value_of, material, scene.occupied
        )
        if scene.rack is not None:
            keep.append(rack_ring(scene.rack)[0].adjusted(-4, -4, 4, 4))
        out["layout"] = layout
        out["tag"] = paint_tag(
            p, layout, scene.move, scene.rank, scene.total, bounds, material, 1.0, tuple(keep)
        )

    render(draw, (bounds.width(), bounds.height()), dpr)
    return out["layout"], out["tag"], keep


EDGE_PLAYS = [
    ("INTERNATIONALLY", 0, 0, "H"),
    ("INTERNATIONALLY", 14, 0, "H"),
    ("INTERNATIONALLY", 0, 0, "V"),
    ("INTERNATIONALLY", 0, 14, "V"),
    ("QUARTZES", 0, 7, "H"),
    ("QUARTZES", 7, 0, "V"),
    ("AT", 14, 13, "H"),
    ("ZA", 0, 0, "V"),
]


@pytest.mark.parametrize("cell", [9.0, 24.0, 51.0, 90.0])
@pytest.mark.parametrize(("word", "row", "col", "direction"), EDGE_PLAYS)
def test_tag_stays_inside_off_the_rack_and_off_the_first_tile(cell, word, row, col, direction):
    m = mapper_for(cell, 40.0, 40.0)
    move = make_move(word, row, col, direction)
    board = QRectF(m.x0, m.y0, 15 * cell, 15 * cell)
    rack = QRectF(board.center().x() - 120, board.bottom() + 18, 240, 40)
    bounds = QRectF(0, 0, board.right() + 40, rack.bottom() + 24)
    scene = OverlayScene(
        move=move, rect_of=m.rect, board=board, rack=rack, rack_read=(7, 7), rank=2, total=3
    )
    layout, tag, keep = layout_of(scene, bounds)
    assert tag is not None
    assert bounds.contains(tag)
    assert not tag.intersects(layout.tiles[0])
    assert not tag.intersects(keep[0])


def test_tag_keeps_off_neighbouring_tiles_when_there_is_room():
    cell = 30.0
    m = mapper_for(cell)
    move = make_move("OCEAN", 7, 5, "H")
    above = {(6, c) for c in range(2, 14)}
    scene = OverlayScene(
        move=move,
        rect_of=m.rect,
        occupied=lambda r, c: (r, c) in above,
        rank=1,
        total=3,
    )
    bounds = QRectF(0, 0, 560, 420)
    layout, tag, _ = layout_of(scene, bounds)
    assert tag is not None and layout.others
    for r in layout.others:
        assert not tag.intersects(r)


def test_existing_letters_get_a_bar_not_a_tile():
    m = mapper_for(40.0)
    move = make_move("TONE", 3, 2, "H", through=(1,))
    layout, _, _ = layout_of(
        OverlayScene(move=move, rect_of=m.rect, rank=1, total=1), QRectF(0, 0, 400, 300)
    )
    assert len(layout.tiles) == 3 and len(layout.existing) == 1


def test_asking_ring_surrounds_the_square_and_leaves_it_clear():
    m = mapper_for(40.0)
    rect = m.rect(4, 4, 0.03)
    scene = OverlayScene(asking=(rect,))
    arr = render(lambda p: paint_scene(p, scene, bounds=QRectF(0, 0, 320, 260)), (320, 260), 2.0)
    inner = rect.adjusted(6, 6, -6, -6)
    assert not arr[
        int(inner.top() * 2) : int(inner.bottom() * 2),
        int(inner.left() * 2) : int(inner.right() * 2),
        3,
    ].any()
    top = int(rect.top() * 2)
    px = arr[top - 10 : top - 2, int(rect.center().x() * 2) - 4 : int(rect.center().x() * 2) + 4]
    strongest = px.reshape(-1, 4)[px[..., 3].argmax()]
    b, g, r, a = (int(v) for v in strongest)
    assert a > 150 and r > 200 and g > 140 and b < 120


@pytest.mark.parametrize("material", [DARK, LIGHT])
@pytest.mark.parametrize(
    "backdrop", [(0, 0, 0), (255, 255, 255), (251, 230, 202), (49, 51, 56), (240, 100, 90)]
)
def test_tag_text_reads_on_any_backdrop(material, backdrop):
    m = mapper_for(30.0)
    move = make_move("OCEAN", 4, 4)
    bounds = QRectF(0, 0, 360, 260)
    img = QImage(360, 260, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(*backdrop))
    p = QPainter(img)
    layout = paint_ghosts(p, move, m.rect, None, material)
    tag = paint_tag(p, layout, move, 1, 3, bounds, material)
    p.end()
    body = img.pixelColor(int(tag.left() + tag.height() * 0.2), int(tag.center().y()))
    ink = material.text
    a = ink.alphaF()
    blended = tuple(
        ink.getRgb()[i] * a + (body.red(), body.green(), body.blue())[i] * (1 - a) for i in range(3)
    )
    assert contrast(blended, (body.red(), body.green(), body.blue())) >= 4.5


def window_with(scene) -> OverlayWindow:
    win = OverlayWindow()
    win.resize(440, 480)
    win.show_scene(scene)
    return win


def test_guides_flash_when_the_lock_changes_then_fade_away():
    win = OverlayWindow()
    locked = busy_scene(guide_alpha=None, move=None)
    win.show_scene(locked)
    assert win.guide_alpha == 1.0 and win.guides_pending
    assert win._hold.isActive()
    win._release_guides()
    assert win.guide_alpha == 0.0 and not win.guides_pending
    win.show_scene(dataclasses.replace(locked, rank=2))
    assert win.guide_alpha == 0.0
    win.show_scene(dataclasses.replace(locked, lock="board moved"))
    assert win.guide_alpha == 1.0


def test_guides_stay_while_something_needs_attention():
    win = OverlayWindow()
    win.show_scene(busy_scene(guide_alpha=None, coverage=Coverage.PARTIAL))
    win._release_guides()
    assert win.guide_alpha == 1.0
    win.show_scene(busy_scene(guide_alpha=None, rack_read=(5, 7)))
    win._release_guides()
    assert win.guide_alpha == 1.0
    win.show_scene(busy_scene(guide_alpha=None))
    assert win._hold.isActive()
    win._release_guides()
    assert win.guide_alpha == 0.0


def test_guides_mode_off_and_always():
    win = OverlayWindow()
    win.show_scene(busy_scene(guide_alpha=None, guides_mode="off", coverage=Coverage.PARTIAL))
    assert win.guide_alpha == 0.0
    win.show_scene(busy_scene(guide_alpha=None, guides_mode="always"))
    win._release_guides()
    assert win.guide_alpha == 1.0
    win.show_scene(busy_scene(guide_alpha=None, guides_mode="auto"))
    win._release_guides()
    assert win.guide_alpha == 0.0


def test_pinned_guide_alpha_is_used_as_given():
    win = OverlayWindow()
    win.show_scene(busy_scene(guide_alpha=0.4))
    assert win.guide_alpha == pytest.approx(0.4)
    assert not win._hold.isActive()


def test_an_equal_scene_does_not_redraw(monkeypatch):
    win = OverlayWindow()
    calls = []
    monkeypatch.setattr(win, "update", lambda *a: calls.append(1))
    win.show_scene(busy_scene(guide_alpha=0.0))
    assert len(calls) == 1
    win.show_scene(busy_scene(guide_alpha=0.0))
    win.show_scene(busy_scene(guide_alpha=0.0))
    assert len(calls) == 1
    win.show_scene(busy_scene(guide_alpha=0.0, rank=2))
    assert len(calls) == 2
    win.clear()
    assert len(calls) == 3


def test_paint_reports_its_time_when_telemetry_is_on(monkeypatch):
    events = []
    monkeypatch.setattr(telemetry, "enabled", lambda: True)
    monkeypatch.setattr(telemetry, "emit", lambda ev, **kw: events.append((ev, kw)))
    win = window_with(busy_scene(guide_alpha=0.0))
    win.grab()
    assert events
    ev, fields = events[-1]
    assert ev == "paint" and fields["kind"] == "overlay" and fields["paint_ms"] >= 0


def test_paint_is_silent_when_telemetry_is_off(monkeypatch):
    events = []
    monkeypatch.setattr(telemetry, "enabled", lambda: False)
    monkeypatch.setattr(telemetry, "emit", lambda ev, **kw: events.append(ev))
    win = window_with(busy_scene(guide_alpha=0.0))
    win.grab()
    assert not events


def test_capture_suppression_hides_and_restores():
    win = window_with(busy_scene(guide_alpha=0.0))
    win.begin_capture()
    assert win.suppressed and win.windowOpacity() == 0.0
    win.end_capture()
    assert not win.suppressed and win.windowOpacity() == 1.0
    assert len(win.suppress_log) == 1
    win.clear()
    win.begin_capture()
    assert not win.suppressed


def test_show_move_keeps_working_with_a_mapper():
    win = OverlayWindow()
    m = mapper_for(30.0)
    move = make_move("OCEAN", 7, 5)
    win.show_move(move, m)
    assert win.move_shown is move
    win.show_move(None, None)
    assert win.move_shown is None


def test_preview_mapper_is_unchanged():
    m = CellMapper(10.0, 20.0, 30.0, 40.0)
    r = m.rect(2, 3)
    assert r.x() == pytest.approx(10 + (3 + 0.06) * 30)
    assert r.y() == pytest.approx(20 + (2 + 0.06) * 40)
    assert r.width() == pytest.approx(30 * 0.88)
    assert r.height() == pytest.approx(40 * 0.88)


def test_preview_skips_walls_and_draws_the_play_as_ghost_tiles():
    cells = [[None] * 6 for _ in range(4)]
    cells[0][0] = WALL_CELL
    cells[1][1] = ("A", False)
    board = Board(6, 4, cells)
    m = CellMapper(4.0, 4.0, 24.0, 24.0)
    move = make_move("TOP", 2, 1, "H")

    def draw(p):
        paint_board(p, board, m)
        paint_move(p, move, lambda r, c: m.rect(r, c, 0.04))

    arr = render(draw, (160, 120), 2.0)
    assert arr[8:40, 8:40, 3].max() == 0
    field = arr[2 * 24 * 2 + 8 + 20, 4 * 2 + 5 * 24 * 2 + 40]
    assert field[3] == 255
    tile = arr[(4 + 2 * 24 + 12) * 2, (4 + 1 * 24 + 4) * 2]
    assert luminance((tile[2], tile[1], tile[0])) < 0.1


def test_tag_text_shows_a_rank_only_when_there_is_a_choice():
    move = make_move("AT", 1, 1)
    assert glass.tag_text(move, 2, 3) == ("+34", "2 of 3")
    assert glass.tag_text(move, 1, 1) == ("+34", "")
    assert glass.tag_text(move, None, None) == ("+34", "")
    assert glass.tag_text(move, 2, None) == ("+34", "#2")
    big = dataclasses.replace(move, score=128)
    assert glass.tag_text(big, 1, 3)[0] == "+128"


def test_taken_squares_near_a_play_skip_its_own_and_the_board_edge():
    move = make_move("AT", 0, 0)
    seen = []

    def occupied(r, c):
        seen.append((r, c))
        if r > 20:
            raise IndexError
        return (r, c) in {(0, 0), (1, 1), (2, 3)}

    assert glass.near_taken(move, occupied) == [(1, 1), (2, 3)]
    assert all(r >= 0 and c >= 0 for r, c in seen)
    assert glass.near_taken(move, None) == []

"""Realistic data and factories for the states.

Two worlds are built here, both without a live capture loop so every run is the same:

    the real board    tests/fixtures/discord/board-grown.png read with the real pipeline, a rack
                      drawn under it, and moves found by the real solver. Used by the panel,
                      the overlay and the auto-calibration dialog.
    the demo board    the synthetic game of the tests. Used by the main window and the manual
                      calibration and correction dialogs.

Everything that constructs a user interface class lives in the make_* functions, so a rename in
the application changes one place here.
"""

from __future__ import annotations

import dataclasses
import shutil
import sys
import tempfile
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication, QToolTip, QWidget

from tilelens.platforms.base import Region
from tilelens.rules.lexicon import Lexicon
from tilelens.rules.profile import load_profile
from tilelens.solver.movegen import MoveGenerator, SearchResult
from tilelens.state.controller import HELD, WAITING, Snapshot, Status
from tilelens.state.memory import Coverage
from tilelens.ui.glass import OverlayScene
from tilelens.ui.preview import CellMapper
from tilelens.vision.autoread import ViewReader
from tilelens.vision.lexfix import drop_isolated
from tilelens.vision.recognizer import COMMITTED, Calibration, CellRead, RackRead, Reading
from ui_gallery import backdrops, compose, scheme
from ui_gallery.compose import Stage

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "discord"
WORDS = ROOT / "profiles" / "demo_words.txt"

FRAME_SCALE = 2.0  # the fixture is a Retina capture: two pixels per point
RACK = "AEOTNR?"
PEACH_BGR = (178, 214, 242)
MAIN_SIZE = (520, 400)  # what the application resizes its main window to
PANEL_INSET = (24, 72)  # how the session places the panel in the capture region

_deferred: list[Callable[[], None]] = []
_workdir: Path | None = None


def defer(fn: Callable[[], None]) -> None:
    """Run fn after the current state has been grabbed."""
    _deferred.append(fn)


def run_deferred() -> None:
    """Runs the cleanups last-in first-out; one that raises is reported like any exception
    inside a Qt callback, so the state it belongs to is marked failed."""
    while _deferred:
        fn = _deferred.pop()
        try:
            fn()
        except Exception:
            sys.excepthook(*sys.exc_info())
    QApplication.processEvents()


def workdir() -> Path:
    global _workdir
    if _workdir is None:
        _workdir = Path(tempfile.mkdtemp(prefix="ui-gallery-"))
    return _workdir


def shutdown() -> None:
    global _workdir
    run_deferred()
    if _workdir is not None:
        shutil.rmtree(_workdir, ignore_errors=True)
        _workdir = None


# the real board


@lru_cache(maxsize=1)
def growing_profile():
    return load_profile(FIXTURES / "growing-profile.toml")


@lru_cache(maxsize=1)
def _raw_frame() -> np.ndarray:
    img = cv2.imread(str(FIXTURES / "board-grown.png"))
    if img is None:
        raise FileNotFoundError(FIXTURES / "board-grown.png")
    return img


@lru_cache(maxsize=1)
def _real_reading() -> Reading:
    reader = ViewReader(Calibration.load(FIXTURES / "calibration.json"), growing_profile())
    reading = reader.read(_raw_frame())
    drop_isolated(reading)
    return reading


@lru_cache(maxsize=1)
def lexicon() -> Lexicon:
    words = [
        w.strip().upper()
        for w in WORDS.read_text().splitlines()
        if w.strip() and not w.startswith("#")
    ]
    return Lexicon.from_words(words)


@lru_cache(maxsize=1)
def generator() -> MoveGenerator:
    return MoveGenerator(growing_profile(), lexicon(), use_cache=False)


def _draw_rack(frame: np.ndarray, rack: str) -> QRectF:
    """Seven game-colored tiles under the board, in frame pixels; returns their rect in points."""
    profile = growing_profile()
    img = backdrops.bgr_to_qimage(frame)
    h, w = frame.shape[:2]
    side, gap = 100.0, 12.0
    total = len(rack) * side + (len(rack) - 1) * gap
    x0, y = (w - total) / 2, 1078.0
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    big, small = QFont(), QFont()
    big.setPixelSize(64)
    big.setBold(True)
    small.setPixelSize(26)
    small.setBold(True)
    for i, ch in enumerate(rack):
        x = x0 + i * (side + gap)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(214, 120, 80))
        p.drawRoundedRect(QRectF(x, y + 8, side, side), 18, 18)
        p.setBrush(QColor(0xEF, 0x8E, 0x66))
        p.drawRoundedRect(QRectF(x, y, side, side), 18, 18)
        p.setPen(QColor(255, 255, 255))
        p.setFont(big)
        p.drawText(QRectF(x, y, side, side), Qt.AlignmentFlag.AlignCenter, ch if ch != "?" else "")
        if ch != "?":
            p.setFont(small)
            value = str(profile.tile_value(ch))
            p.drawText(QRectF(x, y + 6, side - 10, 30), Qt.AlignmentFlag.AlignRight, value)
    p.end()
    frame[:] = backdrops.qimage_to_bgr(img)
    return QRectF(x0 / FRAME_SCALE, y / FRAME_SCALE, total / FRAME_SCALE, side / FRAME_SCALE)


@dataclass
class Scenario:
    profile: object
    frame: np.ndarray
    size_pt: tuple[int, int]
    region: Region
    grid: object
    mapper: CellMapper
    board: object
    reading: Reading
    board_rect: QRectF
    rack_rect: QRectF
    rack: str


@lru_cache(maxsize=4)
def scenario(wide_pt: int = 0) -> Scenario:
    """The real frame with a rack under the board. wide_pt adds that much of the game's own
    background on the right, so the panel can sit beside the board as it does on a large screen."""
    reading = _real_reading()
    frame = _raw_frame().copy()
    rack_rect = _draw_rack(frame, RACK)
    if wide_pt:
        frame = cv2.copyMakeBorder(
            frame, 0, 0, 0, round(wide_pt * FRAME_SCALE), cv2.BORDER_CONSTANT, value=PEACH_BGR
        )
    fh, fw = frame.shape[:2]
    size_pt = (round(fw / FRAME_SCALE), round(fh / FRAME_SCALE))
    region = Region(0, 0, size_pt[0], size_pt[1])
    mapper = CellMapper.for_overlay(reading.grid, (fw, fh), region)
    rows, cols = np.nonzero(reading.view.visible)
    r0, r1, c0, c1 = rows.min(), rows.max(), cols.min(), cols.max()
    board_rect = mapper.rect(int(r0), int(c0), 0).united(mapper.rect(int(r1), int(c1), 0))
    return Scenario(
        growing_profile(),
        frame,
        size_pt,
        region,
        reading.grid,
        mapper,
        reading.placed(),
        reading,
        board_rect,
        rack_rect,
        RACK,
    )


def real_frame_bgr() -> np.ndarray:
    return scenario().frame


@lru_cache(maxsize=4)
def _featured(rack: str) -> tuple:
    sc = scenario()
    ms = generator().solve(sc.board, Counter(rack), top_k=150).moves
    from tilelens.ui.cards import describe

    info = [(m, describe(m)) for m in ms]
    first = ms[0]
    blank = next(m for m, d in info if d["cross"] != "none" and d["blanks"])
    crossing = next(m for m, d in info if d["cross"].count(",") >= 1 and not d["blanks"])
    return tuple(sorted({first, blank, crossing}, key=lambda m: (-m.score, m.word)))


def featured_moves(rack: str = RACK) -> list:
    """Three plays that show what a card can say: a plain one, one with a blank tile and a
    crossing word, one that forms two crossing words."""
    return list(_featured(rack))


def result(complete: bool = True, count: int = 3) -> SearchResult:
    moves = featured_moves()[:count]
    return SearchResult(moves, complete, 4180, 0.041)


# snapshots

PUBLISHED_AT = 1000.0
CLOCK_AGE = 0.4


def _unsure_rack():
    return (("rack", 6, "O", ("O", "Q", "G")),)


def _unsure_cell():
    return (("cell", (22, 33), "S", ("S", "B", "5")),)


def check_reading() -> Reading:
    """A reading with unclear squares and an unclear rack tile, on the demo board."""
    from tilelens.synth.game import FakeGame

    game = FakeGame(seed=74, opening_turns=10)
    profile = game.profile
    cells = [[CellRead() for _ in range(profile.width)] for _ in range(profile.height)]
    for r in range(profile.height):
        for c in range(profile.width):
            cell = game.board.get(r, c)
            if cell:
                cells[r][c] = CellRead(cell[0], cell[1], COMMITTED, False, "", 0.98)
    unclear = [
        (r, c) for r in range(profile.height) for c in range(profile.width) if cells[r][c].letter
    ]
    for (r, c), cands in zip(unclear[1:12:5], (("E", "F"), ("O", "Q"), ("I", "L")), strict=False):
        old = cells[r][c]
        cells[r][c] = CellRead(
            old.letter, old.blank, COMMITTED, True, "letter shape unclear", 0.42, cands
        )
    letters = [t for t in game.rack if t][:7]
    tiles = [
        RackRead(
            ch, False, i == 4, "unclear" if i == 4 else "", (i * 40, 0, 36, 36), 0.9, ("A", "O")
        )
        for i, ch in enumerate(letters)
    ]
    return Reading(cells, tiles, _demo_grid(game), True, 0.01)


def _demo_grid(game):
    from tilelens.vision.grid import Grid

    geo = game.geometry
    return Grid(geo.origin[0], geo.origin[1], geo.pitch, geo.pitch, geo.rows, geo.cols)


def _real_snapshot(status: Status, detail: str = "", **kw) -> Snapshot:
    sc = scenario()
    base = dict(
        board=sc.board,
        rack=Counter(RACK),
        published_at=PUBLISHED_AT,
        confirmed_at=PUBLISHED_AT,
        solve_ms=41.0,
        coverage=Coverage.FULL,
    )
    base.update(kw)
    return Snapshot(status, detail, **base)


def _ready(**kw) -> Snapshot:
    return _real_snapshot(Status.READY, "", result=result(), **kw)


SNAPSHOTS: dict[str, Callable[[], Snapshot | None]] = {
    "setup": lambda: None,
    "reading": lambda: Snapshot(Status.READING, "Reading"),
    "aligning": lambda: Snapshot(Status.READING, "Aligning"),
    "solving": lambda: _real_snapshot(Status.SOLVING, "Searching"),
    "ready": lambda: _ready(),
    "ready-tracking": lambda: _ready(coverage=Coverage.TRACKING),
    "ready-partial": lambda: _ready(coverage=Coverage.PARTIAL),
    "ready-incomplete": lambda: _real_snapshot(
        Status.READY, "", result=result(complete=False), coverage=Coverage.FULL
    ),
    "one-play": lambda: _real_snapshot(Status.READY, "", result=result(count=1)),
    "no-move": lambda: _real_snapshot(
        Status.READY, "No legal placement", result=SearchResult([], True, 0, 0.01)
    ),
    "placing": lambda: _real_snapshot(Status.READY, WAITING, result=result()),
    "updating": lambda: _real_snapshot(Status.READY, "Updating", result=result()),
    "notice": lambda: _ready(notice="Read S at AA22 as A: only A makes a word"),
    "unsure-rack": lambda: _ready(
        unsure=_unsure_rack(),
        notice="Rack tile 7 looks like O; plays leave it out until you tap its letter",
    ),
    "unsure-cell": lambda: _ready(
        unsure=_unsure_cell(),
        notice="Square AH23 looks like S; plays leave it out until you tap its letter (+1 more)",
    ),
    "held": lambda: _real_snapshot(Status.READY, HELD, result=result(), held=1),
    "check": lambda: _real_snapshot(
        Status.CHECK, "3 squares to check; rack to check", reading=check_reading(), result=None
    ),
    "check-scale": lambda: Snapshot(Status.CHECK, "Board size changed; recalibrate"),
    "paused": lambda: _real_snapshot(Status.PAUSED, "Paused", result=result()),
    "obscured": lambda: Snapshot(Status.NOT_VISIBLE, "Board not found"),
    "covered": lambda: Snapshot(Status.NOT_VISIBLE, "Something is covering the game"),
    "permission": lambda: Snapshot(Status.NOT_VISIBLE, "Screen Recording permission is missing"),
}


def snapshot(name: str = "ready", **changes) -> Snapshot | None:
    snap = SNAPSHOTS[name]()
    return dataclasses.replace(snap, **changes) if changes and snap is not None else snap


# demo board (the synthetic game)


@lru_cache(maxsize=1)
def demo_game():
    from tilelens.synth.game import FakeGame

    return FakeGame(seed=5)


def demo_snapshot(status: Status = Status.READY, detail: str = "", **changes) -> Snapshot:
    game = demo_game()
    board, rack = game.position()
    moves = game.gen.solve(board, rack, top_k=3)
    base = dict(
        board=board,
        rack=rack,
        result=moves if status == Status.READY else None,
        published_at=PUBLISHED_AT,
        confirmed_at=PUBLISHED_AT,
        solve_ms=12.0,
        coverage=Coverage.FULL,
    )
    base.update(changes)
    return Snapshot(status, detail, **base)


# overlay scenes


def _last_rack_tile(rack: QRectF) -> QRectF:
    side = rack.height()
    return QRectF(rack.right() - side, rack.top(), side, side)


def overlay_scene(
    *,
    move: int | None = 0,
    coverage: Coverage | None = Coverage.FULL,
    framed: bool = False,
    board: bool = True,
    rack: bool = True,
    rack_read: tuple[int, int] = (7, 7),
    wide_pt: int = 0,
    moves: list | None = None,
    guide_alpha: float | None = 0.0,
    asking: bool = False,
) -> OverlayScene:
    """What the overlay draws on the real frame, from the same geometry the session uses.

    guide_alpha 0 shows the steady state while a play is on show, 1 the moment the guides flash.
    """
    sc = scenario(wide_pt)
    chosen = moves if moves is not None else featured_moves()
    cells = sc.reading.cells
    rows, cols = len(cells), len(cells[0])

    def occupied(row: int, col: int) -> bool:
        if not (0 <= row < rows and 0 <= col < cols):
            return False
        cell = cells[row][col]
        return bool(cell.letter or cell.blank or cell.uncertain)

    return OverlayScene(
        move=None if move is None else chosen[move],
        rect_of=sc.mapper.rect,
        board=sc.board_rect if board else None,
        coverage=coverage,
        rack=sc.rack_rect if rack else None,
        rack_read=rack_read if rack else None,
        value_of=sc.profile.tile_value,
        framed=framed,
        rank=None if move is None else move + 1,
        total=None if move is None else min(3, len(chosen)),
        guide_alpha=guide_alpha,
        occupied=occupied,
        asking=(_last_rack_tile(sc.rack_rect),) if asking and rack else (),
    )


# factories


def make_panel(snap, selected: int = 0, age: float | None = 0.4, framed: bool = False, note=""):
    from tilelens.ui.glass_panel import GlassPanel

    panel = GlassPanel()
    panel.show_snapshot(snap, selected, age, framed)
    if note:
        panel.set_note(note)
    return panel


def make_overlay(scene: OverlayScene | None = None):
    from tilelens.ui.overlay import OverlayWindow

    overlay = OverlayWindow()
    if scene is not None:
        overlay.show_scene(scene)
    return overlay


def make_session(profile: str = "demo", words: bool = True):
    """A session with no calibration and no capture loop: the state of a first launch."""
    from tilelens.capture.source import CallableSource
    from tilelens.ui.session import LiveSession
    from tilelens.ui.settings import Settings

    settings = Settings(calibration=str(workdir() / "none.json"))
    if profile == "auto":
        settings.profile = str(FIXTURES / "growing-profile.toml")
    if words:
        settings.words = str(WORDS)
    session = LiveSession(
        settings,
        source=CallableSource(lambda: None),
        region_provider=lambda: Region(0, 0, 800, 600),
        use_process=False,
    )
    defer(session.stop)
    return session


def make_main_window(
    snap: Snapshot | None,
    *,
    selected: int = 0,
    message: str = "",
    profile: str = "demo",
    words: bool = True,
    size: tuple[int, int] = MAIN_SIZE,
):
    from tilelens.ui.main_window import MainWindow

    session = make_session(profile, words)
    if snap is not None:
        session.controller = SimpleNamespace(snapshot=snap, last_read_ms=18.0, clock=None)
    session.selected = selected
    published = snap.published_at if snap is not None and snap.published_at else 0.0
    win = MainWindow(session, clock=lambda: published + CLOCK_AGE)
    win.resize(*size)
    win.show_snapshot(snap)
    if message:
        win.show_message(message)
    return win


@lru_cache(maxsize=1)
def demo_calibration_frame() -> np.ndarray:
    from tilelens.synth.game import FakeGame

    return FakeGame(seed=74, opening_turns=10).frame()


def make_calibration_dialog(rect: bool = False, rack: bool = True):
    from tilelens.synth.game import FakeGame
    from tilelens.ui.calibrate_dialog import CalibrationDialog

    game = FakeGame(seed=74, opening_turns=10)
    dlg = CalibrationDialog(
        game.frame(),
        game.profile,
        board=game.board,
        rack="".join(t for t in game.rack if t) if rack else "",
    )
    if rect:
        geo = game.geometry
        dlg.picker.set_rect((geo.origin[0] + 4, geo.origin[1] - 3, 13 * geo.pitch, 13 * geo.pitch))
    return dlg, game


def make_auto_dialog(frame: np.ndarray | None = None, rack: str = ""):
    from tilelens.ui.auto_calibrate import AutoCalibrationDialog

    return AutoCalibrationDialog(
        scenario().frame if frame is None else frame, growing_profile(), rack=rack
    )


def make_corrections_dialog():
    from tilelens.ui.corrections import CorrectionDialog

    return CorrectionDialog(check_reading())


# helpers for pop-ups


def tooltip(owner: QWidget, anchor: QWidget, text: str | None = None, pad: int = 28) -> QImage:
    """The owner window with the tooltip of one of its widgets drawn where it would appear."""
    text = text if text is not None else anchor.toolTip()
    if not text:
        raise LookupError(f"{type(anchor).__name__} has no tooltip text")
    scheme.force_windows([owner])
    compose.present(owner)
    QToolTip.showText(anchor.mapToGlobal(QPoint(0, anchor.height())), text, anchor)
    compose.settle(60)
    tips = [w for w in QApplication.topLevelWidgets() if w.objectName() == "qtooltip_label"]
    if not tips:
        raise RuntimeError("the tooltip did not appear")
    tip_img = compose.to_image(tips[0])
    QToolTip.hideText()
    owner_img = compose.to_image(owner)
    ratio = owner_img.devicePixelRatio() or 1.0
    extra = 90
    canvas = QImage(
        owner_img.width() + round(2 * pad * ratio),
        owner_img.height() + round((2 * pad + extra) * ratio),
        QImage.Format.Format_ARGB32_Premultiplied,
    )
    canvas.setDevicePixelRatio(ratio)
    translucent = owner.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    canvas.fill(Qt.GlobalColor.transparent if translucent else owner.palette().window().color())
    p = QPainter(canvas)
    p.drawImage(QPointF(pad, pad), owner_img)
    at = anchor.mapTo(owner, QPoint(0, anchor.height() + 6))
    x = min(at.x(), owner.width() - tip_img.width() / ratio)
    p.drawImage(QPointF(pad + max(0, x), pad + at.y()), tip_img)
    p.end()
    defer(owner.close)
    return canvas


def stage(size: tuple[int, int], *items) -> Stage:
    return Stage(size, list(items))


__all__ = [
    "FRAME_SCALE",
    "MAIN_SIZE",
    "PANEL_INSET",
    "RACK",
    "SNAPSHOTS",
    "Scenario",
    "Stage",
    "check_reading",
    "defer",
    "demo_game",
    "demo_snapshot",
    "featured_moves",
    "growing_profile",
    "make_auto_dialog",
    "make_calibration_dialog",
    "make_corrections_dialog",
    "make_main_window",
    "make_overlay",
    "make_panel",
    "make_session",
    "overlay_scene",
    "real_frame_bgr",
    "result",
    "scenario",
    "snapshot",
    "stage",
    "tooltip",
]

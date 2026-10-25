import copy

import numpy as np

from tilelens.rules.lexicon import Lexicon
from tilelens.rules.profile import demo_profile
from tilelens.solver.movegen import MoveGenerator
from tilelens.state.controller import HELD, LiveController, Status
from tilelens.state.worker import InlineSolver
from tilelens.vision.grid import Grid
from tilelens.vision.recognizer import COMMITTED, CellRead, RackRead, Reading

PROFILE = demo_profile("demo_garden")
LEX = Lexicon.from_words(["CAT", "ACT", "TAC", "AT", "TA", "CATS", "SCAT", "CAST", "COT", "TO"])
W, H = PROFILE.width, PROFILE.height
GRID = Grid(0, 0, 10, 10, H, W)


def reading(rack, board=None, uncertain_rack=()):
    cells = [[CellRead() for _ in range(W)] for _ in range(H)]
    for (r, c), ch in (board or {}).items():
        cells[r][c] = CellRead(ch, False, COMMITTED, False, "", 0.99)
    tiles = [
        RackRead(ch, False, i in uncertain_rack, "unclear" if i in uncertain_rack else "",
                 (i * 12, 0, 10, 10), 0.99, ("A", "O"))
        for i, ch in enumerate(rack)
    ]  # fmt: skip
    return Reading(cells, tiles, GRID, True, 0.0)


class Recog:
    tracks_view = True
    cal = None

    def __init__(self):
        self.current = reading("CATSO")
        self.locates = 0

    def read(self, frame):
        return copy.deepcopy(self.current)

    def learn_from_corrections(self, *a, **k):
        pass


class Source:
    n = 0

    def grab(self):
        Source.n += 1
        return np.full((100, 100, 3), Source.n % 250, np.uint8)  # always "changed"


def make(rack_text="CATSO"):
    rec = Recog()
    rec.current = reading(rack_text)
    clock = [100.0]
    gen = MoveGenerator(PROFILE, LEX, use_cache=False)
    ctrl = LiveController(
        Source(), rec, InlineSolver(gen), PROFILE, LEX.version, clock=lambda: clock[0], lexicon=LEX
    )

    def tick(n=1):
        for _ in range(n):
            clock[0] += 0.1
            ctrl.tick()

    return rec, ctrl, tick


def test_clicking_a_suggestion_holds_it_until_the_rack_refills():
    rec, ctrl, tick = make("CATSO")
    tick(8)
    assert ctrl.snapshot.status == Status.READY
    shown = ctrl.snapshot.result
    assert ctrl.hold_move(0)
    assert ctrl.snapshot.detail == HELD and ctrl.snapshot.held == 0
    # tiles leave the rack as they are placed: nothing is recomputed, the ghost stays
    rec.current = reading("CA")
    tick(6)
    assert ctrl.snapshot.result is shown and ctrl.snapshot.held == 0
    assert ctrl.hold.seen_short
    # the rack refills with new letters: tracking resumes on its own
    rec.current = reading("CAOTS")
    tick(12)
    assert ctrl.hold is None and ctrl.snapshot.held is None
    assert ctrl.snapshot.status in (Status.READY, Status.SOLVING, Status.READING)


def test_a_flicker_of_the_rack_does_not_release_the_hold():
    rec, ctrl, tick = make("CATSO")
    tick(8)
    ctrl.hold_move(0)
    rec.current = reading("CATS")
    tick(1)
    rec.current = reading("CATSO")
    tick(8)
    assert ctrl.hold is not None


def test_shuffle_keeps_the_hold():
    rec, ctrl, tick = make("CATSO")
    tick(8)
    ctrl.hold_move(1)
    rec.current = reading("OSTAC")
    tick(10)
    assert ctrl.hold is not None and ctrl.snapshot.held == 1


def test_unsure_rack_tile_does_not_stop_suggestions():
    rec, ctrl, tick = make("CATSO")
    rec.current = reading("CATSO", uncertain_rack=(4,))
    tick(10)
    snap = ctrl.snapshot
    assert snap.status == Status.READY and snap.result is not None
    assert snap.unsure and snap.unsure[0][0] == "rack" and "tile 5" in snap.notice


def test_unsure_board_square_becomes_a_wall_and_suggestions_continue():
    rec, ctrl, tick = make("CATSO")
    r = reading("CATSO", board={(4, 4): "T"})
    r.cells[4][5] = CellRead("S", False, COMMITTED, True, "unclear", 0.5, ("X", "S"))
    rec.current = r
    tick(10)
    snap = ctrl.snapshot
    assert snap.status == Status.READY
    assert snap.board.is_unknown(4, 5)
    assert snap.unsure[0][0] == "cell"


def test_word_list_settles_the_letter_without_asking():
    rec, ctrl, tick = make("CATSO")
    r = reading("CATSO", board={(4, 3): "C", (4, 5): "T"})
    r.cells[4][4] = CellRead("S", False, COMMITTED, True, "unclear", 0.5, ("S", "A"))
    rec.current = r
    tick(10)
    snap = ctrl.snapshot
    assert snap.status == Status.READY and not snap.unsure
    assert snap.board.letter(4, 4) == "A"
    assert "A" in snap.notice


def test_unclear_square_with_no_tile_beside_it_is_ignored_silently():
    rec, ctrl, tick = make("CATSO")
    r = reading("CATSO", board={(4, 4): "T"})
    r.cells[9][9] = CellRead("J", False, COMMITTED, True, "unclear", 0.5, ("J", "L"))
    rec.current = r
    tick(10)
    snap = ctrl.snapshot
    assert snap.status == Status.READY and not snap.unsure and snap.notice == ""

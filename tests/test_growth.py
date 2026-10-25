"""A board that grows: Letter League adds columns and rows as play nears an edge.

The profile describes the board it started with; `expand` gives room around it that repeats the
premium pattern. TileLens finds the board's real edges on screen, scans everything inside them,
and treats everything outside as wall.
"""

import copy
import tomllib
from collections import Counter
from pathlib import Path

import cv2
import pytest
from conftest import TINY_RAW, make_profile

from tilelens.rules.board import WALL_CELL, Board, Tile
from tilelens.rules.lexicon import Lexicon
from tilelens.rules.profile import ProfileError, load_profile, parse_profile
from tilelens.solver.movegen import MoveGenerator
from tilelens.state.position import position_id
from tilelens.vision.autoread import ViewReader
from tilelens.vision.lexfix import drop_isolated
from tilelens.vision.locate import locate_board
from tilelens.vision.recognizer import Calibration

FIXTURES = Path(__file__).parent / "fixtures" / "discord"


def grown_raw(**board):
    raw = copy.deepcopy(TINY_RAW)
    raw["board"]["expand"] = 6
    raw["board"]["period"] = [3, 3]
    raw["board"]["layout"] = [
        "d..d..d",
        ".D..D..",
        "..t..t.",
        "d..*..d",
        ".D..D..",
        "..t..t.",
        "d..d..d",
    ]
    raw["board"].update(board)
    return raw


def test_the_profile_repeats_its_pattern_into_the_room_around_the_board():
    p = parse_profile(grown_raw())
    assert (p.width, p.height) == (7 + 12, 7 + 12) and p.core == (7, 7) and p.expand == 6
    assert p.start_squares == ((9, 9),)
    # inside the core nothing changed; outside, the same premiums repeat with the period
    assert p.layout_chars[6][6:13] == "d..d..d" and p.layout_chars[9][9] == "*"
    for r in range(p.height):
        for c in range(p.width):
            rr, cc = r - 6, c - 6
            if 0 <= rr < 7 and 0 <= cc < 7:
                continue
            assert p.layout[r][c] == p.layout[(r - 6) % 3 + 6][(c - 6) % 3 + 6]
    # room for growth does not change how the game looks, so a calibration stays valid
    plain = parse_profile({**grown_raw(), "board": {**grown_raw()["board"], "expand": 0}})
    assert p.fingerprint() == plain.fingerprint()


def test_a_layout_that_does_not_repeat_is_rejected_with_the_square():
    raw = grown_raw()
    raw["board"]["layout"][0] = "d..d.td"
    with pytest.raises(ProfileError) as err:
        parse_profile(raw)
    assert "breaks the 3x3 period" in str(err.value)


def test_growing_needs_a_period():
    raw = grown_raw()
    del raw["board"]["period"]
    with pytest.raises(ProfileError):
        parse_profile(raw)


def test_squares_outside_the_real_board_are_walls():
    board = Board.from_rows(["." * 8 for _ in range(8)]).with_bounds((1, 1, 6, 6))
    assert board.get(0, 0) == WALL_CELL and board.get(1, 1) is None
    assert not board.in_bounds(0, 3) and board.in_bounds(3, 3)
    assert board.transpose().bounds == (1, 1, 6, 6)
    assert board != Board.from_rows(["." * 8 for _ in range(8)])
    assert Board.from_rows(["." * 8 for _ in range(8)]).with_bounds((1, 1, 6, 6)) == board
    assert not board.has_unknown()  # being outside the board is not being unseen


def test_no_move_leaves_the_real_board():
    profile = make_profile()
    words = Lexicon.from_words(["AT", "TA", "TAT", "TATA", "ATT", "AA"])
    gen = MoveGenerator(profile, words, use_cache=False)
    rows = ["." * 7 for _ in range(7)]
    rows[3] = "..TA..."
    board = Board.from_rows(rows).with_bounds((2, 1, 4, 5))
    moves = gen.all_moves(board, Counter("ATTA"))
    assert moves
    for key in moves:
        assert all(2 <= t.row <= 4 and 1 <= t.col <= 5 for t in key)
    unbounded = gen.all_moves(Board.from_rows(rows), Counter("ATTA"))
    assert len(unbounded) > len(moves)
    first = gen.solve(Board(7, 7).with_bounds((2, 2, 4, 4)), Counter("TAT")).moves
    assert first and all(2 <= t.row <= 4 and 2 <= t.col <= 4 for m in first for t in m.tiles)


def test_the_bounds_are_part_of_the_position():
    profile = make_profile()
    a = Board.from_rows(["." * 7 for _ in range(7)])
    assert position_id(a, Counter("A"), profile, "v") != position_id(
        a.with_bounds((1, 1, 5, 5)), Counter("A"), profile, "v"
    )


@pytest.fixture(scope="module")
def grown_profile():
    return load_profile(FIXTURES / "growing-profile.toml")


def test_the_screenshot_board_is_30_columns_wide_not_27(grown_profile):
    img = cv2.imread(str(FIXTURES / "board-grown.png"))
    view = locate_board(img, grown_profile)
    r0, c0, r1, c1 = view.extent
    assert (r1 - r0 + 1, c1 - c0 + 1) == (19, 30)  # three columns more than the profile's 27
    assert view.match_rate > 0.9 and view.margin > 0.25
    # the board starts at the core's own left edge and the start square is where it should be
    assert c0 == grown_profile.expand
    assert view.visible[r0 : r1 + 1, c0 : c1 + 1].sum() >= 0.9 * 19 * 30
    # every square in the three added columns is in view, so they are scanned
    assert view.visible[r0 + 3 : r1 + 1, c1 - 2 : c1 + 1].all()
    assert not view.visible[:, : c0 - 1].any() and not view.visible[:, c1 + 1 :].any()


def test_tiles_are_found_on_the_grown_board_and_nothing_outside_it(grown_profile):
    img = cv2.imread(str(FIXTURES / "board-grown.png"))
    reader = ViewReader(Calibration.load(FIXTURES / "calibration.json"), grown_profile)
    reading = reader.read(img)
    drop_isolated(reading)  # a lone tile-like shape (the timer's button) is not a tile
    board = reading.placed()
    rows = {}
    for t in board.tiles():
        rows.setdefault(t.row, []).append(t)
    words = [
        "".join(t.letter for t in sorted(ts, key=lambda t: t.col)) for _, ts in sorted(rows.items())
    ]
    assert words == ["FACIA", "DENIED", "UNROUGH"]
    assert board.bounds == reading.bounds
    start = grown_profile.start_squares[0]
    assert board.letter(*start) == "C" or board.letter(start[0], start[1]) is None
    far = [
        (r, c)
        for r in range(grown_profile.height)
        for c in range(grown_profile.width)
        if not board.inside(r, c)
    ]
    assert far and all(board.get(r, c) == WALL_CELL for r, c in far[:50])


def test_a_play_in_the_new_columns_is_possible(grown_profile):
    img = cv2.imread(str(FIXTURES / "board-grown.png"))
    reader = ViewReader(Calibration.load(FIXTURES / "calibration.json"), grown_profile)
    reading = reader.read(img)
    drop_isolated(reading)
    board = reading.placed()
    c1 = board.bounds[3]
    assert c1 - grown_profile.expand + 1 == 30
    # a long word along the UNROUGH row runs past the 27th column into the added ones
    words = Lexicon.from_words(["UNROUGHAAAAAA", "AA"])
    gen = MoveGenerator(grown_profile, words, use_cache=False)
    moves = gen.all_moves(board, Counter("AAAAAA"))
    reach = max((t.col for key in moves for t in key), default=0)
    assert reach - grown_profile.expand >= 27, "plays can use the columns the board grew"
    assert all(board.inside(t.row, t.col) for key in moves for t in key)
    # without growth room the same play is impossible
    short = board.with_bounds(
        (board.bounds[0], board.bounds[1], board.bounds[2], grown_profile.expand + 26)
    )
    assert not any(
        t.col - grown_profile.expand >= 27
        for key in gen.all_moves(short, Counter("AAAAAA"))
        for t in key
    )


def test_the_profile_file_of_the_fixture_is_valid_toml():
    tomllib.loads((FIXTURES / "growing-profile.toml").read_text())
    assert Tile(0, 0, "A")

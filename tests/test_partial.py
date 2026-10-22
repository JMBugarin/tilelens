import random

import pytest
from randgen import random_position

from tilelens.rules.board import WALL_CELL, Board, parse_rack
from tilelens.rules.legality import check_placement
from tilelens.rules.lexicon import Lexicon
from tilelens.rules.scoring import score_placement
from tilelens.solver.movegen import MoveGenerator, resolve_unknown


def hide(board: Board, rng: random.Random, fraction: float) -> Board:
    cells = [list(row) for row in board.cells]
    for r in range(board.height):
        for c in range(board.width):
            if rng.random() < fraction:
                cells[r][c] = WALL_CELL
    return Board(board.width, board.height, cells)


def test_wall_round_trip():
    b = Board.from_rows(["#A.", "..#"])
    assert b.to_rows() == ["#A.", "..#"]
    assert b.is_unknown(0, 0) and b.has_unknown()
    assert [t.letter for t in b.tiles()] == ["A"]
    assert Board.from_rows(["#.", ".#"]).is_empty()


@pytest.mark.parametrize("chunk", range(3))
def test_moves_with_unknown_squares_are_legal_on_the_true_board(chunk):
    """Whatever is hidden, every suggestion must be legal and score the same on the real board."""
    checked = 0
    for seed in range(chunk * 120, chunk * 120 + 120):
        profile, lexicon, true_board, rack = random_position(seed)
        rng = random.Random(seed)
        seen = hide(true_board, rng, rng.choice([0.1, 0.3, 0.6]))
        gen = MoveGenerator(profile, lexicon)
        for key, score in gen.all_moves(seen, rack).items():
            check_placement(true_board, rack, key, profile, lexicon)
            assert score_placement(true_board, key, profile).total == score
            checked += 1
    assert checked > 0


def test_visible_moves_match_full_search_when_nothing_relevant_is_hidden():
    profile, lexicon, true_board, rack = random_position(7)
    gen = MoveGenerator(profile, lexicon)
    assert gen.all_moves(true_board, rack) == gen.all_moves(
        hide(true_board, random.Random(1), 0.0), rack
    )


def test_untouched_start_square_means_empty_board(tiny):
    walls = Board.from_rows(["#" * tiny.width] * tiny.height)
    cells = [list(r) for r in walls.cells]
    for r, c in tiny.start_squares:
        cells[r][c] = None
    partial = Board(tiny.width, tiny.height, cells)
    assert resolve_unknown(partial, tiny) == Board(tiny.width, tiny.height)
    assert resolve_unknown(walls, tiny) == walls
    lexicon = Lexicon.from_words(["CAT"])
    gen = MoveGenerator(tiny, lexicon)
    assert gen.solve(partial, parse_rack("CAT")).moves
    assert not gen.solve(walls, parse_rack("CAT")).moves

"""Tiles already on the board count at the number printed on them.

Letter League stamps a tile with the points it earned (letter and word premiums) and counts that
number whenever the tile is used again. Placing an O beside a T stamped 2 and under an N stamped 3
scored 11 in the game where the plain letter values give 8.
"""

from collections import Counter

from conftest import make_profile

from tilelens.rules.board import Board, Tile
from tilelens.rules.lexicon import Lexicon
from tilelens.rules.scoring import score_placement
from tilelens.solver.movegen import MoveGenerator
from tilelens.state.position import position_id
from tilelens.state.worker import InlineSolver

LEX = Lexicon.from_words(["TO", "NO", "ON", "OT"])


def board(stamped: bool) -> Board:
    rows = ["." * 7 for _ in range(7)]
    rows[3] = "...N..."
    rows[4] = "..T...."
    b = Board.from_rows(rows)
    return b.with_stamps({(3, 3): 3, (4, 2): 2}) if stamped else b


def test_existing_tiles_count_at_their_stamped_value():
    profile = make_profile()
    move = [Tile(4, 3, "O")]
    plain = score_placement(board(False), move, profile)
    stamped = score_placement(board(True), move, profile)
    assert [(w.text, w.total) for w in plain.words] == [("TO", 2), ("NO", 2)]
    assert [(w.text, w.total) for w in stamped.words] == [("TO", 3), ("NO", 4)]
    assert stamped.total == 7


def test_the_search_scores_with_stamps_and_agrees_with_the_scorer():
    profile = make_profile()
    gen = MoveGenerator(profile, LEX, use_cache=False)
    result = gen.solve(board(True), Counter("O"))
    best = {m.tiles: m.score for m in result.moves}
    assert best[(Tile(4, 3, "O"),)] == 7
    for move in result.moves:  # the generator itself checks each score against the scorer
        assert move.score == score_placement(board(True), move.tiles, profile).total


def test_stamps_reach_the_solver_and_change_the_position_id():
    profile = make_profile()
    solver = InlineSolver(MoveGenerator(profile, LEX, use_cache=False))
    plain, stamped = board(False), board(True)
    assert position_id(plain, Counter("O"), profile, "v") != position_id(
        stamped, Counter("O"), profile, "v"
    )
    solver.submit("a", stamped.to_rows(), "O", stamped.stamp_list())
    result = solver.poll()[0].result
    assert max(m.score for m in result.moves) == 7


def test_stamps_survive_transposing_and_equality():
    b = board(True)
    assert b.transpose().stamps == {(3, 3): 3, (2, 4): 2}
    assert b != board(False) and b == board(True)

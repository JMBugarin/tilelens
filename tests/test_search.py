import os

import pytest
from conftest import make_profile
from randgen import random_position

from tilelens.rules.board import Board, Tile, parse_placement, parse_rack
from tilelens.rules.lexicon import Lexicon
from tilelens.rules.profile import demo_profile
from tilelens.solver.dawg import Dawg, load_or_build
from tilelens.solver.exhaustive import exhaustive_moves
from tilelens.solver.movegen import MoveGenerator, TopK


def lex(*words):
    return Lexicon.from_words(words)


def board(*rows):
    return Board.from_rows(list(rows))


EMPTY7 = board(*["......."] * 7)
ROW3_CAT = board(".......", ".......", ".......", "..CAT..", ".......", ".......", ".......")


def ranked(found, k=3):
    return sorted(found.items(), key=lambda kv: (-kv[1], kv[0]))[:k]


# prefix graph


def test_dawg_contains_and_minimizes():
    words = ["CAT", "CATS", "BAT", "BATS", "TAB", "TABS", "AT", "A"]
    d = Dawg.build(words)
    for w in words:
        assert w in d
    for w in ["CA", "BATSS", "", "TA", "X"]:
        assert w not in d
    assert d.words() == sorted(words)
    trie_nodes = 1 + len({w[:i] for w in words for i in range(1, len(w) + 1)})
    assert len(d) < trie_nodes


def test_dawg_save_load_round_trip(tmp_path):
    d = Dawg.build(["ÑU", "CAT", "CATS"], version="v1")
    d.save(tmp_path / "d.npz")
    back = Dawg.load(tmp_path / "d.npz")
    assert back.words() == d.words() and back.version == "v1"


def test_dawg_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("TILELENS_CACHE", str(tmp_path))
    first = load_or_build(["CAT", "DOG"], "abc")
    assert (tmp_path / "dawg-abc.npz").is_file()
    second = load_or_build(["IGNORED"], "abc")
    assert second.words() == first.words() == ["CAT", "DOG"]
    (tmp_path / "dawg-abc.npz").write_bytes(b"corrupt")
    assert load_or_build(["EMU"], "abc").words() == ["EMU"]


# hand-checked search results


def test_opening_move(tiny):
    gen = MoveGenerator(tiny, lex("CAT", "ACT", "AT"))
    result = gen.solve(EMPTY7, parse_rack("CAT"))
    assert result.complete and result.label == "highest scoring"
    assert [(m.word, m.score) for m in result.moves[:2]] == [("ACT", 10), ("CAT", 10)]
    assert result.moves[0].tiles < result.moves[1].tiles
    assert all(any((t.row, t.col) == (3, 3) for t in m.tiles) for m in result.moves)


def test_no_moves(tiny):
    gen = MoveGenerator(tiny, lex("CAT"))
    result = gen.solve(ROW3_CAT, parse_rack("QZ"))
    assert result.moves == [] and result.complete
    assert gen.solve(ROW3_CAT, parse_rack("")).moves == []


def test_fewer_than_three(tiny):
    gen = MoveGenerator(tiny, lex("CAT", "CATS"))
    result = gen.solve(ROW3_CAT, parse_rack("S"))
    assert [(m.word, m.score) for m in result.moves] == [("CATS", 6)]


def test_single_tile_found_once(tiny):
    b = board(".......", ".......", "...O...", "..A....", ".......", ".......", ".......")
    gen = MoveGenerator(tiny, lex("AX", "OX"))
    found = gen.all_moves(b, parse_rack("X"))
    assert found[parse_placement("3,3,X")] == 36
    assert found[parse_placement("2,4,X")] == 25
    assert len(found) == 3
    move = gen.solve(b, parse_rack("X")).moves[0]
    assert move.score == 36 and move.direction == "H" and move.word == "AX"


def test_blank_assignments_are_distinct(tiny):
    gen = MoveGenerator(tiny, lex("CAT"))
    found = gen.all_moves(ROW3_CAT.__class__(7, 7), parse_rack("CA?"))
    real_t = [k for k in found if not any(t.blank for t in k)]
    assert real_t == []
    blank_keys = {k for k in found if sum(t.blank for t in k) == 1}
    assert parse_placement("3,2,C 3,3,A 3,4,t") in blank_keys
    assert parse_placement("3,1,C 3,2,A 3,3,t") in blank_keys


def test_two_blanks(tiny):
    gen = MoveGenerator(tiny, lex("ZAX"))
    found = gen.all_moves(EMPTY7, parse_rack("A??"))
    assert parse_placement("3,2,z 3,3,A 3,4,x") in found
    assert found[parse_placement("3,2,z 3,3,A 3,4,x")] == 2


def test_tie_break_is_coordinate_based(tiny):
    gen = MoveGenerator(tiny, lex("AT", "TA"))
    result = gen.solve(EMPTY7, parse_rack("AT"), top_k=10)
    scores = [m.score for m in result.moves]
    assert scores == sorted(scores, reverse=True)
    for a, b in zip(result.moves, result.moves[1:], strict=False):
        if a.score == b.score:
            assert a.tiles < b.tiles


def test_top_k_dedupes_and_orders():
    top = TopK(2)
    k1 = (Tile(0, 1, "A"),)
    k2 = (Tile(0, 0, "A"),)
    k3 = (Tile(0, 2, "A"),)
    for score, key in [(5, k1), (5, k1), (5, k2), (5, k3), (1, k3)]:
        top.offer(score, key)
    assert top.items == [(5, k2), (5, k1)]


def test_cancellation_marks_incomplete():
    p = demo_profile("demo_garden")
    lexicon = Lexicon.load(p.word_list, p.alphabet)
    gen = MoveGenerator(p, lexicon)
    calls = []

    def stop():
        calls.append(1)
        return True

    result = gen.solve(Board(p.width, p.height), parse_rack("AERST??"), should_stop=stop)
    assert not result.complete and calls
    assert result.label == "best found so far"


def test_search_matches_scorer_on_demo_profile():
    p = demo_profile("demo_pocket")
    lexicon = Lexicon.load(p.word_list, p.alphabet)
    gen = MoveGenerator(p, lexicon)
    b = Board(p.width, p.height)
    rack = parse_rack("TONES")
    found = gen.all_moves(b, rack)
    assert found == exhaustive_moves(b, rack, p, lexicon)
    result = gen.solve(b, rack)
    assert [(m.tiles, m.score) for m in result.moves] == ranked(found)


def test_non_square_board():
    p = make_profile(
        board__width=5,
        board__height=3,
        board__layout=["T.d.D", "..*..", "D.d.T"],
        rules__start_squares=[[1, 2]],
    )
    lexicon = lex("CAT", "AT", "TA", "ACT")
    gen = MoveGenerator(p, lexicon)
    b = Board(5, 3)
    rack = parse_rack("CAT?")
    assert gen.all_moves(b, rack) == exhaustive_moves(b, rack, p, lexicon)


def check_seed(seed):
    profile, lexicon, b, rack = random_position(seed)
    gen = MoveGenerator(profile, lexicon)
    expected = exhaustive_moves(b, rack, profile, lexicon)
    got = gen.all_moves(b, rack)
    assert got == expected, f"seed {seed}: board={b.to_rows()} rack={dict(rack)}"
    result = gen.solve(b, rack)
    assert [(m.tiles, m.score) for m in result.moves] == ranked(expected), f"seed {seed}"
    return len(expected)


@pytest.mark.parametrize("chunk", range(4))
def test_randomized_against_exhaustive(chunk):
    total = 0
    for seed in range(chunk * 100, chunk * 100 + 100):
        total += check_seed(seed)
    assert total > 0


@pytest.mark.slow
def test_randomized_against_exhaustive_large():
    n = int(os.environ.get("TILELENS_RANDOM_CASES", "5000"))
    for seed in range(10_000, 10_000 + n):
        check_seed(seed)

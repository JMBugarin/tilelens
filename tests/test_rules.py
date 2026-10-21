from collections import Counter

import pytest
from conftest import make_profile

from tilelens.rules.board import Board, Tile, parse_placement, parse_rack, rack_to_str
from tilelens.rules.legality import IllegalPlacement, check_placement, formed_words
from tilelens.rules.lexicon import Lexicon, WordListError
from tilelens.rules.scoring import evaluate, score_placement


def lex(*words):
    return Lexicon.from_words(words)


def empty(profile):
    return Board(profile.width, profile.height)


def board(*rows):
    return Board.from_rows(list(rows))


ROW3_CAT = board(
    ".......",
    ".......",
    ".......",
    "..CAT..",
    ".......",
    ".......",
    ".......",
)


def score(b, rack, placement, profile, words):
    return evaluate(b, parse_rack(rack), parse_placement(placement), profile, lex(*words)).total


def illegal(b, rack, placement, profile, words):
    with pytest.raises(IllegalPlacement) as exc:
        evaluate(b, parse_rack(rack), parse_placement(placement), profile, lex(*words))
    return exc.value.code


# board, rack and placement helpers


def test_board_round_trip_and_blanks():
    b = board("Ab.", "..c")
    assert b.get(0, 0) == ("A", False)
    assert b.get(0, 1) == ("B", True)
    assert b.to_rows() == ["Ab.", "..c"]
    assert b.transpose().to_rows() == ["A.", "b.", ".c"]
    assert not b.is_empty() and Board(3, 2).is_empty()
    assert b.with_tiles([Tile(1, 0, "Z")]).to_rows() == ["Ab.", "Z.c"]
    assert hash(b) == hash(board("Ab.", "..c")) and b == board("Ab.", "..c")


def test_board_rejects_bad_input():
    with pytest.raises(ValueError):
        board("AB", "A")
    with pytest.raises(ValueError):
        board("A1")
    with pytest.raises(ValueError):
        Board(2, 2, [[None, None]])


def test_rack_parsing():
    assert parse_rack("aB??c") == Counter({"A": 1, "B": 1, "C": 1, "?": 2})
    assert rack_to_str(parse_rack("?zA")) == "AZ?"
    with pytest.raises(ValueError):
        parse_rack("A1")


def test_parse_placement_sorts_and_marks_blanks():
    assert parse_placement("3,4,t 3,2,C") == (Tile(3, 2, "C"), Tile(3, 4, "T", True))


# lexicon


def test_lexicon_normalizes_and_filters():
    lx = Lexicon.from_words(["cat", " Dog ", "# comment", "", "a", "x-ray", "café"], "ACDGOTX")
    assert lx.words == {"CAT", "DOG"}
    assert lx.skipped == 3


def test_lexicon_version_is_order_independent():
    assert lex("CAT", "DOG").version == lex("DOG", "CAT", "cat").version
    assert lex("CAT").version != lex("CAT", "DOG").version


def test_lexicon_load(tmp_path):
    p = tmp_path / "w.txt"
    p.write_text("﻿cat\nCATS\n")
    assert Lexicon.load(p).words == {"CAT", "CATS"}
    with pytest.raises(WordListError, match="not found"):
        Lexicon.load(tmp_path / "none.txt")
    p.write_text("# only comments\n")
    with pytest.raises(WordListError, match="no usable"):
        Lexicon.load(p)
    p.write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(WordListError, match="UTF-8"):
        Lexicon.load(p)


# legality


def test_first_move_scores_with_start_premium(tiny):
    assert score(empty(tiny), "CAT", "3,2,C 3,3,A 3,4,T", tiny, ["CAT"]) == 10


def test_first_move_must_cover_start(tiny):
    assert illegal(empty(tiny), "CAT", "2,2,C 2,3,A 2,4,T", tiny, ["CAT"]) == "first_move_start"


def test_first_move_anywhere_when_no_start_squares():
    p = make_profile(rules__start_squares=[])
    assert score(empty(p), "CAT", "0,0,C 0,1,A 0,2,T", p, ["CAT"]) == 15


def test_single_tile_on_empty_board_forms_no_word(tiny):
    assert illegal(empty(tiny), "A", "3,3,A", tiny, ["A"]) == "no_word"


def test_basic_rejections(tiny):
    b, w = ROW3_CAT, ["CAT", "CATS", "AT", "TA"]
    assert illegal(b, "S", "", tiny, w) == "empty"
    assert illegal(b, "S", "3,7,S", tiny, w) == "out_of_bounds"
    assert illegal(b, "S", "3,4,S", tiny, w) == "occupied"
    assert illegal(b, "SS", "3,5,S 3,5,S", tiny, w) == "duplicate_square"
    assert illegal(b, "AT", "4,1,A 5,2,T", tiny, w) == "not_collinear"
    assert illegal(b, "S", "0,0,S", tiny, w) == "not_connected"
    assert illegal(b, "Q", "3,5,Q", tiny, w) == "invalid_word"
    assert illegal(b, "Ñ", "3,5,Ñ", tiny, w) == "not_in_alphabet"


def test_gaps(tiny):
    w = ["CATS", "SCATS", "CAT"]
    assert illegal(ROW3_CAT, "SS", "3,1,S 3,6,S", tiny, w) == "gap"
    assert illegal(empty(tiny), "CAT", "3,2,C 3,3,A 3,5,T", tiny, w) == "gap"
    single_a = board(".......", ".......", ".......", "...A...", ".......", ".......", ".......")
    assert score(single_a, "CT", "3,2,C 3,4,T", tiny, ["CAT"]) == 5


def test_rack_counts_and_blanks(tiny):
    b = empty(tiny)
    w = ["AA", "AAA"]
    assert illegal(b, "A", "3,3,A 3,4,A", tiny, w) == "rack"
    assert illegal(b, "A?", "3,3,A 3,4,A", tiny, w) == "rack"
    assert score(b, "A?", "3,3,A 3,4,a", tiny, w) == 2
    assert score(b, "AAA", "3,2,A 3,3,A 3,4,A", tiny, w) == 6
    assert illegal(b, "AA", "3,2,A 3,3,A 3,4,A", tiny, w) == "rack"
    assert illegal(b, "AA?", "3,2,a 3,3,a 3,4,A", tiny, w) == "rack"


def test_cross_word_must_be_valid(tiny):
    assert illegal(ROW3_CAT, "AT", "4,3,T 4,4,O", tiny, ["TO"]) == "rack"
    assert illegal(ROW3_CAT, "TO", "4,3,T 4,4,O", tiny, ["TO"]) == "invalid_word"


def test_min_word_length():
    p = make_profile(rules__min_word_length=3)
    b = board(".......", ".......", ".......", "...A...", ".......", ".......", ".......")
    assert illegal(b, "T", "3,4,T", p, ["AT"]) == "too_short"


# scoring


def test_extension_counts_old_letters_at_face_value(tiny):
    assert score(ROW3_CAT, "S", "3,5,S", tiny, ["CATS"]) == 6
    assert score(ROW3_CAT, "S", "3,1,S", tiny, ["SCAT"]) == 6
    shifted = board(".......", ".......", ".......", "...CAT.", ".......", ".......", ".......")
    assert score(shifted, "S", "3,6,S", tiny, ["CATS"]) == 7


def test_hook_plus_new_word(tiny):
    assert score(ROW3_CAT, "SO", "3,5,S 4,5,O", tiny, ["CATS", "SO"]) == 8


def test_parallel_play_with_two_cross_words(tiny):
    b = board(".......", ".......", ".......", "...AT..", ".......", ".......", ".......")
    words = formed_words(b, parse_placement("4,3,T 4,4,O"))
    assert sorted((w.text, w.direction) for w in words) == [("AT", "V"), ("TO", "H"), ("TO", "V")]
    assert score(b, "TO", "4,3,T 4,4,O", tiny, ["AT", "TO"]) == 10


def test_single_tile_forming_two_words_not_double_counted(tiny):
    b = board(".......", ".......", "...O...", "..A....", ".......", ".......", ".......")
    tiles = parse_placement("3,3,X")
    words = formed_words(b, tiles)
    assert sorted(w.text for w in words) == ["AX", "OX"]
    assert score(b, "X", "3,3,X", tiny, ["AX", "OX"]) == 36


def test_single_tile_forming_one_word(tiny):
    b = board(".......", ".......", ".......", "..A....", ".......", ".......", ".......")
    assert score(b, "T", "3,3,T", tiny, ["AT"]) == 4
    assert len(formed_words(b, parse_placement("3,3,T"))) == 1


def test_blank_scores_zero_but_triggers_word_premium(tiny):
    assert score(empty(tiny), "C?T", "3,2,C 3,3,a 3,4,T", tiny, ["CAT"]) == 8
    assert score(empty(tiny), "?AT", "3,2,c 3,3,A 3,4,T", tiny, ["CAT"]) == 4


def test_blank_on_letter_premium_scores_zero(tiny):
    b = board(".......", ".......", "...A...", ".......", ".......", ".......", ".......")
    assert score(b, "?", "2,2,x", tiny, ["XA"]) == 1
    assert score(b, "X", "2,2,X", tiny, ["XA"]) == 25


@pytest.mark.parametrize(
    ("stacking", "expected"), [("multiply", 153 + 50), ("add", 85 + 50), ("max", 51 + 50)]
)
def test_multiple_word_premiums_and_full_rack_bonus(stacking, expected):
    p = make_profile(rules__start_squares=[], rules__word_premium_stacking=stacking)
    placement = "0,0,B 0,1,A 0,2,T 0,3,C 0,4,H 0,5,E 0,6,S"
    assert score(empty(p), "BATCHES", placement, p, ["BATCHES"]) == expected


def test_full_rack_bonus_needs_configured_tile_count():
    p = make_profile(rules__start_squares=[])
    assert score(empty(p), "CAT", "0,0,C 0,1,A 0,2,T", p, ["CAT"]) == 15
    p3 = make_profile(rules__start_squares=[], scoring__full_rack_tiles=3)
    assert score(empty(p3), "CAT", "0,0,C 0,1,A 0,2,T", p3, ["CAT"]) == 65


def test_edges(tiny):
    p = make_profile(rules__start_squares=[])
    assert score(empty(p), "CAT", "0,4,C 0,5,A 0,6,T", p, ["CAT"]) == 15
    assert score(empty(p), "CAT", "4,6,C 5,6,A 6,6,T", p, ["CAT"]) == 15
    corner = board("......C", "......A", ".......", ".......", ".......", ".......", ".......")
    assert score(corner, "T", "2,6,T", tiny, ["CAT"]) == 5


def test_score_breakdown_details(tiny):
    b = board(".......", ".......", ".......", "...AT..", ".......", ".......", ".......")
    result = evaluate(b, parse_rack("TO"), parse_placement("4,3,T 4,4,O"), tiny, lex("AT", "TO"))
    main = next(w for w in result.words if w.direction == "H")
    assert (main.text, main.letter_sum, main.word_multiplier, main.total) == ("TO", 4, 1, 4)
    o_cell = main.cells[1]
    assert (o_cell.letter, o_cell.new, o_cell.letter_multiplier) == ("O", True, 3)
    assert result.bonus == 0 and result.tiles_used == 2


def test_check_placement_returns_words(tiny):
    words = check_placement(ROW3_CAT, parse_rack("S"), parse_placement("3,5,S"), tiny, lex("CATS"))
    assert [w.text for w in words] == ["CATS"]


def test_score_placement_skips_legality(tiny):
    assert score_placement(ROW3_CAT, parse_placement("3,5,Q"), tiny).total == 15

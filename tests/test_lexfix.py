from tilelens.rules.lexicon import Lexicon
from tilelens.vision.grid import Grid
from tilelens.vision.lexfix import settle
from tilelens.vision.recognizer import COMMITTED, UNSEEN, CellRead, Reading

LEX = Lexicon.from_words(["JOB", "JIB", "SIX", "WEB", "AT", "AN"])


def reading(rows):
    cells = []
    for line in rows:
        row = []
        for ch in line:
            if ch == ".":
                row.append(CellRead())
            elif ch == "#":
                row.append(CellRead(state=UNSEEN))
            elif ch.islower():
                row.append(
                    CellRead(ch.upper(), False, COMMITTED, True, "unclear", 0.6, ("I", "O", "S"))
                )
            else:
                row.append(CellRead(ch, False, COMMITTED, False, "", 0.99))
        cells.append(row)
    return Reading(cells, [], Grid(0, 0, 10, 10, len(rows), len(rows[0])), True, 0.0)


def test_unsure_letter_takes_next_candidate_that_makes_a_word():
    r = reading(["J.B", "..."])
    r.cells[0][1] = CellRead("S", False, COMMITTED, True, "unclear", 0.5, ("S", "O", "I"))
    out = settle(r, LEX)
    assert r.cells[0][1].letter == "O" and not r.cells[0][1].uncertain
    assert out.changed == [(0, 1, "S", "O")]


def test_no_valid_word_leaves_square_unresolved():
    r = reading(["JXB", "..."])
    r.cells[0][1] = CellRead("X", False, COMMITTED, True, "unclear", 0.5, ("X", "Q", "Z"))
    out = settle(r, LEX)
    assert out.unresolved == [(0, 1)] and r.cells[0][1].uncertain
    assert out.bad_words[0][0] == "JXB"


def test_run_touching_unseen_square_is_not_judged():
    r = reading(["JS#", "..."])
    r.cells[0][1] = CellRead("S", False, COMMITTED, True, "unclear", 0.5, ("S", "O"))
    out = settle(r, LEX)
    assert out.unresolved == [(0, 1)]


def test_confident_letters_are_left_alone():
    r = reading(["QZ.", "..."])
    out = settle(r, LEX)
    assert not out.changed and r.cells[0][0].letter == "Q"


def test_weak_letter_in_invalid_word_is_retried():
    r = reading(["WEB", "..."])
    r.cells[0][0] = CellRead("V", False, COMMITTED, False, "", 0.88, ("V", "W"))
    out = settle(r, LEX)
    assert r.cells[0][0].letter == "W" and out.changed


def test_lone_tile_with_nothing_beside_it_is_dropped():
    from tilelens.vision.lexfix import drop_isolated

    r = reading(["WEB..", ".....", "....X"])
    assert drop_isolated(r) == [(2, 4)]
    assert r.cells[2][4].letter is None and r.cells[0][0].letter == "W"


def test_tile_beside_unseen_square_is_kept():
    from tilelens.vision.lexfix import drop_isolated

    r = reading(["WEB..", ".....", "...#X"])
    assert drop_isolated(r) == []

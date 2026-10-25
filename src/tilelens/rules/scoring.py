from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from tilelens.rules.board import Board, Tile
from tilelens.rules.legality import FormedWord, check_placement, formed_words
from tilelens.rules.profile import Profile


@dataclass(frozen=True)
class CellScore:
    row: int
    col: int
    letter: str
    blank: bool
    new: bool
    value: int
    letter_multiplier: int


@dataclass(frozen=True)
class WordScore:
    text: str
    row: int
    col: int
    direction: str
    cells: tuple[CellScore, ...]
    letter_sum: int
    word_multiplier: int
    total: int


@dataclass(frozen=True)
class MoveScore:
    words: tuple[WordScore, ...]
    tiles_used: int
    bonus: int
    total: int


def score_word(
    word: FormedWord, profile: Profile, stamps: dict[tuple[int, int], int] | None = None
) -> WordScore:
    cells = []
    letter_sum = 0
    word_mults = []
    for c in word.cells:
        value = profile.tile_value(c.letter, c.blank)
        if not c.new and not c.blank and stamps:
            value = stamps.get((c.row, c.col), value)
        lm = profile.letter_multiplier(c.row, c.col) if c.new else 1
        if c.new:
            word_mults.append(profile.word_multiplier(c.row, c.col))
        letter_sum += value * lm
        cells.append(CellScore(c.row, c.col, c.letter, c.blank, c.new, value, lm))
    wm = profile.combine_word_multipliers(word_mults)
    return WordScore(
        word.text, word.row, word.col, word.direction, tuple(cells), letter_sum, wm, letter_sum * wm
    )


def score_words(
    words: Sequence[FormedWord],
    tiles_used: int,
    profile: Profile,
    stamps: dict[tuple[int, int], int] | None = None,
) -> MoveScore:
    scored = tuple(score_word(w, profile, stamps) for w in words)
    bonus = profile.full_rack_bonus if tiles_used == profile.full_rack_tiles else 0
    return MoveScore(scored, tiles_used, bonus, sum(w.total for w in scored) + bonus)


def score_placement(board: Board, tiles: Sequence[Tile], profile: Profile) -> MoveScore:
    """Score without checking legality; use evaluate() for checked scoring."""
    return score_words(formed_words(board, tiles), len(tiles), profile, board.stamps)


def evaluate(
    board: Board, rack: Counter, tiles: Sequence[Tile], profile: Profile, lexicon
) -> MoveScore:
    words = check_placement(board, rack, tiles, profile, lexicon)
    return score_words(words, len(tiles), profile, board.stamps)

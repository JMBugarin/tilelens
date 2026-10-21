from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from tilelens.rules.board import Board, Tile
from tilelens.rules.profile import BLANK, Profile

ACROSS = "H"
DOWN = "V"


@dataclass(frozen=True)
class WordCell:
    row: int
    col: int
    letter: str
    blank: bool
    new: bool


@dataclass(frozen=True)
class FormedWord:
    row: int
    col: int
    direction: str
    cells: tuple[WordCell, ...]

    @property
    def text(self) -> str:
        return "".join(c.letter for c in self.cells)


class IllegalPlacement(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _run(board: Board, new: dict[tuple[int, int], Tile], row: int, col: int, direction: str):
    dr, dc = (0, 1) if direction == ACROSS else (1, 0)

    def filled(r, c):
        return board.in_bounds(r, c) and ((r, c) in new or board.occupied(r, c))

    r, c = row, col
    while filled(r - dr, c - dc):
        r, c = r - dr, c - dc
    start = (r, c)
    cells = []
    while filled(r, c):
        if (r, c) in new:
            t = new[(r, c)]
            cells.append(WordCell(r, c, t.letter, t.blank, True))
        else:
            letter, blank = board.get(r, c)
            cells.append(WordCell(r, c, letter, blank, False))
        r, c = r + dr, c + dc
    return FormedWord(start[0], start[1], direction, tuple(cells))


def formed_words(board: Board, tiles: Sequence[Tile]) -> list[FormedWord]:
    """Every distinct run of two or more letters that passes through a new tile."""
    new = {(t.row, t.col): t for t in tiles}
    seen: dict[tuple[int, int, str], FormedWord] = {}
    for t in sorted(tiles):
        for direction in (ACROSS, DOWN):
            w = _run(board, new, t.row, t.col, direction)
            key = (w.row, w.col, direction)
            if len(w.cells) >= 2 and key not in seen:
                seen[key] = w
    return list(seen.values())


def placement_direction(tiles: Sequence[Tile]) -> str | None:
    rows = {t.row for t in tiles}
    cols = {t.col for t in tiles}
    if len(rows) == 1:
        return ACROSS
    if len(cols) == 1:
        return DOWN
    return None


def check_placement(
    board: Board,
    rack: Counter,
    tiles: Sequence[Tile],
    profile: Profile,
    lexicon,
) -> list[FormedWord]:
    """Return the words formed by a legal placement or raise IllegalPlacement."""
    if not tiles:
        raise IllegalPlacement("empty", "no tiles placed")
    squares = set()
    for t in tiles:
        if not board.in_bounds(t.row, t.col):
            raise IllegalPlacement("out_of_bounds", f"{t} is outside the board")
        if (t.row, t.col) in squares:
            raise IllegalPlacement("duplicate_square", f"two tiles on {t.row},{t.col}")
        squares.add((t.row, t.col))
        if board.occupied(t.row, t.col):
            raise IllegalPlacement("occupied", f"{t.row},{t.col} is already occupied")
        if t.letter not in profile.alphabet:
            raise IllegalPlacement("not_in_alphabet", f"{t.letter!r} is not in this profile")

    direction = placement_direction(tiles)
    if direction is None:
        raise IllegalPlacement("not_collinear", "tiles must share one row or one column")

    need = Counter(BLANK if t.blank else t.letter for t in tiles)
    for letter, n in need.items():
        if rack.get(letter, 0) < n:
            what = "blank" if letter == BLANK else f"'{letter}'"
            raise IllegalPlacement("rack", f"rack does not hold {n} x {what}")

    ordered = sorted(tiles)
    if direction == ACROSS:
        row = ordered[0].row
        line = [(row, c) for c in range(ordered[0].col, ordered[-1].col + 1)]
    else:
        col = ordered[0].col
        line = [(r, col) for r in range(ordered[0].row, ordered[-1].row + 1)]
    for sq in line:
        if sq not in squares and not board.occupied(*sq):
            raise IllegalPlacement("gap", f"gap at {sq[0]},{sq[1]}")

    if board.is_empty():
        if profile.start_squares and not squares & set(profile.start_squares):
            raise IllegalPlacement("first_move_start", "first placement must cover a start square")
    else:
        touches = any(
            board.in_bounds(t.row + dr, t.col + dc) and board.occupied(t.row + dr, t.col + dc)
            for t in tiles
            for dr, dc in ((0, 1), (0, -1), (1, 0), (-1, 0))
        )
        if not touches:
            raise IllegalPlacement("not_connected", "placement does not touch existing tiles")

    words = formed_words(board, tiles)
    if not words:
        raise IllegalPlacement("no_word", "placement forms no word")
    for w in words:
        if len(w.cells) < profile.min_word_length:
            raise IllegalPlacement("too_short", f"'{w.text}' is shorter than the minimum")
        if w.text not in lexicon:
            raise IllegalPlacement("invalid_word", f"'{w.text}' is not in the word list")
    return words

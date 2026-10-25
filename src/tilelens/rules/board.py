from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from tilelens.rules.profile import BLANK

EMPTY_CHARS = frozenset(".- _")
WALL = "#"
WALL_CELL = (WALL, False)


@dataclass(frozen=True, order=True)
class Tile:
    row: int
    col: int
    letter: str
    blank: bool = False

    def __str__(self) -> str:
        return f"{self.letter.lower() if self.blank else self.letter}@{self.row},{self.col}"


Cell = tuple[str, bool] | None


class Board:
    __slots__ = ("_cells", "_hash", "_stamps", "height", "width")

    def __init__(
        self,
        width: int,
        height: int,
        cells: Iterable[Iterable[Cell]] | None = None,
        stamps: dict[tuple[int, int], int] | None = None,
    ):
        """stamps: the value shown on a played tile when it differs from the letter's own.

        Some games stamp a tile with the points it earned (letter and word premiums), and
        count that stamped value whenever the tile is used again.
        """
        self.width = width
        self.height = height
        self._stamps = tuple(sorted((stamps or {}).items()))
        if cells is None:
            self._cells = tuple((None,) * width for _ in range(height))
        else:
            self._cells = tuple(tuple(row) for row in cells)
            if len(self._cells) != height or any(len(r) != width for r in self._cells):
                raise ValueError("cell grid does not match board size")
        self._hash = None

    @classmethod
    def empty_like(cls, profile) -> Board:
        return cls(profile.width, profile.height)

    @classmethod
    def from_rows(cls, rows: list[str]) -> Board:
        """Uppercase letters are tiles, lowercase letters are blanks showing that letter."""
        height = len(rows)
        width = len(rows[0]) if rows else 0
        cells = []
        for line in rows:
            if len(line) != width:
                raise ValueError("rows have different lengths")
            row: list[Cell] = []
            for ch in line:
                if ch in EMPTY_CHARS:
                    row.append(None)
                elif ch == WALL:
                    row.append(WALL_CELL)
                elif ch.isalpha():
                    row.append((ch.upper(), ch.islower()))
                else:
                    raise ValueError(f"unexpected board character {ch!r}")
            cells.append(row)
        return cls(width, height, cells)

    @property
    def stamps(self) -> dict[tuple[int, int], int]:
        return dict(self._stamps)

    def stamp(self, row: int, col: int) -> int | None:
        for (r, c), v in self._stamps:
            if r == row and c == col:
                return v
        return None

    def stamp_list(self) -> list[tuple[int, int, int]]:
        return [(r, c, v) for (r, c), v in self._stamps]

    def with_stamps(self, stamps: dict[tuple[int, int], int]) -> Board:
        return Board(self.width, self.height, self._cells, stamps)

    def to_rows(self) -> list[str]:
        out = []
        for row in self._cells:
            out.append("".join("." if c is None else (c[0].lower() if c[1] else c[0]) for c in row))
        return out

    def get(self, row: int, col: int) -> Cell:
        return self._cells[row][col]

    def letter(self, row: int, col: int) -> str | None:
        c = self._cells[row][col]
        return c[0] if c else None

    def occupied(self, row: int, col: int) -> bool:
        return self._cells[row][col] is not None

    def in_bounds(self, row: int, col: int) -> bool:
        return 0 <= row < self.height and 0 <= col < self.width

    def is_empty(self) -> bool:
        """True when no tile is known to be on the board (unknown squares do not count)."""
        return all(c is None or c[0] == WALL for row in self._cells for c in row)

    def is_unknown(self, row: int, col: int) -> bool:
        c = self._cells[row][col]
        return c is not None and c[0] == WALL

    def has_unknown(self) -> bool:
        return any(c is not None and c[0] == WALL for row in self._cells for c in row)

    def tiles(self) -> list[Tile]:
        return [
            Tile(r, c, cell[0], cell[1])
            for r, row in enumerate(self._cells)
            for c, cell in enumerate(row)
            if cell is not None and cell[0] != WALL
        ]

    def with_tiles(self, tiles: Iterable[Tile]) -> Board:
        cells = [list(row) for row in self._cells]
        for t in tiles:
            cells[t.row][t.col] = (t.letter, t.blank)
        return Board(self.width, self.height, cells, self.stamps)

    def transpose(self) -> Board:
        return Board(
            self.height,
            self.width,
            zip(*self._cells, strict=True),
            {(c, r): v for (r, c), v in self._stamps},
        )

    @property
    def cells(self) -> tuple[tuple[Cell, ...], ...]:
        return self._cells

    def __eq__(self, other) -> bool:
        return (
            isinstance(other, Board)
            and self._cells == other._cells
            and self._stamps == other._stamps
        )

    def __hash__(self) -> int:
        if self._hash is None:
            self._hash = hash((self._cells, self._stamps))
        return self._hash

    def __repr__(self) -> str:
        return "Board(\n  " + "\n  ".join(self.to_rows()) + "\n)"


def parse_rack(text: str) -> Counter:
    rack: Counter = Counter()
    for ch in text:
        if ch in " ,":
            continue
        if ch == BLANK:
            rack[BLANK] += 1
        elif ch.isalpha():
            rack[ch.upper()] += 1
        else:
            raise ValueError(f"unexpected rack character {ch!r}")
    return rack


def rack_to_str(rack: Counter) -> str:
    return "".join(sorted(rack.elements(), key=lambda ch: (ch == BLANK, ch)))


def parse_placement(text: str) -> tuple[Tile, ...]:
    """Parse 'r,c,X' items separated by spaces; a lowercase letter marks a blank."""
    tiles = []
    for item in text.split():
        r, c, ch = item.split(",")
        tiles.append(Tile(int(r), int(c), ch.upper(), ch.islower()))
    return tuple(sorted(tiles))

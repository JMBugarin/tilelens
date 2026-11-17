"""Remembers squares that are out of view while the player is zoomed in or panned.

Squares seen in earlier views are kept only while nothing can have changed out of sight:

- After the player's own move (the rack changed, or new tiles appeared that the unchanged rack
  could have supplied, as when the player draws back the letters just played), the others move
  next, possibly where the view does not reach. Out-of-view squares are unknown until a change
  by someone else is seen.
- A change in view that the player did not make starts a new record from that frame; panning
  and zooming then fill it in.

Squares never seen since the last change stay walls, so the search cannot use them.
"""

from __future__ import annotations

from collections import Counter
from enum import StrEnum

from tilelens.rules.board import Board
from tilelens.rules.profile import BLANK


class Coverage(StrEnum):
    FULL = "full"  # the whole board is in view
    TRACKING = "tracking"  # every square is in view or remembered
    PARTIAL = "partial"  # some squares are unknown


COVERAGE_NOTES = {
    Coverage.FULL: "Board aligned",
    Coverage.TRACKING: "Partial view · tracking",
    Coverage.PARTIAL: "Partial view · zoom out briefly to check the whole board",
}


class BoardMemory:
    def __init__(self) -> None:
        self.board: Board | None = None
        self.rack: Counter | None = None
        self.open = False

    def forget(self) -> None:
        self.board = None
        self.rack = None
        self.open = False

    def update(self, seen: Board, rack: Counter) -> tuple[Board, Coverage]:
        """Combine a confirmed reading with what is remembered.

        Returns the board to search (unknown squares are walls) and how much of it is known.
        """
        rack = Counter(rack)
        mem = self.board
        if mem is None or (mem.width, mem.height) != (seen.width, seen.height):
            self.board, self.rack, self.open = seen, rack, True
        elif rack != self.rack:
            self.board, self.rack, self.open = seen, rack, False
        elif changed := [
            (r, c)
            for r in range(seen.height)
            for c in range(seen.width)
            if not seen.is_unknown(r, c)
            and not mem.is_unknown(r, c)
            and seen.cells[r][c] != mem.cells[r][c]
        ]:
            self.board, self.open = seen, not self._own_play(mem, seen, changed)
        elif self.open:
            self.board = Board(
                seen.width,
                seen.height,
                [
                    [
                        mem.cells[r][c] if seen.is_unknown(r, c) else seen.cells[r][c]
                        for c in range(seen.width)
                    ]
                    for r in range(seen.height)
                ],
                {
                    **{k: v for k, v in mem.stamps.items() if seen.is_unknown(*k)},
                    **seen.stamps,
                },
                seen.bounds,
            )
        else:
            self.board = seen
        if not seen.has_unknown():
            return seen, Coverage.FULL
        if self.open and not self.board.has_unknown():
            return self.board, Coverage.TRACKING
        return self.board, Coverage.PARTIAL

    def _own_play(self, mem: Board, seen: Board, changed: list[tuple[int, int]]) -> bool:
        """Every change is a new tile on an empty square, and the rack held them all."""
        played = Counter()
        for r, c in changed:
            if mem.cells[r][c] is not None or seen.cells[r][c] is None:
                return False
            letter, blank = seen.cells[r][c]
            played[BLANK if blank else letter] += 1
        return not played - (self.rack or Counter())


__all__ = ["COVERAGE_NOTES", "BoardMemory", "Coverage"]

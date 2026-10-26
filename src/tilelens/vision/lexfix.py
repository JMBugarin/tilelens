"""Use the word list to settle letters the camera is unsure about.

Every run of two or more tiles on the board is a word, so a square read as I or S is settled by
whichever choice makes the words through it valid. Letters read with low confidence inside a
word that is not in the list are retried the same way.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from itertools import product

from tilelens.rules.lexicon import Lexicon
from tilelens.vision.recognizer import (
    COMMITTED,
    RECENT,
    TENTATIVE,
    UNKNOWN,
    UNSEEN,
    CellRead,
    Reading,
)

SHAKY = 0.93  # a letter read at least this well is not second-guessed by the word list
MAX_GROUP = 5
MAX_CHOICES = 4


@dataclass
class LexResult:
    changed: list[tuple[int, int, str, str]] = field(default_factory=list)  # r, c, was, now
    unresolved: list[tuple[int, int]] = field(default_factory=list)
    bad_words: list[tuple[str, list[tuple[int, int]]]] = field(default_factory=list)


def _runs(letters, blocked):
    """Words on the board as (text, cells, complete). Incomplete runs touch unseen squares."""
    rows, cols = len(letters), len(letters[0])
    for horizontal in (True, False):
        outer, inner = (rows, cols) if horizontal else (cols, rows)
        for a in range(outer):
            b = 0
            while b < inner:
                r, c = (a, b) if horizontal else (b, a)
                if letters[r][c] is None:
                    b += 1
                    continue
                start = b
                while b < inner and letters[a if horizontal else b][b if horizontal else a]:
                    b += 1
                cells = [(a, k) if horizontal else (k, a) for k in range(start, b)]
                if len(cells) < 2:
                    continue
                ends = []
                if start > 0:
                    ends.append((a, start - 1) if horizontal else (start - 1, a))
                if b < inner:
                    ends.append((a, b) if horizontal else (b, a))
                complete = not any(e in blocked for e in ends)
                yield cells, complete


def _grid(reading: Reading):
    letters, blocked = [], set()
    for r, row in enumerate(reading.cells):
        out = []
        for c, cell in enumerate(row):
            if cell.state == UNSEEN or cell.state in (TENTATIVE, UNKNOWN):
                blocked.add((r, c))
                out.append(None)
            else:
                out.append(cell.letter if cell.letter else None)
        letters.append(out)
    return letters, blocked


def _choices(cell) -> list[str]:
    out = [cell.letter] if cell.letter else []
    out += [k for k in cell.candidates if k not in out]
    return out[:MAX_CHOICES]


def settle(reading: Reading, lexicon: Lexicon) -> LexResult:
    """Fix reading.cells in place where the word list decides; report what is still unclear."""
    result = LexResult()
    if not reading.visible or not reading.cells:
        return result
    letters, blocked = _grid(reading)
    runs = list(_runs(letters, blocked))

    def word(cells, override):
        return "".join(override.get(rc, letters[rc[0]][rc[1]]) for rc in cells)

    suspects: set[tuple[int, int]] = set()
    for r, row in enumerate(reading.cells):
        for c, cell in enumerate(row):
            if cell.uncertain and cell.letter and cell.state in (COMMITTED, RECENT):
                suspects.add((r, c))
    for cells, complete in runs:
        if complete and word(cells, {}) not in lexicon:
            for rc in cells:
                cell = reading.cells[rc[0]][rc[1]]
                if cell.letter_score < SHAKY and not cell.blank:
                    suspects.add(rc)
    if not suspects:
        return result

    # cells that share a word are settled together
    groups: list[set[tuple[int, int]]] = []
    for rc in sorted(suspects):
        linked = [g for g in groups if any(rc in cells for cells, _ in runs if set(cells) & g)]
        merged = {rc}
        for g in linked:
            merged |= g
            groups.remove(g)
        groups.append(merged)

    for group in groups:
        cells = sorted(group)
        touching = [(rs, ok) for rs, ok in runs if set(rs) & group]
        if len(cells) > MAX_GROUP:
            result.unresolved += [rc for rc in cells if reading.cells[rc[0]][rc[1]].uncertain]
            continue
        options = [_choices(reading.cells[r][c]) for r, c in cells]
        if any(not o for o in options):
            result.unresolved += cells
            continue
        ranked = sorted(product(*[range(len(o)) for o in options]), key=lambda ix: (sum(ix), ix))
        winner = None
        for ix in ranked:
            override = {rc: options[i][k] for i, (rc, k) in enumerate(zip(cells, ix, strict=True))}
            if all(word(rs, override) in lexicon for rs, ok in touching if ok):
                winner = override
                break
        checkable = any(ok for _, ok in touching)
        if winner is None or not checkable:
            for rc in cells:
                if reading.cells[rc[0]][rc[1]].uncertain:
                    result.unresolved.append(rc)
            for rs, ok in touching:
                if ok and word(rs, {}) not in lexicon:
                    result.bad_words.append((word(rs, {}), rs))
            continue
        for (r, c), letter in winner.items():
            cell = reading.cells[r][c]
            if cell.letter != letter or cell.uncertain:
                result.changed.append((r, c, cell.letter or "", letter))
                reading.cells[r][c] = replace(
                    cell,
                    letter=letter,
                    uncertain=False,
                    reason="",
                    state=cell.state if cell.state in (COMMITTED, RECENT) else COMMITTED,
                )
    return result


def valid_word_cells(reading: Reading, lexicon: Lexicon) -> set[tuple[int, int]]:
    """Squares that belong to a whole word found in the word list.

    Only these are trustworthy enough to learn from: letters that make words with their
    neighbours are real tiles, whereas text from another window can look like anything."""
    letters, blocked = _grid(reading)
    out: set[tuple[int, int]] = set()
    for cells, complete in _runs(letters, blocked):
        if complete and "".join(letters[r][c] for r, c in cells) in lexicon:
            out.update(cells)
    return out


def drop_isolated(reading: Reading) -> list[tuple[int, int]]:
    """Remove a lone tile with nothing beside it on every side: tiles always join the others,
    so a tile that does not belongs to the game's interface (an avatar, a label)."""
    cells = reading.cells
    rows, cols = len(cells), len(cells[0]) if cells else 0

    def is_tile(r, c):
        cell = cells[r][c]
        return bool(cell.letter) and cell.state in (COMMITTED, RECENT)

    tiles = [(r, c) for r in range(rows) for c in range(cols) if is_tile(r, c)]
    if len(tiles) < 3:
        return []
    dropped = []
    for r, c in tiles:
        around = [
            (r + dr, c + dc)
            for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1))
            if 0 <= r + dr < rows and 0 <= c + dc < cols
        ]
        if any(is_tile(rr, cc) or cells[rr][cc].state == UNSEEN for rr, cc in around):
            continue
        dropped.append((r, c))
    for r, c in dropped:
        cells[r][c] = CellRead()
    return dropped


__all__ = ["LexResult", "drop_isolated", "settle", "valid_word_cells"]

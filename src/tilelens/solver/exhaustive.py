"""Brute-force reference solver used to validate the move generator.

It shares nothing with the graph search: it enumerates every line segment, every word of the
right length, every blank assignment, and asks the rules module whether each placement is legal.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from tilelens.rules.board import Board, Tile
from tilelens.rules.legality import IllegalPlacement, check_placement
from tilelens.rules.lexicon import Lexicon
from tilelens.rules.profile import BLANK, Profile
from tilelens.rules.scoring import score_words


def exhaustive_moves(
    board: Board, rack: Counter, profile: Profile, lexicon: Lexicon
) -> dict[tuple[Tile, ...], int]:
    by_length: dict[int, list[str]] = defaultdict(list)
    for w in lexicon.words:
        by_length[len(w)].append(w)
    rack_total = sum(rack.values())
    found: dict[tuple[Tile, ...], int] = {}

    lines = []
    for r in range(board.height):
        lines.append([(r, c) for c in range(board.width)])
    for c in range(board.width):
        lines.append([(r, c) for r in range(board.height)])

    for line in lines:
        n = len(line)
        for s in range(n):
            if s > 0 and board.occupied(*line[s - 1]):
                continue
            for e in range(s + 2, n + 1):
                if e < n and board.occupied(*line[e]):
                    continue
                squares = line[s:e]
                empties = [i for i, sq in enumerate(squares) if not board.occupied(*sq)]
                if not empties or len(empties) > rack_total:
                    continue
                for word in by_length.get(e - s, ()):
                    if any(
                        board.letter(*squares[i]) != word[i]
                        for i in range(len(squares))
                        if i not in empties
                    ):
                        continue
                    for tiles in _assignments(squares, empties, word, rack):
                        try:
                            words = check_placement(board, rack, tiles, profile, lexicon)
                        except IllegalPlacement:
                            continue
                        found[tiles] = score_words(words, len(tiles), profile).total
    return found


def _assignments(squares, empties, word, rack):
    out = []

    def rec(i, avail, acc):
        if i == len(empties):
            out.append(tuple(sorted(acc)))
            return
        idx = empties[i]
        r, c = squares[idx]
        letter = word[idx]
        if avail.get(letter, 0) > 0:
            avail[letter] -= 1
            acc.append(Tile(r, c, letter, False))
            rec(i + 1, avail, acc)
            acc.pop()
            avail[letter] += 1
        if avail.get(BLANK, 0) > 0:
            avail[BLANK] -= 1
            acc.append(Tile(r, c, letter, True))
            rec(i + 1, avail, acc)
            acc.pop()
            avail[BLANK] += 1

    rec(0, Counter(rack), [])
    return out

from __future__ import annotations

import hashlib
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from tilelens.rules.board import Board, Tile
from tilelens.rules.legality import ACROSS, DOWN, placement_direction
from tilelens.rules.lexicon import Lexicon
from tilelens.rules.profile import BLANK, Profile
from tilelens.rules.scoring import MoveScore, score_placement
from tilelens.solver.dawg import Dawg, load_or_build

MoveKey = tuple[Tile, ...]


@dataclass(frozen=True)
class Move:
    tiles: MoveKey
    score: int
    word: str
    row: int
    col: int
    direction: str
    breakdown: MoveScore = field(compare=False, repr=False)


@dataclass
class SearchResult:
    moves: list[Move]
    complete: bool
    moves_seen: int
    elapsed: float

    @property
    def label(self) -> str:
        return "highest scoring" if self.complete else "best found so far"


class _Stop(Exception):
    pass


def rank_key(score: int, key: MoveKey):
    return (-score, key)


class TopK:
    def __init__(self, k: int):
        self.k = k
        self.items: list[tuple[int, MoveKey]] = []

    def offer(self, score: int, key: MoveKey) -> None:
        items = self.items
        if len(items) == self.k:
            worst = items[-1]
            if (-score, key) >= (-worst[0], worst[1]):
                return
        for _s, k in items:
            if k == key:
                return
        items.append((score, key))
        items.sort(key=lambda it: (-it[0], it[1]))
        if len(items) > self.k:
            items.pop()


def playable_words(profile: Profile, lexicon: Lexicon) -> frozenset[str]:
    alphabet = profile.alphabet
    return frozenset(
        w for w in lexicon.words if len(w) >= profile.min_word_length and set(w) <= alphabet
    )


def graph_for(words: frozenset[str], use_cache: bool = False) -> Dawg:
    digest = hashlib.sha256("\n".join(sorted(words)).encode()).hexdigest()[:16]
    return load_or_build(words, f"{digest}-{len(words)}", use_cache=use_cache)


class MoveGenerator:
    def __init__(
        self,
        profile: Profile,
        lexicon: Lexicon,
        dawg: Dawg | None = None,
        use_cache: bool = False,
    ):
        self.profile = profile
        self.words = playable_words(profile, lexicon)
        self.lexicon_version = lexicon.version
        self.dawg = dawg or graph_for(self.words, use_cache=use_cache)
        self.alphabet = sorted(profile.alphabet)

    def solve(
        self,
        board: Board,
        rack: Counter,
        top_k: int = 3,
        should_stop: Callable[[], bool] | None = None,
    ) -> SearchResult:
        t0 = time.perf_counter()
        top = TopK(top_k)
        seen = [0]

        def sink(score, key):
            seen[0] += 1
            top.offer(score, key)

        complete = self._run(board, rack, sink, should_stop)
        moves = [self._make_move(board, key, score) for score, key in top.items]
        return SearchResult(moves, complete, seen[0], time.perf_counter() - t0)

    def all_moves(self, board: Board, rack: Counter) -> dict[MoveKey, int]:
        found: dict[MoveKey, int] = {}

        def sink(score, key):
            prev = found.get(key)
            if prev is not None and prev != score:
                raise AssertionError(f"inconsistent scores for {key}: {prev} vs {score}")
            found[key] = score

        self._run(board, rack, sink, None)
        return found

    def _make_move(self, board: Board, key: MoveKey, score: int) -> Move:
        breakdown = score_placement(board, key, self.profile)
        if breakdown.total != score:
            raise AssertionError(f"search score {score} != scorer {breakdown.total} for {key}")
        direction = placement_direction(key) if len(key) > 1 else None
        words = breakdown.words
        main = None
        if direction is not None:
            main = next(w for w in words if w.direction == direction)
        else:
            main = next((w for w in words if w.direction == ACROSS), words[0])
        return Move(key, score, main.text, main.row, main.col, main.direction, breakdown)

    def _run(self, board, rack, sink, should_stop) -> bool:
        rack_counts = {k: v for k, v in rack.items() if v > 0}
        total_tiles = sum(rack_counts.values())
        if total_tiles == 0:
            return True
        empty_board = board.is_empty()
        try:
            for transposed in (False, True):
                self._scan(
                    board, rack_counts, total_tiles, transposed, empty_board, sink, should_stop
                )
        except _Stop:
            return False
        return True

    def _scan(self, board, rack, total_tiles, transposed, empty_board, sink, should_stop):
        p = self.profile
        words = self.words
        edges = self.dawg.edges
        term = self.dawg.terminal
        values = p.letter_values
        alphabet = self.alphabet
        if transposed:
            H, W = board.width, board.height
            cell = lambda r, c: board.get(c, r)  # noqa: E731
            lm_at = lambda r, c: p.letter_multiplier(c, r)  # noqa: E731
            wm_at = lambda r, c: p.word_multiplier(c, r)  # noqa: E731
            starts = {(c, r) for r, c in p.start_squares}
        else:
            H, W = board.height, board.width
            cell = board.get
            lm_at = p.letter_multiplier
            wm_at = p.word_multiplier
            starts = set(p.start_squares)

        g = [[None] * W for _ in range(H)]
        bl = [[False] * W for _ in range(H)]
        for r in range(H):
            for c in range(W):
                x = cell(r, c)
                if x is not None:
                    g[r][c] = x[0]
                    bl[r][c] = x[1]
        LM = [[lm_at(r, c) for c in range(W)] for r in range(H)]
        WM = [[wm_at(r, c) for c in range(W)] for r in range(H)]

        anchor = [[False] * W for _ in range(H)]
        cross_ok: list[list[set | None]] = [[None] * W for _ in range(H)]
        cross_sum = [[0] * W for _ in range(H)]
        if empty_board:
            for r in range(H):
                for c in range(W):
                    anchor[r][c] = (r, c) in starts if starts else True
        else:
            for r in range(H):
                for c in range(W):
                    if g[r][c] is not None:
                        continue
                    up = r > 0 and g[r - 1][c] is not None
                    down = r < H - 1 and g[r + 1][c] is not None
                    left = c > 0 and g[r][c - 1] is not None
                    right = c < W - 1 and g[r][c + 1] is not None
                    anchor[r][c] = up or down or left or right
                    if up or down:
                        rr = r - 1
                        above = []
                        s = 0
                        while rr >= 0 and g[rr][c] is not None:
                            above.append(g[rr][c])
                            s += 0 if bl[rr][c] else values[g[rr][c]]
                            rr -= 1
                        prefix = "".join(reversed(above))
                        rr = r + 1
                        below = []
                        while rr < H and g[rr][c] is not None:
                            below.append(g[rr][c])
                            s += 0 if bl[rr][c] else values[g[rr][c]]
                            rr += 1
                        suffix = "".join(below)
                        cross_ok[r][c] = {L for L in alphabet if prefix + L + suffix in words}
                        cross_sum[r][c] = s

        full_tiles = p.full_rack_tiles
        bonus_value = p.full_rack_bonus
        combine = p.combine_word_multipliers
        stacking_multiply = p.word_stacking == "multiply"
        counter = [0]

        for r in range(H):
            row = g[r]
            if not any(anchor[r]):
                continue
            blr = bl[r]
            lmr = LM[r]
            wmr = WM[r]
            ccr = cross_ok[r]
            csr = cross_sum[r]
            anc = anchor[r]
            placed: dict[int, tuple[str, bool]] = {}

            def record(
                start, end, r=r, row=row, blr=blr, lmr=lmr, wmr=wmr, ccr=ccr, csr=csr, placed=placed
            ):
                main = 0
                wm_product = 1
                wms = []
                cross = 0
                for col in range(start, end):
                    pl = placed.get(col)
                    if pl is None:
                        if not blr[col]:
                            main += values[row[col]]
                    else:
                        v = 0 if pl[1] else values[pl[0]]
                        lm = lmr[col]
                        wm = wmr[col]
                        main += v * lm
                        if wm != 1:
                            wm_product *= wm
                            wms.append(wm)
                        if ccr[col] is not None:
                            cross += (csr[col] + v * lm) * wm
                n = len(placed)
                mult = wm_product if stacking_multiply else combine(wms)
                score = main * mult + cross + (bonus_value if n == full_tiles else 0)
                if transposed:
                    key = tuple(sorted(Tile(c, r, L, b) for c, (L, b) in placed.items()))
                else:
                    key = tuple(sorted(Tile(r, c, L, b) for c, (L, b) in placed.items()))
                sink(score, key)

            def extend(node, col, anchor_col, start, row=row, ccr=ccr, placed=placed):
                counter[0] += 1
                if should_stop is not None and counter[0] & 2047 == 0 and should_stop():
                    raise _Stop
                if col < W:
                    ch = row[col]
                    if ch is None:
                        if col > anchor_col and term[node]:
                            record(start, col)
                        allowed = ccr[col]
                        nblank = rack.get(BLANK, 0)
                        for L, child in edges[node].items():
                            if allowed is not None and L not in allowed:
                                continue
                            if rack.get(L, 0) > 0:
                                rack[L] -= 1
                                placed[col] = (L, False)
                                extend(child, col + 1, anchor_col, start)
                                del placed[col]
                                rack[L] += 1
                            if nblank > 0:
                                rack[BLANK] = nblank - 1
                                placed[col] = (L, True)
                                extend(child, col + 1, anchor_col, start)
                                del placed[col]
                                rack[BLANK] = nblank
                    else:
                        child = edges[node].get(ch)
                        if child is not None:
                            extend(child, col + 1, anchor_col, start)
                elif col > anchor_col and term[node]:
                    record(start, col)

            def left_part(node, anchor_col, limit, partial, placed=placed):
                start = anchor_col - len(partial)
                for i, item in enumerate(partial):
                    placed[start + i] = item
                extend(node, anchor_col, anchor_col, start)
                for i in range(len(partial)):
                    del placed[start + i]
                if limit <= 0:
                    return
                nblank = rack.get(BLANK, 0)
                for L, child in edges[node].items():
                    if rack.get(L, 0) > 0:
                        rack[L] -= 1
                        partial.append((L, False))
                        left_part(child, anchor_col, limit - 1, partial)
                        partial.pop()
                        rack[L] += 1
                    if nblank > 0:
                        rack[BLANK] = nblank - 1
                        partial.append((L, True))
                        left_part(child, anchor_col, limit - 1, partial)
                        partial.pop()
                        rack[BLANK] = nblank

            for a in range(W):
                if not anc[a]:
                    continue
                if a > 0 and row[a - 1] is not None:
                    s = a - 1
                    while s > 0 and row[s - 1] is not None:
                        s -= 1
                    node = 0
                    for c in range(s, a):
                        node = edges[node].get(row[c])
                        if node is None:
                            break
                    if node is not None:
                        extend(node, a, a, s)
                else:
                    limit = 0
                    c = a - 1
                    while c >= 0 and row[c] is None and not anc[c] and limit < total_tiles - 1:
                        limit += 1
                        c -= 1
                    left_part(0, a, limit, [])


__all__ = ["DOWN", "Move", "MoveGenerator", "SearchResult", "TopK"]

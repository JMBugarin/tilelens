from __future__ import annotations

import hashlib
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from tilelens.rules.board import WALL_CELL, Board, Tile
from tilelens.rules.legality import ACROSS, DOWN, placement_direction
from tilelens.rules.lexicon import Lexicon
from tilelens.rules.profile import BLANK, Profile
from tilelens.rules.scoring import MoveScore, score_placement
from tilelens.solver.dawg import Dawg, load_or_build

MoveKey = tuple[Tile, ...]
NO_FLOOR = float("-inf")
CROSS_MEMO_LIMIT = 20_000


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


@dataclass(slots=True)
class _Anchor:
    col: int = 0
    prefix_score_bound: int = 0
    prefix_word_bound: int = 1


class _Stop(Exception):
    pass


def rank_key(score: int, key: MoveKey):
    return (-score, key)


class TopK:
    def __init__(self, k: int):
        self.k = k
        self.items: list[tuple[int, MoveKey]] = []
        self.floor = NO_FLOOR

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
        if len(items) == self.k:
            self.floor = items[-1][0]


class _Everything:
    """Collects every placement with its score; nothing is ever below its floor."""

    floor = NO_FLOOR

    def __init__(self):
        self.found: dict[MoveKey, int] = {}

    def offer(self, score: int, key: MoveKey) -> None:
        prev = self.found.get(key)
        if prev is not None and prev != score:
            raise AssertionError(f"inconsistent scores for {key}: {prev} vs {score}")
        self.found[key] = score


def resolve_unknown(board: Board, profile: Profile) -> Board:
    """Unknown squares are walls: no placement may touch them. One exception: if every start
    square is visible and empty, no move has been played yet, so the whole board is empty."""
    if not board.has_unknown() or not board.is_empty() or not profile.start_squares:
        return board
    if all(board.get(r, c) is None for r, c in profile.start_squares):
        return Board(board.width, board.height, None, None, board.bounds)
    return board


def playable_words(profile: Profile, lexicon: Lexicon) -> frozenset[str]:
    alphabet = profile.alphabet
    return frozenset(
        w for w in lexicon.words if len(w) >= profile.min_word_length and set(w) <= alphabet
    )


def graph_for(words: frozenset[str], use_cache: bool = False) -> Dawg:
    digest = hashlib.sha256("\n".join(sorted(words)).encode()).hexdigest()[:16]
    return load_or_build(words, f"{digest}-{len(words)}", use_cache=use_cache)


def _row_bounds(row, tile_values, letter_mult, word_mult, cross_checks, cross_scores, rmax):
    """For every column and every count of tiles still to place, what the rest of a word could
    add: the existing tiles it must pass over, the best letter multiplier and word multiplier it
    could land on, and what its cross words could add. Each is an overestimate."""
    width = len(row)
    zero = [0] * (rmax + 1)
    one = [1] * (rmax + 1)
    existing_points = [zero] * (width + 1)
    max_letter_mult = [one] * (width + 1)
    word_product = [one] * (width + 1)
    cross_points = [zero] * (width + 1)
    cross_factor = [zero] * (width + 1)
    for col in range(width - 1, -1, -1):
        if row[col] is not None:
            v = tile_values[col]
            existing_points[col] = [v + x for x in existing_points[col + 1]]
            max_letter_mult[col], word_product[col] = (
                max_letter_mult[col + 1],
                word_product[col + 1],
            )
            cross_points[col], cross_factor[col] = cross_points[col + 1], cross_factor[col + 1]
        else:
            lm = letter_mult[col]
            w = word_mult[col]
            existing_points[col] = [0, *existing_points[col + 1][:-1]]
            max_letter_mult[col] = [1] + [
                lm if lm > x else x for x in max_letter_mult[col + 1][:-1]
            ]
            word_product[col] = [1] + [w * x for x in word_product[col + 1][:-1]]
            if cross_checks[col] is not None:
                base = cross_scores[col] * w
                lw = lm * w
                cross_points[col] = [0] + [base + x for x in cross_points[col + 1][:-1]]
                cross_factor[col] = [0] + [lw if lw > x else x for x in cross_factor[col + 1][:-1]]
            else:
                cross_points[col] = [0, *cross_points[col + 1][:-1]]
                cross_factor[col] = [0, *cross_factor[col + 1][:-1]]
    return existing_points, max_letter_mult, word_product, cross_points, cross_factor


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
        self._cross_memo: dict[tuple[str, str], frozenset[str]] = {}
        self._layout: dict[bool, tuple[list, list]] = {}

    def solve(
        self,
        board: Board,
        rack: Counter,
        top_k: int = 3,
        should_stop: Callable[[], bool] | None = None,
    ) -> SearchResult:
        t0 = time.perf_counter()
        top = TopK(top_k)
        board = resolve_unknown(board, self.profile)
        complete, seen = self._run(board, rack, top, should_stop)
        moves = [self._make_move(board, key, score) for score, key in top.items]
        return SearchResult(moves, complete, seen, time.perf_counter() - t0)

    def all_moves(self, board: Board, rack: Counter) -> dict[MoveKey, int]:
        sink = _Everything()
        self._run(board, rack, sink, None)
        return sink.found

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

    def _run(self, board, rack, sink, should_stop) -> tuple[bool, int]:
        rack_counts = {
            letter: count
            for letter, count in rack.items()
            if count > 0 and (letter == BLANK or letter in self.profile.letter_values)
        }
        total_tiles = sum(rack_counts.values())
        if total_tiles == 0:
            return True, 0
        board = resolve_unknown(board, self.profile)
        empty_board = board.is_empty() and not board.has_unknown()
        seen = [0]
        try:
            if should_stop is not None and should_stop():
                raise _Stop
            for transposed in (False, True):
                self._scan(
                    board,
                    rack_counts,
                    total_tiles,
                    transposed,
                    empty_board,
                    sink,
                    should_stop,
                    seen,
                )
        except _Stop:
            return False, seen[0]
        return True, seen[0]

    def _layout_grids(self, transposed: bool):
        grids = self._layout.get(transposed)
        if grids is None:
            p = self.profile
            if transposed:
                rows, cols = p.width, p.height
                lm = [[p.letter_multiplier(c, r) for c in range(cols)] for r in range(rows)]
                wm = [[p.word_multiplier(c, r) for c in range(cols)] for r in range(rows)]
            else:
                rows, cols = p.height, p.width
                lm = [[p.letter_multiplier(r, c) for c in range(cols)] for r in range(rows)]
                wm = [[p.word_multiplier(r, c) for c in range(cols)] for r in range(rows)]
            grids = self._layout[transposed] = (lm, wm)
        return grids

    def _cross_set(self, prefix: str, suffix: str) -> frozenset[str]:
        memo = self._cross_memo
        key = (prefix, suffix)
        hit = memo.get(key)
        if hit is None:
            words = self.words
            hit = frozenset(L for L in self.alphabet if prefix + L + suffix in words)
            if len(memo) >= CROSS_MEMO_LIMIT:
                memo.clear()
            memo[key] = hit
        return hit

    def _scan(
        self, board, rack, total_tiles, transposed, empty_board, sink, should_stop, seen
    ) -> None:
        p = self.profile
        edges = self.dawg.edges
        term = self.dawg.terminal
        values = p.letter_values
        lm_full, wm_full = self._layout_grids(transposed)

        cells = board.cells
        bounds = board.bounds
        if transposed:
            H, W = board.width, board.height
            starts = {(c, r) for r, c in p.start_squares}
        else:
            H, W = board.height, board.width
            starts = set(p.start_squares)

        if bounds is None:
            rlo, rhi, clo, chi = 0, H - 1, 0, W - 1
            inside = None
        else:
            b0, b1, b2, b3 = bounds
            if transposed:
                b0, b1, b2, b3 = b1, b0, b3, b2
            rlo, rhi = max(0, b0 - 1), min(H - 1, b2 + 1)
            clo, chi = max(0, b1 - 1), min(W - 1, b3 + 1)
            inside = (b0, b1, b2, b3)
        if rlo > rhi or clo > chi:
            return
        width = chi - clo + 1
        height = rhi - rlo + 1

        g: list[list] = []
        blank_grid: list[list] = []
        for r in range(rlo, rhi + 1):
            grow: list = [None] * width
            blrow: list = [False] * width
            row_in = inside is None or inside[0] <= r <= inside[2]
            for j in range(width):
                c = clo + j
                if not row_in or (inside is not None and not inside[1] <= c <= inside[3]):
                    x = WALL_CELL
                else:
                    x = cells[c][r] if transposed else cells[r][c]
                if x is not None:
                    grow[j] = x[0]
                    blrow[j] = x[1]
            g.append(grow)
            blank_grid.append(blrow)

        stamps = board.stamps
        if transposed:
            stamps = {(c, r): v for (r, c), v in stamps.items()}
        V = [
            [
                0
                if blr[j]
                else stamps.get((rlo + i, clo + j), values.get(gr[j], 0))
                if gr[j]
                else 0
                for j in range(width)
            ]
            for i, (gr, blr) in enumerate(zip(g, blank_grid, strict=True))
        ]

        anchor = [[False] * width for _ in range(height)]
        cross_ok: list[list[frozenset | None]] = [[None] * width for _ in range(height)]
        cross_sum = [[0] * width for _ in range(height)]
        cross_set = self._cross_set
        if empty_board:
            for i in range(height):
                for j in range(width):
                    anchor[i][j] = (rlo + i, clo + j) in starts if starts else True
        else:
            for i in range(height):
                gi = g[i]
                for j in range(width):
                    if gi[j] is not None:
                        continue
                    up = i > 0 and g[i - 1][j] is not None
                    down = i < height - 1 and g[i + 1][j] is not None
                    left = j > 0 and gi[j - 1] is not None
                    right = j < width - 1 and gi[j + 1] is not None
                    anchor[i][j] = up or down or left or right
                    if up or down:
                        ii = i - 1
                        above = []
                        s = 0
                        while ii >= 0 and g[ii][j] is not None:
                            above.append(g[ii][j])
                            s += V[ii][j]
                            ii -= 1
                        ii = i + 1
                        below = []
                        while ii < height and g[ii][j] is not None:
                            below.append(g[ii][j])
                            s += V[ii][j]
                            ii += 1
                        cross_ok[i][j] = cross_set("".join(reversed(above)), "".join(below))
                        cross_sum[i][j] = s

        full_tiles = p.full_rack_tiles
        bonus_value = p.full_rack_bonus
        stacking = p.word_stacking
        counter = [0]
        rack_letters = tuple(k for k in rack if k != BLANK)
        n_letters = len(rack_letters)
        current_anchor = _Anchor()
        offer = sink.offer
        rack_points = sum(values[L] * k for L, k in rack.items() if L != BLANK)
        bonus_bound = max(bonus_value, 0) if total_tiles >= full_tiles else 0
        bounded = (
            rack.get(BLANK, 0) > 0
            and min(values.values(), default=0) >= 0
            and min(stamps.values(), default=0) >= 0
        )

        if stacking == "multiply":
            initial_multiplier, multiplier_offset = 1, 0

            def stack(acc, wm):
                return acc * wm

        elif stacking == "add":
            initial_multiplier, multiplier_offset = 0, 1

            def stack(acc, wm):
                return acc + wm - 1

        else:
            initial_multiplier, multiplier_offset = 1, 0

            def stack(acc, wm):
                return acc if acc >= wm else wm

        for i in range(height):
            row = g[i]
            anc = anchor[i]
            if not any(anc):
                continue
            if should_stop is not None and should_stop():
                raise _Stop
            r_abs = rlo + i
            tile_values = V[i]
            letter_mult = lm_full[r_abs][clo : chi + 1]
            word_mult = wm_full[r_abs][clo : chi + 1]
            cross_checks = cross_ok[i]
            cross_scores = cross_sum[i]
            prefix: list[tuple[str, bool]] = []
            suffix: list[tuple[int, str, bool]] = []
            prune = bounded and sink.floor > NO_FLOOR
            if prune:
                existing_points, max_letter_mult, word_product, cross_points, cross_factor = (
                    _row_bounds(
                        row,
                        tile_values,
                        letter_mult,
                        word_mult,
                        cross_checks,
                        cross_scores,
                        min(total_tiles, width),
                    )
                )
            else:
                existing_points = max_letter_mult = word_product = cross_points = cross_factor = (
                    None
                )

            def record(
                main,
                cross,
                multiplier,
                n,
                r_abs=r_abs,
                letter_mult=letter_mult,
                word_mult=word_mult,
                prefix=prefix,
                suffix=suffix,
            ):
                seen[0] += 1
                nl = len(prefix)
                if nl:
                    col = current_anchor.col - nl
                    for L, b in prefix:
                        if not b:
                            main += values[L] * letter_mult[col]
                        wm = word_mult[col]
                        if wm != 1:
                            multiplier = stack(multiplier, wm)
                        col += 1
                score = main * (multiplier + multiplier_offset) + cross
                if n == full_tiles:
                    score += bonus_value
                if score < sink.floor:
                    return
                if transposed:
                    tiles = [Tile(clo + c, r_abs, L, b) for c, L, b in suffix]
                    if nl:
                        start = clo + current_anchor.col - nl
                        tiles = [
                            Tile(start + k, r_abs, L, b) for k, (L, b) in enumerate(prefix)
                        ] + tiles
                else:
                    tiles = [Tile(r_abs, clo + c, L, b) for c, L, b in suffix]
                    if nl:
                        start = clo + current_anchor.col - nl
                        tiles = [
                            Tile(r_abs, start + k, L, b) for k, (L, b) in enumerate(prefix)
                        ] + tiles
                offer(score, tuple(tiles))

            def extend(
                node,
                col,
                main,
                cross,
                multiplier,
                n,
                rest,
                row=row,
                letter_mult=letter_mult,
                word_mult=word_mult,
                tile_values=tile_values,
                cross_checks=cross_checks,
                cross_scores=cross_scores,
                suffix=suffix,
                record=record,
                prune=prune,
                existing_points=existing_points,
                max_letter_mult=max_letter_mult,
                word_product=word_product,
                cross_points=cross_points,
                cross_factor=cross_factor,
            ):
                counter[0] += 1
                if should_stop is not None and counter[0] & 2047 == 0 and should_stop():
                    raise _Stop
                if prune:
                    rem = min(total_tiles - n, width)
                    main_bound = (
                        main
                        + current_anchor.prefix_score_bound
                        + existing_points[col][rem]
                        + rest * max_letter_mult[col][rem]
                    )
                    mult_bound = (
                        (multiplier + multiplier_offset)
                        * current_anchor.prefix_word_bound
                        * word_product[col][rem]
                    )
                    cross_bound = cross + cross_points[col][rem] + rest * cross_factor[col][rem]
                    # A tie can still win on tile order.
                    if main_bound * mult_bound + cross_bound + bonus_bound < sink.floor:
                        return
                if col < width:
                    ch = row[col]
                    if ch is None:
                        if col > current_anchor.col and term[node]:
                            record(main, cross, multiplier, n)
                        if n == total_tiles:
                            return
                        allowed = cross_checks[col]
                        ed = edges[node]
                        nblank = rack.get(BLANK, 0)
                        if nblank > 0:
                            cand = allowed if allowed is not None and len(allowed) < len(ed) else ed
                        else:
                            cand = ed
                            size = len(ed)
                            if n_letters < size:
                                cand, size = rack_letters, n_letters
                            if allowed is not None and len(allowed) < size:
                                cand = allowed
                        lm = letter_mult[col]
                        wm = word_mult[col]
                        base = cross_scores[col]
                        for L in cand:
                            child = ed.get(L)
                            if child is None or (allowed is not None and L not in allowed):
                                continue
                            have = rack.get(L, 0)
                            if have > 0:
                                rack[L] = have - 1
                                v = values[L] * lm
                                suffix.append((col, L, False))
                                extend(
                                    child,
                                    col + 1,
                                    main + v,
                                    cross + (base + v) * wm if allowed is not None else cross,
                                    multiplier if wm == 1 else stack(multiplier, wm),
                                    n + 1,
                                    rest - values[L],
                                )
                                suffix.pop()
                                rack[L] = have
                            if nblank > 0:
                                rack[BLANK] = nblank - 1
                                suffix.append((col, L, True))
                                extend(
                                    child,
                                    col + 1,
                                    main,
                                    cross + base * wm if allowed is not None else cross,
                                    multiplier if wm == 1 else stack(multiplier, wm),
                                    n + 1,
                                    rest,
                                )
                                suffix.pop()
                                rack[BLANK] = nblank
                    else:
                        child = edges[node].get(ch)
                        if child is not None:
                            extend(
                                child, col + 1, main + tile_values[col], cross, multiplier, n, rest
                            )
                elif col > current_anchor.col and term[node]:
                    record(main, cross, multiplier, n)

            eligible = []

            def left_part(
                node,
                nl,
                prefix_points,
                extend=extend,
                prefix=prefix,
                eligible=eligible,
                prune=prune,
            ):
                counter[0] += 1
                if should_stop is not None and counter[0] & 2047 == 0 and should_stop():
                    raise _Stop
                ed = edges[node]
                nblank = rack.get(BLANK, 0)
                if nblank > 0:
                    avail = ed
                elif n_letters < len(ed):
                    avail = [L for L in rack_letters if rack[L] > 0 and L in ed]
                else:
                    avail = [L for L in ed if rack.get(L, 0) > 0]
                rest = rack_points - prefix_points
                floor = sink.floor if prune else NO_FLOOR
                alive = False
                for (
                    a,
                    allowed,
                    prefix_letter_mult,
                    prefix_word_mult,
                    max_prefix_letter,
                    fixed_main,
                    main_factor,
                    max_word_mult,
                    fixed_cross,
                    max_cross_factor,
                ) in eligible[nl]:
                    if floor > NO_FLOOR and (
                        prefix_points * max_prefix_letter + fixed_main + rest * main_factor
                    ) * max_word_mult + fixed_cross + rest * max_cross_factor < (
                        floor - bonus_bound
                    ):
                        continue
                    alive = True
                    if allowed is None or not allowed.isdisjoint(avail):
                        current_anchor.col = a
                        current_anchor.prefix_score_bound = prefix_points * prefix_letter_mult
                        current_anchor.prefix_word_bound = prefix_word_mult
                        extend(node, a, 0, 0, initial_multiplier, nl, rest)
                if floor > NO_FLOOR and not alive:
                    return
                if nl >= len(eligible) - 1:
                    return
                cand = ed if nblank > 0 or n_letters >= len(ed) else rack_letters
                for L in cand:
                    child = ed.get(L)
                    if child is None:
                        continue
                    have = rack.get(L, 0)
                    if have > 0:
                        rack[L] = have - 1
                        prefix.append((L, False))
                        left_part(child, nl + 1, prefix_points + values[L])
                        prefix.pop()
                        rack[L] = have
                    if nblank > 0:
                        rack[BLANK] = nblank - 1
                        prefix.append((L, True))
                        left_part(child, nl + 1, prefix_points)
                        prefix.pop()
                        rack[BLANK] = nblank

            lefts: list[tuple[int, int]] = []
            for a in range(width):
                if not anc[a]:
                    continue
                if a > 0 and row[a - 1] is not None:
                    current_anchor.col = a
                    current_anchor.prefix_score_bound = 0
                    current_anchor.prefix_word_bound = 1
                    s = a - 1
                    while s > 0 and row[s - 1] is not None:
                        s -= 1
                    node = 0
                    main0 = 0
                    for c in range(s, a):
                        node = edges[node].get(row[c])
                        if node is None:
                            break
                        main0 += tile_values[c]
                    if node is not None:
                        extend(node, a, main0, 0, initial_multiplier, 0, rack_points)
                else:
                    limit = 0
                    c = a - 1
                    while c >= 0 and row[c] is None and not anc[c] and limit < total_tiles - 1:
                        limit += 1
                        c -= 1
                    lefts.append((limit, a))
            if lefts:
                deepest = max(limit for limit, _a in lefts)
                eligible.extend([] for _ in range(deepest + 1))
                for limit, a in lefts:
                    prefix_letter_mult = [1] * (limit + 1)
                    prefix_word_mult = [1] * (limit + 1)
                    if prune:
                        for k in range(1, limit + 1):
                            prefix_letter_mult[k] = max(
                                prefix_letter_mult[k - 1], letter_mult[a - k]
                            )
                            prefix_word_mult[k] = prefix_word_mult[k - 1] * word_mult[a - k]
                    for depth in range(limit + 1):
                        if prune:
                            r0 = min(total_tiles - depth, width)
                            bound = (
                                prefix_letter_mult[limit],
                                existing_points[a][r0],
                                max(prefix_letter_mult[limit], max_letter_mult[a][r0]),
                                prefix_word_mult[limit] * word_product[a][r0],
                                cross_points[a][r0],
                                cross_factor[a][r0],
                            )
                        else:
                            bound = (1, 0, 1, 1, 0, 0)
                        eligible[depth].append(
                            (
                                a,
                                cross_checks[a],
                                prefix_letter_mult[depth],
                                prefix_word_mult[depth],
                                *bound,
                            )
                        )
                left_part(0, 0, 0)


__all__ = ["DOWN", "Move", "MoveGenerator", "SearchResult", "TopK"]

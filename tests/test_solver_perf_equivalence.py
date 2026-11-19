"""The move generator against a frozen copy of its original algorithm, and against brute force.

`RefGenerator` is the search as it was before the performance pass, run over a plain trie. A
faster search must return the same legal placements, the same scores and the same ranking
(including tie order) as this reference on every position, so the checks here compare exact
sets, not approximations.
"""

import os
import random
from collections import Counter
from pathlib import Path

import pytest
from randgen import random_lexicon, random_profile, random_rack

from tilelens.paths import words_dir
from tilelens.rules.board import Board, Tile, parse_rack
from tilelens.rules.legality import ACROSS, placement_direction
from tilelens.rules.lexicon import Lexicon
from tilelens.rules.profile import BLANK, demo_profile
from tilelens.rules.scoring import evaluate, score_placement
from tilelens.solver.dawg import Dawg
from tilelens.solver.exhaustive import exhaustive_moves
from tilelens.solver.movegen import MoveGenerator

ENABLE = words_dir() / "enable1.txt"


class RefGenerator:
    def __init__(self, profile, lexicon):
        self.profile = profile
        self.words = frozenset(
            w
            for w in lexicon.words
            if len(w) >= profile.min_word_length and set(w) <= profile.alphabet
        )
        self.edges: list[dict[str, int]] = [{}]
        self.term = [False]
        for w in self.words:
            node = 0
            for ch in w:
                nxt = self.edges[node].get(ch)
                if nxt is None:
                    nxt = len(self.edges)
                    self.edges[node][ch] = nxt
                    self.edges.append({})
                    self.term.append(False)
                node = nxt
            self.term[node] = True
        self.alphabet = sorted(profile.alphabet)

    def all_moves(self, board, rack):
        found = {}

        def sink(score, key):
            prev = found.get(key)
            assert prev is None or prev == score, f"inconsistent scores for {key}"
            found[key] = score

        self._run(board, rack, sink)
        return found

    def describe(self, board, key, score):
        board = self.resolve(board)
        breakdown = score_placement(board, key, self.profile)
        assert breakdown.total == score
        direction = placement_direction(key) if len(key) > 1 else None
        words = breakdown.words
        if direction is not None:
            main = next(w for w in words if w.direction == direction)
        else:
            main = next((w for w in words if w.direction == ACROSS), words[0])
        return (key, score, main.text, main.row, main.col, main.direction)

    def resolve(self, board):
        starts = self.profile.start_squares
        if (
            board.has_unknown()
            and board.is_empty()
            and starts
            and all(board.get(r, c) is None for r, c in starts)
        ):
            return Board(board.width, board.height, None, None, board.bounds)
        return board

    def _run(self, board, rack, sink):
        rack_counts = {k: v for k, v in rack.items() if v > 0}
        total_tiles = sum(rack_counts.values())
        if total_tiles == 0:
            return
        board = self.resolve(board)
        empty_board = board.is_empty() and not board.has_unknown()
        for transposed in (False, True):
            self._scan(board, rack_counts, total_tiles, transposed, empty_board, sink)

    def _scan(self, board, rack, total_tiles, transposed, empty_board, sink):
        p = self.profile
        words = self.words
        edges = self.edges
        term = self.term
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
        stamps = board.stamps
        if transposed:
            stamps = {(c, r): v for (r, c), v in stamps.items()}
        V = [
            [
                0 if bl[r][c] else stamps.get((r, c), values.get(g[r][c], 0)) if g[r][c] else 0
                for c in range(W)
            ]
            for r in range(H)
        ]
        LM = [[lm_at(r, c) for c in range(W)] for r in range(H)]
        WM = [[wm_at(r, c) for c in range(W)] for r in range(H)]

        anchor = [[False] * W for _ in range(H)]
        cross_ok = [[None] * W for _ in range(H)]
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
                            s += V[rr][c]
                            rr -= 1
                        prefix = "".join(reversed(above))
                        rr = r + 1
                        below = []
                        while rr < H and g[rr][c] is not None:
                            below.append(g[rr][c])
                            s += V[rr][c]
                            rr += 1
                        suffix = "".join(below)
                        cross_ok[r][c] = {L for L in alphabet if prefix + L + suffix in words}
                        cross_sum[r][c] = s

        for r in range(H):
            row = g[r]
            if not any(anchor[r]):
                continue
            placed = {}

            def record(start, end, r=r, placed=placed):
                main = 0
                wms = []
                cross = 0
                for col in range(start, end):
                    pl = placed.get(col)
                    if pl is None:
                        main += V[r][col]
                    else:
                        v = 0 if pl[1] else values[pl[0]]
                        lm = LM[r][col]
                        wm = WM[r][col]
                        main += v * lm
                        if wm != 1:
                            wms.append(wm)
                        if cross_ok[r][col] is not None:
                            cross += (cross_sum[r][col] + v * lm) * wm
                n = len(placed)
                bonus = p.full_rack_bonus if n == p.full_rack_tiles else 0
                score = main * p.combine_word_multipliers(wms) + cross + bonus
                if transposed:
                    key = tuple(sorted(Tile(c, r, L, b) for c, (L, b) in placed.items()))
                else:
                    key = tuple(sorted(Tile(r, c, L, b) for c, (L, b) in placed.items()))
                sink(score, key)

            def extend(node, col, anchor_col, start, r=r, row=row, placed=placed):
                if col < W:
                    ch = row[col]
                    if ch is None:
                        if col > anchor_col and term[node]:
                            record(start, col)
                        allowed = cross_ok[r][col]
                        for L, child in edges[node].items():
                            if allowed is not None and L not in allowed:
                                continue
                            if rack.get(L, 0) > 0:
                                rack[L] -= 1
                                placed[col] = (L, False)
                                extend(child, col + 1, anchor_col, start)
                                del placed[col]
                                rack[L] += 1
                            if rack.get(BLANK, 0) > 0:
                                rack[BLANK] -= 1
                                placed[col] = (L, True)
                                extend(child, col + 1, anchor_col, start)
                                del placed[col]
                                rack[BLANK] += 1
                    else:
                        child = edges[node].get(ch)
                        if child is not None:
                            extend(child, col + 1, anchor_col, start)
                elif col > anchor_col and term[node]:
                    record(start, col)

            def left_part(node, anchor_col, limit, partial, placed=placed, extend=extend):
                start = anchor_col - len(partial)
                for i, item in enumerate(partial):
                    placed[start + i] = item
                extend(node, anchor_col, anchor_col, start)
                for i in range(len(partial)):
                    del placed[start + i]
                if limit <= 0:
                    return
                for L, child in edges[node].items():
                    if rack.get(L, 0) > 0:
                        rack[L] -= 1
                        partial.append((L, False))
                        left_part(child, anchor_col, limit - 1, partial)
                        partial.pop()
                        rack[L] += 1
                    if rack.get(BLANK, 0) > 0:
                        rack[BLANK] -= 1
                        partial.append((L, True))
                        left_part(child, anchor_col, limit - 1, partial)
                        partial.pop()
                        rack[BLANK] += 1

            for a in range(W):
                if not anchor[r][a]:
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
                    while (
                        c >= 0 and row[c] is None and not anchor[r][c] and limit < total_tiles - 1
                    ):
                        limit += 1
                        c -= 1
                    left_part(0, a, limit, [])


def describe_moves(moves):
    return [(m.tiles, m.score, m.word, m.row, m.col, m.direction) for m in moves]


def check_position(gen, ref, board, rack, context=""):
    expected = ref.all_moves(board, rack)
    assert gen.all_moves(board, rack) == expected, context
    ranking = sorted(expected.items(), key=lambda kv: (-kv[1], kv[0]))
    for k in (3, 10):
        result = gen.solve(board, rack, top_k=k)
        assert result.complete, context
        want = [ref.describe(board, key, score) for key, score in ranking[:k]]
        assert describe_moves(result.moves) == want, f"{context} top {k}"
    return len(expected)


def sprinkle_stamps(rng, board, profile):
    stamps = {}
    for t in board.tiles():
        if not t.blank and rng.random() < 0.6:
            stamps[(t.row, t.col)] = profile.letter_values[t.letter] + rng.randint(0, 12)
    return board.with_stamps(stamps)


def tile_bounds(board, rng):
    tiles = board.tiles()
    if not tiles:
        r0 = rng.randrange(board.height)
        c0 = rng.randrange(board.width)
        return (r0, c0, rng.randint(r0, board.height - 1), rng.randint(c0, board.width - 1))
    rows = [t.row for t in tiles]
    cols = [t.col for t in tiles]
    return (
        max(0, min(rows) - rng.randint(0, 2)),
        max(0, min(cols) - rng.randint(0, 2)),
        min(board.height - 1, max(rows) + rng.randint(0, 2)),
        min(board.width - 1, max(cols) + rng.randint(0, 2)),
    )


def sprinkle_walls(rng, board):
    rows = board.to_rows()
    grid = [list(r) for r in rows]
    for _ in range(rng.randint(1, 4)):
        r, c = rng.randrange(board.height), rng.randrange(board.width)
        if grid[r][c] == ".":
            grid[r][c] = "#"
    return Board.from_rows(["".join(r) for r in grid]).with_stamps(board.stamps)


VARIANTS = ("plain", "stamps", "bounds", "walls", "bounds+stamps", "walls+stamps")


def play(rng, ref, board, rack_for, turns):
    for _ in range(turns):
        moves = ref.all_moves(board, rack_for())
        if moves:
            board = board.with_tiles(rng.choice(sorted(moves)))
    return board


def random_case(seed):
    rng = random.Random(seed)
    profile = random_profile(rng)
    letters = sorted(profile.alphabet)
    lexicon = random_lexicon(rng, letters)
    ref = RefGenerator(profile, lexicon)
    board = play(
        rng,
        ref,
        Board(profile.width, profile.height),
        lambda: random_rack(rng, letters, profile.rack_size),
        rng.randint(0, 9),
    )
    variant = VARIANTS[seed % len(VARIANTS)]
    if "stamps" in variant:
        board = sprinkle_stamps(rng, board, profile)
    if variant.startswith("walls"):
        board = sprinkle_walls(rng, board)
    if variant.startswith("bounds"):
        board = board.with_bounds(tile_bounds(board, rng))
    rack = random_rack(rng, letters, profile.rack_size)
    return profile, lexicon, ref, board, rack, variant


N_RANDOM = 1800


@pytest.mark.parametrize("chunk", range(6))
def test_random_positions_match_the_reference(chunk):
    size = N_RANDOM // 6
    total = 0
    for seed in range(chunk * size, (chunk + 1) * size):
        profile, lexicon, ref, board, rack, variant = random_case(seed)
        gen = MoveGenerator(profile, lexicon)
        total += check_position(gen, ref, board, rack, f"seed {seed} {variant}")
    assert total > 0


@pytest.mark.parametrize("chunk", range(5))
def test_random_positions_match_brute_force(chunk):
    checked = 0
    for seed in range(50_000 + chunk * 120, 50_000 + (chunk + 1) * 120):
        profile, lexicon, _ref, board, rack, variant = random_case(seed)
        if "bounds" in variant or (variant.startswith("walls") and board.is_empty()):
            continue
        gen = MoveGenerator(profile, lexicon)
        expected = {
            key: evaluate(board, rack, key, profile, lexicon).total
            for key in exhaustive_moves(board, rack, profile, lexicon)
        }
        got = gen.all_moves(board, rack)
        assert got == expected, f"seed {seed} {variant}"
        ranking = sorted(expected.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
        assert [(m.tiles, m.score) for m in gen.solve(board, rack).moves] == ranking
        checked += 1
    assert checked > 60


@pytest.mark.parametrize("index", range(40))
def test_bounded_boards_never_offer_a_move_that_brute_force_rejects(index):
    profile, lexicon, _ref, board, rack, _variant = random_case(
        60_000 + 6 * index + 2 + index % 2 * 2
    )
    gen = MoveGenerator(profile, lexicon)
    legal = exhaustive_moves(board, rack, profile, lexicon)
    for key, score in gen.all_moves(board, rack).items():
        assert key in legal and score == evaluate(board, rack, key, profile, lexicon).total


DEMO_WORDS = Path(__file__).parents[1] / "profiles" / "demo_words.txt"


def demo_games(count, seed):
    """Boards of early, mid, late and dense play on the demo profile, played by the reference."""
    profile = demo_profile("demo_garden")
    lexicon = Lexicon.load(DEMO_WORDS, profile.alphabet)
    ref = RefGenerator(profile, lexicon)
    rng = random.Random(seed)
    letters = sorted(profile.alphabet)
    pool = [L for L, n in profile.distribution.items() for _ in range(n)]
    out = []
    for _ in range(count):
        board = Board(profile.width, profile.height)
        bag = pool[:]
        rng.shuffle(bag)
        for turn in range(rng.randint(0, 45)):
            rack = Counter(bag[:7])
            if rng.random() < 0.3:
                rack[BLANK] += rng.randint(1, 2)
            moves = ref.all_moves(board, rack)
            if not moves:
                bag = bag[1:] + bag[:1]
                continue
            key = rng.choice(sorted(moves, key=lambda k: (-moves[k], k))[:6])
            board = board.with_tiles(key)
            bag = bag[sum(1 for t in key if not t.blank) :]
            if turn % 9 == 8:
                out.append(board)
        out.append(board)
    return profile, lexicon, ref, out, letters


def test_demo_games_match_the_reference():
    profile, lexicon, ref, boards, letters = demo_games(40, 20261026)
    gen = MoveGenerator(profile, lexicon)
    rng = random.Random(7)
    positions = 0
    for board in boards:
        for blanks in (0, 1, 2):
            rack = random_rack(rng, letters, 7)
            rack[BLANK] = blanks
            if sum(rack.values()) > 7:
                continue
            check_position(gen, ref, sprinkle_stamps(rng, board, profile), rack, "demo")
            positions += 1
    assert positions > 150


def test_blank_racks_prune_without_changing_any_top_k():
    profile, lexicon, ref, boards, letters = demo_games(30, 424242)
    gen = MoveGenerator(profile, lexicon)
    rng = random.Random(21)
    positions = 0
    for board in boards:
        rack = random_rack(rng, letters, 6)
        rack[BLANK] = rng.choice([1, 2])
        board = sprinkle_stamps(rng, board, profile)
        ranking = sorted(ref.all_moves(board, rack).items(), key=lambda kv: (-kv[1], kv[0]))
        for k in (1, 2, 3, 6):
            result = gen.solve(board, rack, top_k=k)
            want = [ref.describe(board, key, score) for key, score in ranking[:k]]
            assert result.complete and describe_moves(result.moves) == want, f"k={k}"
        positions += 1
    assert positions > 60


def test_demo_games_with_exact_tiles_match_brute_force():
    profile, lexicon, _ref, boards, letters = demo_games(3, 99)
    gen = MoveGenerator(profile, lexicon)
    rng = random.Random(3)
    for board in boards[:6]:
        rack = Counter(rng.choice(letters) for _ in range(3))
        expected = {
            key: evaluate(board, rack, key, profile, lexicon).total
            for key in exhaustive_moves(board, rack, profile, lexicon)
        }
        assert gen.all_moves(board, rack) == expected


@pytest.mark.skipif(not ENABLE.is_file(), reason="needs the installed ENABLE word list")
def test_real_word_list_on_the_grown_board_matches_the_reference():
    from collections import namedtuple

    import cv2

    from tilelens.rules.profile import load_profile
    from tilelens.vision.autoread import ViewReader
    from tilelens.vision.lexfix import drop_isolated
    from tilelens.vision.recognizer import Calibration

    fixtures = Path(__file__).parent / "fixtures" / "discord"
    profile = load_profile(fixtures / "growing-profile.toml")
    lexicon = Lexicon.load(ENABLE, profile.alphabet)
    reading = ViewReader(Calibration.load(fixtures / "calibration.json"), profile).read(
        cv2.imread(str(fixtures / "board-grown.png"))
    )
    drop_isolated(reading)
    real = reading.placed()
    ref = RefGenerator(profile, lexicon)
    gen = MoveGenerator(profile, lexicon)
    rng = random.Random(11)
    board = real
    Case = namedtuple("Case", "board rack")
    cases = []
    for _ in range(int(os.environ.get("TILELENS_GROWN_TURNS", "6"))):
        rack = Counter(rng.sample("AEIORSTNLDUCMPHBG", 6))
        cases.append(Case(board, rack))
        cases.append(Case(board, rack + Counter({BLANK: 1})))
        top = gen.solve(board, rack, top_k=4).moves
        if top:
            board = board.with_tiles(rng.choice(top).tiles)
        board = sprinkle_stamps(rng, board, profile)
    for case in cases:
        check_position(gen, ref, case.board, case.rack, "grown board")
    assert gen.solve(real, parse_rack("AEIRST?")).complete


def test_cancelled_searches_are_never_complete_and_never_wrong():
    profile = demo_profile("demo_garden")
    lexicon = Lexicon.load(DEMO_WORDS, profile.alphabet)
    gen = MoveGenerator(profile, lexicon)
    ref = RefGenerator(profile, lexicon)
    boards = demo_games(2, 5)[3]
    board = boards[-1]
    rack = parse_rack("AERSTN?")
    full = gen.all_moves(board, rack)
    assert full == ref.all_moves(board, rack)
    complete_ranking = gen.solve(board, rack)
    assert complete_ranking.complete
    stopped_early = 0
    for polls in (1, 2, 3, 5, 9, 20, 60):
        calls = [0]

        def stop(limit=polls, calls=calls):
            calls[0] += 1
            return calls[0] >= limit

        result = gen.solve(board, rack, should_stop=stop)
        if result.complete:
            assert describe_moves(result.moves) == describe_moves(complete_ranking.moves)
        else:
            stopped_early += 1
            assert result.label == "best found so far"
        for move in result.moves:
            assert full[move.tiles] == move.score
    assert stopped_early > 0


def test_a_search_that_is_stopped_before_it_starts_finds_nothing_and_says_so():
    profile = demo_profile("demo_garden")
    lexicon = Lexicon.load(DEMO_WORDS, profile.alphabet)
    gen = MoveGenerator(profile, lexicon)
    board = demo_games(1, 1)[3][-1]
    result = gen.solve(board, parse_rack("AERSTN?"), should_stop=lambda: True)
    assert not result.complete
    full = gen.all_moves(board, parse_rack("AERSTN?"))
    assert all(full[m.tiles] == m.score for m in result.moves)


def test_dawg_round_trip_keeps_every_word(tmp_path):
    rng = random.Random(4)
    words = {"".join(rng.choice("ABCDE") for _ in range(rng.randint(2, 8))) for _ in range(3000)}
    graph = Dawg.build(words, version="t")
    assert graph.words() == sorted(words)
    graph.save(tmp_path / "g.npz")
    back = Dawg.load(tmp_path / "g.npz")
    assert back.words() == sorted(words) and back.version == "t"
    assert len(back) == len(graph) and back.terminal == graph.terminal
    for w in list(words)[:200]:
        assert w in back
    assert "ABCDEABCDE" not in back


def test_letters_outside_the_profile_are_ignored():
    profile = demo_profile("demo_garden")
    lexicon = Lexicon.from_words(["AT"])
    gen = MoveGenerator(profile, lexicon)
    board = Board(profile.width, profile.height)
    playable = Counter("AT")
    assert gen.all_moves(board, playable | Counter("É")) == gen.all_moves(board, playable)

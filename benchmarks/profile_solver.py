"""Where the move generator spends its time, and what a search costs in realistic conditions.

Usage: uv run python benchmarks/profile_solver.py [scenario ...] [--repeat N] [--profile]
           [--lines module:function ...] [--json FILE] [--boards FILE]

Scenarios (the real board is tests/fixtures/discord/board-grown.png on the 27x19 growing profile
with the ENABLE word list, played forward by the generator itself so the boards get denser):
  demo-opening        demo garden, 204k-word system list, empty board, 7 tiles
  demo-mid            mid game (30-55 tiles), 7 tiles
  demo-dense-2blank   dense board (70+ tiles), 5 tiles and 2 blanks
  demo-dense-1blank   dense board, 6 tiles and 1 blank
  demo-late           dense board, 2-4 tiles
  real-<stage>-b<k>   the real board with every square visible, early/mid/late/full stages (18, 50,
                      100, 160 tiles), k = 0, 1, 2 blanks in the rack, stamped tiles; "asread" is
                      the board as the reader returns that frame (its top rows hidden behind the
                      timer panel, so unknown squares act as walls)
  controller          what the solver worker does for one position: Board.from_rows, stamps,
                      bounds, rack parsing, the search and the position id
  build-graph         building the search graph from the word list, cold
  load-graph          loading the cached graph
  memory              peak resident memory while building the graph and searching in a loop

--profile prints cProfile's top entries per scenario; --lines prints the time spent on each
source line of the named functions (sys.monitoring; nested functions are included), for example
--lines tilelens.solver.movegen:MoveGenerator._scan. --boards caches the played-forward boards
so that two runs (old code and new code) search the same positions.
"""

from __future__ import annotations

import argparse
import cProfile
import importlib
import json
import pstats
import random
import statistics
import sys
import tempfile
import time
import types
from collections import Counter, defaultdict
from pathlib import Path

from tilelens.paths import words_dir

ROOT = Path(__file__).resolve().parent.parent
DISCORD = ROOT / "tests" / "fixtures" / "discord"
ENABLE = words_dir() / "enable1.txt"
SYSTEM_WORDS = Path("/usr/share/dict/words")
STAGES = {"early": 0, "mid": 1, "late": 2, "full": 3, "asread": 4}
STAGE_TILES = (0, 50, 100, 160)
SCENARIOS = [
    "demo-opening",
    "demo-mid",
    "demo-dense-2blank",
    "demo-dense-1blank",
    "demo-late",
    *(f"real-{s}-b{k}" for s in STAGES for k in (0, 1, 2)),
    "controller",
    "build-graph",
    "load-graph",
    "memory",
]


def rss_mb() -> float:
    try:
        import resource
    except ImportError:
        raise SystemExit("the memory scenario requires POSIX resource usage counters") from None
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (2**20 if sys.platform == "darwin" else 2**10)


def stats(samples_ms: list[float]) -> dict:
    s = sorted(samples_ms)
    return {
        "n": len(s),
        "median_ms": round(statistics.median(s), 3),
        "p95_ms": round(s[min(len(s) - 1, round(0.95 * (len(s) - 1)))], 3),
        "worst_ms": round(s[-1], 3),
        "best_ms": round(s[0], 3),
    }


def draw(rng, pool, n, blanks):
    from tilelens.rules.profile import BLANK

    letters = [x for x in pool if x != BLANK]
    rack = Counter(rng.sample(letters, max(0, min(n - blanks, len(letters)))))
    if blanks:
        rack[BLANK] = blanks
    return rack


def unseen(profile, board):
    from tilelens.rules.profile import BLANK
    from tilelens.solver.selfplay import full_bag

    bag = Counter(full_bag(profile))
    for t in board.tiles():
        bag[BLANK if t.blank else t.letter] -= 1
    return list((+bag).elements())


def load_words(path: Path, alphabet):
    from tilelens.rules.lexicon import Lexicon

    lines = [w for w in path.read_text(errors="ignore").splitlines() if w.islower()]
    return Lexicon.from_words((w for w in lines if 2 <= len(w) <= 15), alphabet)


class Demo:
    """The 13x13 demo board with a 204k-word list: the shape the benchmark has always used."""

    def __init__(self, word_list):
        from tilelens.rules.board import Board
        from tilelens.rules.profile import demo_profile
        from tilelens.solver.movegen import MoveGenerator
        from tilelens.solver.selfplay import play_game

        self.profile = demo_profile("demo_garden")
        lexicon = load_words(word_list, self.profile.alphabet)
        self.gen = MoveGenerator(self.profile, lexicon, use_cache=False)
        rng = random.Random(7)
        self.boards = {"mid": [], "dense": []}
        for _ in range(6):
            for t in play_game(self.profile, self.gen, rng, max_turns=60):
                filled = len(t.board.tiles())
                if 30 <= filled <= 55:
                    self.boards["mid"].append(t.board)
                elif filled >= 70:
                    self.boards["dense"].append(t.board)
        self.empty = Board(self.profile.width, self.profile.height)

    def cases(self, name: str, rng):
        specs = {
            "demo-opening": ([self.empty], 0, 7),
            "demo-mid": (self.boards["mid"], 0, 7),
            "demo-dense-2blank": (self.boards["dense"], 2, 7),
            "demo-dense-1blank": (self.boards["dense"], 1, 7),
            "demo-late": (self.boards["dense"], 0, 0),
        }
        boards, blanks, n = specs[name]
        boards = rng.sample(boards, min(len(boards), 40))
        out = []
        for b in boards:
            for _ in range(40 if b is self.empty else 2):
                size = n or rng.randint(2, 4)
                pool = unseen(self.profile, b)
                out.append((b, draw(rng, pool, min(size, len(pool)), blanks)))
        return out


class Real:
    """The real Discord board on the growing profile, with ENABLE, played forward."""

    def __init__(self, boards_file: Path | None = None):
        from tilelens.rules.board import Board
        from tilelens.rules.profile import load_profile
        from tilelens.solver.movegen import MoveGenerator

        self.profile = load_profile(DISCORD / "growing-profile.toml")
        lexicon = load_words_enable(self.profile)
        self.gen = MoveGenerator(self.profile, lexicon, use_cache=True)
        if boards_file is not None and boards_file.is_file():
            data = json.loads(boards_file.read_text())
            self.stages = [
                [
                    Board.from_rows(b["rows"])
                    .with_stamps({(r, c): v for r, c, v in b["stamps"]})
                    .with_bounds(tuple(b["bounds"]))
                    for b in stage
                ]
                for stage in data
            ]
            return
        self.stages = self._play_forward()
        if boards_file is not None:
            boards_file.write_text(
                json.dumps(
                    [
                        [
                            {"rows": b.to_rows(), "stamps": b.stamp_list(), "bounds": b.bounds}
                            for b in stage
                        ]
                        for stage in self.stages
                    ]
                )
            )

    def _real_board(self):
        import cv2

        from tilelens.vision.autoread import ViewReader
        from tilelens.vision.lexfix import drop_isolated
        from tilelens.vision.recognizer import Calibration

        reader = ViewReader(Calibration.load(DISCORD / "calibration.json"), self.profile)
        reading = reader.read(cv2.imread(str(DISCORD / "board-grown.png")))
        drop_isolated(reading)
        return reading.placed()

    def _play_forward(self):
        p = self.profile
        rng = random.Random(20261026)
        as_read = self._real_board()
        board = clear_unknown(as_read, p)
        stages: list[list] = [[], [], [], [], [as_read]]
        stages[0].append(board)
        for _ in range(6):
            b = board
            taken = Counter()
            for turn in range(220):
                count = len(b.tiles())
                stage = max(i for i in range(4) if count >= STAGE_TILES[i])
                if stage and turn % 3 == 0 and taken[stage] < 4:
                    stages[stage].append(b)
                    taken[stage] += 1
                if count >= STAGE_TILES[3] + 20:
                    break
                pool = unseen(p, b)
                rack = draw(rng, pool, 7, rng.choice([0, 0, 1, 2]))
                moves = self.gen.solve(b, rack, top_k=6).moves
                if not moves:
                    continue
                move = rng.choice(moves)
                b = b.with_tiles(move.tiles)
                stamps = dict(b.stamps)
                for t in move.tiles:
                    if not t.blank and rng.random() < 0.5:
                        stamps[(t.row, t.col)] = p.letter_values[t.letter] * rng.choice([1, 2, 3])
                b = b.with_stamps(stamps)
                b = b.with_bounds(grow(b, p))
        return stages

    def cases(self, name: str, rng):
        _, stage, blanks = name.split("-")
        boards = self.stages[STAGES[stage]]
        k = int(blanks[1:])
        out = []
        for b in boards:
            for _ in range(2):
                out.append((b, draw(rng, unseen(self.profile, b), 7, k)))
        return out


def grow(board, profile):
    """The real board's edges after a play: three squares more on any side a tile has reached."""
    tiles = board.tiles()
    r0, c0, r1, c1 = board.bounds
    rows = [t.row for t in tiles]
    cols = [t.col for t in tiles]
    if min(rows) <= r0:
        r0 = max(0, r0 - 3)
    if min(cols) <= c0:
        c0 = max(0, c0 - 3)
    if max(rows) >= r1:
        r1 = min(profile.height - 1, r1 + 3)
    if max(cols) >= c1:
        c1 = min(profile.width - 1, c1 + 3)
    return (r0, c0, r1, c1)


def clear_unknown(board, profile):
    from tilelens.rules.board import WALL, Board

    cells = [[None if c is not None and c[0] == WALL else c for c in row] for row in board.cells]
    r0, c0, r1, c1 = board.bounds
    return Board(board.width, board.height, cells, board.stamps, (profile.expand, c0, r1, c1))


def load_words_enable(profile):
    from tilelens.rules.lexicon import Lexicon

    return Lexicon.load(ENABLE, profile.alphabet)


_REAL: dict = {}
_DEMO: dict = {}


def real(boards_file):
    if "r" not in _REAL:
        _REAL["r"] = Real(boards_file)
    return _REAL["r"]


def demo():
    if "d" not in _DEMO:
        _DEMO["d"] = Demo(SYSTEM_WORDS)
    return _DEMO["d"]


def controller_step(env):
    from tilelens.rules.board import parse_rack
    from tilelens.state.position import position_id
    from tilelens.state.worker import _solve

    def step(case):
        board, rack = case
        rows = board.to_rows()
        stamps = board.stamp_list()
        rack_text = "".join(rack.elements())
        res = _solve(env.gen, rows, rack_text, stamps, board.bounds, None)
        position_id(board, parse_rack(rack_text), env.profile, env.gen.lexicon_version)
        return res

    return step


def build(name: str, boards_file):
    """Returns (cases, step): step(case) runs one search."""
    rng = random.Random(11)
    if name.startswith("demo-"):
        env = demo()
        return env.cases(name, rng), lambda case: env.gen.solve(*case)
    if name.startswith("real-"):
        env = real(boards_file)
        return env.cases(name, rng), lambda case: env.gen.solve(*case)
    if name == "controller":
        env = real(boards_file)
        cases = env.cases("real-mid-b1", rng) + env.cases("real-late-b0", rng)
        return cases, controller_step(env)
    raise SystemExit(f"unknown scenario {name}")


def measure_searches(name: str, repeat: int, boards_file) -> dict:
    cases, step = build(name, boards_file)
    for case in cases[:3]:
        step(case)
    times = []
    incomplete = 0
    for i in range(repeat):
        case = cases[i % len(cases)]
        t0 = time.perf_counter()
        res = step(case)
        times.append((time.perf_counter() - t0) * 1000)
        incomplete += not res.complete
    return {
        "scenario": name,
        "cases": len(cases),
        "incomplete": incomplete,
        **stats(times),
        "samples_ms": [round(t, 3) for t in times],
    }


def measure_graph(name: str, repeat: int) -> dict:
    from tilelens.rules.profile import load_profile
    from tilelens.solver.dawg import Dawg
    from tilelens.solver.movegen import graph_for, playable_words

    p = load_profile(DISCORD / "growing-profile.toml")
    words = playable_words(p, load_words_enable(p))
    times = []
    if name == "build-graph":
        for _ in range(repeat):
            t0 = time.perf_counter()
            graph_for(words, use_cache=False)
            times.append((time.perf_counter() - t0) * 1000)
    else:
        graph = graph_for(words, use_cache=False)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "g.npz"
            graph.save(path)
            Dawg.load(path)
            for _ in range(repeat):
                t0 = time.perf_counter()
                Dawg.load(path)
                times.append((time.perf_counter() - t0) * 1000)
    return {"scenario": name, "words": len(words), **stats(times)}


def measure_memory(boards_file, loops: int = 400) -> dict:
    import gc

    from tilelens.rules.profile import load_profile
    from tilelens.solver.movegen import MoveGenerator, playable_words

    p = load_profile(DISCORD / "growing-profile.toml")
    lex = load_words_enable(p)
    base = rss_mb()
    gen = MoveGenerator(p, lex, use_cache=False)
    after_build = rss_mb()
    env = real(boards_file)
    cases = env.cases("real-mid-b1", random.Random(3)) + env.cases("real-full-b2", random.Random(3))
    gc.collect()
    series = []
    for i in range(loops):
        board, rack = cases[i % len(cases)]
        gen.solve(board, rack)
        if i % 50 == 49:
            gc.collect()
            series.append(round(rss_mb(), 1))
    return {
        "scenario": "memory",
        "words": len(playable_words(p, lex)),
        "rss_before_mb": round(base, 1),
        "peak_after_build_mb": round(after_build, 1),
        "peak_after_searches_mb": round(rss_mb(), 1),
        "peak_by_50_searches_mb": series,
    }


def profile_scenario(name: str, repeat: int, top: int, boards_file) -> None:
    cases, step = build(name, boards_file)
    step(cases[0])
    prof = cProfile.Profile()
    for i in range(repeat):
        prof.enable()
        step(cases[i % len(cases)])
        prof.disable()
    print(f"\n== cProfile: {name} ({repeat} searches)")
    pstats.Stats(prof).sort_stats("tottime").print_stats(top)


def code_objects(code: types.CodeType):
    yield code
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            yield from code_objects(const)


class LineTimer:
    """Time per source line of chosen functions and the functions nested in them."""

    def __init__(self, targets: list[str]) -> None:
        self.codes = {}
        for target in targets:
            module, _, qual = target.partition(":")
            obj = importlib.import_module(module)
            for part in qual.split("."):
                obj = getattr(obj, part)
            for code in code_objects(obj.__code__):
                self.codes[code] = f"{target}:{code.co_qualname}"
        self.spent: dict[tuple, float] = defaultdict(float)
        self.last: dict = {}
        self.mon = sys.monitoring

    def start(self) -> None:
        mon = self.mon
        tool = mon.PROFILER_ID
        mon.use_tool_id(tool, "profile_solver")
        ev = mon.events
        mon.register_callback(tool, ev.PY_START, self._start)
        mon.register_callback(tool, ev.LINE, self._line)
        mon.register_callback(tool, ev.PY_RETURN, self._end)
        mon.set_events(tool, ev.PY_START | ev.LINE | ev.PY_RETURN)

    def stop(self) -> None:
        mon = self.mon
        mon.set_events(mon.PROFILER_ID, 0)
        mon.free_tool_id(mon.PROFILER_ID)

    def _start(self, code, offset):
        if code not in self.codes:
            return self.mon.DISABLE
        self.last.setdefault(code, []).append(None)

    def _line(self, code, line):
        if code not in self.codes:
            return self.mon.DISABLE
        stack = self.last[code]
        now = time.perf_counter()
        prev = stack[-1]
        if prev is not None:
            self.spent[(code, prev[0])] += now - prev[1]
        stack[-1] = (line, time.perf_counter())

    def _end(self, code, offset, retval):
        if code not in self.codes:
            return self.mon.DISABLE
        stack = self.last.get(code)
        if not stack:
            return None
        prev = stack.pop()
        if prev is not None:
            self.spent[(code, prev[0])] += time.perf_counter() - prev[1]
        return None

    def report(self, runs: int, top: int = 18) -> None:
        for code, target in self.codes.items():
            rows = [(t, line) for (c, line), t in self.spent.items() if c is code]
            if not rows:
                continue
            total = sum(t for t, _ in rows)
            lines = Path(code.co_filename).read_text().splitlines()
            print(f"\n== lines: {target}  {total / runs * 1000:.3f} ms per search")
            for t, line in sorted(rows, reverse=True)[:top]:
                print(
                    f"{t / runs * 1000:9.3f} ms {100 * t / total:5.1f}%  {line:5d}  "
                    f"{lines[line - 1].strip()[:90]}"
                )


def line_profile(name: str, repeat: int, targets: list[str], boards_file) -> None:
    cases, step = build(name, boards_file)
    step(cases[0])
    timer = LineTimer(targets)
    timer.start()
    try:
        for i in range(repeat):
            step(cases[i % len(cases)])
    finally:
        timer.stop()
    print(f"\n== line timing under scenario {name} ({repeat} searches)")
    timer.report(repeat)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("scenarios", nargs="*", choices=SCENARIOS)
    ap.add_argument("--repeat", type=int, default=60)
    ap.add_argument("--profile", action="store_true")
    ap.add_argument("--top", type=int, default=16)
    ap.add_argument("--lines", nargs="*", default=[])
    ap.add_argument("--json", type=Path)
    ap.add_argument("--boards", type=Path)
    ap.add_argument("--demo-words", type=Path, default=SYSTEM_WORDS)
    args = ap.parse_args()
    if args.repeat < 1:
        ap.error("--repeat must be positive")
    names = args.scenarios or SCENARIOS
    if any(name.startswith("demo-") for name in names):
        if not args.demo_words.is_file():
            ap.error("demo scenarios need a dictionary; pass --demo-words PATH")
        _DEMO["d"] = Demo(args.demo_words)
    results = []
    for name in names:
        if name in ("build-graph", "load-graph"):
            res = measure_graph(name, max(3, args.repeat // 6))
        elif name == "memory":
            res = measure_memory(args.boards)
        else:
            res = measure_searches(name, args.repeat, args.boards)
        results.append(res)
        if "median_ms" in res:
            print(
                f"{name:20s} n={res['n']:4d} median {res['median_ms']:9.3f} ms  "
                f"p95 {res['p95_ms']:9.3f}  worst {res['worst_ms']:9.3f}",
                file=sys.stderr,
            )
        else:
            print(json.dumps(res), file=sys.stderr)
        if args.profile and name not in ("build-graph", "load-graph", "memory"):
            profile_scenario(name, max(3, args.repeat // 3), args.top, args.boards)
        if args.lines and name not in ("build-graph", "load-graph", "memory"):
            line_profile(name, max(3, args.repeat // 3), args.lines, args.boards)
    if args.json:
        args.json.write_text(json.dumps(results, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

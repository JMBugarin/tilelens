"""Solver benchmark.

Usage: uv run python benchmarks/bench_solver.py [--words PATH] [--games N] [--out FILE]

Without --words it uses /usr/share/dict/words when present (read at run time, never shipped),
falling back to the small demo list.
"""

from __future__ import annotations

import argparse
import json
import platform
import random
import resource
import statistics
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

from tilelens.rules.board import Board
from tilelens.rules.lexicon import Lexicon
from tilelens.rules.profile import BLANK, demo_profile
from tilelens.solver.dawg import Dawg
from tilelens.solver.movegen import MoveGenerator
from tilelens.solver.selfplay import full_bag, play_game

SYSTEM_WORDS = Path("/usr/share/dict/words")


def load_words(path: Path | None, alphabet) -> tuple[Lexicon, str]:
    if path is None and SYSTEM_WORDS.is_file():
        path = SYSTEM_WORDS
    if path is None:
        p = demo_profile()
        return Lexicon.load(p.word_list, alphabet), "demo list"
    lines = [w for w in path.read_text(errors="ignore").splitlines() if w.islower()]
    lex = Lexicon.from_words((w for w in lines if 2 <= len(w) <= 15), alphabet)
    return lex, str(path)


def stats(samples: list[float]) -> dict:
    s = sorted(samples)
    p95 = s[min(len(s) - 1, round(0.95 * (len(s) - 1)))]
    return {
        "n": len(s),
        "median_ms": round(statistics.median(s) * 1000, 2),
        "p95_ms": round(p95 * 1000, 2),
        "worst_ms": round(s[-1] * 1000, 2),
    }


def unseen(profile, board: Board) -> list[str]:
    bag = Counter(full_bag(profile))
    for t in board.tiles():
        bag[BLANK if t.blank else t.letter] -= 1
    return list((+bag).elements())


def draw(rng, pool, n, blanks):
    letters = [x for x in pool if x != BLANK]
    rack = Counter(rng.sample(letters, max(0, min(n - blanks, len(letters)))))
    if blanks:
        rack[BLANK] = blanks
    return rack


def hardware() -> dict:
    def sysctl(key):
        try:
            return subprocess.run(
                ["sysctl", "-n", key], capture_output=True, text=True, check=True
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return ""

    return {
        "machine": platform.machine(),
        "cpu": sysctl("machdep.cpu.brand_string") or platform.processor(),
        "memory_gb": round(int(sysctl("hw.memsize") or 0) / 2**30, 1),
        "os": platform.platform(),
        "python": sys.version.split()[0],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--words", type=Path)
    ap.add_argument("--games", type=int, default=6)
    ap.add_argument("--racks", type=int, default=8)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    profile = demo_profile("demo_garden")
    lexicon, source = load_words(args.words, profile.alphabet)
    t0 = time.perf_counter()
    gen = MoveGenerator(profile, lexicon)
    build_s = time.perf_counter() - t0
    tmp = Path(__file__).with_name("results") / "dawg.local.npz"
    tmp.parent.mkdir(exist_ok=True)
    gen.dawg.save(tmp)
    t0 = time.perf_counter()
    Dawg.load(tmp)
    load_s = time.perf_counter() - t0
    tmp.unlink()

    rng = random.Random(args.seed)
    boards: dict[str, list[Board]] = {"mid": [], "dense": []}
    for _ in range(args.games):
        turns = play_game(profile, gen, rng, max_turns=60)
        for t in turns:
            filled = len(t.board.tiles())
            if 30 <= filled <= 55:
                boards["mid"].append(t.board)
            elif filled >= 70:
                boards["dense"].append(t.board)
    empty = Board(profile.width, profile.height)

    scenarios = {
        "opening, 7 tiles": [(empty, 0, 7)],
        "mid-game, 7 tiles": [(b, 0, 7) for b in boards["mid"]],
        "dense, 5 tiles + 2 blanks": [(b, 2, 7) for b in boards["dense"]],
        "dense, 1 blank": [(b, 1, 7) for b in boards["dense"]],
        "late game, 2-4 tiles": [(b, 0, 0) for b in boards["dense"]],
    }
    results = {}
    for name, cases in scenarios.items():
        cases = rng.sample(cases, min(len(cases), 40))
        samples = []
        incomplete = 0
        for b, blanks, n in cases:
            for _ in range(args.racks * 5 if b is empty else max(1, args.racks // 4)):
                pool = unseen(profile, b)
                size = n or rng.randint(2, 4)
                rack = draw(rng, pool, min(size, len(pool)), blanks)
                t = time.perf_counter()
                res = gen.solve(b, rack)
                samples.append(time.perf_counter() - t)
                incomplete += not res.complete
        results[name] = stats(samples) | {"boards": len(cases), "incomplete": incomplete}

    report = {
        "hardware": hardware(),
        "profile": profile.name,
        "word_list": source,
        "words": len(gen.words),
        "dawg_nodes": len(gen.dawg),
        "dawg_build_s": round(build_s, 2),
        "dawg_cache_load_s": round(load_s, 2),
        "peak_rss_mb": round(
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            / (2**20 if sys.platform == "darwin" else 2**10),
            1,
        ),
        "scenarios": results,
    }
    text = json.dumps(report, indent=2)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

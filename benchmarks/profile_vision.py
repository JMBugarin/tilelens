"""Where the board reader spends its time.

Usage: uv run python benchmarks/profile_vision.py [scenario ...] [--repeat N] [--profile]
           [--lines module:function ...] [--json FILE]

Scenarios (the real Discord frame is tests/fixtures/discord/board-grown.png):
  cold-locate    locate_board on the real frame
  cold-read      a fresh ViewReader reading the real frame
  warm-read      the same frame read again (every square is a cache hit)
  cells-cold     every square read from scratch on an aligned view (what a re-lock costs besides
                 locating)
  changed-read   frames alternating between two versions that differ in a few squares
  rack-read      the rack search and rack tile reading on a synthetic view that shows the rack
  rack-search    the rack search on the real frame, which shows no rack
  synth-recognizer  Recognizer.read on a synthetic scene (the path calibration and tests use)

--profile prints cProfile's top entries per scenario; --lines prints the time spent on each
source line of the named functions (sys.monitoring, so callee time counts toward the calling
line), for example --lines tilelens.vision.autoread:ViewReader.read.
"""

from __future__ import annotations

import argparse
import copy
import cProfile
import importlib
import json
import pstats
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DISCORD = ROOT / "tests" / "fixtures" / "discord"
SCENARIOS = [
    "cold-locate",
    "cold-read",
    "warm-read",
    "cells-cold",
    "changed-read",
    "rack-read",
    "rack-search",
    "synth-recognizer",
]


def real_setup():
    from tilelens.rules.profile import load_profile
    from tilelens.vision.recognizer import Calibration

    profile = load_profile(DISCORD / "growing-profile.toml")
    img = cv2.imread(str(DISCORD / "board-grown.png"))
    return profile, img, Calibration.load(DISCORD / "calibration.json")


def with_tiles(img: np.ndarray, count: int = 3) -> np.ndarray:
    """The real frame with a few tiles drawn on empty squares."""
    out = img.copy()
    pitch, x0, y0 = 51.145408730750646, -361.8537667700608, -534.4695212363442
    for i in range(count):
        x, y = round(x0 + 22 * pitch), round(y0 + (26 + i) * pitch)
        s = round(pitch)
        cv2.rectangle(out, (x + 2, y + 2), (x + s - 3, y + s - 3), (102, 142, 239), -1)
        cv2.putText(out, "KRT"[i % 3], (x + 12, y + s - 12), cv2.FONT_HERSHEY_DUPLEX, 1.5,
                    (30, 30, 30), 3)  # fmt: skip
        cv2.putText(out, "5", (x + s - 18, y + 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                    (30, 30, 30), 1)  # fmt: skip
    return out


def synthetic_view(theme: str = "meadow"):
    from tilelens.rules.profile import demo_profile
    from tilelens.synth.render import render
    from tilelens.synth.scenes import random_scene
    from tilelens.synth.views import ViewSpec, render_view, with_appearance
    from tilelens.vision.autoread import add_screen, calibrate_auto

    profile = with_appearance(demo_profile("demo_garden"), theme)
    cal = None
    for seed in (1000, 1001, 1002):
        scene = random_scene(seed, theme=theme, cell=40, tentative=False, min_turn=8)
        img, labels = render_view(scene, ViewSpec())
        text = "".join(t for t in scene.rack if t)
        if cal is None:
            cal = calibrate_auto(img, profile, text)
        else:
            add_screen(cal, img, profile, text)
    scene = random_scene(31, theme=theme, cell=40, tentative=False)
    view = render_view(scene, ViewSpec(zoom=0.9))[0]
    plain = render(scene)[0]
    return profile, cal, view, plain, scene


def recorded_recognizer(theme: str = "meadow", cell: int = 36):
    from tilelens.rules.profile import demo_profile
    from tilelens.synth.render import render
    from tilelens.synth.scenes import random_scene
    from tilelens.vision.evaluate import calibrate_on
    from tilelens.vision.recognizer import Recognizer

    cal = calibrate_on(theme, [1000, 1001, 1002], cell)
    img = render(random_scene(21, theme=theme, cell=cell))[0]
    return Recognizer(cal, demo_profile("demo_garden")), img


def build(name: str):
    """Returns (setup, step): step() runs one repetition; setup() prepares state for a run."""
    from tilelens.vision.autoread import ViewReader
    from tilelens.vision.locate import locate_board

    if name in ("cold-locate", "cold-read", "warm-read", "cells-cold", "changed-read",
                "rack-search"):  # fmt: skip
        profile, img, cal = real_setup()
    if name == "cold-locate":
        return (lambda: None), lambda _: locate_board(img, profile)
    if name == "cold-read":
        return (lambda: ViewReader(copy.deepcopy(cal), profile)), lambda r: r.read(img)
    if name == "warm-read":

        def setup():
            reader = ViewReader(copy.deepcopy(cal), profile)
            reader.read(img)
            return reader

        return setup, lambda r: r.read(img)
    if name == "cells-cold":

        def setup():
            reader = ViewReader(copy.deepcopy(cal), profile)
            reader.read(img)
            return reader

        def step(r):
            r._cache = {}
            r.read(img)

        return setup, step
    if name == "changed-read":
        frames = [img, with_tiles(img)]

        def setup():
            reader = ViewReader(copy.deepcopy(cal), profile)
            reader.read(img)
            reader.flip = 0
            return reader

        def step(r):
            r.flip ^= 1
            r.read(frames[r.flip])

        return setup, step
    if name == "rack-search":

        def setup():
            reader = ViewReader(copy.deepcopy(cal), profile)
            reader.read(img)
            return reader

        return setup, lambda r: r._read_rack(img, r.view)
    if name == "rack-read":
        sprofile, scal, view, _, _ = synthetic_view()

        def setup():
            reader = ViewReader(copy.deepcopy(scal), sprofile)
            reader.read(view)
            return reader

        return setup, lambda r: r._read_rack(view, r.view)
    if name == "synth-recognizer":
        rec, img2 = recorded_recognizer()
        return (lambda: rec), lambda r: r.read(img2)
    raise SystemExit(f"unknown scenario {name}")


def measure(name: str, repeat: int) -> dict:
    setup, step = build(name)
    cold = name.startswith("cold")
    state = setup()
    step(state)
    times = []
    for _ in range(repeat):
        if cold:
            state = setup()
        t0 = time.perf_counter()
        step(state)
        times.append((time.perf_counter() - t0) * 1000)
    times.sort()
    return {
        "scenario": name,
        "n": len(times),
        "median_ms": round(statistics.median(times), 2),
        "p95_ms": round(times[min(len(times) - 1, int(0.95 * len(times)))], 2),
        "worst_ms": round(times[-1], 2),
        "best_ms": round(times[0], 2),
        "samples_ms": [round(t, 3) for t in times],
    }


def profile_scenario(name: str, repeat: int, top: int) -> None:
    setup, step = build(name)
    state = setup()
    step(state)
    cold = name.startswith("cold")
    prof = cProfile.Profile()
    for _ in range(repeat):
        if cold:
            state = setup()
        prof.enable()
        step(state)
        prof.disable()
    print(f"\n== cProfile: {name} ({repeat} runs)")
    pstats.Stats(prof).sort_stats("tottime").print_stats(top)


class LineTimer:
    """Time per source line of chosen functions, using sys.monitoring."""

    def __init__(self, targets: list[str]) -> None:
        self.codes = {}
        for target in targets:
            module, _, qual = target.partition(":")
            obj = importlib.import_module(module)
            for part in qual.split("."):
                obj = getattr(obj, part)
            self.codes[obj.__code__] = target
        self.spent: dict[tuple, float] = defaultdict(float)
        self.calls: dict[tuple, int] = defaultdict(int)
        self.last: dict = {}
        self.mon = sys.monitoring

    def start(self) -> None:
        mon = self.mon
        tool = mon.PROFILER_ID
        mon.use_tool_id(tool, "profile_vision")
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
        self.last[code] = None

    def _line(self, code, line):
        if code not in self.codes:
            return self.mon.DISABLE
        now = time.perf_counter()
        prev = self.last.get(code)
        if prev is not None:
            self.spent[(code, prev[0])] += now - prev[1]
        self.calls[(code, line)] += 1
        self.last[code] = (line, time.perf_counter())

    def _end(self, code, offset, retval):
        if code not in self.codes:
            return self.mon.DISABLE
        prev = self.last.get(code)
        if prev is not None:
            self.spent[(code, prev[0])] += time.perf_counter() - prev[1]
            self.last[code] = None

    def report(self, runs: int, top: int = 18) -> None:
        for code, target in self.codes.items():
            rows = [(t, line) for (c, line), t in self.spent.items() if c is code]
            if not rows:
                continue
            total = sum(t for t, _ in rows)
            lines = Path(code.co_filename).read_text().splitlines()
            print(f"\n== lines: {target}  {total / runs * 1000:.2f} ms per run")
            for t, line in sorted(rows, reverse=True)[:top]:
                print(f"{t / runs * 1000:8.3f} ms {100 * t / total:5.1f}%  {line:5d}  "
                      f"{lines[line - 1].strip()[:90]}")  # fmt: skip


def line_profile(name: str, repeat: int, targets: list[str]) -> None:
    setup, step = build(name)
    state = setup()
    step(state)
    timer = LineTimer(targets)
    cold = name.startswith("cold")
    timer.start()
    try:
        for _ in range(repeat):
            if cold:
                state = setup()
            step(state)
    finally:
        timer.stop()
    print(f"\n== line timing under scenario {name} ({repeat} runs)")
    timer.report(repeat)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("scenarios", nargs="*", choices=SCENARIOS)
    ap.add_argument("--repeat", type=int, default=30)
    ap.add_argument("--profile", action="store_true")
    ap.add_argument("--top", type=int, default=14)
    ap.add_argument("--lines", nargs="*", default=[])
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()
    names = args.scenarios or SCENARIOS
    results = []
    for name in names:
        res = measure(name, args.repeat)
        results.append(res)
        print(
            f"{name:18s} n={res['n']:3d} median {res['median_ms']:8.2f} ms  "
            f"p95 {res['p95_ms']:8.2f}  worst {res['worst_ms']:8.2f}",
            file=sys.stderr,
        )
        if args.profile:
            profile_scenario(name, max(3, args.repeat // 3), args.top)
        if args.lines:
            line_profile(name, max(3, args.repeat // 3), args.lines)
    if args.json:
        args.json.write_text(json.dumps(results, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

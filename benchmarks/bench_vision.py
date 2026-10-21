"""Recognition accuracy on synthetic boards.

Usage: uv run python benchmarks/bench_vision.py [--scenes N] [--out FILE]

Each theme is calibrated on three of its own screens (as a user would calibrate on their own
game) and then read on N other screens. "ember" is the held-out theme: no recognizer parameter
was tuned while looking at it.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from bench_solver import hardware

from tilelens.synth.render import render
from tilelens.synth.scenes import demo_engine, random_scene
from tilelens.synth.themes import THEMES
from tilelens.vision.evaluate import Score, calibrate_on, score_reading
from tilelens.vision.recognizer import Recognizer


def run(theme: str, scenes: int, cell: int, tentative: bool) -> dict:
    profile = demo_engine("demo_garden")[0]
    t0 = time.perf_counter()
    cal = calibrate_on(theme, [1000, 1001, 1002], cell)
    cal_s = time.perf_counter() - t0
    rec = Recognizer(cal, profile)
    total = Score()
    times = []
    for seed in range(scenes):
        img, labels = render(random_scene(seed, theme=theme, cell=cell, tentative=tentative))
        reading = rec.read(img)
        times.append(reading.elapsed)
        total.merge(score_reading(reading, labels, f"{theme}:{seed}"))
    out = total.summary()
    out["calibration_s"] = round(cal_s, 2)
    out["read_median_ms"] = round(statistics.median(times) * 1000, 1)
    out["read_worst_ms"] = round(max(times) * 1000, 1)
    out["silent_error_examples"] = total.details[:5]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenes", type=int, default=60)
    ap.add_argument("--cell", type=int, default=36)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    report = {"hardware": hardware(), "cell_px": args.cell, "scenes_per_theme": args.scenes}
    themes = {}
    for name, theme in THEMES.items():
        themes[name] = {
            "held_out": theme.held_out,
            "played_tiles_only": run(name, args.scenes, args.cell, False),
            "with_tentative_tiles": run(name, args.scenes // 2, args.cell, True),
        }
        s = themes[name]["played_tiles_only"]
        print(
            f"{name:7s} held_out={theme.held_out!s:5s} cells {s['cell_accuracy_pct']}% "
            f"flagged {s['cell_flagged_pct']}% silent {s['cell_silent_error_pct']}% | rack "
            f"{s['rack_accuracy_pct']}% silent {s['rack_silent_error_pct']}% | positions exact "
            f"{s['position_exact_pct']}% silent {s['position_silent_error_pct']}% | read "
            f"{s['read_median_ms']} ms median",
            file=sys.stderr,
        )
    report["themes"] = themes
    text = json.dumps(report, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

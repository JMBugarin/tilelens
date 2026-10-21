"""End-to-end latency: a stable change in the fake game to the result shown in the panel.

Usage: uv run python benchmarks/bench_e2e.py [--mode screen|inproc] [--events N]
       [--overlay exclude|suppress|panel_only] [--out FILE]

"screen" mode needs the Screen Recording permission and opens real windows.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["screen", "inproc"], default="screen")
    ap.add_argument("--events", type=int, default=40)
    ap.add_argument("--overlay", choices=["exclude", "suppress", "panel_only"], default="exclude")
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    from bench_solver import hardware

    from tilelens.app import versions
    from tilelens.ui.e2e import run

    report = run(args.mode, args.events, args.seed, args.overlay)
    out = {"hardware": hardware(), "versions": versions(), "result": report.summary()}
    text = json.dumps(out, indent=2)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

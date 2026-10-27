"""Run the real app against the real screen for a while and record its cost and latency.

    uv run python benchmarks/bench_live.py --seconds 300 --label discord-idle
    uv run python benchmarks/bench_live.py --seconds 900 --label letter-league-game

TileLens is started as a child process with TILELENS_TRACE set. Every second the process tree's
CPU and resident memory are sampled with `ps`. At the end the trace is analyzed
(analyze_trace.py) and everything is written to benchmarks/results/live-<label>.json.

What is measured depends on what is on the screen while this runs: with Discord open and no game
the loop is idle (that is reported as such); with a Letter League game on screen and moves being
played, the latency numbers are live gameplay. The label is only a name; the report records
whether any position change was observed.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from analyze_trace import load, summarize
from bench_solver import hardware

ROOT = Path(__file__).resolve().parents[1]


def sample(pid: int) -> tuple[float, float] | None:
    out = subprocess.run(
        ["ps", "-o", "%cpu=,rss=", "-p", str(pid)], capture_output=True, text=True
    ).stdout.split()
    if len(out) != 2:
        return None
    return float(out[0]), float(out[1]) / 1024


def children(pid: int) -> list[int]:
    out = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True).stdout.split()
    found = [int(p) for p in out]
    for p in list(found):
        found += children(p)
    return found


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=120)
    ap.add_argument("--label", default="live")
    ap.add_argument("--settle", type=float, default=15, help="seconds to skip at the start")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    trace = Path(tempfile.gettempdir()) / f"tilelens-trace-{os.getpid()}.jsonl"
    trace.unlink(missing_ok=True)
    env = dict(os.environ, TILELENS_TRACE=str(trace))
    proc = subprocess.Popen(
        [sys.executable, "-c", "from tilelens.app import main; main()"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
    )
    cpu, rss, series = [], [], []
    t0 = time.monotonic()
    try:
        while time.monotonic() - t0 < args.seconds:
            time.sleep(1.0)
            if proc.poll() is not None:
                break
            total_cpu = total_rss = 0.0
            for pid in [proc.pid, *children(proc.pid)]:
                s = sample(pid)
                if s:
                    total_cpu += s[0]
                    total_rss += s[1]
            elapsed = time.monotonic() - t0
            series.append({"t": round(elapsed, 1), "cpu_pct": total_cpu, "rss_mb": total_rss})
            if elapsed >= args.settle:
                cpu.append(total_cpu)
                rss.append(total_rss)
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()

    events = load(trace) if trace.exists() else []
    report = {
        "label": args.label,
        "hardware": hardware(),
        "duration_s": round(time.monotonic() - t0, 1),
        "settle_skipped_s": args.settle,
        "process_exited_early": proc.returncode not in (None, 0, -15),
        "cpu_pct_all_processes": {
            "mean": round(statistics.fmean(cpu), 1) if cpu else None,
            "median": round(statistics.median(cpu), 1) if cpu else None,
            "max": round(max(cpu), 1) if cpu else None,
        },
        "rss_mb_all_processes": {
            "first": round(rss[0], 1) if rss else None,
            "last": round(rss[-1], 1) if rss else None,
            "max": round(max(rss), 1) if rss else None,
            "growth_mb": round(rss[-1] - rss[0], 1) if len(rss) > 1 else None,
        },
        "trace": summarize(events),
        "samples": series[:: max(1, len(series) // 120)],
    }
    out = args.out or ROOT / "benchmarks" / "results" / f"live-{args.label}.json"
    out.write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "samples"}, indent=2))


if __name__ == "__main__":
    main()

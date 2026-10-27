"""Latency statistics from a live-loop trace.

    TILELENS_TRACE=/tmp/live.jsonl uv run tilelens      # play, then quit
    uv run python benchmarks/analyze_trace.py /tmp/live.jsonl [--out FILE]

An episode starts at the first captured frame that differs from a quiet period before it (the
game changed) and ends when the overlay or panel was painted with a result published after that
frame. The game changes some time before the frame that shows it, so every latency here is
measured from the first frame that shows the change, and is a lower bound by up to one capture
interval. Episodes that never produced a published result are counted and listed, not dropped.
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

QUIET = 0.6  # seconds without a change before a change counts as a new event


def stats(values: list[float]) -> dict | None:
    if not values:
        return None
    s = sorted(values)
    return {
        "n": len(s),
        "mean_ms": round(statistics.fmean(s), 1),
        "median_ms": round(statistics.median(s), 1),
        "p95_ms": round(s[min(len(s) - 1, int(0.95 * len(s)))], 1),
        "worst_ms": round(s[-1], 1),
    }


def load(path: Path) -> list[dict]:
    events = []
    for line in path.read_text().splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events


def episodes(events: list[dict]) -> list[dict]:
    out, last_change, current = [], None, None
    for ev in events:
        if ev["ev"] == "frame" and ev.get("changed"):
            if last_change is None or ev["t"] - last_change > QUIET:
                if current is not None:
                    out.append(current)
                current = {"start": ev["t"], "reads": [], "stable": None, "publish": None}
            last_change = ev["t"]
        elif current is not None:
            if ev["ev"] == "read" and current["publish"] is None:
                current["reads"].append(ev)
            elif ev["ev"] == "stable" and current["stable"] is None:
                current["stable"] = ev["t"]
            elif ev["ev"] == "publish" and current["publish"] is None and current["stable"]:
                current["publish"] = ev
            elif (
                ev["ev"] == "paint"
                and current["publish"] is not None
                and "paint" not in current
                and ev["t"] >= current["publish"]["t"]
            ):
                current["paint"] = ev
    if current is not None:
        out.append(current)
    return out


def summarize(events: list[dict]) -> dict:
    eps = episodes(events)
    detect_to_stable, solve, publish_to_paint, paint_ms, total_pub, total_paint = (
        [],
        [],
        [],
        [],
        [],
        [],
    )
    unresolved = []
    for e in eps:
        pub = e["publish"]
        if pub is None:
            unresolved.append(round(e["start"], 3))
            continue
        detect_to_stable.append((e["stable"] - e["start"]) * 1000)
        solve.append(pub.get("solve_ms", 0.0))
        total_pub.append((pub["t"] - e["start"]) * 1000)
        if "paint" in e:
            gap = (e["paint"]["t"] - pub["t"]) * 1000
            publish_to_paint.append(gap)
            paint_ms.append(e["paint"].get("paint_ms", 0.0))
            total_paint.append((e["paint"]["t"] - e["start"]) * 1000 + paint_ms[-1])
    frames = [e for e in events if e["ev"] == "frame"]
    reads = [e for e in events if e["ev"] == "read"]
    return {
        "events_in_trace": len(events),
        "episodes": len(eps),
        "episodes_with_result": len(detect_to_stable),
        "episodes_without_result_at": unresolved,
        "frames": len(frames),
        "frames_changed": sum(1 for f in frames if f.get("changed")),
        "grab_ms": stats([f["grab_ms"] for f in frames if "grab_ms" in f]),
        "read_ms": stats([r["read_ms"] for r in reads]),
        "relocations": sum(1 for r in reads if r.get("relocated")),
        "change_to_stable_ms": stats(detect_to_stable),
        "solve_ms": stats(solve),
        "change_to_result_published_ms": stats(total_pub),
        "publish_to_paint_ms": stats(publish_to_paint),
        "paint_ms": stats(paint_ms),
        "change_to_result_painted_ms": stats(total_paint),
        "published_from_cache": sum(
            1 for e in eps if e["publish"] is not None and e["publish"].get("cached")
        ),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("trace", type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    report = summarize(load(args.trace))
    text = json.dumps(report, indent=2)
    print(text)
    if args.out:
        args.out.write_text(text)


if __name__ == "__main__":
    main()

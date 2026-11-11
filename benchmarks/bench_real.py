"""Replay benchmark on frames captured from the real game, against hand-written labels.

    uv run python benchmarks/bench_real.py [--scales 0.5 0.65 0.8 1.0 1.25 1.5] [--runs 15]

Every case in benchmarks/real/cases.json is a frame saved from a real Discord Letter League
window plus labels written by eye, independent of TileLens. The report separates what ran on the
native frame from what ran on a resampled copy of it (a smaller window or a zoomed view), and it
lists every mistake instead of only a rate. Live measurements (the running app on a real game) are
a different thing: see benchmarks/analyze_trace.py.
"""

from __future__ import annotations

import argparse
import copy
import json
import random
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from bench_solver import hardware

from tilelens.rules.board import Tile
from tilelens.rules.legality import IllegalPlacement, check_placement
from tilelens.rules.lexicon import Lexicon
from tilelens.rules.profile import load_profile
from tilelens.rules.scoring import score_words
from tilelens.solver.movegen import MoveGenerator
from tilelens.vision.autoread import ViewReader
from tilelens.vision.cells import extract_glyph, find_tile_blobs, to_lab
from tilelens.vision.lexfix import drop_isolated, settle
from tilelens.vision.recognizer import (
    COMMITTED,
    PRIOR_MARGIN,
    PRIOR_MIN,
    RECENT,
    Calibration,
    font_prior_scores,
)
from tilelens.vision.values import tile_value

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "benchmarks" / "real" / "cases.json"
RACK_TILE = np.array([0x66, 0x8E, 0xEF], np.uint8)
WORD_FILES = (
    Path.home() / "Library" / "Application Support" / "tilelens" / "words" / "enable1.txt",
    Path("/usr/share/dict/words"),
)
BAG = (
    "AAAAAAAAABBCCDDDDEEEEEEEEEEEEFFGGGHHIIIIIIIIIJKLLLLMMNNNNNN"
    "OOOOOOOOPPQRRRRRRSSSSTTTTTTUUUUVVWWXYYZ"
)


def stats(samples: list[float]) -> dict:
    s = sorted(samples)
    return {
        "n": len(s),
        "median_ms": round(statistics.median(s), 2),
        "p95_ms": round(s[min(len(s) - 1, int(0.95 * len(s)))], 2),
        "worst_ms": round(s[-1], 2),
    }


def resample(img: np.ndarray, scale: float) -> np.ndarray:
    if scale == 1.0:
        return img
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
    return cv2.resize(img, None, fx=scale, fy=scale, interpolation=interp)


def board_tiles(reading, offset) -> dict:
    r0, c0 = offset
    out = {}
    for r, row in enumerate(reading.cells):
        for c, cell in enumerate(row):
            if cell.letter and cell.state in (COMMITTED, RECENT) and not cell.foreign:
                out[(r - r0, c - c0)] = cell
    return out


def compare(truth: dict, got: dict) -> dict:
    exact = wrong = missed = false = silent = flagged = 0
    mistakes = []
    for pos, want in truth.items():
        cell = got.get(pos)
        if cell is None:
            missed += 1
            mistakes.append(f"missed {want['letter']} at {pos}")
            continue
        right = cell.letter == want["letter"] and bool(cell.blank) == bool(want.get("blank"))
        if right:
            exact += 1
        else:
            wrong += 1
            if cell.uncertain:
                flagged += 1
            else:
                silent += 1
            kind = "flagged" if cell.uncertain else "SILENT"
            mistakes.append(f"{kind} {want['letter']}->{cell.letter} at {pos}")
    for pos, cell in got.items():
        if pos not in truth:
            false += 1
            mistakes.append(f"false {cell.letter} at {pos}")
    values = [(p, cell.value) for p, cell in got.items() if p in truth and cell.value is not None]
    value_ok = sum(1 for p, v in values if truth[p].get("value") == v)
    return {
        "labelled": len(truth),
        "exact": exact,
        "wrong_letter": wrong,
        "wrong_but_flagged": flagged,
        "wrong_silent": silent,
        "missed": missed,
        "false_detections": false,
        "values_read": len(values),
        "values_correct": value_ok,
        "mistakes": mistakes,
    }


def offset_of(reading) -> tuple[int, int]:
    rows, cols = np.nonzero(reading.view.visible)
    return int(rows.min()), int(cols.min())


def run_board(case: dict, scales: list[float], runs: int) -> dict:
    img0 = cv2.imread(str(ROOT / case["frame"]))
    profile = load_profile(ROOT / case["profile"])
    cal0 = Calibration.load(ROOT / case["calibration"])
    truth = {(t["r"], t["c"]): t for t in case["tiles"]}
    lexicon = load_lexicon(profile)
    out = []
    for scale in scales:
        img = resample(img0, scale)
        reader = ViewReader(copy.deepcopy(cal0), profile)
        t0 = time.perf_counter()
        try:
            reading = reader.read(img)
        except Exception as exc:
            out.append({"scale": scale, "frame_px": list(img.shape[1::-1]), "error": repr(exc)})
            continue
        cold = (time.perf_counter() - t0) * 1000
        first = reading
        warm = []
        for _ in range(runs):
            t0 = time.perf_counter()
            reading = reader.read(img)
            warm.append((time.perf_counter() - t0) * 1000)
        entry = {
            "scale": scale,
            "frame_px": list(img.shape[1::-1]),
            "derived_from_real_frame": scale != 1.0,
            "cold_read_ms": round(cold, 1),
            "warm_read": stats(warm),
            "locates_over_run": reader.locates,
        }
        if first.view is None or not first.view.visible.any():
            entry["error"] = "board not found"
            out.append(entry)
            continue
        off = offset_of(first)
        entry["first_frame"] = compare(truth, board_tiles(first, off))
        entry["raw"] = compare(truth, board_tiles(reading, offset_of(reading)))
        dropped = drop_isolated(reading)
        settle(reading, lexicon)
        entry["after_app_cleanup"] = compare(truth, board_tiles(reading, offset_of(reading)))
        entry["isolated_tiles_dropped"] = len(dropped)
        entry["uncertain_cells"] = len(reading.uncertain_cells)
        out.append(entry)
    return {"id": case["id"], "kind": "board", "scenario": case["scenario"], "results": out}


def word_file() -> Path:
    for path in WORD_FILES:
        if path.exists():
            return path
    raise SystemExit("no word list found")


def load_lexicon(profile) -> Lexicon:
    return Lexicon.load(word_file(), profile.alphabet)


def draw_rack(rng: random.Random) -> Counter:
    letters = [rng.choice(BAG) for _ in range(7)]
    if rng.random() < 0.25:
        letters[0] = "?"
    return Counter(letters)


def run_moves(case: dict, racks: int, seed: int) -> dict:
    img = cv2.imread(str(ROOT / case["frame"]))
    profile = load_profile(ROOT / case["profile"])
    reader = ViewReader(Calibration.load(ROOT / case["calibration"]), profile)
    reading = reader.read(img)
    drop_isolated(reading)
    lexicon = load_lexicon(profile)
    settle(reading, lexicon)
    board = reading.placed()
    t0 = time.perf_counter()
    gen = MoveGenerator(profile, lexicon)
    build_s = time.perf_counter() - t0
    rng = random.Random(seed)
    times, checked, illegal, mis_scored, rank_wrong, empty = [], 0, [], [], 0, 0
    for i in range(racks):
        rack = draw_rack(rng)
        result = gen.solve(board, rack, top_k=3)
        times.append(result.elapsed * 1000)
        if not result.moves:
            empty += 1
            continue
        every = gen.all_moves(board, rack)
        best = sorted(every.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
        if [m.score for m in result.moves] != [s for _, s in best]:
            rank_wrong += 1
        for move in result.moves:
            checked += 1
            tiles = [Tile(t.row, t.col, t.letter, t.blank) for t in move.tiles]
            try:
                words = check_placement(board, rack, tiles, profile, lexicon)
            except IllegalPlacement as exc:
                illegal.append(f"rack {i}: {move.word}: {exc}")
                continue
            if score_words(words, len(tiles), profile, board.stamps).total != move.score:
                mis_scored.append(f"rack {i}: {move.word}")
    return {
        "id": case["id"],
        "kind": "moves-on-real-board",
        "racks_are_synthetic": True,
        "word_list": str(word_file()),
        "word_count": len(lexicon.words),
        "racks": racks,
        "empty_results": empty,
        "moves_checked": checked,
        "illegal": illegal,
        "score_disagrees_with_rules_module": mis_scored,
        "top3_differs_from_full_enumeration": rank_wrong,
        "solve": stats(times) if times else None,
        "graph_build_s": round(build_s, 2),
        "scoring_vs_game": "not measured: needs the game's own score before and after a play",
    }


def run_rack(case: dict, scales: list[float]) -> dict:
    img0 = cv2.imread(str(ROOT / case["frame"]))
    lab = to_lab(RACK_TILE.reshape(1, 1, 3)).reshape(3)
    out = []
    for scale in scales:
        if scale > 1.0:
            continue
        img = resample(img0, scale)
        h, w = img.shape[:2]
        t0 = time.perf_counter()
        blobs = find_tile_blobs(img, (0, 0, w, h), lab, 22.0, (max(4, int(10 * scale)), 300))
        side = float(np.median([(b.rect[2] + b.rect[3]) / 2 for b in blobs])) if blobs else 0
        blobs = [b for b in blobs if abs((b.rect[2] + b.rect[3]) / 2 - side) < 0.2 * side]
        letters, values, sure = [], [], 0
        for b in blobs:
            glyph = extract_glyph(b.crop, b.ring)
            ranked = sorted(font_prior_scores(glyph.vec).items(), key=lambda kv: -kv[1])
            (top, s1), (_, s2) = ranked[0], ranked[1]
            letters.append(top)
            if s1 >= PRIOR_MIN and s1 - s2 >= PRIOR_MARGIN:
                sure += 1
            values.append(tile_value(img, b.rect))
        ms = (time.perf_counter() - t0) * 1000
        truth = case["letters"]
        got = "".join(letters)
        out.append(
            {
                "scale": scale,
                "tiles_found": len(blobs),
                "tiles_labelled": len(truth),
                "letters_correct": sum(1 for a, b in zip(got, truth, strict=False) if a == b),
                "letters_named_with_confidence": sure,
                "values_correct": sum(
                    1 for a, b in zip(values, case["values"], strict=False) if a == b
                ),
                "read_ms": round(ms, 1),
                "derived_from_real_frame": scale != 1.0,
            }
        )
    return {"id": case["id"], "kind": "rack", "scenario": case["scenario"], "results": out}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scales", type=float, nargs="+", default=[0.5, 0.65, 0.8, 1.0, 1.25, 1.5])
    ap.add_argument("--runs", type=int, default=15)
    ap.add_argument("--racks", type=int, default=40)
    ap.add_argument("--seed", type=int, default=11)
    default_out = ROOT / "benchmarks" / "results" / "real-replay.json"
    ap.add_argument("--out", type=Path, default=default_out)
    args = ap.parse_args()
    cases = json.loads(CASES.read_text())["cases"]
    report = {"hardware": hardware(), "data": "real frames; labels by eye", "cases": []}
    for case in cases:
        if case["kind"] == "board":
            report["cases"].append(run_board(case, args.scales, args.runs))
            report["cases"].append(run_moves(case, args.racks, args.seed))
        else:
            report["cases"].append(run_rack(case, args.scales))
    args.out.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

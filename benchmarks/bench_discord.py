"""Rack reading accuracy on real Letter League captures (tests/fixtures/discord).

Each strip is read with no calibration at all (stock-font letter shapes and printed values),
at full size and shrunk, as a smaller Discord window would show it.

    uv run python benchmarks/bench_discord.py
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import cv2
import numpy as np

from tilelens.vision.cells import extract_glyph, find_tile_blobs, to_lab
from tilelens.vision.recognizer import PRIOR_MARGIN, PRIOR_MIN, font_prior_scores
from tilelens.vision.values import tile_value

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "discord"
RACK_TILE = np.array([0x66, 0x8E, 0xEF], np.uint8)
CASES = {
    "rack-vpoknlj": ("VPOKNLJ", [4, 3, 1, 5, 1, 2, 8]),
    "rack-vyeizae": ("VYEIZAE", [4, 4, 1, 1, 10, 1, 1]),
}


def tiles(img):
    lab = to_lab(RACK_TILE.reshape(1, 1, 3)).reshape(3)
    h, w = img.shape[:2]
    blobs = find_tile_blobs(img, (0, 0, w, h), lab, 22.0, (10, 300))
    if not blobs:
        return []
    side = float(np.median([(b.rect[2] + b.rect[3]) / 2 for b in blobs]))
    return [b for b in blobs if abs((b.rect[2] + b.rect[3]) / 2 - side) < 0.2 * side]


def main() -> None:
    rows = []
    for scale in (1.0, 0.75, 0.5):
        tot = right = sure = vals = vals_ok = 0
        read_ms = []
        for name, (letters, values) in CASES.items():
            img = cv2.imread(str(FIXTURES / f"{name}.png"))
            if scale != 1.0:
                img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            t0 = time.perf_counter()
            blobs = tiles(img)
            for blob, truth, value in zip(blobs, letters, values, strict=False):
                glyph = extract_glyph(blob.crop, blob.ring)
                ranked = sorted(font_prior_scores(glyph.vec).items(), key=lambda kv: -kv[1])
                tot += 1
                right += ranked[0][0] == truth
                sure += ranked[0][1] >= PRIOR_MIN and ranked[0][1] - ranked[1][1] >= PRIOR_MARGIN
                vals += 1
                vals_ok += tile_value(img, blob.rect) == value
            read_ms.append((time.perf_counter() - t0) * 1000)
        rows.append(
            {
                "scale": scale,
                "tiles": tot,
                "letter_correct": right,
                "letter_named_without_help": sure,
                "values_correct": vals_ok,
                "rack_read_ms_median": round(float(np.median(read_ms)), 1),
            }
        )
        print(rows[-1])
    out = ROOT / "benchmarks" / "results" / "discord-rack.json"
    out.write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()

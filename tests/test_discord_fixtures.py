"""Regression tests on rack strips cropped from a real Letter League game in Discord.

The strips are Retina captures. They pin the failures seen in play: a thin L whose point value
was folded into its shape, and a Z whose two-digit value touches its top.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from tilelens.vision.cells import extract_glyph, find_tile_blobs, to_lab
from tilelens.vision.recognizer import PRIOR_MARGIN, PRIOR_MIN, font_prior_scores
from tilelens.vision.values import tile_value

FIXTURES = Path(__file__).parent / "fixtures" / "discord"
RACK_TILE = np.array([0x66, 0x8E, 0xEF], np.uint8)  # #EF8E66 as BGR
CASES = [
    ("rack-vpoknlj", "VPOKNLJ", [4, 3, 1, 5, 1, 2, 8]),
    ("rack-vyeizae", "VYEIZAE", [4, 4, 1, 1, 10, 1, 1]),
]


def tiles(name):
    img = cv2.imread(str(FIXTURES / f"{name}.png"))
    lab = to_lab(RACK_TILE.reshape(1, 1, 3)).reshape(3)
    h, w = img.shape[:2]
    blobs = find_tile_blobs(img, (0, 0, w, h), lab, 22.0, (20, 300))
    side = float(np.median([(b.rect[2] + b.rect[3]) / 2 for b in blobs]))
    return img, [b for b in blobs if abs((b.rect[2] + b.rect[3]) / 2 - side) < 0.2 * side]


@pytest.mark.parametrize(("name", "letters", "values"), CASES)
def test_stock_shapes_name_every_letter(name, letters, values):
    _, blobs = tiles(name)
    assert len(blobs) == 7
    for blob, truth in zip(blobs, letters, strict=True):
        glyph = extract_glyph(blob.crop, blob.ring)
        ranked = sorted(font_prior_scores(glyph.vec).items(), key=lambda kv: -kv[1])
        (top, s1), (_, s2) = ranked[0], ranked[1]
        assert top == truth, (truth, ranked[:3])
        assert s1 >= PRIOR_MIN and s1 - s2 >= PRIOR_MARGIN, (truth, ranked[:3])


@pytest.mark.parametrize(("name", "letters", "values"), CASES)
def test_point_values_are_read_off_the_tiles(name, letters, values):
    img, blobs = tiles(name)
    got = [tile_value(img, b.rect) for b in blobs]
    assert got == values


def test_stamped_values_on_played_tiles_are_read():
    """AROUND was played for triple word, so each tile shows its value times three (the A also
    sat on a double letter). The game counts those numbers when the tiles are used again."""
    img = cv2.imread(str(FIXTURES / "board-around.png"))
    pitch = 62.6
    got = []
    for i, base in enumerate([1, 1, 1, 1, 1]):  # A R O U N
        rect = (round(6 + i * pitch), 6, round(pitch), round(pitch))
        got.append(tile_value(img, rect, 999, base))
    assert got == [6, 3, 3, 3, 3]

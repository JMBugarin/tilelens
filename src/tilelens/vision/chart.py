"""Learn every letter at once from a chart that shows one tile per letter.

Some games have a screen that lists the whole tile set, one tile per letter in alphabetical
order and the blank last. Reading it teaches TileLens the shape of every letter in the game's
own font, so a fresh calibration does not have to wait for each letter to show up on the rack.
"""

from __future__ import annotations

import numpy as np

from tilelens.rules.profile import Profile
from tilelens.vision.cells import Blob, extract_glyph, find_tile_blobs, to_lab
from tilelens.vision.recognizer import Calibration, CalibrationError


def chart_tiles(frame: np.ndarray, profile: Profile) -> list[Blob]:
    """Rack-colored tiles of one size anywhere in the frame, in reading order."""
    app = profile.appearance
    if app is None or app.rack_tile is None:
        return []
    H, W = frame.shape[:2]
    tile_lab = to_lab(np.array(app.rack_tile, np.uint8).reshape(1, 1, 3)).reshape(3)
    blobs = find_tile_blobs(frame, (0, 0, W, H), tile_lab, 22.0, (0.02 * H, 0.3 * H))
    if not blobs:
        return []
    sizes = np.array([(b.rect[2] + b.rect[3]) / 2 for b in blobs])
    med = float(np.median(sizes))
    blobs = [b for b, s in zip(blobs, sizes, strict=True) if abs(s - med) < 0.2 * med]
    rows: list[list[Blob]] = []
    for b in sorted(blobs, key=lambda b: b.rect[1] + b.rect[3] / 2):
        cy = b.rect[1] + b.rect[3] / 2
        if rows and abs(rows[-1][0].rect[1] + rows[-1][0].rect[3] / 2 - cy) < 0.3 * med:
            rows[-1].append(b)
        else:
            rows.append([b])
    return [b for row in rows for b in sorted(row, key=lambda b: b.rect[0])]


def chart_letters(profile: Profile) -> list[str]:
    return sorted(profile.alphabet)


def looks_like_chart(frame: np.ndarray, profile: Profile) -> bool:
    n = len(chart_tiles(frame, profile))
    letters = len(chart_letters(profile))
    return n in (letters, letters + 1)


def learn_chart(cal: Calibration, frame: np.ndarray, profile: Profile) -> int:
    """Add a rack template for every letter on the chart. Returns how many were learned."""
    tiles = chart_tiles(frame, profile)
    letters = chart_letters(profile)
    if len(tiles) not in (len(letters), len(letters) + 1):
        raise CalibrationError(
            f"found {len(tiles)} tiles; a tile chart shows {len(letters)} letters "
            "(and optionally a blank last)"
        )
    glyphs = []
    for tile, letter in zip(tiles, letters, strict=False):
        glyph = extract_glyph(tile.crop, tile.ring)
        if glyph is None:
            raise CalibrationError(f"no letter found on the chart tile for '{letter}'")
        glyphs.append((letter, glyph.vec))
    if len(tiles) > len(letters) and extract_glyph(tiles[-1].crop, tiles[-1].ring) is not None:
        raise CalibrationError("the last chart tile should be the blank, but it shows a glyph")
    # the chart must not repeat a shape: two letters that look the same mean a wrong order
    mat = np.array([v for _, v in glyphs])
    sims = mat @ mat.T - np.eye(len(mat))
    if float(sims.max()) > 0.97:
        i, j = np.unravel_index(int(np.argmax(sims)), sims.shape)
        raise CalibrationError(
            f"chart tiles for '{glyphs[i][0]}' and '{glyphs[j][0]}' look the same; "
            "is the whole chart in view?"
        )
    for letter, vec in glyphs:
        cal.add_template(letter, vec, rack=True)
    return len(glyphs)


__all__ = ["chart_tiles", "learn_chart", "looks_like_chart"]

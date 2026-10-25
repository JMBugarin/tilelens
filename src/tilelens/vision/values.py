"""Reading the point value printed on a tile, so scores follow the game's own values."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import cv2
import numpy as np

from tilelens.vision.cells import PATCH, crop, mask_to_vec, patch_features, to_lab

DIGIT_MIN = 0.80
DIGIT_MARGIN = 0.06
MIN_AREA = 6
SIDE = 96
VOTES_NEEDED = 3
VOTE_SHARE = 0.8
MAX_VALUE = 20

_DIGITS: tuple[list[str], np.ndarray] | None = None


def digit_priors() -> tuple[list[str], np.ndarray]:
    global _DIGITS
    if _DIGITS is None:
        try:
            data = np.load(Path(__file__).with_name("priors.npz"))
            _DIGITS = (
                [str(x) for x in data["digits"]],
                data["digit_vecs"].astype(np.float32),
            )
        except (OSError, ValueError, KeyError):
            _DIGITS = ([], np.zeros((0, 0, 0), np.float32))
    return _DIGITS


def _digit(mask: np.ndarray) -> str | None:
    chars, vecs = digit_priors()
    vec = mask_to_vec(mask)
    if vec is None or not chars:
        return None
    best = (vecs @ vec).max(axis=0)
    order = np.argsort(-best)
    if best[order[0]] < DIGIT_MIN or best[order[0]] - best[order[1]] < DIGIT_MARGIN:
        return None
    return chars[int(order[0])]


def _value_mask(inner: np.ndarray, ring: np.ndarray) -> np.ndarray | None:
    """Pixels of the printed number: what differs from the tile in its top-right corner.

    Its own threshold is used because the number can be much fainter than the letter, and
    shapes that run off the corner's inner sides belong to the letter, not the number.
    """
    size = inner.shape[0]
    y1, x0, x1 = round(size * 0.46), round(size * 0.52), size - round(size * 0.03)
    dist = np.linalg.norm(to_lab(inner) - ring[None, None, :3], axis=2)[:y1, x0:x1]
    scaled = np.clip(dist * 4.0, 0, 255).astype(np.uint8)
    t, _ = cv2.threshold(scaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    thr = max(12.0, float(t) / 4.0)
    zone = (dist > thr).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(zone, connectivity=8)
    out = np.zeros(inner.shape[:2], bool)
    height, width = zone.shape
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area < MIN_AREA or x == 0 or y + h >= height or h > 0.4 * size:
            continue
        out[:y1, x0:x1] |= labels == i
    return out if out.any() else None


def tile_value(img: np.ndarray, rect: tuple[int, int, int, int]) -> int | None:
    """The number printed on the tile at rect (full-resolution pixels), or None."""
    x, y, w, h = rect
    if w < 24 or h < 24:
        return None
    tile = cv2.resize(crop(img, rect), (PATCH, PATCH), interpolation=cv2.INTER_AREA)
    ring = patch_features(to_lab(tile))[:3]
    inner = crop(img, (x + round(w * 0.06), y + round(h * 0.06), round(w * 0.88), round(h * 0.88)))
    inner = cv2.resize(inner, (SIDE, SIDE), interpolation=cv2.INTER_AREA)
    mask = _value_mask(inner, ring)
    if mask is None:
        return None
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    comps = [i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] >= MIN_AREA]
    if not comps or len(comps) > 2:
        return None
    comps.sort(key=lambda i: stats[i, cv2.CC_STAT_LEFT])
    masks = [labels == i for i in comps]
    if len(masks) == 1:
        cw, ch = stats[comps[0], cv2.CC_STAT_WIDTH], stats[comps[0], cv2.CC_STAT_HEIGHT]
        if cw > 1.15 * ch:  # two touching digits
            left = masks[0].copy()
            right = masks[0].copy()
            cut = stats[comps[0], cv2.CC_STAT_LEFT] + cw // 2
            left[:, cut:] = False
            right[:, :cut] = False
            masks = [left, right]
    text = ""
    for m in masks:
        d = _digit(m)
        if d is None:
            return None
        text += d
    value = int(text)
    return value if 0 < value <= MAX_VALUE else None


class ValueVotes:
    """Point values seen per letter; a value is trusted once it is seen often and consistently."""

    def __init__(self) -> None:
        self.votes: dict[str, Counter] = {}

    def add(self, letter: str, value: int) -> None:
        self.votes.setdefault(letter, Counter())[value] += 1

    def trusted(self) -> dict[str, int]:
        out = {}
        for letter, counts in self.votes.items():
            value, n = counts.most_common(1)[0]
            if n >= VOTES_NEEDED and n >= VOTE_SHARE * sum(counts.values()):
                out[letter] = value
        return out

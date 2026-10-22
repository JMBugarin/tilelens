from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from tilelens.vision.grid import Grid

PATCH = 32
CROP = 48
GLYPH = 24


def to_lab(bgr: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(bgr.astype(np.float32) / 255.0, cv2.COLOR_BGR2Lab)


def _band(lo: float, hi: float, size: int = PATCH) -> np.ndarray:
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32) + 0.5
    edge = np.minimum(np.minimum(xx, size - xx), np.minimum(yy, size - yy)) / size
    return (edge >= lo) & (edge < hi)


RING = _band(0.10, 0.24)
OUTER = _band(0.02, 0.09)


def crop(img: np.ndarray, rect: tuple[int, int, int, int]) -> np.ndarray:
    x, y, w, h = rect
    H, W = img.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(W, x + w), min(H, y + h)
    if x1 <= x0 or y1 <= y0:
        return np.zeros((max(1, h), max(1, w), 3), img.dtype)
    out = img[y0:y1, x0:x1]
    if (x0, y0, x1, y1) != (x, y, x + w, y + h):
        out = cv2.copyMakeBorder(out, y0 - y, y + h - y1, x0 - x, x + w - x1, cv2.BORDER_REPLICATE)
    return out


def patch_features(patch_lab: np.ndarray) -> np.ndarray:
    """6-vector: median Lab of the inner ring and of the outer band of a PATCH x PATCH cell."""
    ring = np.median(patch_lab[RING], axis=0)
    outer = np.median(patch_lab[OUTER], axis=0)
    return np.concatenate([ring, outer]).astype(np.float32)


def board_features(img: np.ndarray, grid: Grid) -> tuple[np.ndarray, list[np.ndarray]]:
    """Return (rows, cols, 6) appearance features and per-cell CROP x CROP BGR tile crops."""
    feats = np.zeros((grid.rows, grid.cols, 6), np.float32)
    crops: list[np.ndarray] = []
    for r in range(grid.rows):
        for c in range(grid.cols):
            cell = crop(img, grid.cell_rect(r, c))
            patch = cv2.resize(cell, (PATCH, PATCH), interpolation=cv2.INTER_AREA)
            feats[r, c] = patch_features(to_lab(patch))
            inner = crop(img, grid.cell_rect(r, c, inset=0.06))
            crops.append(cv2.resize(inner, (CROP, CROP), interpolation=cv2.INTER_AREA))
    return feats, crops


@dataclass
class Glyph:
    vec: np.ndarray
    ink: np.ndarray
    value_mark: bool
    ink_fraction: float
    value_ink: np.ndarray | None = None
    touches_edge: bool = False


def _otsu(values: np.ndarray) -> float:
    v = np.clip(values * 2.0, 0, 255).astype(np.uint8)
    t, _ = cv2.threshold(v, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return float(t) / 2.0


def extract_glyph(crop_bgr: np.ndarray, background_lab: np.ndarray) -> Glyph | None:
    """Separate the letter from the tile background, dropping a corner point-value mark."""
    size = crop_bgr.shape[0]
    lab = to_lab(crop_bgr)
    dist = np.linalg.norm(lab - background_lab[None, None, :3], axis=2)
    raw_dist = dist.copy()
    margin = max(1, round(size * 0.08))
    dist[:margin] = 0
    dist[-margin:] = 0
    dist[:, :margin] = 0
    dist[:, -margin:] = 0
    thr = max(16.0, _otsu(dist[margin:-margin, margin:-margin]))
    mask = (dist > thr).astype(np.uint8)
    n, labels, stats, cents = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if n <= 1:
        return None
    min_area = max(4, int(0.004 * size * size))
    edge = 0.2 * size

    def hugs_edge(comp):
        x, y, w, h = stats[comp, :4]
        return y + h < edge or x + w < edge or y > size - edge or x > size - edge

    def in_outer_band(comp):
        x, y, w, h = stats[comp, :4]
        band = 0.27 * size
        return y + h < band or x + w < band or y > size - band or x > size - band

    def in_any_corner(comp):
        cx, cy = cents[comp]
        return abs(cx - size / 2) > 0.2 * size and abs(cy - size / 2) > 0.2 * size

    def in_value_corner(comp):
        cx, cy = cents[comp]
        return cx > 0.6 * size and (cy > 0.58 * size or cy < 0.42 * size)

    comps = [i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] >= min_area]
    central = [i for i in comps if not hugs_edge(i)]
    if not central:
        return None

    def centrality(i):
        cx, cy = cents[i]
        off = max(abs(cx - size / 2), abs(cy - size / 2)) / size
        return stats[i, cv2.CC_STAT_AREA] * (1.0 if off < 0.3 else 0.2)

    main = max(central, key=centrality)
    main_area = stats[main, cv2.CC_STAT_AREA]
    if main_area < min_area * 3:
        return None
    mx, my = cents[main]
    if max(abs(mx - size / 2), abs(my - size / 2)) > 0.27 * size and in_value_corner(main):
        return None
    keep = np.zeros(n, bool)
    keep[main] = True
    value = False
    value_pixels = np.zeros(labels.shape, bool)
    for comp in comps:
        if comp == main:
            continue
        area = stats[comp, cv2.CC_STAT_AREA]
        if in_value_corner(comp) and area < 0.45 * main_area:
            value = True
            value_pixels |= labels == comp
        elif in_any_corner(comp) and area < 0.45 * main_area:
            continue
        elif (
            not hugs_edge(comp)
            and area >= 0.08 * main_area
            # a small mark near the tile's edge (a corner label) is not part of the letter,
            # judged against the square too, since thin letters are small themselves
            and not ((area < 0.3 * main_area or area < 0.02 * size * size) and in_outer_band(comp))
        ):
            keep[comp] = True
    letter = keep[labels]
    near_letter = cv2.dilate(letter.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
    z0, z1 = round(size * 0.6), round(size * 0.92)
    for y_lo, y_hi in ((round(size * 0.58), z1), (round(size * 0.08), round(size * 0.42))):
        zone = np.zeros_like(letter)
        zone[y_lo:y_hi, z0:z1] = True
        faint = (raw_dist > max(10.0, 0.45 * thr)) & zone & ~near_letter
        if faint.sum() >= max(3, 0.004 * size * size):
            ys_f, xs_f = np.nonzero(faint)
            if np.ptp(ys_f) >= 2 and np.ptp(xs_f) >= 1:
                value = True
                value_pixels |= faint
    mx0, my0, mw, mh = stats[main, :4]
    lim = margin + 1
    touches = mx0 <= lim or my0 <= lim or mx0 + mw >= size - lim or my0 + mh >= size - lim
    ys, xs = np.nonzero(letter)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    g = letter[y0:y1, x0:x1].astype(np.float32)
    h, w = g.shape
    side = max(h, w)
    sq = np.zeros((side, side), np.float32)
    sq[(side - h) // 2 : (side - h) // 2 + h, (side - w) // 2 : (side - w) // 2 + w] = g
    small = cv2.resize(sq, (GLYPH, GLYPH), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), 1.3).ravel()
    small = small - small.mean()
    norm = float(np.linalg.norm(small))
    if norm < 1e-6:
        return None
    strongest = dist[letter]
    cut = np.percentile(strongest, 70)
    ink = np.median(lab[letter & (dist >= cut)], axis=0)
    value_ink = None
    if value_pixels.sum() >= 3:
        strongest_v = raw_dist[value_pixels]
        cut_v = np.percentile(strongest_v, 60)
        value_ink = np.median(lab[value_pixels & (raw_dist >= cut_v)], axis=0).astype(np.float32)
    return Glyph(
        (small / norm).astype(np.float32),
        ink.astype(np.float32),
        value,
        float(letter.mean()),
        value_ink,
        bool(touches),
    )


@dataclass
class Blob:
    rect: tuple[int, int, int, int]
    crop: np.ndarray
    ring: np.ndarray


def find_tile_blobs(
    img: np.ndarray,
    band: tuple[int, int, int, int],
    tile_lab: np.ndarray,
    radius: float,
    size_range: tuple[float, float],
    native: float | None = None,
) -> list[Blob]:
    """Tile-colored squares inside `band` (x0, y0, x1, y1), left to right."""
    x0, y0, x1, y1 = band
    H, W = img.shape[:2]
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
    if x1 <= x0 or y1 <= y0:
        return []
    region = img[y0:y1, x0:x1]
    lab = to_lab(region)
    near = (np.linalg.norm(lab - tile_lab[None, None, :3], axis=2) < radius).astype(np.uint8)
    near = cv2.morphologyEx(near, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(near, connectivity=4)
    lo, hi = size_range
    blobs = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        side = (w + h) / 2
        if not (lo <= side <= hi) or not (0.7 <= w / max(h, 1) <= 1.45):
            continue
        if area < 0.45 * w * h:
            continue
        rect = (int(x + x0), int(y + y0), int(w), int(h))
        tile = crop(img, rect)
        patch = cv2.resize(tile, (PATCH, PATCH), interpolation=cv2.INTER_AREA)
        ring = patch_features(to_lab(patch))
        inner = crop(img, (rect[0] + round(w * 0.06), rect[1] + round(h * 0.06),
                           round(w * 0.88), round(h * 0.88)))  # fmt: skip
        if native:
            n_px = max(8, round(native * 0.88))
            inner = cv2.resize(inner, (n_px, n_px), interpolation=cv2.INTER_AREA)
        blobs.append(
            Blob(rect, cv2.resize(inner, (CROP, CROP), interpolation=cv2.INTER_AREA), ring)
        )
    blobs.sort(key=lambda b: b.rect[0])
    return blobs


def corner_mark_ink(crop_bgr: np.ndarray, background_lab: np.ndarray) -> np.ndarray | None:
    """Color of a point-value mark in either right-hand corner, if there is one."""
    size = crop_bgr.shape[0]
    lab = to_lab(crop_bgr)
    dist = np.linalg.norm(lab - background_lab[None, None, :3], axis=2)
    zone = np.zeros(dist.shape, bool)
    x0, x1 = round(size * 0.6), round(size * 0.94)
    zone[round(size * 0.06) : round(size * 0.42), x0:x1] = True
    zone[round(size * 0.58) : round(size * 0.94), x0:x1] = True
    marked = zone & (dist > 20)
    if marked.sum() < 3:
        return None
    return np.median(lab[marked], axis=0).astype(np.float32)

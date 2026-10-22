"""Find the board in a frame from the colors of its empty squares.

Works when the board is zoomed, panned, partly off screen or covered by other interface
elements: the visible pattern of premium squares is registered against the profile's layout.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from tilelens.rules.profile import Profile
from tilelens.vision.cells import to_lab
from tilelens.vision.grid import Grid

COLOR_MATCH = 14.0
MIN_CELLS = 12


class BoardNotFound(RuntimeError):
    pass


@dataclass
class BoardView:
    grid: Grid
    visible: np.ndarray
    match_rate: float
    margin: float
    cells_used: int

    @property
    def visible_count(self) -> int:
        return int(self.visible.sum())

    @property
    def full(self) -> bool:
        return bool(self.visible.all())


def _drop_blocks(lattice: np.ndarray, layout: np.ndarray, plain: int) -> np.ndarray:
    """Premium-colored areas wider than one square are interface panels, not squares."""

    def same_neighbours(grid):
        g = np.pad(grid, 1, constant_values=-9)
        core = g[1:-1, 1:-1]
        return (
            (core == g[:-2, 1:-1]) | (core == g[2:, 1:-1]) | (core == g[1:-1, :-2])
            | (core == g[1:-1, 2:])
        ) & (core != plain) & (core >= 0)  # fmt: skip

    if same_neighbours(layout).any():
        return lattice
    out = lattice.copy()
    out[same_neighbours(lattice)] = -1
    return out


def _palette(profile: Profile):
    app = profile.appearance
    if app is None:
        raise BoardNotFound("the profile has no [appearance] section")
    keys = sorted(app.squares)
    bgr = np.array([app.squares[k] for k in keys], np.uint8).reshape(-1, 1, 3)
    lab = to_lab(bgr).reshape(-1, 3)
    return keys, lab


def _aliases(lab: np.ndarray) -> np.ndarray:
    """Squares whose colors cannot be told apart share one class."""
    alias = np.arange(len(lab))
    for i in range(len(lab)):
        for j in range(i):
            if alias[j] == j and np.linalg.norm(lab[i] - lab[j]) < COLOR_MATCH:
                alias[i] = j
                break
    return alias


def _layout_classes(profile: Profile, keys: list[str]) -> np.ndarray:
    index = {k: i for i, k in enumerate(keys)}
    legend_char = {}
    raw_layout = profile.raw.get("board", {}).get("layout", [])
    out = np.full((profile.height, profile.width), index["."], np.int32)
    for r, line in enumerate(raw_layout):
        for c, ch in enumerate(line):
            legend_char[(r, c)] = ch
            out[r, c] = index.get(ch, index["."])
    return out


def _cell_counts(integrals, xs, ys, pitch, inset):
    """Per-class pixel counts in the inset box of every lattice cell."""
    xa = np.round(xs + inset * pitch).astype(int)
    xb = np.round(xs + (1 - inset) * pitch).astype(int)
    ya = np.round(ys + inset * pitch).astype(int)
    yb = np.round(ys + (1 - inset) * pitch).astype(int)
    XA, YA = np.meshgrid(xa, ya)
    XB, YB = np.meshgrid(xb, yb)
    counts = [ii[YB, XB] - ii[YA, XB] - ii[YB, XA] + ii[YA, XA] for ii in integrals]
    area = np.maximum(1, (XB - XA) * (YB - YA))
    return np.stack(counts, axis=-1).astype(np.float32), area.astype(np.float32)


def _lattice_score(integrals, shape, pitch, fx, fy, inset=0.12):
    H, W = shape
    xs = np.arange(fx, W - pitch, pitch)
    ys = np.arange(fy, H - pitch, pitch)
    if len(xs) < 2 or len(ys) < 2:
        return -1.0, None, xs, ys
    counts, area = _cell_counts(integrals, xs, ys, pitch, inset)
    purity = counts.max(axis=-1) / area
    pure = purity >= 0.85
    return float(pure.sum() + 0.1 * purity.sum()), (counts, area, pure), xs, ys


def _comb(profile: np.ndarray, pitch: float, phase: float) -> float:
    grid = np.arange(len(profile), dtype=np.float32)
    on = np.interp(np.arange(phase, len(profile) - 1, pitch), grid, profile)
    off = np.interp(np.arange(phase + pitch / 2, len(profile) - 1, pitch), grid, profile)
    return float(on.mean() - off.mean()) if len(on) and len(off) else 0.0


def _smooth(profile: np.ndarray, sigma: float) -> np.ndarray:
    k = max(1, int(sigma * 3))
    xs = np.arange(-k, k + 1, dtype=np.float32)
    kern = np.exp(-(xs**2) / (2 * max(0.7, sigma) ** 2))
    return np.convolve(profile, kern / kern.sum(), mode="same")


def _best_phase(profile: np.ndarray, pitch: float, step: float = 0.25) -> tuple[float, float]:
    sm = _smooth(profile, pitch * 0.05)
    best = max((_comb(sm, pitch, ph), ph) for ph in np.arange(0, pitch, step))
    return best[1], best[0]


def _fit_lattices(cls, n_classes, px, py, extent):
    """Lattice fits for the strongest grid rhythm and its multiples and fractions."""
    H, W = cls.shape
    integrals = [
        cv2.integral((cls == k).astype(np.uint8)).astype(np.int64) for k in range(n_classes)
    ]
    px = px / (px.mean() + 1e-6)
    py = py / (py.mean() + 1e-6)

    def evaluate(p):
        fx, cx = _best_phase(px, p, max(0.25, p / 40))
        fy, cy = _best_phase(py, p, max(0.25, p / 40))
        _, data, _, _ = _lattice_score(integrals, (H, W), p, fx, fy, inset=0.1)
        if data is None:
            return -1.0, fx, fy
        return max(0.0, cx + cy) * (0.7 + 0.3 * float(data[2].mean())), fx, fy

    def refine(p, spread=0.03, n=25):
        return max(
            ((*evaluate(q), q) for q in p * (1 + np.linspace(-spread, spread, n))),
            key=lambda t: t[0],
        )

    # at least four whole squares must fit across the board area in view
    lo = 8.0
    hi = max(lo * 1.5, extent / 4.5)
    grid_p = np.exp(np.arange(np.log(lo), np.log(hi), 0.006))
    curve = []
    for p in grid_p:
        _, cx = _best_phase(px, p, max(0.5, p / 24))
        _, cy = _best_phase(py, p, max(0.5, p / 24))
        curve.append(cx + cy)
    curve = np.array(curve)
    peaks = [
        i for i in range(1, len(curve) - 1) if curve[i] >= curve[i - 1] and curve[i] >= curve[i + 1]
    ]
    if not peaks or curve[max(peaks, key=lambda i: curve[i])] <= 0:
        raise BoardNotFound("no grid found")
    # few lines fit at long periods, so chance alignments score high there; rank both ways
    weighted = curve * np.sqrt(extent / grid_p)
    order = (
        sorted(peaks, key=lambda i: -weighted[i])[:6] + sorted(peaks, key=lambda i: -curve[i])[:4]
    )
    pitches: list[float] = []
    for i in order:
        if curve[i] <= 0:
            continue
        # lines through the middle of each square show up as half the true period
        for q in (float(grid_p[i]), 2 * float(grid_p[i])):
            if q <= hi and all(abs(q - o) / o > 0.03 for o in pitches):
                pitches.append(q)
    fits = []
    for q in pitches:
        sc, fx, fy, p = refine(q, 0.008, 9)
        if sc <= 0:
            continue
        _, data, _, _ = _lattice_score(integrals, (H, W), p, fx, fy, inset=0.25)
        if data is None:
            continue
        counts, area, _ = data
        purity = counts.max(axis=-1) / area
        lattice = np.where(purity >= 0.6, counts.argmax(axis=-1), -1).astype(np.int32)
        fits.append((p, fx, fy, lattice))
    return fits


def _register(lattice, layout, plain, n_classes):
    nrows, ncols = lattice.shape
    R, C = layout.shape
    pad_r, pad_c = nrows, ncols
    padded = np.full((R + 2 * pad_r, C + 2 * pad_c), -2, np.int32)
    padded[pad_r : pad_r + R, pad_c : pad_c + C] = layout
    inside = (padded >= 0).astype(np.float32)
    score = np.zeros((padded.shape[0] - nrows + 1, padded.shape[1] - ncols + 1), np.float32)
    premium_hits = np.zeros_like(score)
    premium_total = 0.0
    for k in range(n_classes):
        templ = (lattice == k).astype(np.float32)
        count = float(templ.sum())
        if count == 0:
            continue
        hit = cv2.matchTemplate((padded == k).astype(np.float32), templ, cv2.TM_CCORR)
        if k == plain:
            inside_hits = cv2.matchTemplate(inside, templ, cv2.TM_CCORR)
            score += hit - (inside_hits - hit)
        else:
            score += 3.0 * (hit - (count - hit))
            premium_hits += hit
            premium_total += count
    if premium_total < 4:
        return None
    flat = score.ravel()
    order = np.argsort(-flat)
    by, bx = np.unravel_index(int(order[0]), score.shape)
    second = float(flat[order[1]]) if len(order) > 1 else -np.inf
    match_rate = float(premium_hits[by, bx]) / premium_total
    margin = (float(flat[order[0]]) - second) / (3.0 * premium_total)
    return int(by) - pad_r, int(bx) - pad_c, match_rate, margin


def classify_pixels(lab: np.ndarray, palette_lab: np.ndarray) -> np.ndarray:
    d = np.linalg.norm(lab[:, :, None, :] - palette_lab[None, None, :, :], axis=3)
    cls = np.argmin(d, axis=2).astype(np.int32)
    cls[np.min(d, axis=2) > COLOR_MATCH] = -1
    return cls


def locate_board(frame: np.ndarray, profile: Profile, work_width: int = 1800) -> BoardView:
    keys, palette_lab = _palette(profile)
    H, W = frame.shape[:2]
    s = min(1.0, work_width / W)
    small = cv2.resize(frame, (round(W * s), round(H * s)), interpolation=cv2.INTER_AREA)
    lab = to_lab(small)
    alias = _aliases(palette_lab)
    cls = classify_pixels(lab, palette_lab)
    cls = np.where(cls >= 0, alias[np.maximum(cls, 0)], -1).astype(np.int32)
    mask = cls >= 0
    if mask.mean() < 0.03:
        raise BoardNotFound("no board colors in view")

    ys, xs = np.nonzero(mask)
    y0, y1 = np.percentile(ys, [1, 99]).astype(int)
    x0, x1 = np.percentile(xs, [1, 99]).astype(int)
    chroma = np.hypot(lab[:, :, 1], lab[:, :, 2])
    white = ((lab[:, :, 0] > 93) & (chroma < 7)).astype(np.float32)
    region = np.zeros_like(white)
    region[y0 : y1 + 1, x0 : x1 + 1] = 1
    near_board = cv2.dilate(mask.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(np.float32)
    lines = white * near_board * region
    px = lines.sum(axis=0) / max(1, y1 - y0)
    py = lines.sum(axis=1) / max(1, x1 - x0)
    if px.max() < 0.2 or py.max() < 0.2:
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)
        px = (np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)) * mask)[y0 : y1 + 1].sum(axis=0)
        py = (np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)) * mask)[:, x0 : x1 + 1].sum(axis=1)
    layout = alias[_layout_classes(profile, keys)].astype(np.int32)
    plain = int(alias[keys.index(".")])
    best = None
    for pitch_c, fx, fy, lattice in _fit_lattices(cls, len(keys), px, py, min(x1 - x0, y1 - y0)):
        if int((lattice >= 0).sum()) < MIN_CELLS:
            continue
        lattice = _drop_blocks(lattice, layout, plain)
        reg = _register(lattice, layout, plain, len(keys))
        if reg is None:
            continue
        quality = reg[2] + reg[3]
        if best is None or quality > best[0]:
            best = (quality, pitch_c, fx, fy, lattice, reg)
    if best is None:
        raise BoardNotFound("too few board squares visible")
    _, pitch, ox, oy, lattice, (r0, c0, match_rate, margin) = best
    used = int((lattice >= 0).sum())
    if match_rate < 0.6 or margin < 0.25:
        raise BoardNotFound(
            f"board pattern not recognized (match {match_rate:.2f}, margin {margin:.2f})"
        )

    grid = Grid(
        (ox - c0 * pitch) / s,
        (oy - r0 * pitch) / s,
        pitch / s,
        pitch / s,
        profile.height,
        profile.width,
        match_rate,
    )
    visible = np.zeros((profile.height, profile.width), bool)
    tol = 0.02 * grid.pitch_x
    for r in range(profile.height):
        for c in range(profile.width):
            x, y, w, h = grid.cell_rect(r, c)
            visible[r, c] = x >= -tol and y >= -tol and x + w <= W + tol and y + h <= H + tol
    return BoardView(grid, visible, match_rate, margin, used)

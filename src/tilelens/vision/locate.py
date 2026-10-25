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
    extent: tuple[int, int, int, int] | None = None  # first and last row and column of the board
    canvas: tuple[float, float, float, float] = (0.0, 0.0, 1e9, 1e9)
    span: tuple[int, int, int, int] | None = None  # the stretch of squares that was measured

    @property
    def visible_count(self) -> int:
        return int(self.visible.sum())

    @property
    def full(self) -> bool:
        r0, c0, r1, c1 = self.extent or (0, 0, *(n - 1 for n in self.visible.shape))
        return bool(self.visible[r0 : r1 + 1, c0 : c1 + 1].all())


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
    raw_layout = profile.layout_chars or profile.raw.get("board", {}).get("layout", [])
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


def _register(lattice, layout, plain, n_classes, period=None):
    """Offsets (layout index of lattice cell 0,0) that match the premium squares.

    With a repeating layout the same pattern fits at offsets one period apart. Those are
    returned together (best first) and the margin is measured against the best offset that is
    not one of them, so a repeating board is not mistaken for an unclear one.
    """
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
    best = int(np.argmax(flat))
    by, bx = np.unravel_index(best, score.shape)
    top = float(flat[best])
    candidates = [(int(by) - pad_r, int(bx) - pad_c)]
    if period is not None:
        pr, pc = period
        ys, xs = np.mgrid[0 : score.shape[0], 0 : score.shape[1]]
        same = ((ys - by) % pr == 0) & ((xs - bx) % pc == 0)
        near = same & (score >= top - 3.0)
        found = np.argwhere(near)
        candidates = [(int(y) - pad_r, int(x) - pad_c) for y, x in found]
        candidates.sort(key=lambda o: o != (int(by) - pad_r, int(bx) - pad_c))
        others = np.where(same, -np.inf, score).ravel()
    else:
        others = flat.copy()
        others[best] = -np.inf
    second = float(others.max()) if others.size else -np.inf
    match_rate = float(premium_hits[by, bx]) / premium_total
    margin = (top - second) / (3.0 * premium_total)
    return candidates, match_rate, margin


def classify_pixels(lab: np.ndarray, palette_lab: np.ndarray) -> np.ndarray:
    d = np.linalg.norm(lab[:, :, None, :] - palette_lab[None, None, :, :], axis=3)
    cls = np.argmin(d, axis=2).astype(np.int32)
    cls[np.min(d, axis=2) > COLOR_MATCH] = -1
    return cls


@dataclass
class BoardHint:
    """What is already known about the board, to settle which copy of a repeating pattern a
    view shows: where tiles were last seen (layout indices)."""

    tiles: frozenset = frozenset()


STRICT_PLAIN = 4.5  # Lab distance within which a square shows its own plain color
BACKGROUND = 6.0  # Lab distance within which a square shows the board's background
STRICT_PREMIUM = 18.0  # premium squares carry a label, which shifts their average color
STAR_MIN, STAR_MAX = 0.03, 0.55


def _canvas(mask: np.ndarray, scale: float, W: int, H: int) -> tuple[float, float, float, float]:
    """Bounding box (full-frame pixels) of the area where board colors are common."""
    colfrac, rowfrac = mask.mean(axis=0), mask.mean(axis=1)
    # relative to the strongest line, so a beige video tile below the canvas is left out
    cols = colfrac > max(0.05, 0.35 * colfrac.max())
    rows = rowfrac > max(0.05, 0.35 * rowfrac.max())
    if not cols.any() or not rows.any():
        return 0.0, 0.0, float(W), float(H)
    xs, ys = np.nonzero(cols)[0], np.nonzero(rows)[0]
    return (
        float(xs.min()) / scale,
        float(ys.min()) / scale,
        float(xs.max() + 1) / scale,
        float(ys.max() + 1) / scale,
    )


def _survey(small, lab, palette_lab, plain, ox, oy, pitch, shape):
    """Per lattice cell: does it show an empty square's own color, and how violet is it."""
    rows, cols = shape
    H, W = lab.shape[:2]
    xs = np.arange(ox, W - pitch, pitch)[:cols]
    ys = np.arange(oy, H - pitch, pitch)[:rows]
    inset = 0.15
    xa = np.round(xs + inset * pitch).astype(int)
    xb = np.round(xs + (1 - inset) * pitch).astype(int)
    ya = np.round(ys + inset * pitch).astype(int)
    yb = np.round(ys + (1 - inset) * pitch).astype(int)
    XA, YA = np.meshgrid(xa, ya)
    XB, YB = np.meshgrid(xb, yb)
    area = np.maximum(1, (XB - XA) * (YB - YA)).astype(np.float32)

    def mean_of(channel):
        ii = cv2.integral(channel.astype(np.float64))
        return (ii[YB, XB] - ii[YA, XB] - ii[YB, XA] + ii[YA, XA]) / area

    mean = np.stack([mean_of(lab[:, :, k]) for k in range(3)], axis=-1)
    d_all = np.linalg.norm(mean[:, :, None, :] - palette_lab[None, None, :, :], axis=3)
    nearest = d_all.argmin(axis=2)
    dist = d_all.min(axis=2)
    limit = np.where(nearest == plain, STRICT_PLAIN, STRICT_PREMIUM)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    violet = ((hsv[:, :, 0] >= 118) & (hsv[:, :, 0] <= 158) & (hsv[:, :, 1] >= 70)
              & (hsv[:, :, 2] >= 90))  # fmt: skip
    frac = mean_of(violet.astype(np.float32))
    return dist < limit, frac


def _dense_run(counts: np.ndarray) -> tuple[int, int]:
    """The stretch of rows (or columns) that holds the board: where empty squares are common.

    A few stray squares elsewhere (a panel's plain-colored corner) do not extend it. A line
    with few squares (something covers part of it) belongs to the board, and so does a line
    with none if a dense one follows it within two lines; a sparse stretch that never gets
    dense again does not."""
    top = float(counts.max())
    peak = int(counts.argmax())
    sparse = max(2.0, 0.2 * top)
    dense = 0.5 * top

    def walk(step):
        edge, i = peak, peak + step
        while 0 <= i < len(counts):
            if counts[i] >= sparse:
                edge = i
            elif not any(
                0 <= i + k * step < len(counts) and counts[i + k * step] >= dense for k in (1, 2)
            ):
                break
            i += step
        return edge

    return walk(-1), walk(1)


def _star_cells(strict, frac):
    """Cells with a little violet that are not part of a large violet panel."""
    out = []
    big = frac >= 0.55
    near_big = cv2.dilate(big.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
    for i, j in np.argwhere((frac >= STAR_MIN) & (frac <= STAR_MAX) & ~near_big):
        out.append((int(i), int(j)))
    return out


def _choose_offset(cands, strict, frac, profile, hint, board_box):
    """Among offsets that fit equally well because the pattern repeats, the one the board's
    content points to: tiles seen before, the star on the start square, a tile on it."""
    rows, cols = strict.shape
    stars = _star_cells(strict, frac)
    starts = set(profile.start_squares)
    known = hint.tiles if hint is not None else frozenset()
    center = ((profile.height - 1) / 2, (profile.width - 1) / 2)
    rmin, rmax, cmin, cmax = board_box
    best = None
    for r0, c0 in cands:
        if (
            r0 + rmin < 0
            or c0 + cmin < 0
            or r0 + rmax >= profile.height
            or c0 + cmax >= profile.width
        ):
            continue
        sc = 0.0
        for r, c in known:
            i, j = r - r0, c - c0
            if 0 <= i < rows and 0 <= j < cols:
                sc += 1.0 if not strict[i, j] else -2.0
        if not known:
            for i, j in stars:
                sc += 6.0 if (i + r0, j + c0) in starts else -3.0
            for sr, scol in starts:
                i, j = sr - r0, scol - c0
                if 0 <= i < rows and 0 <= j < cols:
                    sc += 1.0 if not strict[i, j] else -1.0
        mid = ((r0 + rows / 2) - center[0]) ** 2 + ((c0 + cols / 2) - center[1]) ** 2
        sc -= 0.001 * mid
        if best is None or sc > best[0]:
            best = (sc, (r0, c0))
    return best[1] if best else cands[0]


def _measure(lab, scale, grid, canvas, profile, palette_lab, keys):
    """The board's extent (first and last row and column) from how squares look on screen.

    A square counts when its average color is its own empty color. The extent is the dense
    stretch of such squares; a side whose neighbouring line is off screen (or outside the
    game's canvas) is open, since the board may go on there, and a side whose neighbouring line
    is visible and not board is the board's edge.
    """
    H, W = lab.shape[:2]
    rows, cols = profile.height, profile.width
    pitch = grid.pitch_x * scale
    xs = (grid.x0 + np.arange(cols) * grid.pitch_x) * scale
    ys = (grid.y0 + np.arange(rows) * grid.pitch_y) * scale
    inset = 0.15
    xa, xb = (
        np.round(xs + inset * pitch).astype(int),
        np.round(xs + (1 - inset) * pitch).astype(int),
    )
    ya, yb = (
        np.round(ys + inset * pitch).astype(int),
        np.round(ys + (1 - inset) * pitch).astype(int),
    )
    cx0, cy0, cx1, cy1 = (v * scale for v in canvas)
    tol = 0.02 * pitch
    col_ok = (xa >= 0) & (xb <= W) & (xs >= cx0 - tol) & (xs + pitch <= cx1 + tol)
    row_ok = (ya >= 0) & (yb <= H) & (ys >= cy0 - tol) & (ys + pitch <= cy1 + tol)
    xa_c, xb_c = np.clip(xa, 0, W), np.clip(xb, 0, W)
    ya_c, yb_c = np.clip(ya, 0, H), np.clip(yb, 0, H)
    XA, YA = np.meshgrid(xa_c, ya_c)
    XB, YB = np.meshgrid(xb_c, yb_c)
    area = np.maximum(1, (XB - XA) * (YB - YA)).astype(np.float32)
    mean = np.zeros((rows, cols, 3), np.float32)
    for k in range(3):
        ii = cv2.integral(lab[:, :, k].astype(np.float64))
        mean[:, :, k] = (ii[YB, XB] - ii[YA, XB] - ii[YB, XA] + ii[YA, XA]) / area
    classes = _layout_classes(profile, keys)
    d = np.linalg.norm(mean - palette_lab[classes], axis=2)
    limit = np.where(classes == keys.index("."), STRICT_PLAIN, STRICT_PREMIUM)
    on_screen = row_ok[:, None] & col_ok[None, :]
    strict = (d < limit) & on_screen
    if not strict.any():
        whole = (0, 0, rows - 1, cols - 1)
        return whole, whole
    rlo, rhi = _dense_run(strict.sum(axis=1))
    clo, chi = _dense_run(strict.sum(axis=0))
    # the background around the board: what squares far outside it look like. A side is the
    # board's edge only when the line just beyond it is that background; a line that is
    # covered, off screen or of another color leaves the board open there.
    rr, cc = np.mgrid[0:rows, 0:cols]
    far = on_screen & ~strict & ((rr < rlo - 3) | (rr > rhi + 3) | (cc < clo - 3) | (cc > chi + 3))
    bg = np.median(mean[far], axis=0) if int(far.sum()) >= 12 else None

    def is_background(cells):
        cells = cells & on_screen
        if bg is None or int(cells.sum()) < 3:
            return False
        close = np.linalg.norm(mean[cells] - bg, axis=1) < BACKGROUND
        return float(close.mean()) >= 0.6

    in_cols = (cc >= clo) & (cc <= chi)
    in_rows = (rr >= rlo) & (rr <= rhi)
    top_open = rlo == 0 or not is_background(in_cols & (rr == rlo - 1))
    bottom_open = rhi == rows - 1 or not is_background(in_cols & (rr == rhi + 1))
    left_open = clo == 0 or not is_background(in_rows & (cc == clo - 1))
    right_open = chi == cols - 1 or not is_background(in_rows & (cc == chi + 1))
    bounds = (
        0 if top_open else rlo,
        0 if left_open else clo,
        rows - 1 if bottom_open else rhi,
        cols - 1 if right_open else chi,
    )
    return bounds, (rlo, clo, rhi, chi)


def measure_extent(frame: np.ndarray, profile: Profile, view: BoardView, work_width: int = 1200):
    """Re-measure the board's extent in a frame where it has not moved (it may have grown)."""
    keys, palette_lab = _palette(profile)
    H, W = frame.shape[:2]
    s = min(1.0, work_width / W)
    small = cv2.resize(frame, (round(W * s), round(H * s)), interpolation=cv2.INTER_AREA)
    return _measure(to_lab(small), s, view.grid, view.canvas, profile, palette_lab, keys)


def with_extent(view: BoardView, measured, profile: Profile) -> BoardView:
    """The same view with its visible squares recomputed for a new measurement.

    Only squares in the stretch where empty squares were found are read; the bounds say where
    the board ends, which can lie beyond what is visible."""
    extent, span = measured
    grid = view.grid
    cx0, cy0, cx1, cy1 = view.canvas
    tol = 0.02 * grid.pitch_x
    visible = np.zeros((profile.height, profile.width), bool)
    for r in range(span[0], span[2] + 1):
        for c in range(span[1], span[3] + 1):
            x, y, w, h = grid.cell_rect(r, c)
            visible[r, c] = (
                x >= cx0 - tol and y >= cy0 - tol and x + w <= cx1 + tol and y + h <= cy1 + tol
            )
    return BoardView(
        grid,
        visible,
        view.match_rate,
        view.margin,
        view.cells_used,
        tuple(extent),
        view.canvas,
        tuple(span),
    )


def locate_board(
    frame: np.ndarray, profile: Profile, work_width: int = 1200, hint: BoardHint | None = None
) -> BoardView:
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
    plain_key = keys.index(".")
    best = None
    for pitch_c, fx, fy, lattice in _fit_lattices(cls, len(keys), px, py, min(x1 - x0, y1 - y0)):
        if int((lattice >= 0).sum()) < MIN_CELLS:
            continue
        # only squares that show an empty square's own color take part: the background around
        # the board and anything lying over it must not be matched against the layout
        strict, _ = _survey(small, lab, palette_lab, plain_key, fx, fy, pitch_c, lattice.shape)
        # plain squares must show the plain color exactly (the background around the board
        # looks almost the same); premium squares are recognised by their own color
        lattice = np.where(strict | (lattice != plain), lattice, -1)
        if int((lattice >= 0).sum()) < MIN_CELLS:
            continue
        lattice = _drop_blocks(lattice, layout, plain)
        reg = _register(lattice, layout, plain, len(keys), profile.period)
        if reg is None:
            continue
        quality = reg[1] + reg[2]
        if best is None or quality > best[0]:
            best = (quality, pitch_c, fx, fy, lattice, reg)
    if best is None:
        raise BoardNotFound("too few board squares visible")
    _, pitch, ox, oy, lattice, (cands, match_rate, margin) = best
    used = int((lattice >= 0).sum())
    if match_rate < 0.6 or margin < 0.25:
        raise BoardNotFound(
            f"board pattern not recognized (match {match_rate:.2f}, margin {margin:.2f})"
        )
    strict, frac = _survey(small, lab, palette_lab, plain_key, ox, oy, pitch, lattice.shape)
    if strict.any():
        rlo, rhi = _dense_run(strict.sum(axis=1))
        clo, chi = _dense_run(strict.sum(axis=0))
        box = (rlo, rhi, clo, chi)
    else:
        box = (0, lattice.shape[0] - 1, 0, lattice.shape[1] - 1)
    r0, c0 = _choose_offset(cands, strict, frac, profile, hint, box)
    canvas = _canvas(mask, s, W, H)

    grid = Grid(
        (ox - c0 * pitch) / s,
        (oy - r0 * pitch) / s,
        pitch / s,
        pitch / s,
        profile.height,
        profile.width,
        match_rate,
    )
    # the board's extent: measured on the squares themselves, so a board that has grown, or
    # that is hidden in part, is judged by what is there rather than by the layout alone
    extent = _measure(lab, s, grid, canvas, profile, palette_lab, keys)
    base = BoardView(
        grid, np.zeros((profile.height, profile.width), bool), match_rate, margin, used
    )
    base.canvas = canvas
    return with_extent(base, extent, profile)

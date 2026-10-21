from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class Grid:
    x0: float
    y0: float
    pitch_x: float
    pitch_y: float
    rows: int
    cols: int
    score: float = 0.0

    def cell_rect(self, row: int, col: int, inset: float = 0.0) -> tuple[int, int, int, int]:
        x = self.x0 + col * self.pitch_x + inset * self.pitch_x
        y = self.y0 + row * self.pitch_y + inset * self.pitch_y
        w = self.pitch_x * (1 - 2 * inset)
        h = self.pitch_y * (1 - 2 * inset)
        return (round(x), round(y), max(1, round(w)), max(1, round(h)))

    def cell_center(self, row: int, col: int) -> tuple[float, float]:
        return (self.x0 + (col + 0.5) * self.pitch_x, self.y0 + (row + 0.5) * self.pitch_y)

    @property
    def rect(self) -> tuple[int, int, int, int]:
        return (
            round(self.x0),
            round(self.y0),
            round(self.cols * self.pitch_x),
            round(self.rows * self.pitch_y),
        )

    def scaled(self, fx: float, fy: float | None = None) -> Grid:
        fy = fx if fy is None else fy
        return Grid(
            self.x0 * fx,
            self.y0 * fy,
            self.pitch_x * fx,
            self.pitch_y * fy,
            self.rows,
            self.cols,
            self.score,
        )

    def to_dict(self) -> dict:
        return {
            "x0": self.x0,
            "y0": self.y0,
            "pitch_x": self.pitch_x,
            "pitch_y": self.pitch_y,
            "rows": self.rows,
            "cols": self.cols,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Grid:
        return cls(d["x0"], d["y0"], d["pitch_x"], d["pitch_y"], d["rows"], d["cols"])


class GridNotFound(RuntimeError):
    pass


def _edge_profiles(gray: np.ndarray, y_range=None, x_range=None):
    g = gray.astype(np.float32)
    dx = np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3))
    dy = np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3))
    if y_range is not None:
        dx = dx[y_range[0] : y_range[1]]
    if x_range is not None:
        dy = dy[:, x_range[0] : x_range[1]]
    px = dx.sum(axis=0)
    py = dy.sum(axis=1)
    return px, py


def _smooth(profile: np.ndarray, sigma: float) -> np.ndarray:
    k = max(1, int(sigma * 3))
    xs = np.arange(-k, k + 1, dtype=np.float32)
    kernel = np.exp(-(xs**2) / (2 * sigma**2))
    kernel /= kernel.sum()
    return np.convolve(profile, kernel, mode="same")


def _fit_axis(profile, n, pitch_lo, pitch_hi, step=0.25, origin_range=None):
    """Find origin and pitch of n equal cells whose inner boundaries carry the most edge energy."""
    length = len(profile)
    prof = _smooth(profile, max(1.2, 0.07 * pitch_lo))
    prof = prof / (prof.mean() + 1e-6)
    xs = np.arange(length, dtype=np.float32)
    k = np.arange(1, n, dtype=np.float32)
    km = np.arange(n, dtype=np.float32) + 0.5
    best = (-np.inf, 0.0, 0.0)
    mid = (n - 1) // 2

    def score_of(origins, pitch):
        on = np.interp(origins[:, None] + k[None, :] * pitch, xs, prof)
        off = np.interp(origins[:, None] + km[None, :] * pitch, xs, prof)
        on_sorted = np.sort(on, axis=1)
        on_capped = np.minimum(on, on_sorted[:, mid : mid + 1] * 1.5)
        return on_capped.mean(axis=1) - np.median(off, axis=1) + 0.5 * on_sorted[:, (n - 1) // 4]

    pitch = pitch_lo
    while pitch <= pitch_hi + 1e-9:
        span = n * pitch
        lo, hi = 0.0, length - 1 - span
        if origin_range is not None:
            lo, hi = max(lo, origin_range[0]), min(hi, origin_range[1])
        if hi >= lo:
            origins = np.arange(lo, hi + 0.5, 0.5, dtype=np.float32)
            score = score_of(origins, pitch)
            i = int(np.argmax(score))
            if score[i] > best[0]:
                best = (float(score[i]), float(origins[i]), float(pitch))
        pitch += step
    _, origin, pitch = best
    if np.isfinite(best[0]) and origin_range is None:
        # The board border can pass for an inner boundary; prefer the shift whose two outer
        # boundaries also carry edges.
        cands = [
            origin + j * pitch
            for j in (-1, 0, 1)
            if 0 <= origin + j * pitch <= length - 1 - n * pitch
        ]
        outer = [np.interp([c, c + n * pitch], xs, prof).min() for c in cands]
        inner = score_of(np.array(cands, np.float32), pitch)
        total = inner + np.array(outer)
        origin = cands[int(np.argmax(total))]
    return best[0], origin, pitch


def detect_grid(
    img: np.ndarray,
    rows: int,
    cols: int,
    min_pitch: float = 10.0,
    hint: tuple[float, float, float, float] | None = None,
) -> Grid:
    """Locate a rows x cols board of square cells by its periodic cell boundaries.

    `hint` is a rough (x, y, w, h) of the grid, for example a rectangle the user dragged;
    the fit then stays within about half a cell of it.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    H, W = gray.shape
    if hint is not None:
        hx, hy, hw, hh = hint
        p_est = (hw / cols + hh / rows) / 2
        p_lo, p_hi = max(min_pitch, p_est * 0.9), p_est * 1.1
        rx = (hx - 0.45 * p_est, hx + 0.45 * p_est)
        ry = (hy - 0.45 * p_est, hy + 0.45 * p_est)
    else:
        p_lo, p_hi = min_pitch, min(W / cols, H / rows)
        rx = ry = None
    if p_hi < min_pitch:
        raise GridNotFound("image is too small for the board")
    px, _ = _edge_profiles(gray)
    sx, ox, pitch_x = _fit_axis(px, cols, p_lo, p_hi, origin_range=rx)
    xr = (max(0, int(ox)), min(W, int(ox + cols * pitch_x) + 1))
    _, py = _edge_profiles(gray, x_range=xr)
    sy, oy, pitch_y = _fit_axis(py, rows, pitch_x * 0.97, pitch_x * 1.03, 0.1, ry)
    yr = (max(0, int(oy)), min(H, int(oy + rows * pitch_y) + 1))
    px2, _ = _edge_profiles(gray, y_range=yr)
    keep_lo = max(0, int(ox - pitch_x * 0.6))
    keep_hi = min(W, int(ox + cols * pitch_x + pitch_x * 0.6))
    px2[:keep_lo] = 0
    px2[keep_hi:] = 0
    sx, ox, pitch_x = _fit_axis(px2, cols, pitch_y * 0.97, pitch_y * 1.03, 0.1, rx)
    if not (np.isfinite(sx) and np.isfinite(sy)) or min(sx, sy) <= 0.3:
        raise GridNotFound("no regular grid found")
    return Grid(ox, oy, pitch_x, pitch_y, rows, cols, min(sx, sy))

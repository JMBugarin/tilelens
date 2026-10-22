"""Zoomed, panned and partly covered views of a synthetic board, with ground truth.

Mimics games that let the player zoom and pan the board while the rack, timers and score panels
stay fixed on top of it.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

import cv2
import numpy as np

from tilelens.rules.profile import Appearance, Profile
from tilelens.synth.render import Scene, _premium_color, _text, layout, render
from tilelens.synth.themes import THEMES, Theme

Rect = tuple[int, int, int, int]


def appearance_for(theme: Theme | str, profile: Profile) -> Appearance:
    theme = THEMES[theme] if isinstance(theme, str) else theme
    squares = {".": tuple(theme.square)}
    legend = profile.raw.get("board", {}).get("legend", {})
    for ch in legend:
        for r in range(profile.height):
            for c in range(profile.width):
                if profile.raw["board"]["layout"][r][c] == ch:
                    squares[ch] = tuple(_premium_color(theme, profile.premium_at(r, c)))
                    break
            if ch in squares:
                break
    return Appearance(squares, None, tuple(theme.tile))


def with_appearance(profile: Profile, theme: Theme | str) -> Profile:
    from dataclasses import replace

    return replace(profile, appearance=appearance_for(theme, profile))


@dataclass
class ViewSpec:
    size: tuple[int, int] = (960, 640)
    zoom: float = 1.0
    center: tuple[float, float] = (0.5, 0.5)
    panels: list[tuple[Rect, tuple[int, int, int], str]] = field(default_factory=list)
    rack_height: float = 0.11


def _board_image(scene: Scene) -> tuple[np.ndarray, dict]:
    board_only = Scene(
        profile=scene.profile,
        tiles=scene.tiles,
        rack=[],
        theme=scene.theme,
        cell=scene.cell,
        header_text="",
        timer="",
        chat=[],
    )
    return render(board_only)


def _rack_strip(scene: Scene) -> np.ndarray:
    img, labels = render(scene)
    x, y, w, h = labels["geometry"]["rack"]
    return img[y : y + h, x : x + w].copy()


def render_view(scene: Scene, spec: ViewSpec) -> tuple[np.ndarray, dict]:
    theme = scene.theme_obj
    full, labels = _board_image(scene)
    geo = layout(scene.profile, scene.cell)
    W, H = spec.size
    bx, by, bw, bh = geo.board
    cx = bx + spec.center[0] * bw
    cy = by + spec.center[1] * bh
    base = min(W / bw, H / bh) * 0.95
    scale = base * spec.zoom
    # output pixel = (full pixel - (cx, cy)) * scale + (W/2, H/2)
    M = np.array([[scale, 0, W / 2 - cx * scale], [0, scale, H / 2 - cy * scale]], np.float32)
    out = cv2.warpAffine(
        full,
        M,
        (W, H),
        flags=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=tuple(int(v) for v in theme.window),
    )
    pitch = geo.pitch * scale
    x0 = geo.origin[0] * scale + M[0, 2]
    y0 = geo.origin[1] * scale + M[1, 2]

    covers: list[Rect] = []
    strip = _rack_strip(scene)
    sh = max(8, round(H * spec.rack_height))
    sw = round(strip.shape[1] * sh / strip.shape[0])
    sx, sy = (W - sw) // 2, H - sh - round(H * 0.02)
    out[sy : sy + sh, sx : sx + sw] = cv2.resize(strip, (sw, sh), interpolation=cv2.INTER_AREA)
    covers.append((sx, sy, sw, sh))
    rack_slots = []
    rx, ry, rw, rh = geo.rack
    f = sh / strip.shape[0]
    for x, y, w, h in geo.rack_slots:
        rack_slots.append(
            (sx + round((x - rx) * f), sy + round((y - ry) * f), round(w * f), round(h * f))
        )

    for (px, py, pw, ph), color, text in spec.panels:
        cv2.rectangle(out, (px, py), (px + pw - 1, py + ph - 1), color, -1)
        if text:
            _text(out, text, (px + pw / 2, py + ph / 2), ph * 0.3, ("sans", 700), (255, 255, 255))
        covers.append((px, py, pw, ph))

    rows, cols = scene.profile.height, scene.profile.width
    visible = np.zeros((rows, cols), bool)
    for r in range(rows):
        for c in range(cols):
            cx0, cy0 = x0 + c * pitch, y0 + r * pitch
            inside = cx0 >= 0 and cy0 >= 0 and cx0 + pitch <= W and cy0 + pitch <= H
            hidden = any(
                cx0 < qx + qw and qx < cx0 + pitch and cy0 < qy + qh and qy < cy0 + pitch
                for qx, qy, qw, qh in covers
            )
            visible[r, c] = inside and not hidden
    labels = dict(labels)
    labels["view"] = {
        "x0": float(x0),
        "y0": float(y0),
        "pitch": float(pitch),
        "visible": visible.tolist(),
        "rack_slots": rack_slots,
    }
    labels["rack"] = [
        {"letter": None if t == "?" else t, "blank": t == "?"} if t else None for t in scene.rack
    ]
    return out, labels


def random_view(rng: random.Random, size=(960, 640)) -> ViewSpec:
    W, H = size
    zoom = rng.choice([1.0, 1.0, 1.4, 1.8, 2.4])
    center = (rng.uniform(0.3, 0.7), rng.uniform(0.3, 0.7)) if zoom > 1 else (0.5, 0.5)
    panels = []
    for _ in range(rng.randint(0, 3)):
        pw, ph = rng.randint(80, 260), rng.randint(40, 120)
        px, py = rng.randint(0, W - pw), rng.randint(0, H - ph)
        color = rng.choice([(150, 70, 120), (60, 60, 60), (240, 240, 240), (90, 140, 230)])
        panels.append(((px, py, pw, ph), color, rng.choice(["", "34", "Nice!", "Turn 3"])))
    return ViewSpec(size, zoom, center, panels)

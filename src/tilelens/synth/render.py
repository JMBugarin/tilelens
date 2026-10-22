from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

import cv2
import numpy as np

from tilelens.rules.profile import BLANK, Profile
from tilelens.synth.themes import THEMES, Theme

COMMITTED = "committed"
TENTATIVE = "tentative"
RECENT = "recent"
TILE_STATES = (COMMITTED, TENTATIVE, RECENT)

Rect = tuple[int, int, int, int]


@dataclass
class Scene:
    profile: Profile
    tiles: dict[tuple[int, int], tuple[str, bool, str]] = field(default_factory=dict)
    rack: list[str | None] = field(default_factory=list)
    theme: Theme | str = "meadow"
    cell: int = 36
    scale: float = 1.0
    header_text: str = "Ana 212   Bo 187"
    timer: str = "1:24"
    chat: list[str] = field(default_factory=lambda: ["Bo: nice one", "Ana: thanks"])
    drag: tuple[float, float, str] | None = None
    fade: dict[tuple[int, int], float] = field(default_factory=dict)

    @property
    def theme_obj(self) -> Theme:
        return THEMES[self.theme] if isinstance(self.theme, str) else self.theme


@dataclass
class Geometry:
    size: tuple[int, int]
    board: Rect
    origin: tuple[float, float]
    pitch: float
    rows: int
    cols: int
    rack: Rect
    rack_slots: list[Rect]
    header: Rect
    timer: Rect
    chat: Rect

    def cell_rect(self, row: int, col: int, gap: float = 0.0) -> Rect:
        x = self.origin[0] + col * self.pitch + gap / 2
        y = self.origin[1] + row * self.pitch + gap / 2
        s = self.pitch - gap
        return (round(x), round(y), round(s), round(s))

    def scaled(self, f: float) -> Geometry:
        def sr(r):
            return tuple(round(v * f) for v in r)

        return Geometry(
            (round(self.size[0] * f), round(self.size[1] * f)),
            sr(self.board),
            (self.origin[0] * f, self.origin[1] * f),
            self.pitch * f,
            self.rows,
            self.cols,
            sr(self.rack),
            [sr(r) for r in self.rack_slots],
            sr(self.header),
            sr(self.timer),
            sr(self.chat),
        )

    def to_dict(self) -> dict:
        return {
            "size": list(self.size),
            "board": list(self.board),
            "origin": list(self.origin),
            "pitch": self.pitch,
            "rows": self.rows,
            "cols": self.cols,
            "rack": list(self.rack),
            "rack_slots": [list(r) for r in self.rack_slots],
            "header": list(self.header),
            "timer": list(self.timer),
            "chat": list(self.chat),
        }


def layout(profile: Profile, cell: int) -> Geometry:
    m = cell
    border = max(2, cell // 6)
    header_h = round(cell * 1.4)
    board_w = profile.width * cell + 2 * border
    board_h = profile.height * cell + 2 * border
    chat_w = cell * 6
    rack_tile = round(cell * 1.2)
    rack_gap = round(cell * 0.18)
    n = profile.rack_size
    rack_inner = n * rack_tile + (n - 1) * rack_gap
    rack_w = rack_inner + 2 * rack_gap
    rack_h = rack_tile + 2 * rack_gap
    width = m + max(board_w, rack_w) + m + chat_w + m
    height = header_h + m + board_h + m + rack_h + m
    bx, by = m, header_h + m
    rx = bx + (board_w - rack_w) // 2
    ry = by + board_h + m
    slots = [
        (rx + rack_gap + i * (rack_tile + rack_gap), ry + rack_gap, rack_tile, rack_tile)
        for i in range(n)
    ]
    cx = bx + max(board_w, rack_w) + m
    return Geometry(
        size=(width, height),
        board=(bx, by, board_w, board_h),
        origin=(bx + border, by + border),
        pitch=float(cell),
        rows=profile.height,
        cols=profile.width,
        rack=(rx, ry, rack_w, rack_h),
        rack_slots=slots,
        header=(0, 0, width, header_h),
        timer=(width - m - cell * 3, round(header_h * 0.15), cell * 3, round(header_h * 0.7)),
        chat=(cx, by, chat_w, board_h),
    )


def _rounded(img, rect: Rect, radius: int, color, thickness=-1):
    x, y, w, h = rect
    r = max(0, min(radius, w // 2, h // 2))
    if r == 0:
        cv2.rectangle(img, (x, y), (x + w - 1, y + h - 1), color, thickness)
        return
    if thickness < 0:
        cv2.rectangle(img, (x + r, y), (x + w - 1 - r, y + h - 1), color, -1)
        cv2.rectangle(img, (x, y + r), (x + w - 1, y + h - 1 - r), color, -1)
        for cx, cy in (
            (x + r, y + r),
            (x + w - 1 - r, y + r),
            (x + r, y + h - 1 - r),
            (x + w - 1 - r, y + h - 1 - r),
        ):
            cv2.circle(img, (cx, cy), r, color, -1, cv2.LINE_AA)
    else:
        cv2.line(img, (x + r, y), (x + w - 1 - r, y), color, thickness, cv2.LINE_AA)
        cv2.line(img, (x + r, y + h - 1), (x + w - 1 - r, y + h - 1), color, thickness, cv2.LINE_AA)
        cv2.line(img, (x, y + r), (x, y + h - 1 - r), color, thickness, cv2.LINE_AA)
        cv2.line(img, (x + w - 1, y + r), (x + w - 1, y + h - 1 - r), color, thickness, cv2.LINE_AA)
        for (cx, cy), a in (
            ((x + r, y + r), 180),
            ((x + w - 1 - r, y + r), 270),
            ((x + r, y + h - 1 - r), 90),
            ((x + w - 1 - r, y + h - 1 - r), 0),
        ):
            cv2.ellipse(img, (cx, cy), (r, r), a, 0, 90, color, thickness, cv2.LINE_AA)


UI_FONT = ("sans", 400)


@lru_cache(maxsize=64)
def _face(name: str):
    return cv2.FontFace(name)


@lru_cache(maxsize=4096)
def _glyph_mask(text: str, font: tuple[str, int], size: int) -> np.ndarray:
    name, weight = font
    w = int(size * (len(text) + 1) * 1.2) + 20
    h = int(size * 2.2) + 10
    canvas = np.zeros((h, w), np.uint8)
    cv2.putText(canvas, text, (10, int(size * 1.5)), (255,), _face(name), size, weight)
    ys, xs = np.nonzero(canvas)
    if len(xs) == 0:
        return np.zeros((1, 1), np.uint8)
    return canvas[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1].copy()


def _text(img, text, center, height, font, color, thickness_ratio=None):
    """Draw text whose ink box is centered on `center`; `height` is roughly the cap height."""
    mask = _glyph_mask(text, font, max(4, round(height * 1.4)))
    mh, mw = mask.shape
    x0 = round(center[0] - mw / 2)
    y0 = round(center[1] - mh / 2)
    H, W = img.shape[:2]
    xa, ya = max(0, x0), max(0, y0)
    xb, yb = min(W, x0 + mw), min(H, y0 + mh)
    if xa >= xb or ya >= yb:
        return
    m = mask[ya - y0 : yb - y0, xa - x0 : xb - x0].astype(np.float32)[..., None] / 255.0
    roi = img[ya:yb, xa:xb].astype(np.float32)
    img[ya:yb, xa:xb] = (roi * (1 - m) + np.array(color, np.float32) * m).astype(np.uint8)


def _premium_label(p) -> str:
    if p.letter > 1 and p.word > 1:
        return f"{p.letter}{p.word}"
    if p.word > 1:
        return f"{p.word}W"
    return f"{p.letter}L"


def _premium_color(theme: Theme, p) -> tuple[int, int, int]:
    if p.name in theme.premium:
        return theme.premium[p.name]
    if p.word >= 3:
        return theme.premium["triple_word"]
    if p.word == 2 and p.letter == 1:
        return theme.premium["double_word"]
    if p.letter >= 3 and p.word == 1:
        return theme.premium["triple_letter"]
    if p.letter == 2 and p.word == 1:
        return theme.premium["double_letter"]
    return theme.premium["other"]


def _blend(a, b, t):
    return tuple(round(x * (1 - t) + y * t) for x, y in zip(a, b, strict=True))


def draw_tile(
    img,
    rect: Rect,
    letter: str | None,
    blank: bool,
    state: str,
    theme: Theme,
    profile: Profile,
    alpha: float = 1.0,
    square_fill=None,
    square_label: str | None = None,
):
    x, y, w, h = rect
    radius = round(w * theme.radius)
    fill = {COMMITTED: theme.tile, TENTATIVE: theme.tentative_tile, RECENT: theme.recent_tile}[
        state
    ]
    if square_fill is not None:
        fill = square_fill
    target = img
    if alpha < 1.0:
        target = img.copy()
    _rounded(target, rect, radius, theme.tile_edge)
    _rounded(target, (x + 1, y + 1, w - 2, max(1, h - 3)), radius, fill)
    if state == TENTATIVE:
        _rounded(target, rect, radius, theme.tentative_edge, max(2, w // 14))
    if letter:
        color = theme.blank_letter if blank else theme.letter
        _text(
            target,
            letter,
            (x + w * 0.47, y + h * 0.47),
            h * theme.letter_scale * 0.62,
            theme.font,
            color,
            theme.letter_thickness,
        )
        if theme.show_values and (not blank or theme.tile_takes_square):
            value = "0" if blank else str(profile.letter_values.get(letter, 0))
            vy = y + h * (0.2 if theme.value_top else 0.8)
            color = theme.blank_mark if blank else theme.value
            _text(target, value, (x + w * 0.82, vy), h * 0.16, UI_FONT, color, 0.12)
        if square_label:
            _text(
                target,
                square_label,
                (x + w * 0.2, y + h * 0.18),
                h * 0.11,
                UI_FONT,
                theme.value,
                0.1,
            )
    if alpha < 1.0:
        cv2.addWeighted(target, alpha, img, 1 - alpha, 0, dst=img)


def render(scene: Scene) -> tuple[np.ndarray, dict]:
    theme = scene.theme_obj
    p = scene.profile
    geo = layout(p, scene.cell)
    W, H = geo.size
    img = np.zeros((H, W, 3), np.uint8)
    img[:] = theme.window

    hx, hy, hw, hh = geo.header
    cv2.rectangle(img, (hx, hy), (hx + hw - 1, hy + hh - 1), theme.header, -1)
    _text(img, scene.header_text, (scene.cell * 4, hh / 2), hh * 0.32, UI_FONT, theme.header_text)
    tx, ty, tw, th = geo.timer
    _rounded(img, geo.timer, th // 3, _blend(theme.header, (0, 0, 0), 0.25))
    _text(img, scene.timer, (tx + tw / 2, ty + th / 2), th * 0.45, UI_FONT, theme.header_text)

    cx, cy, cw, ch = geo.chat
    _rounded(img, geo.chat, scene.cell // 4, _blend(theme.window, theme.header, 0.15))
    line_h = round(scene.cell * 0.6)
    for i, line in enumerate(scene.chat[-12:]):
        text = line[:28]
        mask = _glyph_mask(text, UI_FONT, max(6, round(scene.cell * 0.36)))
        _text(
            img,
            text,
            (cx + 10 + mask.shape[1] / 2, cy + 20 + i * line_h),
            mask.shape[0] / 1.4,
            UI_FONT,
            theme.header,
        )

    bx, by, bw, bh = geo.board
    cv2.rectangle(img, (bx, by), (bx + bw - 1, by + bh - 1), theme.grid, -1)
    gap = max(1.0, scene.cell * theme.gap)
    starts = set(p.start_squares)
    for r in range(p.height):
        for c in range(p.width):
            rect = geo.cell_rect(r, c, gap)
            prem = p.premium_at(r, c)
            color = _premium_color(theme, prem) if prem else theme.square
            x, y, w, h = rect
            cv2.rectangle(img, (x, y), (x + w - 1, y + h - 1), color, -1)
            if (r, c) in starts:
                pts = np.array(
                    [
                        [x + w // 2, y + 3],
                        [x + w - 4, y + h // 2],
                        [x + w // 2, y + h - 4],
                        [x + 3, y + h // 2],
                    ],
                    np.int32,
                )
                cv2.polylines(img, [pts], True, theme.premium_text, 1, cv2.LINE_AA)
            if prem and theme.premium_labels:
                _text(
                    img,
                    _premium_label(prem),
                    (x + w / 2, y + h / 2),
                    h * 0.26,
                    UI_FONT,
                    theme.premium_text,
                    0.1,
                )

    board_labels: list[list[dict | None]] = [[None] * p.width for _ in range(p.height)]
    for (r, c), (letter, blank, state) in sorted(scene.tiles.items()):
        rect = geo.cell_rect(r, c, gap * 0.6)
        alpha = scene.fade.get((r, c), 1.0)
        sq_fill = sq_label = None
        if theme.tile_takes_square:
            prem = p.premium_at(r, c)
            sq_fill = (250, 250, 250) if prem is None else _premium_color(theme, prem)
            sq_label = None if prem is None else _premium_label(prem)
        draw_tile(img, rect, letter, blank, state, theme, p, alpha, sq_fill, sq_label)
        board_labels[r][c] = {"letter": letter, "blank": blank, "state": state}

    rx, ry, rw, rh = geo.rack
    _rounded(img, geo.rack, scene.cell // 4, theme.rack_panel)
    rack_labels = []
    for i, slot in enumerate(geo.rack_slots):
        item = scene.rack[i] if i < len(scene.rack) else None
        if item is None:
            rack_labels.append(None)
            continue
        blank = item == BLANK
        draw_tile(img, slot, None if blank else item, False, COMMITTED, theme, p)
        rack_labels.append({"letter": None if blank else item, "blank": blank})

    if scene.drag is not None:
        dx, dy, letter = scene.drag
        s = round(scene.cell * 1.15)
        shadow = (round(dx - s / 2) + 4, round(dy - s / 2) + 5, s, s)
        _rounded(img, shadow, round(s * theme.radius), _blend(theme.window, (0, 0, 0), 0.35))
        draw_tile(
            img, (round(dx - s / 2), round(dy - s / 2), s, s), letter, False, COMMITTED, theme, p
        )

    if scene.scale != 1.0:
        interp = cv2.INTER_AREA if scene.scale < 1 else cv2.INTER_CUBIC
        img = cv2.resize(img, None, fx=scene.scale, fy=scene.scale, interpolation=interp)
        geo = geo.scaled(scene.scale)

    labels = {
        "theme": theme.name,
        "held_out": theme.held_out,
        "profile": p.name,
        "cell": scene.cell,
        "scale": scene.scale,
        "board": board_labels,
        "rack": rack_labels,
        "geometry": geo.to_dict(),
        "dragging": scene.drag is not None,
    }
    return img, labels

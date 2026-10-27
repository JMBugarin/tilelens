"""Where the results panel floats: beside the board, on the side with the most free room.

Pure geometry, no Qt widgets. Every rectangle is in the same screen coordinates (points) and may
be a tuple (x, y, w, h), a QRect or QRectF, a Region, or anything with x, y, width and height.
The panel size is the size of the glass, not of the window that carries its shadow.
"""

from __future__ import annotations

from dataclasses import dataclass

SIDES = ("right", "left", "below", "above")
DEFAULT_INSET = (24, 72)
EDGE = 8
STICK = 96.0
SCALE_MIN = 0.85
SCALE_MAX = 1.35
SCALE_REFERENCE = 900.0

Rect = tuple[float, float, float, float]


def _num(value) -> float:
    return float(value() if callable(value) else value)


def rect_of(obj) -> Rect | None:
    """(x, y, w, h) of whatever describes a rectangle; None passes through."""
    if obj is None:
        return None
    if isinstance(obj, tuple | list):
        x, y, w, h = obj
        return float(x), float(y), float(w), float(h)
    return tuple(_num(getattr(obj, name)) for name in ("x", "y", "width", "height"))  # type: ignore[return-value]


def size_of(obj) -> tuple[float, float]:
    if isinstance(obj, tuple | list):
        return float(obj[0]), float(obj[1])
    return _num(obj.width), _num(obj.height)


def point_of(obj) -> tuple[float, float]:
    if isinstance(obj, tuple | list):
        return float(obj[0]), float(obj[1])
    return _num(obj.x), _num(obj.y)


def union(*rects: Rect | None) -> Rect | None:
    found = [r for r in rects if r is not None and r[2] > 0 and r[3] > 0]
    if not found:
        return None
    x0 = min(r[0] for r in found)
    y0 = min(r[1] for r in found)
    x1 = max(r[0] + r[2] for r in found)
    y1 = max(r[1] + r[3] for r in found)
    return x0, y0, x1 - x0, y1 - y0


def overlap_area(a: Rect, b: Rect) -> float:
    w = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    h = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return max(0.0, w) * max(0.0, h)


def clamp_position(
    x: float, y: float, panel_size, screen_rect, margin: float = EDGE
) -> tuple[int, int]:
    """The top-left that keeps the panel inside the screen; a panel larger than the screen
    keeps its top-left corner visible."""
    w, h = size_of(panel_size)
    sx, sy, sw, sh = rect_of(screen_rect)
    lo_x, lo_y = sx + margin, sy + margin
    hi_x = max(lo_x, sx + sw - w - margin)
    hi_y = max(lo_y, sy + sh - h - margin)
    return round(min(max(x, lo_x), hi_x)), round(min(max(y, lo_y), hi_y))


@dataclass(frozen=True)
class Placement:
    x: int
    y: int
    side: str  # right, left, below, above, inside (nothing to avoid) or user (dragged)
    overlap: float = 0.0  # share of the panel that sits over the board or rack

    @property
    def point(self) -> tuple[int, int]:
        return self.x, self.y


def _candidate(side: str, obstacle: Rect, w: float, h: float, margin: float) -> tuple[float, float]:
    ox, oy, ow, oh = obstacle
    if side == "right":
        return ox + ow + margin, oy
    if side == "left":
        return ox - margin - w, oy
    if side == "below":
        return ox + ow / 2 - w / 2, oy + oh + margin
    return ox + ow / 2 - w / 2, oy - margin - h


def _room(side: str, obstacle: Rect, screen: Rect, margin: float) -> float:
    ox, oy, ow, oh = obstacle
    sx, sy, sw, sh = screen
    if side == "right":
        return sx + sw - (ox + ow) - margin
    if side == "left":
        return ox - sx - margin
    if side == "below":
        return sy + sh - (oy + oh) - margin
    return oy - sy - margin


def choose_placement(
    region,
    board_rect,
    rack_rect,
    panel_size,
    screen_rect,
    margin: float = 16,
    user_pos=None,
    prefer: str | None = None,
    stick: float = STICK,
) -> Placement:
    """Top-left of the panel for the current board.

    A dragged position (user_pos) wins and is only clamped to the screen. Otherwise the panel
    goes on the side of the board and rack with the most free room, with the order right, left,
    below, above breaking ties. `prefer` is the side used last time: it is kept while it is
    about as good as the best (within `stick` points of room), so a board that creeps a few
    points does not make the panel hop from one side to another. With no board in sight the
    panel sits in the upper right of the region.
    """
    w, h = size_of(panel_size)
    screen = rect_of(screen_rect)
    edge = min(float(margin), float(EDGE))
    if user_pos is not None:
        ux, uy = point_of(user_pos)
        x, y = clamp_position(ux, uy, (w, h), screen, edge)
        return Placement(x, y, "user")
    obstacle = union(rect_of(board_rect), rect_of(rack_rect))
    if obstacle is None:
        rx, ry, rw, _ = rect_of(region) or screen
        x, y = clamp_position(
            rx + rw - w - DEFAULT_INSET[0], ry + DEFAULT_INSET[1], (w, h), screen, edge
        )
        return Placement(x, y, "inside")
    area = max(1.0, w * h)
    scored = []
    for order, side in enumerate(SIDES):
        cx, cy = _candidate(side, obstacle, w, h, margin)
        x, y = clamp_position(cx, cy, (w, h), screen, edge)
        over = overlap_area((x, y, w, h), obstacle) / area
        slack = _room(side, obstacle, screen, margin) - (w if side in ("right", "left") else h)
        scored.append((round(over, 2), -slack, order, side, x, y, over))
    best = min(scored)
    if prefer in SIDES:
        kept = next(s for s in scored if s[3] == prefer)
        if kept[0] <= best[0] and -kept[1] >= -best[1] - stick:
            best = kept
    return Placement(best[4], best[5], best[3], best[6])


def choose_position(
    region,
    board_rect,
    rack_rect,
    panel_size,
    screen_rect,
    margin: float = 16,
    user_pos=None,
    prefer: str | None = None,
) -> tuple[int, int]:
    return choose_placement(
        region, board_rect, rack_rect, panel_size, screen_rect, margin, user_pos, prefer
    ).point


class PanelPlacer:
    """choose_placement with the memory of the side it chose last."""

    def __init__(self, stick: float = STICK):
        self.stick = stick
        self.side: str | None = None

    def reset(self) -> None:
        self.side = None

    def place(
        self,
        region,
        board_rect,
        rack_rect,
        panel_size,
        screen_rect,
        margin: float = 16,
        user_pos=None,
    ) -> Placement:
        found = choose_placement(
            region,
            board_rect,
            rack_rect,
            panel_size,
            screen_rect,
            margin,
            user_pos,
            prefer=self.side,
            stick=self.stick,
        )
        if found.side in SIDES:
            self.side = found.side
        return found


def panel_scale(region_height: float, current: float | None = None) -> float:
    """Size factor of the panel for the game's window: smaller for a small window, larger for
    a big one, in steps of 0.05 and kept while it is within half a step."""
    raw = max(SCALE_MIN, min(SCALE_MAX, float(region_height) / SCALE_REFERENCE))
    stepped = round(raw * 20) / 20
    if current is not None and abs(raw - current) < 0.03:
        return current
    return stepped


__all__ = [
    "DEFAULT_INSET",
    "SIDES",
    "PanelPlacer",
    "Placement",
    "choose_placement",
    "choose_position",
    "clamp_position",
    "overlap_area",
    "panel_scale",
    "rect_of",
    "union",
]

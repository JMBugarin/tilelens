"""Reading a board that was located from its colors (see locate.py).

Squares that are off screen or covered by interface elements are reported as unseen; the search
treats them as walls, so suggestions never depend on squares that cannot be seen.
"""

from __future__ import annotations

import time

import cv2
import numpy as np

from tilelens.rules.profile import BLANK, Profile
from tilelens.vision.cells import (
    CROP,
    PATCH,
    Blob,
    corner_mark_ink,
    crop,
    extract_glyph,
    find_tile_blobs,
    patch_features,
    to_lab,
)
from tilelens.vision.grid import Grid
from tilelens.vision.locate import (
    BoardNotFound,
    BoardView,
    _layout_classes,
    _palette,
    locate_board,
)
from tilelens.vision.recognizer import (
    COMMITTED,
    UNSEEN,
    Calibration,
    CalibrationError,
    CellRead,
    Proto,
    RackRead,
    Reading,
    Recognizer,
    premium_key,
)
from tilelens.vision.values import ValueVotes, tile_value

RING_OK = 16.0
UNIFORM = 9.0
BLANK_MARK = 18.0
BLANK_OFF_LINE = 15.0
BLANK_LETTER = 30.0
BLANK_CHROMA = 12.0
NEW_COLOR_SUPPORT = 3
NEW_COLOR = "tile color not seen yet"
TILE_BACKGROUND = 14.0
CORNER_COVERED = 30.0
EDGE_COVERED = 45.0
FIX_MATCH = 0.9
REMEMBER_MATCH = 0.96
UNMARKED_LETTER = 0.9
LABEL_INK = 25.0
LABEL_MATCH = 0.85
LABEL_CONTRAST = 35.0
LETTER_CONTRAST = 45.0
DEFAULT_RACK_ZONE = (0.0, 1.0, 0.35, 1.0)  # whole width: the game window can sit anywhere


def _expected_lab(profile: Profile) -> np.ndarray:
    keys, palette = _palette(profile)
    return palette[_layout_classes(profile, keys)]


WORK_PITCH = 44.0


def work_frame(img: np.ndarray, grid: Grid) -> tuple[np.ndarray, Grid]:
    """Downscale so squares are about WORK_PITCH pixels; reading cost no longer grows with zoom."""
    f = WORK_PITCH / grid.pitch_x
    if f >= 0.9:
        return img, grid
    small = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    return small, grid.scaled(f)


def cell_sample(img: np.ndarray, grid: Grid, r: int, c: int):
    cell = crop(img, grid.cell_rect(r, c))
    patch = cv2.resize(cell, (PATCH, PATCH), interpolation=cv2.INTER_AREA)
    feat = patch_features(to_lab(patch))
    inner = crop(img, grid.cell_rect(r, c, inset=0.06))
    return feat, cv2.resize(inner, (CROP, CROP), interpolation=cv2.INTER_AREA)


def rack_zone(profile: Profile) -> tuple[float, float, float, float]:
    app = profile.appearance
    return app.rack_zone if app and app.rack_zone else DEFAULT_RACK_ZONE


def _on_board_square(b: Blob, view: BoardView | None, expected, tile_lab) -> bool:
    """A board square that happens to share the rack tile color, seen through the rack zone."""
    if view is None or expected is None:
        return False
    g = view.grid
    x, y, w, h = b.rect
    cx, cy = x + w / 2, y + h / 2
    c = int((cx - g.x0) // g.pitch_x)
    r = int((cy - g.y0) // g.pitch_y)
    if not (0 <= r < g.rows and 0 <= c < g.cols):
        return False
    ccx, ccy = g.cell_center(r, c)
    near = abs(cx - ccx) < 0.15 * g.pitch_x and abs(cy - ccy) < 0.15 * g.pitch_y
    same_size = abs((w + h) / 2 - g.pitch_x) < 0.15 * g.pitch_x
    looks_alike = float(np.linalg.norm(expected[r, c] - tile_lab[:3])) < 25
    return near and same_size and looks_alike


def find_rack(
    img: np.ndarray,
    profile: Profile,
    tile_lab: np.ndarray,
    radius: float,
    view: BoardView | None = None,
    expected: np.ndarray | None = None,
) -> list[Blob]:
    """The row of rack tiles: same-sized tile-colored squares, side by side, in the rack zone."""
    H, W = img.shape[:2]
    x0, x1, y0, y1 = rack_zone(profile)
    band = (round(x0 * W), round(y0 * H), round(x1 * W), round(y1 * H))
    blobs = find_tile_blobs(img, band, tile_lab, radius, (0.01 * H, 0.14 * H))
    blobs = [b for b in blobs if not _on_board_square(b, view, expected, tile_lab)]
    if not blobs:
        return []
    rows: list[list[Blob]] = []
    for b in sorted(blobs, key=lambda b: b.rect[1] + b.rect[3] / 2):
        cy = b.rect[1] + b.rect[3] / 2
        for row in rows:
            ry = row[0].rect[1] + row[0].rect[3] / 2
            if abs(ry - cy) < 0.12 * row[0].rect[3]:
                row.append(b)
                break
        else:
            rows.append([b])
    best = []
    for row in rows:
        sizes = np.array([(b.rect[2] + b.rect[3]) / 2 for b in row])
        med = float(np.median(sizes))
        kept = [b for b, sz in zip(row, sizes, strict=True) if abs(sz - med) < 0.2 * med]
        if len(kept) > len(best):
            best = kept
    best.sort(key=lambda b: b.rect[0])
    return best[: profile.rack_size]


def _label_clusters(glyphs: list[np.ndarray]) -> list[np.ndarray]:
    """The most common shape among a premium type's glyphs is its printed label."""
    if len(glyphs) < 2:
        return []
    mat = np.array(glyphs)
    sims = mat @ mat.T
    support = (sims > LABEL_MATCH).sum(axis=1)
    i = int(np.argmax(support))
    if support[i] < max(2, 0.4 * len(glyphs)):
        return []
    members = [glyphs[j] for j in np.nonzero(sims[i] > LABEL_MATCH)[0]]
    return members[:4]


def _learn_tile_backgrounds(cal: Calibration, seen: list[tuple[np.ndarray, np.ndarray]]) -> None:
    """Keep tile colors seen on at least two squares, so a lone panel corner is not one."""
    colors = [col for col, _ in seen]
    for col in colors:
        support = sum(1 for o in colors if float(np.linalg.norm(o - col)) < TILE_BACKGROUND)
        if support >= 2:
            cal.add_tile_background(col)


def rack_tiles(frame: np.ndarray, profile: Profile, view: BoardView | None = None) -> list[Blob]:
    """Rack tiles found by the profile's rack tile color."""
    app = profile.appearance
    tile_lab = to_lab(np.array(app.rack_tile, np.uint8).reshape(1, 1, 3)).reshape(3)
    return find_rack(frame, profile, tile_lab, 22.0, view, _expected_lab(profile))


def calibrate_auto(frame: np.ndarray, profile: Profile, rack: str) -> Calibration:
    """Learn from one screen: the board is found from its colors; only the rack is typed."""
    app = profile.appearance
    if app is None or app.rack_tile is None:
        raise CalibrationError("the profile needs [appearance] squares and rack_tile")
    try:
        view = locate_board(frame, profile)
    except BoardNotFound as exc:
        raise CalibrationError(f"board not found: {exc}") from None
    grid = view.grid
    board_img, board_grid = work_frame(frame, grid)
    expected = _expected_lab(profile)
    keys = [
        [premium_key(profile, r, c) for c in range(profile.width)] for r in range(profile.height)
    ]
    blobs = rack_tiles(frame, profile, view)
    under_rack = _covered_by(grid, profile.height, profile.width, [b.rect for b in blobs])
    by_key: dict[str, list[np.ndarray]] = {}
    tile_backgrounds: list[np.ndarray] = []
    empty_feats: dict[str, list[np.ndarray]] = {}
    for r in range(profile.height):
        for c in range(profile.width):
            if not view.visible[r, c] or under_rack[r, c]:
                continue
            feat, crop_ = cell_sample(board_img, board_grid, r, c)
            if np.linalg.norm(feat[:3] - expected[r, c]) >= RING_OK:
                glyph = extract_glyph(crop_, feat)
                if glyph is not None and glyph.value_mark and not glyph.touches_edge:
                    tile_backgrounds.append((feat[:3], glyph.vec))
                continue
            glyph = extract_glyph(crop_, feat)
            if glyph is None:
                empty_feats.setdefault(keys[r][c], []).append(feat)
            else:
                by_key.setdefault(keys[r][c], []).append((glyph.vec, glyph.ink, glyph.value_mark))

    letters = [ch for ch in rack.upper() if not ch.isspace()]
    if len(blobs) != len(letters):
        raise CalibrationError(
            f"found {len(blobs)} rack tiles but {len(letters)} letters were typed"
        )
    rings, rack_glyphs, value_flags, blank_ink = [], [], [], None
    for b, ch in zip(blobs, letters, strict=True):
        rings.append(b.ring)
        if ch == BLANK:
            blank_ink = corner_mark_ink(b.crop, b.ring)
            continue
        glyph = extract_glyph(b.crop, b.ring)
        if glyph is None:
            raise CalibrationError(f"no letter found on rack tile '{ch}'")
        rack_glyphs.append((ch, glyph))
        value_flags.append(glyph.value_mark)

    sides = [(b.rect[2] + b.rect[3]) / 2 for b in blobs]
    xs0 = min(b.rect[0] for b in blobs)
    xs1 = max(b.rect[0] + b.rect[2] for b in blobs)
    ys0 = min(b.rect[1] for b in blobs)
    ys1 = max(b.rect[1] + b.rect[3] for b in blobs)
    side = float(np.median(sides))
    cal = Calibration(
        profile_fingerprint=profile.fingerprint(),
        image_size=(frame.shape[1], frame.shape[0]),
        grid=grid,
        rack_band=(int(xs0 - side), int(ys0 - side * 0.4), int(xs1 + side), int(ys1 + side * 0.4)),
        rack_tile=side,
        rack_proto=Proto.fit(rings, floor=6.0),
        empty={k: Proto.fit(v, floor=5.0) for k, v in empty_feats.items()},
        tiles=[Proto.fit(rings, COMMITTED, floor=8.0)],
        normal_ink=np.median([g.ink for _, g in rack_glyphs], axis=0).astype(np.float32)
        if rack_glyphs
        else np.zeros(3, np.float32),
        blank_ink=None,
        values_expected=bool(value_flags) and float(np.mean(value_flags)) > 0.8,
        auto=True,
        blank_value_ink=blank_ink,
    )
    for ch, glyph in rack_glyphs:
        cal.add_template(ch, glyph.vec, rack=True)
    _learn_tile_backgrounds(cal, tile_backgrounds)
    if not cal.tile_backgrounds:
        # a single tile counts when its letter clearly is one of the typed rack letters
        rack_vecs = [g.vec for _, g in rack_glyphs]
        for col, vec in tile_backgrounds:
            if rack_vecs and max(float(v @ vec) for v in rack_vecs) > 0.85:
                cal.add_tile_background(col)
    label_inks = []
    for key, items in by_key.items():
        if cal.values_expected:
            # a glyph with a point value is a tile in the square's own color, not a label
            items = [it for it in items if not it[2]]
        members = _label_clusters([v for v, _, _ in items])
        for vec in members:
            cal.add_empty_glyph(key, vec)
        if members:
            label_inks += [
                ink for v, ink, _ in items if any(float(v @ m) > LABEL_MATCH for m in members)
            ]
    if label_inks:
        cal.label_ink = np.median(np.array(label_inks), axis=0).astype(np.float32)
    return cal


def _corner_covered(crop_: np.ndarray, feat: np.ndarray) -> bool:
    """Part of the square is hidden under something of another color (a panel corner)."""
    size = crop_.shape[0]
    a, b = round(size * 0.14), round(size * 0.4)
    lab = to_lab(crop_)
    ring = feat[:3]
    corners = []
    for ys, xs in (
        (slice(a, b), slice(a, b)),
        (slice(a, b), slice(size - b, size - a)),
        (slice(size - b, size - a), slice(a, b)),
        (slice(size - b, size - a), slice(size - b, size - a)),
    ):
        corners.append(np.median(lab[ys, xs].reshape(-1, 3), axis=0))
    if any(float(np.linalg.norm(k - ring)) > CORNER_COVERED for k in corners):
        return True
    # a panel edge across the square leaves a band of its color along one side
    a, b, m = round(size * 0.06), round(size * 0.14), round(size * 0.25)
    for strip in (
        lab[a:b, m : size - m],
        lab[size - b : size - a, m : size - m],
        lab[m : size - m, a:b],
        lab[m : size - m, size - b : size - a],
    ):
        if float(np.linalg.norm(np.median(strip.reshape(-1, 3), axis=0) - ring)) > EDGE_COVERED:
            return True
    # the ring mixes both colors when a panel hides half the square; corners do not
    return any(
        float(np.linalg.norm(corners[i] - corners[j])) > CORNER_COVERED
        for i in range(4)
        for j in range(i + 1, 4)
    )


def _off_line(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
    """Distance from color p to the segment between colors a and b."""
    a = a.astype(np.float32)
    ab = b.astype(np.float32) - a
    t = float(np.clip(np.dot(p - a, ab) / max(1e-6, float(np.dot(ab, ab))), 0.0, 1.0))
    return float(np.linalg.norm(p - (a + t * ab)))


def _covered_by(grid, rows: int, cols: int, rects) -> np.ndarray:
    """Squares under the rack, which floats on top of the board in zoomed views."""
    out = np.zeros((rows, cols), bool)
    if not rects:
        return out
    xs = [x for x, _, w, _ in rects] + [x + w for x, _, w, _ in rects]
    ys = [y for _, y, _, h in rects] + [y + h for _, y, _, h in rects]
    pad = 0.25 * float(np.median([h for *_, h in rects]))
    x0, x1, y0, y1 = min(xs) - pad, max(xs) + pad, min(ys) - pad, max(ys) + pad
    for r in range(rows):
        for c in range(cols):
            x, y, w, h = grid.cell_rect(r, c)
            out[r, c] = x < x1 and x0 < x + w and y < y1 and y0 < y + h
    return out


class ViewReader:
    """Locates the board each time it moves and reads every visible square."""

    tracks_view = True

    def __init__(self, calibration: Calibration, profile: Profile):
        self.cal = calibration
        self.profile = profile
        self.rec = Recognizer(calibration, profile)
        self.rec.use_priors = True
        self.expected = _expected_lab(profile)
        self.keys = self.rec._keys
        self.view: BoardView | None = None
        app = profile.appearance
        rack_bgr = app.rack_tile if app and app.rack_tile else None
        self.rack_lab = (
            to_lab(np.array(rack_bgr, np.uint8).reshape(1, 1, 3)).reshape(3)
            if rack_bgr
            else calibration.rack_proto.mean[:3]
        )
        self.locates = 0
        self.values = ValueVotes()
        self.known_values: dict[str, int] = dict(profile.letter_values)
        # what the player said a square or rack tile shows, kept with the glyph it was said of
        self.board_fixes: dict[tuple[int, int], list[tuple[np.ndarray, str | None, bool]]] = {}
        self.rack_fixes: list[tuple[np.ndarray, str]] = []
        self.rack_memory: list[tuple[np.ndarray, str]] = []

    def _still_aligned(self, img: np.ndarray, view: BoardView) -> bool:
        if (img.shape[1], img.shape[0]) != getattr(self, "_size", None):
            return False
        rows, cols = np.nonzero(view.visible)
        if len(rows) < 8:
            return False
        rng = np.random.default_rng(len(rows))
        pick = rng.choice(len(rows), size=min(60, len(rows)), replace=False)
        ok = premium_seen = premium_ok = 0
        for i in pick:
            r, c = int(rows[i]), int(cols[i])
            feat, _ = cell_sample(img, view.grid, r, c)
            d = float(np.linalg.norm(feat[:3] - self.expected[r, c]))
            ok += d < RING_OK
            if self.profile.premium_at(r, c) is not None:
                premium_seen += 1
                premium_ok += d < RING_OK
        return ok >= 0.45 * len(pick) and (premium_seen < 3 or premium_ok >= 0.6 * premium_seen)

    def is_aligned(self, img: np.ndarray) -> bool:
        """Cheap check that the last located board is still where it was in this frame."""
        return self.view is not None and self._still_aligned(img, self.view)

    def locate(self, img: np.ndarray) -> BoardView:
        if self.view is not None and self._still_aligned(img, self.view):
            return self.view
        self.view = locate_board(img, self.profile)
        self._size = (img.shape[1], img.shape[0])
        self.locates += 1
        return self.view

    def _learn_new_colors(self) -> bool:
        """A background shared by several squares that each show a point value and a clearly
        matching letter is a tile color (a highlight, or plain tiles not seen at calibration).
        Panels and popups do not line up with squares that way."""
        learned = False
        found = self._new_colors
        for _, _, col in found:
            support = sum(
                1 for _, _, o in found if float(np.linalg.norm(o - col)) < TILE_BACKGROUND
            )
            if support >= NEW_COLOR_SUPPORT and not any(
                float(np.linalg.norm(col - t)) < TILE_BACKGROUND for t in self.cal.tile_backgrounds
            ):
                self.cal.add_tile_background(col)
                learned = True
        return learned

    def _tile_background(self, feat: np.ndarray) -> bool:
        return any(
            float(np.linalg.norm(feat[:3] - t)) < TILE_BACKGROUND for t in self.cal.tile_backgrounds
        )

    def _blank_decision(self, glyph, background) -> tuple[bool, bool, str]:
        # normal letters are dark, so their ink mixes black and the tile; a blank drawn in a
        # color of its own lies off that line
        if _off_line(glyph.ink, np.zeros(3, np.float32), background) > BLANK_LETTER:
            return True, False, ""
        if glyph.value_mark and glyph.value_ink is not None:
            # a normal value is drawn in the letter's color, so its pixels mix letter ink and
            # tile background; a blank's mark in its own color lies off that line. The learned
            # blank mark color only confirms, since the background tints it.
            neutral = float(np.hypot(glyph.value_ink[1], glyph.value_ink[2])) < BLANK_CHROMA
            if neutral or _off_line(glyph.value_ink, glyph.ink, background) <= BLANK_OFF_LINE:
                return False, False, ""
            known = self.cal.blank_value_ink
            if known is not None and float(np.linalg.norm(glyph.value_ink - known)) < BLANK_MARK:
                return True, False, ""
            return True, True, "possible blank"
        if self.cal.values_expected:
            return True, True, "possible blank"
        return False, False, ""

    def read(self, img: np.ndarray) -> Reading:
        t0 = time.perf_counter()
        try:
            view = self.locate(img)
        except BoardNotFound as exc:
            self.view = None
            rows, cols = self.profile.height, self.profile.width
            empty_cells = [[CellRead(state=UNSEEN) for _ in range(cols)] for _ in range(rows)]
            reading = Reading(empty_cells, [], self.cal.grid, False, time.perf_counter() - t0)
            reading.warnings = [str(exc)]
            return reading
        grid = view.grid
        rack = self._read_rack(img, view)
        rack_glyphs = self.last_rack_glyphs
        board_img, board_grid = work_frame(img, grid)
        rows, cols = self.profile.height, self.profile.width
        covered = _covered_by(grid, rows, cols, [t.rect for t in rack if t.rect is not None])
        feats = np.zeros((rows, cols, 6), np.float32)
        crops: list = [None] * (rows * cols)
        cells: list[list[CellRead]] = []
        self._new_colors: list[tuple[int, int, np.ndarray]] = []
        for r in range(rows):
            row = []
            for c in range(cols):
                if not view.visible[r, c] or covered[r, c]:
                    row.append(CellRead(state=UNSEEN))
                    continue
                feat, crop_ = cell_sample(board_img, board_grid, r, c)
                feats[r, c] = feat
                crops[r * cols + c] = crop_
                row.append(self._read_cell(r, c, feat, crop_))
            cells.append(row)
        if self._learn_new_colors():
            # read again the squares whose tile color was not known
            for r in range(rows):
                for c in range(cols):
                    if cells[r][c].reason == NEW_COLOR:
                        cells[r][c] = self._read_cell(r, c, feats[r, c], crops[r * cols + c])
        reading = Reading(cells, rack, grid, True, time.perf_counter() - t0, [])
        reading.features = feats
        reading.crops = [x if x is not None else np.zeros((CROP, CROP, 3), np.uint8) for x in crops]
        reading.view = view
        reading.rack_glyphs = rack_glyphs
        self._vote_values(img, reading, grid)
        return reading

    def _vote_values(self, img: np.ndarray, reading: Reading, grid: Grid) -> None:
        """Read the values printed on rack tiles; board tiles show premium-adjusted numbers."""
        trusted = self.values.trusted()
        for tile in reading.rack:
            letter = tile.letter
            if tile.uncertain or tile.blank or not letter or letter in trusted:
                continue
            value = tile_value(img, tile.rect)
            if value is not None:
                self.values.add(letter, value)

    def _read_cell(self, r: int, c: int, feat: np.ndarray, crop_: np.ndarray) -> CellRead:
        ring_d = float(np.linalg.norm(feat[:3] - self.expected[r, c]))
        ring_ok = ring_d < RING_OK
        if ring_ok and float(crop_.std()) < 6:
            return CellRead()
        if _corner_covered(crop_, feat):
            return CellRead(state=UNSEEN)
        glyph = extract_glyph(crop_, feat)
        key = self.keys[r][c]
        if glyph is None or glyph.ink_fraction < 0.012:
            return CellRead() if ring_ok else CellRead(state=UNSEEN)
        for vec, fixed, blank in self.board_fixes.get((r, c), ()):
            if float(vec @ glyph.vec) > FIX_MATCH:
                return CellRead(fixed, blank, COMMITTED) if fixed else CellRead()
        contrast = float(np.linalg.norm(glyph.ink - feat[:3]))
        letter, s1, s2, cands, same = self.rec.match(glyph, "board")
        label_score = max(
            (float(t @ glyph.vec) for t in self.cal.empty_glyphs.get(key, ())), default=0.0
        )
        premium = self.profile.premium_at(r, c) is not None
        # tiles carry a point value and square labels do not; only an unmarked glyph that
        # clearly is a letter can be a tile here (a blank), and that is asked about
        label_ink = self.cal.label_ink
        unmarked = (
            premium
            and self.cal.values_expected
            and not glyph.value_mark
            and not glyph.touches_edge
            and (label_ink is None or float(np.linalg.norm(glyph.ink - label_ink)) < LABEL_INK)
        )
        label = (
            (label_score > LABEL_MATCH and (label_score >= s1 or not glyph.value_mark))
            or (
                ring_ok
                and premium
                and contrast < LABEL_CONTRAST
                and not (glyph.value_mark and self.cal.values_expected)
            )
            or (ring_ok and unmarked and s1 < UNMARKED_LETTER)
        )
        if label and ring_ok:
            return CellRead()
        if glyph.touches_edge:
            return CellRead(state=UNSEEN)
        if not ring_ok and not self._tile_background(feat):
            # neither the square's color nor a known tile color: a tile in a new color (it
            # carries a point value) is asked about once; anything else covers the square
            if glyph.value_mark or not self.cal.values_expected:
                if self.rec.accept("board", letter, s1, s2, same):
                    self._new_colors.append((r, c, feat[:3].copy()))
                return CellRead(letter, False, COMMITTED, True, NEW_COLOR, s1, cands)
            return CellRead(state=UNSEEN)
        if ring_ok and unmarked:
            return CellRead(letter, True, COMMITTED, True, "square label or blank tile?", s1, cands)
        state = COMMITTED
        if self.rec.accept("board", letter, s1, s2, same):
            blank, unsure, why = self._blank_decision(glyph, feat[:3])
            return CellRead(letter, blank, state, unsure, why, s1, cands)
        if contrast >= LETTER_CONTRAST:
            return CellRead(letter, False, state, True, "letter is unclear", s1, cands)
        return CellRead() if ring_ok else CellRead(state=UNSEEN)

    def _read_rack(self, img: np.ndarray, view: BoardView | None = None) -> list[RackRead]:
        radius = max(18.0, self.cal.rack_proto.radius * 3)
        out = []
        self.last_rack_glyphs = []
        for b in find_rack(img, self.profile, self.rack_lab, radius, view, self.expected):
            glyph = extract_glyph(b.crop, b.ring)
            self.last_rack_glyphs.append(None if glyph is None else glyph.vec)
            if glyph is None or glyph.ink_fraction < 0.01:
                out.append(RackRead(None, True, rect=b.rect))
                continue
            fixed = max(
                ((float(v @ glyph.vec), ch) for v, ch in self.rack_fixes), default=(0.0, "")
            )
            if fixed[0] > FIX_MATCH:
                out.append(RackRead(fixed[1], False, False, "", b.rect, fixed[0]))
                continue
            letter, s1, s2, cands, same = self.rec.match(glyph, "rack")
            unsure = not self.rec.accept("rack", letter, s1, s2, same)
            if unsure:
                seen = max(
                    ((float(v @ glyph.vec), ch) for v, ch in self.rack_memory), default=(0.0, "")
                )
                if seen[0] > REMEMBER_MATCH:
                    # this very tile looked like this when the rack was last confirmed
                    out.append(RackRead(seen[1], False, False, "", b.rect, seen[0], cands))
                    continue
            out.append(
                RackRead(
                    letter, False, unsure, "letter is unclear" if unsure else "", b.rect, s1, cands
                )
            )
        return out

    def learn(self, img, reading: Reading, board_fixes, rack_fixes=None) -> None:
        """Teach letters (and the blank mark) from corrections on a located frame."""
        from tilelens.vision.recognizer import as_fix

        cols = self.profile.width
        for (r, c), raw in board_fixes.items():
            fix = as_fix(raw)
            crop_ = reading.crops[r * cols + c]
            feat = reading.features[r, c]
            glyph = extract_glyph(crop_, feat)
            if glyph is not None:
                self.board_fixes.setdefault((r, c), []).append(
                    (glyph.vec, fix.letter if fix.played else None, fix.blank)
                )
            if fix.letter is None:
                # only a square showing its own color has its printed label in view
                own = float(np.linalg.norm(feat[:3] - self.expected[r, c])) < RING_OK
                if glyph is not None and own:
                    self.cal.add_empty_glyph(self.keys[r][c], glyph.vec)
                continue
            if glyph is not None:
                if float(np.linalg.norm(feat[:3] - self.expected[r, c])) >= RING_OK:
                    self.cal.add_tile_background(feat[:3])
                self.cal.add_template(fix.letter, glyph.vec)
                if fix.blank and glyph.value_ink is not None:
                    self.cal.blank_value_ink = glyph.value_ink
        glyphs = getattr(reading, "rack_glyphs", None) or []
        for i, letter in (rack_fixes or {}).items():
            vec = glyphs[i] if i < len(glyphs) else None
            if vec is not None and letter != BLANK:
                self.rack_memory = [
                    (v, ch) for v, ch in self.rack_memory if float(v @ vec) <= REMEMBER_MATCH
                ]
                self.rack_fixes.append((vec, letter))
                self.cal.add_template(letter, vec, rack=True)
        self.rec._rebuild_templates()

    learn_from_corrections = learn

    def remember_rack(self, reading: Reading) -> None:
        """Keep the shapes of a confirmed rack, so a frame where a tile reads unclear (a hover,
        an animation) still resolves to the letter it was confirmed as."""
        glyphs = getattr(reading, "rack_glyphs", None) or []
        for tile, vec in zip(reading.rack, glyphs, strict=False):
            if vec is None or tile.uncertain or tile.blank or not tile.letter:
                continue
            if any(ch == tile.letter and float(v @ vec) > 0.985 for v, ch in self.rack_memory):
                continue
            self.rack_memory.append((vec, tile.letter))
        del self.rack_memory[:-60]

    def new_game(self) -> None:
        """Corrections tied to squares belong to one game."""
        self.board_fixes.clear()

    def consolidate(self, reading: Reading, per_letter: int = 3, confirmed: bool = False) -> int:
        """Keep board shapes of letters from a fully confirmed reading.

        Board tiles are then matched against board tiles, which is stricter and more accurate
        than matching them against rack or chart shapes. Only a few per letter are kept.
        """
        if not (confirmed or reading.confident) or reading.crops is None:
            return 0
        cols = self.profile.width
        added = 0
        for r, row in enumerate(reading.cells):
            for c, cell in enumerate(row):
                if not cell.letter or cell.blank or cell.uncertain or cell.state == UNSEEN:
                    continue
                if len(self.cal.templates.get(cell.letter, ())) >= per_letter:
                    continue
                glyph = extract_glyph(reading.crops[r * cols + c], reading.features[r, c])
                if glyph is None or glyph.touches_edge:
                    continue
                feat = reading.features[r, c]
                if float(np.linalg.norm(feat[:3] - self.expected[r, c])) >= RING_OK:
                    self.cal.add_tile_background(feat[:3])
                self.cal.add_template(cell.letter, glyph.vec)
                added += 1
        if added:
            self.rec._rebuild_templates()
        return added


def add_screen(cal: Calibration, frame: np.ndarray, profile: Profile, rack: str) -> None:
    """Learn more rack letters (and labels) from another screen with a typed rack."""
    extra = calibrate_auto(frame, profile, rack)
    for letter, vecs in extra.rack_templates.items():
        for v in vecs:
            cal.add_template(letter, v, rack=True)
    for key, vecs in extra.empty_glyphs.items():
        for v in vecs:
            cal.add_empty_glyph(key, v)
    if cal.blank_value_ink is None:
        cal.blank_value_ink = extra.blank_value_ink
    if cal.label_ink is None:
        cal.label_ink = extra.label_ink


class NeedsLetters(CalibrationError):
    """The rack could not be read without help; guess holds the best letter per tile."""

    def __init__(self, message: str, guess: str, unsure: list[int]):
        super().__init__(message)
        self.guess = guess
        self.unsure = unsure


def guess_rack(frame: np.ndarray, profile: Profile, view: BoardView | None = None):
    """Read the rack from stock-font shapes alone: (letters or None per tile, best letters)."""
    from tilelens.vision.recognizer import PRIOR_MARGIN, PRIOR_MIN, font_priors

    letters, vecs = font_priors()
    alphabet = profile.alphabet
    out: list[str | None] = []
    best: list[str] = []
    for b in rack_tiles(frame, profile, view):
        glyph = extract_glyph(b.crop, b.ring)
        if glyph is None or glyph.ink_fraction < 0.01:
            out.append(BLANK)
            best.append(BLANK)
            continue
        scores = (vecs @ glyph.vec).max(axis=0)
        ranked = sorted(
            ((float(s), ch) for ch, s in zip(letters, scores, strict=True) if ch in alphabet),
            reverse=True,
        )
        best.append(ranked[0][1])
        sure = ranked[0][0] >= PRIOR_MIN and ranked[0][0] - ranked[1][0] >= PRIOR_MARGIN
        out.append(ranked[0][1] if sure else None)
    return out, best


def calibrate_unaided(
    frame: np.ndarray, profile: Profile, previous: Calibration | None = None
) -> Calibration:
    """Calibrate from one screen without typing the rack: stock-font shapes name its letters.

    Raises NeedsLetters, naming the tiles that need a human, when they cannot.
    """
    import copy

    try:
        view = locate_board(frame, profile)
    except BoardNotFound as exc:
        raise CalibrationError(f"board not found: {exc}") from None
    letters, best = guess_rack(frame, profile, view)
    if not letters:
        raise CalibrationError("no rack found: bring the game's rack into view")
    if any(ch is None for ch in letters):
        raise NeedsLetters(
            "some rack tiles need confirming",
            "".join(best),
            [i for i, ch in enumerate(letters) if ch is None],
        )
    rack = "".join(letters)
    if previous is not None and previous.auto:
        cal = copy.deepcopy(previous)
        add_screen(cal, frame, profile, rack)
        return cal
    return calibrate_auto(frame, profile, rack)

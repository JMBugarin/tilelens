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

RING_OK = 16.0
UNIFORM = 9.0
BLANK_MARK = 18.0
BLANK_CHROMA = 18.0
LABEL_MATCH = 0.85
LABEL_CONTRAST = 35.0
LETTER_CONTRAST = 45.0
DEFAULT_RACK_ZONE = (0.2, 0.8, 0.55, 1.0)


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
    by_key: dict[str, list[np.ndarray]] = {}
    empty_feats: dict[str, list[np.ndarray]] = {}
    for r in range(profile.height):
        for c in range(profile.width):
            if not view.visible[r, c]:
                continue
            feat, crop_ = cell_sample(board_img, board_grid, r, c)
            if np.linalg.norm(feat[:3] - expected[r, c]) >= RING_OK:
                continue
            glyph = extract_glyph(crop_, feat)
            if glyph is None:
                empty_feats.setdefault(keys[r][c], []).append(feat)
            else:
                by_key.setdefault(keys[r][c], []).append((glyph.vec, glyph.ink, glyph.value_mark))

    letters = [ch for ch in rack.upper() if not ch.isspace()]
    blobs = rack_tiles(frame, profile, view)
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

    def locate(self, img: np.ndarray) -> BoardView:
        if self.view is not None and self._still_aligned(img, self.view):
            return self.view
        self.view = locate_board(img, self.profile)
        self._size = (img.shape[1], img.shape[0])
        self.locates += 1
        return self.view

    def _blank_decision(self, glyph) -> tuple[bool, bool, str]:
        if glyph.value_mark and glyph.value_ink is not None:
            if self.cal.blank_value_ink is not None:
                if float(np.linalg.norm(glyph.value_ink - self.cal.blank_value_ink)) < BLANK_MARK:
                    return True, False, ""
                return False, False, ""
            chroma = float(np.hypot(glyph.value_ink[1], glyph.value_ink[2]))
            if chroma > BLANK_CHROMA:
                return True, True, "possible blank"
            return False, False, ""
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
        board_img, board_grid = work_frame(img, grid)
        rows, cols = self.profile.height, self.profile.width
        covered = _covered_by(grid, rows, cols, [t.rect for t in rack if t.rect is not None])
        feats = np.zeros((rows, cols, 6), np.float32)
        crops: list = [None] * (rows * cols)
        cells: list[list[CellRead]] = []
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
        reading = Reading(cells, rack, grid, True, time.perf_counter() - t0, [])
        reading.features = feats
        reading.crops = [x if x is not None else np.zeros((CROP, CROP, 3), np.uint8) for x in crops]
        reading.view = view
        return reading

    def _read_cell(self, r: int, c: int, feat: np.ndarray, crop_: np.ndarray) -> CellRead:
        ring_d = float(np.linalg.norm(feat[:3] - self.expected[r, c]))
        ring_ok = ring_d < RING_OK
        if ring_ok and float(crop_.std()) < 6:
            return CellRead()
        glyph = extract_glyph(crop_, feat)
        key = self.keys[r][c]
        if glyph is None or glyph.ink_fraction < 0.012:
            return CellRead() if ring_ok else CellRead(state=UNSEEN)
        contrast = float(np.linalg.norm(glyph.ink - feat[:3]))
        letter, s1, s2, cands, same = self.rec.match(glyph, "board")
        label_score = max(
            (float(t @ glyph.vec) for t in self.cal.empty_glyphs.get(key, ())), default=0.0
        )
        label = (label_score > LABEL_MATCH and label_score >= s1) or (
            ring_ok and self.profile.premium_at(r, c) is not None and contrast < LABEL_CONTRAST
        )
        if label and ring_ok:
            return CellRead()
        if glyph.touches_edge:
            return CellRead(state=UNSEEN)
        if self.rec.accept("board", letter, s1, s2, same):
            blank, unsure, why = self._blank_decision(glyph)
            return CellRead(letter, blank, COMMITTED, unsure, why, s1, cands)
        if contrast >= LETTER_CONTRAST:
            return CellRead(letter, False, COMMITTED, True, "letter is unclear", s1, cands)
        return CellRead() if ring_ok else CellRead(state=UNSEEN)

    def _read_rack(self, img: np.ndarray, view: BoardView | None = None) -> list[RackRead]:
        radius = max(18.0, self.cal.rack_proto.radius * 3)
        out = []
        for b in find_rack(img, self.profile, self.rack_lab, radius, view, self.expected):
            glyph = extract_glyph(b.crop, b.ring)
            if glyph is None or glyph.ink_fraction < 0.01:
                out.append(RackRead(None, True, rect=b.rect))
                continue
            letter, s1, s2, _, same = self.rec.match(glyph, "rack")
            unsure = not self.rec.accept("rack", letter, s1, s2, same)
            out.append(
                RackRead(letter, False, unsure, "letter is unclear" if unsure else "", b.rect, s1)
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
            if fix.letter is None:
                if glyph is not None:
                    self.cal.add_empty_glyph(self.keys[r][c], glyph.vec)
                continue
            if glyph is not None:
                self.cal.add_template(fix.letter, glyph.vec)
                if fix.blank and glyph.value_ink is not None:
                    self.cal.blank_value_ink = glyph.value_ink
        if rack_fixes:
            blobs = find_rack(
                img, self.profile, self.rack_lab, 22.0, getattr(reading, "view", None)
            )
            for i, letter in rack_fixes.items():
                if i < len(blobs) and letter != BLANK:
                    glyph = extract_glyph(blobs[i].crop, blobs[i].ring)
                    if glyph is not None:
                        self.cal.add_template(letter, glyph.vec, rack=True)
        self.rec._rebuild_templates()

    learn_from_corrections = learn


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

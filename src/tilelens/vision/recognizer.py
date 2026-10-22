from __future__ import annotations

import base64
import json
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from tilelens.rules.board import WALL_CELL, Board
from tilelens.rules.profile import BLANK, Profile
from tilelens.vision.cells import (
    GLYPH,
    Glyph,
    board_features,
    extract_glyph,
    find_tile_blobs,
)
from tilelens.vision.grid import Grid, detect_grid

COMMITTED = "committed"
RECENT = "recent"
TENTATIVE = "tentative"
UNKNOWN = "unknown"
UNSEEN = "unseen"

MATCH = 3.0
LETTER_MIN = 0.8
LETTER_MARGIN = 0.05
INK_SPLIT = 12.0
MAX_TEMPLATES = 8


EMPTY_MATCH = 0.85
SCALE_TOLERANCE = 0.03
SCALE_CHANGED = "board size changed since calibration; recalibrate"


LETTER_MIN_STRICT = 0.95
CROSS_MIN = 0.90
CROSS_MARGIN = 0.08


def letter_ok(letter: str | None, s1: float, s2: float, strict: bool = False) -> bool:
    if letter is None or s1 < (LETTER_MIN_STRICT if strict else LETTER_MIN):
        return False
    return s1 - s2 >= (LETTER_MARGIN if s1 < 0.95 else LETTER_MARGIN / 2)


class CalibrationError(ValueError):
    pass


@dataclass
class Proto:
    mean: np.ndarray
    radius: float
    state: str = ""

    def distance(self, v: np.ndarray) -> float:
        return float(np.linalg.norm(v - self.mean)) / self.radius

    def to_dict(self) -> dict:
        return {"mean": self.mean.tolist(), "radius": self.radius, "state": self.state}

    @classmethod
    def from_dict(cls, d: dict) -> Proto:
        return cls(np.array(d["mean"], np.float32), float(d["radius"]), d.get("state", ""))

    @classmethod
    def fit(cls, samples: list[np.ndarray], state: str = "", floor: float = 4.0) -> Proto:
        arr = np.array(samples, np.float32)
        mean = arr.mean(axis=0)
        d = np.linalg.norm(arr - mean, axis=1)
        return cls(mean, max(floor, float(np.percentile(d, 95)) if len(d) > 1 else floor), state)


def premium_key(profile: Profile, row: int, col: int) -> str:
    p = profile.premium_at(row, col)
    key = p.name if p else "plain"
    return key + ("*" if (row, col) in set(profile.start_squares) else "")


@dataclass
class Calibration:
    profile_fingerprint: str
    image_size: tuple[int, int]
    grid: Grid
    rack_band: tuple[int, int, int, int]
    rack_tile: float
    rack_proto: Proto
    empty: dict[str, Proto]
    tiles: list[Proto]
    normal_ink: np.ndarray
    blank_ink: np.ndarray | None
    values_expected: bool
    templates: dict[str, list[np.ndarray]] = field(default_factory=dict)
    rack_templates: dict[str, list[np.ndarray]] = field(default_factory=dict)
    empty_glyphs: dict[str, list[np.ndarray]] = field(default_factory=dict)
    masks: list[tuple[int, int, int, int]] = field(default_factory=list)
    auto: bool = False
    blank_value_ink: np.ndarray | None = None
    label_ink: np.ndarray | None = None
    version: int = 1

    def add_empty_glyph(self, key: str, vec: np.ndarray) -> None:
        bucket = self.empty_glyphs.setdefault(key, [])
        if not any(float(t @ vec) > 0.97 for t in bucket):
            bucket.append(vec.astype(np.float32))
            del bucket[:-4]

    def add_template(self, letter: str, vec: np.ndarray, rack: bool = False) -> None:
        store = self.rack_templates if rack else self.templates
        bucket = store.setdefault(letter, [])
        if any(float(t @ vec) > 0.985 for t in bucket):
            return
        bucket.append(vec.astype(np.float32))
        if len(bucket) > MAX_TEMPLATES:
            bucket.pop(0)

    def to_json(self) -> str:
        def enc(vec):
            img = np.clip((vec.reshape(GLYPH, GLYPH) + 1) * 127.5, 0, 255).astype(np.uint8)
            ok, buf = cv2.imencode(".png", img)
            return base64.b64encode(buf.tobytes()).decode()

        data = {
            "version": self.version,
            "profile_fingerprint": self.profile_fingerprint,
            "image_size": list(self.image_size),
            "grid": self.grid.to_dict(),
            "rack_band": list(self.rack_band),
            "rack_tile": self.rack_tile,
            "rack_proto": self.rack_proto.to_dict(),
            "empty": {k: p.to_dict() for k, p in self.empty.items()},
            "tiles": [p.to_dict() for p in self.tiles],
            "normal_ink": self.normal_ink.tolist(),
            "blank_ink": None if self.blank_ink is None else self.blank_ink.tolist(),
            "values_expected": self.values_expected,
            "templates": {k: [enc(v) for v in vs] for k, vs in self.templates.items()},
            "rack_templates": {k: [enc(v) for v in vs] for k, vs in self.rack_templates.items()},
            "empty_glyphs": {k: [enc(v) for v in vs] for k, vs in self.empty_glyphs.items()},
            "masks": [list(m) for m in self.masks],
            "auto": self.auto,
            "label_ink": None if self.label_ink is None else self.label_ink.tolist(),
            "blank_value_ink": (
                None if self.blank_value_ink is None else self.blank_value_ink.tolist()
            ),
        }
        return json.dumps(data)

    @classmethod
    def from_json(cls, text: str) -> Calibration:
        d = json.loads(text)

        def dec(s):
            buf = np.frombuffer(base64.b64decode(s), np.uint8)
            img = cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE).astype(np.float32) / 127.5 - 1
            v = img.ravel() - img.mean()
            return (v / max(np.linalg.norm(v), 1e-6)).astype(np.float32)

        return cls(
            profile_fingerprint=d["profile_fingerprint"],
            image_size=tuple(d["image_size"]),
            grid=Grid.from_dict(d["grid"]),
            rack_band=tuple(d["rack_band"]),
            rack_tile=float(d["rack_tile"]),
            rack_proto=Proto.from_dict(d["rack_proto"]),
            empty={k: Proto.from_dict(p) for k, p in d["empty"].items()},
            tiles=[Proto.from_dict(p) for p in d["tiles"]],
            normal_ink=np.array(d["normal_ink"], np.float32),
            blank_ink=None if d["blank_ink"] is None else np.array(d["blank_ink"], np.float32),
            values_expected=bool(d["values_expected"]),
            templates={k: [dec(s) for s in vs] for k, vs in d["templates"].items()},
            rack_templates={
                k: [dec(s) for s in vs] for k, vs in d.get("rack_templates", {}).items()
            },
            empty_glyphs={k: [dec(s) for s in vs] for k, vs in d.get("empty_glyphs", {}).items()},
            masks=[tuple(m) for m in d.get("masks", [])],
            auto=bool(d.get("auto", False)),
            label_ink=(
                None if d.get("label_ink") is None else np.array(d["label_ink"], np.float32)
            ),
            blank_value_ink=(
                None
                if d.get("blank_value_ink") is None
                else np.array(d["blank_value_ink"], np.float32)
            ),
            version=int(d.get("version", 1)),
        )

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(self.to_json())
        tmp.replace(path)

    @classmethod
    def load(cls, path: str | Path) -> Calibration:
        return cls.from_json(Path(path).read_text())


@dataclass(frozen=True)
class CellFix:
    """A user correction: letter None means empty; played False means not yet submitted."""

    letter: str | None
    blank: bool = False
    played: bool = True


def as_fix(value) -> CellFix:
    if isinstance(value, CellFix):
        return value
    if value is None:
        return CellFix(None)
    letter, blank = value
    return CellFix(letter, blank)


@dataclass
class CellRead:
    letter: str | None = None
    blank: bool = False
    state: str | None = None
    uncertain: bool = False
    reason: str = ""
    letter_score: float = 0.0
    candidates: tuple[str, ...] = ()


@dataclass
class RackRead:
    letter: str | None
    blank: bool
    uncertain: bool = False
    reason: str = ""
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    letter_score: float = 0.0


@dataclass
class Reading:
    cells: list[list[CellRead]]
    rack: list[RackRead]
    grid: Grid
    visible: bool
    elapsed: float
    warnings: list[str] = field(default_factory=list)
    features: np.ndarray | None = field(default=None, repr=False)
    crops: list | None = field(default=None, repr=False)

    @property
    def uncertain_cells(self) -> list[tuple[int, int]]:
        return [
            (r, c)
            for r, row in enumerate(self.cells)
            for c, cell in enumerate(row)
            if cell.uncertain
        ]

    @property
    def rack_uncertain(self) -> bool:
        return any(t.uncertain for t in self.rack)

    @property
    def confident(self) -> bool:
        return (
            self.visible
            and not self.uncertain_cells
            and not self.rack_uncertain
            and not (self.warnings)
        )

    def placed(self, states=(COMMITTED, RECENT)) -> Board:
        rows = len(self.cells)
        cols = len(self.cells[0]) if rows else 0
        cells = [
            [
                WALL_CELL
                if c.state == UNSEEN
                else ((c.letter, c.blank) if c.letter and c.state in states else None)
                for c in row
            ]
            for row in self.cells
        ]
        return Board(cols, rows, cells)

    def tentative_squares(self) -> list[tuple[int, int]]:
        return [
            (r, c)
            for r, row in enumerate(self.cells)
            for c, cell in enumerate(row)
            if cell.letter and cell.state in (TENTATIVE, UNKNOWN)
        ]

    def rack_counter(self) -> Counter:
        out: Counter = Counter()
        for t in self.rack:
            if t.blank:
                out[BLANK] += 1
            elif t.letter:
                out[t.letter] += 1
        return out

    def signature(self) -> tuple:
        cells = tuple(
            tuple((c.letter, c.blank, c.state) if c.letter else None for c in row)
            for row in self.cells
        )
        rack = tuple(sorted((t.letter or "", t.blank) for t in self.rack))
        return (cells, rack)


class Recognizer:
    def __init__(self, calibration: Calibration, profile: Profile):
        self.cal = calibration
        self.profile = profile
        self._keys = [
            [premium_key(profile, r, c) for c in range(profile.width)]
            for r in range(profile.height)
        ]
        self._rebuild_templates()

    def _rebuild_templates(self) -> None:
        self._sets = {}
        for name, store in (("board", self.cal.templates), ("rack", self.cal.rack_templates)):
            letters, vecs = [], []
            for letter, bucket in sorted(store.items()):
                for v in bucket:
                    letters.append(letter)
                    vecs.append(v)
            matrix = np.array(vecs, np.float32) if vecs else np.zeros((0, GLYPH * GLYPH))
            self._sets[name] = (np.array(letters), matrix)
        known = set(self.cal.templates) | set(self.cal.rack_templates)
        self.missing_letters = sorted(self.profile.alphabet - known)
        self._strict = {
            "board": bool(self.profile.alphabet - set(self.cal.templates)),
            "rack": bool(self.profile.alphabet - set(self.cal.rack_templates)),
        }

    def learn(self, letter: str, glyph: Glyph, rack: bool = False) -> None:
        self.cal.add_template(letter, glyph.vec, rack=rack)
        self._rebuild_templates()

    def _scores(self, name: str, vec: np.ndarray) -> dict[str, float]:
        letters, matrix = self._sets[name]
        best: dict[str, float] = {}
        if len(letters):
            for letter, s in zip(letters, matrix @ vec, strict=True):
                if s > best.get(letter, -2.0):
                    best[str(letter)] = float(s)
        return best

    def match(self, glyph: Glyph, home: str = "board"):
        """Best letter for a glyph, preferring templates captured from the same place.

        Returns (letter, score, runner_up, candidates, same_source).
        """
        other = "rack" if home == "board" else "board"
        same = self._scores(home, glyph.vec)
        cross = self._scores(other, glyph.vec)
        merged = {k: (v, True) for k, v in same.items()}
        for k, v in cross.items():
            if k not in merged:
                merged[k] = (v, False)
        if not merged:
            return None, 0.0, 0.0, (), False
        ranked = sorted(merged.items(), key=lambda kv: -kv[1][0])
        letter, (s1, same_source) = ranked[0]
        s2 = ranked[1][1][0] if len(ranked) > 1 else -1.0
        return letter, s1, s2, tuple(k for k, _ in ranked[:3]), same_source

    def accept(self, home: str, letter, s1: float, s2: float, same_source: bool) -> bool:
        if letter is None:
            return False
        if same_source:
            return letter_ok(letter, s1, s2, self._strict[home])
        return s1 >= CROSS_MIN and s1 - s2 >= CROSS_MARGIN

    def grid_for(self, img: np.ndarray) -> Grid:
        h, w = img.shape[:2]
        cw, ch = self.cal.image_size
        if (w, h) == (cw, ch):
            return self.cal.grid
        guess = self.cal.grid.scaled(w / cw, h / ch)
        try:
            return detect_grid(img, guess.rows, guess.cols, hint=guess.rect)
        except Exception:
            return guess

    def _empty_like(self, key: str, glyph: Glyph | None) -> bool:
        if glyph is None or glyph.ink_fraction < 0.012:
            return True
        return any(float(t @ glyph.vec) > EMPTY_MATCH for t in self.cal.empty_glyphs.get(key, ()))

    def _blank_decision(self, glyph: Glyph) -> tuple[bool, bool, str]:
        """Return (is_blank, uncertain, reason)."""
        dn = float(np.linalg.norm(glyph.ink - self.cal.normal_ink))
        db = (
            None
            if self.cal.blank_ink is None
            else float(np.linalg.norm(glyph.ink - self.cal.blank_ink))
        )
        ink_says_blank = (db is not None and db + 4 < dn) or (db is None and dn > INK_SPLIT * 1.5)
        ink_says_normal = dn < INK_SPLIT and (db is None or dn + 4 < db)
        if self.cal.values_expected:
            if glyph.value_mark and not ink_says_blank:
                return False, False, ""
            if not glyph.value_mark and ink_says_blank:
                return True, False, ""
            return (not glyph.value_mark), True, "blank or not is unclear"
        if ink_says_normal:
            return False, False, ""
        if ink_says_blank and db is not None:
            return True, False, ""
        return True, True, "possible blank"

    def read(self, img: np.ndarray) -> Reading:
        t0 = time.perf_counter()
        grid = self.grid_for(img)
        feats, crops = board_features(img, grid)
        rows, cols = grid.rows, grid.cols
        cells: list[list[CellRead]] = []
        matched = 0
        for r in range(rows):
            row = []
            for c in range(cols):
                v = feats[r, c]
                ep = self.cal.empty.get(self._keys[r][c])
                d_empty = ep.distance(v) if ep else np.inf
                tile_d = [(p.distance(v), p) for p in self.cal.tiles]
                d_tile, tp = min(tile_d, key=lambda x: x[0]) if tile_d else (np.inf, None)
                if d_empty < MATCH or d_tile < MATCH:
                    matched += 1
                cell = CellRead()
                glyph = extract_glyph(crops[r * cols + c], v)
                empty_like = self._empty_like(self._keys[r][c], glyph)
                if empty_like and d_empty < MATCH:
                    row.append(cell)
                    continue
                if empty_like:
                    if d_tile < MATCH:
                        cell.state = tp.state
                        cell.uncertain, cell.reason = True, "tile without a readable letter"
                    elif ep is None and d_tile > 2 * MATCH:
                        pass
                    else:
                        cell.uncertain, cell.reason = True, "unrecognized square"
                    row.append(cell)
                    continue
                cell.state = tp.state if d_tile < MATCH else UNKNOWN
                if cell.state == UNKNOWN:
                    cell.uncertain, cell.reason = True, "tile color not seen during calibration"
                letter, s1, s2, cands, same = self.match(glyph, "board")
                cell.letter, cell.letter_score, cell.candidates = letter, s1, cands
                if not self.accept("board", letter, s1, s2, same):
                    cell.uncertain, cell.reason = True, "letter is unclear"
                blank, unsure, why = self._blank_decision(glyph)
                cell.blank = blank
                if unsure:
                    cell.uncertain = True
                    cell.reason = cell.reason or why
                row.append(cell)
            cells.append(row)

        rack = self._read_rack(img, grid)
        visible = matched >= 0.6 * rows * cols
        warnings = []
        if visible:
            warnings = self._sanity(cells, rack)
            ratio = grid.pitch_x / self.cal.grid.pitch_x
            if abs(ratio - 1) > SCALE_TOLERANCE:
                warnings.append(SCALE_CHANGED)
        reading = Reading(cells, rack, grid, visible, time.perf_counter() - t0, warnings)
        reading.features = feats
        reading.crops = crops
        return reading

    def learn_from_corrections(
        self,
        img: np.ndarray,
        reading: Reading,
        board_fixes: dict[tuple[int, int], CellFix | tuple[str, bool] | None],
        rack_fixes: dict[int, str] | None = None,
    ) -> None:
        """Teach the recognizer from user corrections on one frame.

        board_fixes maps (row, col) to a CellFix, a (letter, blank) pair for a played tile, or
        None for an empty square; rack_fixes maps a rack slot index to its letter or '?'.
        """
        feats = reading.features
        crops = reading.crops
        cols = len(reading.cells[0])
        for (r, c), raw in board_fixes.items():
            fix = as_fix(raw)
            v = feats[r, c]
            key = self._keys[r][c]
            glyph = extract_glyph(crops[r * cols + c], v)
            if fix.letter is None:
                proto = self.cal.empty.get(key)
                if proto is None:
                    self.cal.empty[key] = Proto(v.copy(), 6.0)
                elif proto.distance(v) >= MATCH:
                    proto.radius = float(np.linalg.norm(v - proto.mean)) / (MATCH * 0.8)
                if glyph is not None and glyph.ink_fraction >= 0.012:
                    self.cal.add_empty_glyph(key, glyph.vec)
                continue
            state = RECENT if fix.played else TENTATIVE
            near = [p for p in self.cal.tiles if p.distance(v) < MATCH]
            if not near:
                self.cal.tiles.append(Proto(v.copy(), 8.0, state))
            elif not fix.played:
                for p in near:
                    p.state = TENTATIVE
            if glyph is not None:
                self.cal.add_template(fix.letter, glyph.vec)
                if fix.blank and self.cal.blank_ink is None:
                    self.cal.blank_ink = glyph.ink.copy()
        if rack_fixes:
            blobs = self.rack_blobs(img, reading.grid)
            for i, letter in rack_fixes.items():
                if i < len(blobs) and letter != BLANK:
                    glyph = extract_glyph(blobs[i].crop, blobs[i].ring)
                    if glyph is not None:
                        self.cal.add_template(letter, glyph.vec, rack=True)
        self._rebuild_templates()

    def _rack_band(self, img, grid: Grid):
        h, w = img.shape[:2]
        cw, ch = self.cal.image_size
        fx, fy = w / cw, h / ch
        x0, y0, x1, y1 = self.cal.rack_band
        return (round(x0 * fx), round(y0 * fy), round(x1 * fx), round(y1 * fy)), (fx + fy) / 2

    def rack_blobs(self, img, grid):
        band, f = self._rack_band(img, grid)
        size = self.cal.rack_tile * f
        return find_tile_blobs(
            img,
            band,
            self.cal.rack_proto.mean,
            self.cal.rack_proto.radius * MATCH,
            (size * 0.7, size * 1.35),
            native=grid.pitch_x,
        )

    def _read_rack(self, img, grid) -> list[RackRead]:
        blobs = self.rack_blobs(img, grid)
        out = []
        for b in blobs:
            glyph = extract_glyph(b.crop, b.ring)
            if glyph is None or glyph.ink_fraction < 0.01:
                out.append(RackRead(None, True, rect=b.rect))
                continue
            letter, s1, s2, _, same = self.match(glyph, "rack")
            unsure = not self.accept("rack", letter, s1, s2, same)
            out.append(
                RackRead(letter, False, unsure, "letter is unclear" if unsure else "", b.rect, s1)
            )
        if len(out) > self.profile.rack_size:
            for t in out:
                t.uncertain, t.reason = True, "more rack tiles than the rack holds"
        return out

    def _sanity(self, cells, rack) -> list[str]:
        counts: Counter = Counter()
        for row in cells:
            for c in row:
                if c.letter:
                    counts[BLANK if c.blank else c.letter] += 1
        for t in rack:
            if t.blank:
                counts[BLANK] += 1
            elif t.letter:
                counts[t.letter] += 1
        out = []
        for letter, n in counts.items():
            limit = (
                self.profile.blanks if letter == BLANK else self.profile.distribution.get(letter, 0)
            )
            if n > limit:
                out.append(f"more '{letter}' tiles than the set contains ({n} > {limit})")
        return out


def _parse_truth_board(rows: list[str], profile: Profile) -> list[list[tuple[str, bool] | None]]:
    if len(rows) != profile.height or any(len(r) != profile.width for r in rows):
        raise CalibrationError(
            f"typed board must be {profile.height} rows of {profile.width} characters"
        )
    out = []
    for line in rows:
        out.append([None if ch in ".-_ " else (ch.upper(), ch.islower()) for ch in line])
    return out


def _cluster(samples: list[np.ndarray], threshold: float = 14.0) -> list[list[np.ndarray]]:
    clusters: list[list[np.ndarray]] = []
    for s in samples:
        for cl in clusters:
            if np.linalg.norm(np.mean(cl, axis=0) - s) < threshold:
                cl.append(s)
                break
        else:
            clusters.append([s])
    clusters.sort(key=len, reverse=True)
    return clusters


def calibrate(
    samples: list[tuple[np.ndarray, list[str], str]],
    profile: Profile,
    board_hint: tuple[float, float, float, float] | None = None,
    grid: Grid | None = None,
) -> Calibration:
    """Learn colors and glyph templates from screens whose board and rack the user typed.

    Each sample is (image, board rows, rack letters left to right). Board rows use '.' for empty
    squares, uppercase for tiles and lowercase for blanks; '?' in the rack marks a blank.
    """
    if not samples:
        raise CalibrationError("at least one sample is needed")
    first = samples[0][0]
    size = (first.shape[1], first.shape[0])
    if grid is None:
        grid = detect_grid(first, profile.height, profile.width, hint=board_hint)
    empty_samples: dict[str, list[np.ndarray]] = {}
    tile_samples: list[np.ndarray] = []
    normal_ink, blank_ink, value_flags = [], [], []
    templates: list[tuple[str, np.ndarray]] = []
    rack_templates: list[tuple[str, np.ndarray]] = []
    empty_glyph_samples: list[tuple[str, np.ndarray]] = []
    rack_rects, rack_rings = [], []
    keys = [
        [premium_key(profile, r, c) for c in range(profile.width)] for r in range(profile.height)
    ]

    parsed = []
    for img, rows, rack in samples:
        if (img.shape[1], img.shape[0]) != size:
            raise CalibrationError("all calibration screens must be the same size")
        truth = _parse_truth_board(rows, profile)
        parsed.append((img, truth, rack))
        feats, crops = board_features(img, grid)
        for r in range(profile.height):
            for c in range(profile.width):
                v = feats[r, c]
                t = truth[r][c]
                if t is None:
                    empty_samples.setdefault(keys[r][c], []).append(v)
                    eg = extract_glyph(crops[r * profile.width + c], v)
                    if eg is not None and eg.ink_fraction >= 0.012:
                        empty_glyph_samples.append((keys[r][c], eg.vec))
                    continue
                tile_samples.append(v)
                glyph = extract_glyph(crops[r * profile.width + c], v)
                if glyph is None:
                    raise CalibrationError(
                        f"no letter found on the tile at row {r + 1}, column {c + 1}; "
                        "check the board rectangle and the typed board"
                    )
                templates.append((t[0], glyph.vec))
                if t[1]:
                    blank_ink.append(glyph.ink)
                else:
                    normal_ink.append(glyph.ink)
                    value_flags.append(glyph.value_mark)

    if not tile_samples:
        raise CalibrationError("the calibration board needs at least one tile")
    clusters = _cluster(tile_samples)
    tiles = [Proto.fit(clusters[0], COMMITTED, floor=8.0)]
    tiles += [Proto.fit(cl, RECENT, floor=8.0) for cl in clusters[1:]]
    empty = {k: Proto.fit(v, floor=5.0) for k, v in empty_samples.items()}

    pitch = (grid.pitch_x + grid.pitch_y) / 2
    gx, gy, gw, gh = grid.rect
    for img, _truth, rack in parsed:
        letters = [ch for ch in rack if not ch.isspace()]
        if not letters:
            raise CalibrationError("type the rack letters (use ? for a blank)")
        blobs = []
        for proto in tiles:
            band = (0, 0, img.shape[1], img.shape[0])
            found = [
                b
                for b in find_tile_blobs(
                    img,
                    band,
                    proto.mean,
                    max(proto.radius * MATCH, 14.0),
                    (pitch * 0.75, pitch * 2.2),
                    native=grid.pitch_x,
                )
                if not _overlaps(b.rect, (gx, gy, gw, gh))
            ]
            groups = _rows_of(found, pitch)
            exact = [g for g in groups if len(g) == len(letters)]
            if exact:
                blobs = min(exact, key=lambda g: abs(g[0].rect[1] - (gy + gh)))
                break
        if not blobs:
            raise CalibrationError(
                f"could not find {len(letters)} rack tiles; check the typed rack and that the "
                "rack is inside the captured region"
            )
        for b, ch in zip(blobs, letters, strict=True):
            rack_rects.append(b.rect)
            rack_rings.append(b.ring)
            if ch == BLANK:
                continue
            glyph = extract_glyph(b.crop, b.ring)
            if glyph is None:
                raise CalibrationError(f"no letter found on rack tile '{ch}'")
            rack_templates.append((ch.upper(), glyph.vec))

    sides = [(w + h) / 2 for _, _, w, h in rack_rects]
    tile_side = float(np.median(sides))
    xs0 = min(x for x, _, _, _ in rack_rects)
    xs1 = max(x + w for x, _, w, _ in rack_rects)
    ys0 = min(y for _, y, _, _ in rack_rects)
    ys1 = max(y + h for _, y, _, h in rack_rects)
    spread = tile_side * 1.3 * profile.rack_size
    band = (
        int(max(0, min(xs0, (xs0 + xs1) / 2 - spread / 2) - tile_side)),
        int(ys0 - tile_side * 0.4),
        int(max(xs1, (xs0 + xs1) / 2 + spread / 2) + tile_side),
        int(ys1 + tile_side * 0.4),
    )
    ink_n = np.median(np.array(normal_ink), axis=0) if normal_ink else np.zeros(3, np.float32)
    ink_b = np.median(np.array(blank_ink), axis=0) if blank_ink else None
    cal = Calibration(
        profile_fingerprint=profile.fingerprint(),
        image_size=size,
        grid=grid,
        rack_band=band,
        rack_tile=tile_side,
        rack_proto=Proto.fit(rack_rings, floor=5.0),
        empty=empty,
        tiles=tiles,
        normal_ink=ink_n.astype(np.float32),
        blank_ink=None if ink_b is None else ink_b.astype(np.float32),
        values_expected=bool(value_flags) and float(np.mean(value_flags)) > 0.8,
    )
    for letter, vec in templates:
        cal.add_template(letter, vec)
    rec = Recognizer(cal, profile)
    for img, _truth, rack in parsed:
        letters = [ch for ch in rack if not ch.isspace()]
        blobs = rec.rack_blobs(img, grid)
        if len(blobs) != len(letters):
            continue
        for b, ch in zip(blobs, letters, strict=True):
            glyph = extract_glyph(b.crop, b.ring)
            if ch != BLANK and glyph is not None:
                cal.add_template(ch.upper(), glyph.vec, rack=True)
    if not cal.rack_templates:
        for letter, vec in rack_templates:
            cal.add_template(letter, vec, rack=True)
    for key, vec in empty_glyph_samples:
        cal.add_empty_glyph(key, vec)

    rec._rebuild_templates()
    for img, truth, _rack in parsed:
        reading = rec.read(img)
        wrong = 0
        for r in range(profile.height):
            for c in range(profile.width):
                t = truth[r][c]
                got = reading.cells[r][c]
                if (t is None) != (got.letter is None) or (t and (got.letter, got.blank) != t):
                    wrong += 1
        if wrong > 0.03 * profile.width * profile.height:
            raise CalibrationError(
                f"{wrong} squares disagree with the typed board after calibration; "
                "check the board rectangle"
            )
    return cal


def _overlaps(a, b) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def _rows_of(blobs, pitch):
    groups: list[list] = []
    for b in sorted(blobs, key=lambda b: b.rect[1]):
        cy = b.rect[1] + b.rect[3] / 2
        for g in groups:
            gy = g[0].rect[1] + g[0].rect[3] / 2
            if abs(gy - cy) < pitch * 0.5:
                g.append(b)
                break
        else:
            groups.append([b])
    for g in groups:
        g.sort(key=lambda b: b.rect[0])
    return groups

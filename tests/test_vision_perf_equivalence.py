"""Pins what the board reader returns, so speed-ups cannot change a result.

tests/fixtures/vision_equivalence.json holds a compact digest of the output of the reference
implementation for every input below: the real Discord frame at other sizes and cropped, the
checked-in synthetic scenes, generated scenes in every theme and cell size, zoomed, panned and
resized views, frame sequences read by one reader, and frames without a board. Letters, states,
flags, extents and masks are hashed and must match exactly; floating point values may differ by
less than 1e-6. A generated input that is not bit-identical to the one the digest was made from
(another platform or renderer) is skipped, not compared.

    python tests/test_vision_perf_equivalence.py --update          rewrite the digest file
    python tests/test_vision_perf_equivalence.py --dump FILE.pkl   full observations for diffing
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import dataclasses
import functools
import hashlib
import json
import pickle
import platform
import random
import sys
import time
import types
from pathlib import Path

import cv2
import numpy as np
import pytest

from tilelens.rules.profile import demo_profile, load_profile
from tilelens.synth.render import COMMITTED, render
from tilelens.synth.scenes import demo_engine, random_scene
from tilelens.synth.themes import THEMES
from tilelens.synth.views import ViewSpec, random_view, render_view, with_appearance
from tilelens.vision import autoread, locate
from tilelens.vision.autoread import ViewReader, add_screen, calibrate_auto
from tilelens.vision.cells import corner_mark_ink, extract_glyph, find_tile_blobs, to_lab
from tilelens.vision.evaluate import calibrate_on
from tilelens.vision.locate import BoardNotFound, locate_board
from tilelens.vision.recognizer import Calibration, CalibrationError, Recognizer
from tilelens.vision.values import tile_value

ROOT = Path(__file__).parent
SYNTHETIC = ROOT / "fixtures" / "synthetic"
DISCORD = ROOT / "fixtures" / "discord"
DIGESTS = ROOT / "fixtures" / "vision_equivalence.json"
FLOAT_TOL = 1.6e-6
CACHE: Path | None = None

THEME_NAMES = list(THEMES)
CELLS = (24, 36, 48)
REAL_SCALES = (0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6)
LONG_GROUP = 16


def _sha(data: bytes | str, n: int = 12) -> str:
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha1(data).hexdigest()[:n]


def _array_hash(a: np.ndarray) -> str:
    a = np.ascontiguousarray(a)
    return _sha(f"{a.shape}{a.dtype}".encode() + a.tobytes())


def _j(x):
    if isinstance(x, dict):
        return {str(k): _j(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_j(v) for v in x]
    if isinstance(x, np.ndarray):
        return _j(x.tolist())
    if isinstance(x, np.generic):
        return x.item()
    return x


def cached_frame(name: str, make) -> np.ndarray:
    path = CACHE / (name.replace("/", "_") + ".png") if CACHE else None
    if path is not None and path.exists():
        img = cv2.imread(str(path))
        if img is not None:
            return img
    img = make()
    if path is not None:
        CACHE.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(path), img)
    return img


class Ctx:
    """Collects the hash of every input a case used."""

    def __init__(self) -> None:
        self.hashes: list[str] = []

    def frame(self, name: str, make) -> np.ndarray:
        img = cached_frame(name, make)
        self.hashes.append(_array_hash(img))
        return img


def view_obs(view) -> tuple[dict, dict]:
    g = view.grid
    d = {
        "visible": _array_hash(view.visible),
        "count": view.visible_count,
        "used": view.cells_used,
        "extent": view.extent,
        "span": view.span,
        "dims": (g.rows, g.cols),
    }
    f = [g.x0, g.y0, g.pitch_x, g.pitch_y, g.score, view.match_rate, view.margin, *view.canvas]
    return d, {"view": f}


def reading_obs(rd) -> dict:
    cells = [
        [[c.letter, c.blank, c.state, c.uncertain, c.reason, c.candidates, c.value, c.star,
          c.foreign] for c in row]
        for row in rd.cells
    ]  # fmt: skip
    rack = [[t.letter, t.blank, t.uncertain, t.reason, t.rect, t.candidates] for t in rd.rack]
    g = rd.grid
    d = {
        "cells": cells,
        "rack": rack,
        "meta": [rd.visible, rd.bounds, rd.start_open, rd.star_seen, rd.warnings, (g.rows, g.cols)],
        "base": sorted(rd.base_values.items()),
    }
    f = {
        "grid": [g.x0, g.y0, g.pitch_x, g.pitch_y, g.score],
        "score": [c.letter_score for row in rd.cells for c in row],
        "rackscore": [t.letter_score for t in rd.rack],
    }
    if rd.crops is not None:
        d["crops"] = _array_hash(np.stack(rd.crops))
    if rd.features is not None:
        f["feat"] = rd.features.ravel().tolist()
    vecs = getattr(rd, "rack_glyphs", None)
    if vecs is not None:
        d["glyphs"] = [v is None for v in vecs]
        f["glyphs"] = [x for v in vecs if v is not None for x in v.tolist()]
    view = getattr(rd, "view", None)
    if view is not None:
        vd, vf = view_obs(view)
        d["view"] = vd
        f.update(vf)
    return {"d": _j(d), "f": {k: [float(x) for x in v] for k, v in f.items()}}


def error_obs(exc: Exception) -> dict:
    return {"d": {"error": [type(exc).__name__, str(exc)]}, "f": {}}


def merge(frames: list[dict]) -> dict:
    d: dict = {}
    f: dict = {}
    for obs in frames:
        for k, v in obs["d"].items():
            d.setdefault(k, []).append(v)
        for k, v in obs["f"].items():
            f.setdefault(k, []).extend(v)
    return {"d": d, "f": f}


class Clock:
    """The reader re-measures the board's edges on a wall clock; tests drive it by hand."""

    def __init__(self, step: float) -> None:
        self.step = step
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now

    @staticmethod
    def perf_counter() -> float:
        return time.perf_counter()


@contextlib.contextmanager
def clock(step: float):
    c = Clock(step)
    saved = autoread.time
    autoread.time = types.SimpleNamespace(monotonic=c.monotonic, perf_counter=c.perf_counter)
    try:
        yield c
    finally:
        autoread.time = saved


def view_read(reader: ViewReader, img: np.ndarray) -> dict:
    rd = reader.read(img)
    obs = reading_obs(rd)
    obs["d"]["locates"] = reader.locates
    return obs


def one_read(cal: Calibration, profile, img: np.ndarray) -> dict:
    with clock(1.0):
        return view_read(ViewReader(copy.deepcopy(cal), profile), img)


def sequence(cal: Calibration, profile, frames: list[np.ndarray], step: float) -> dict:
    with clock(step) as c:
        reader = ViewReader(copy.deepcopy(cal), profile)
        out = []
        for img in frames:
            c.now += step
            out.append(view_read(reader, img))
    return merge(out)


def recognizer_read(theme: str, cell: int, img: np.ndarray, name: str = "demo_garden") -> dict:
    try:
        cal = recorded_calibration(theme, cell, name)
        return reading_obs(Recognizer(cal, demo_engine(name)[0]).read(img))
    except Exception as exc:
        return error_obs(exc)


def locate_obs(img: np.ndarray, profile) -> dict:
    try:
        d, f = view_obs(locate_board(img, profile))
    except BoardNotFound as exc:
        return error_obs(exc)
    return {"d": _j(d), "f": f}


@functools.cache
def real_setup():
    profile = load_profile(DISCORD / "growing-profile.toml")
    img = cv2.imread(str(DISCORD / "board-grown.png"))
    cal = Calibration.load(DISCORD / "calibration.json")
    return profile, img, cal


def resample(img: np.ndarray, f: float) -> np.ndarray:
    inter = cv2.INTER_AREA if f < 1 else cv2.INTER_LINEAR
    return cv2.resize(img, None, fx=f, fy=f, interpolation=inter)


def real_variant(img: np.ndarray, name: str) -> np.ndarray:
    h, w = img.shape[:2]
    if name == "pad":
        out = np.full((h + 300, w + 500, 3), (54, 51, 49), np.uint8)
        out[120 : 120 + h, 340 : 340 + w] = img
        return out
    crops = {
        "cropl": (0, 0, int(w * 0.25), 0),
        "cropt": (int(h * 0.25), 0, 0, 0),
        "cropr": (0, int(w * 0.3), 0, 0),
        "cropb": (0, 0, 0, int(h * 0.2)),
        "cropall": (int(h * 0.1), int(w * 0.1), int(w * 0.1), int(h * 0.1)),
    }
    t, r, left, b = crops[name]
    return img[t : h - b, left : w - r].copy()


REAL_CELL = (50.0, 20, 24)


def real_edit(img: np.ndarray, kind: str) -> np.ndarray:
    """A few squares changed: a tile drawn on an empty square, or a panel over some squares."""
    out = img.copy()
    pitch, x0, y0 = 51.145408730750646, -361.8537667700608, -534.4695212363442
    if kind == "panel":
        px, py = round(x0 + 20 * pitch), round(y0 + 20 * pitch)
        cv2.rectangle(out, (px, py), (px + 140, py + 100), (60, 40, 40), -1)
        return out
    spots = {"tile1": [(26, 22)], "tile3": [(26, 22), (27, 22), (28, 22)]}[kind]
    for i, (r, c) in enumerate(spots):
        x, y = round(x0 + c * pitch), round(y0 + r * pitch)
        s = round(pitch)
        cv2.rectangle(out, (x + 2, y + 2), (x + s - 3, y + s - 3), (102, 142, 239), -1)
        cv2.putText(out, "KRT"[i % 3], (x + 12, y + s - 12), cv2.FONT_HERSHEY_DUPLEX, 1.5,
                    (30, 30, 30), 3)  # fmt: skip
        cv2.putText(out, "5", (x + s - 18, y + 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                    (30, 30, 30), 1)  # fmt: skip
    return out


def case_real_scale(f: float):
    def run(ctx: Ctx) -> dict:
        profile, img, cal = real_setup()
        frame = ctx.frame(f"real/{f}", lambda: resample(img, f) if f != 1.0 else img)
        return merge([locate_obs(frame, profile), one_read(cal, profile, frame)])

    return run


def case_real_variant(name: str):
    def run(ctx: Ctx) -> dict:
        profile, img, cal = real_setup()
        frame = ctx.frame(f"real/{name}", lambda: real_variant(img, name))
        return one_read(cal, profile, frame)

    return run


def case_real_sequence(kind: str, step: float):
    def run(ctx: Ctx) -> dict:
        profile, img, cal = real_setup()
        names = {
            "resize": [
                "1.0",
                "1.0",
                "0.9",
                "0.9",
                "1.0",
                "1.25",
                "1.25",
                "0.7",
                "1.0",
                "pad",
                "1.0",
            ],
            "edit": ["1.0", "tile1", "tile1", "tile3", "1.0", "panel", "panel", "panel", "1.0"],
        }[kind]
        frames = []
        for n in names:
            if n in ("tile1", "tile3", "panel"):
                make = functools.partial(real_edit, img, n)
            elif n == "pad":
                make = functools.partial(real_variant, img, "pad")
            else:
                make = functools.partial(resample, img, float(n)) if n != "1.0" else (lambda: img)
            frames.append(ctx.frame(f"real/seq-{n}", make))
        return sequence(cal, profile, frames, step)

    return run


def case_fixture(name: str, path: str):
    def run(ctx: Ctx) -> dict:
        theme = name.split("-")[1]
        profile = with_appearance(demo_profile("demo_garden"), theme)
        img = ctx.frame(f"fixture/{name}", lambda: cv2.imread(str(SYNTHETIC / f"{name}.png")))
        cell = json.loads((SYNTHETIC / f"{name}.json").read_text())["cell"]
        rec = recognizer_read(theme, cell, img)
        return merge([rec, one_read(auto_calibration(theme), profile, img)])

    return run


def case_rack_strip(name: str):
    def run(ctx: Ctx) -> dict:
        img = ctx.frame(f"rack/{name}", lambda: cv2.imread(str(DISCORD / f"{name}.png")))
        profile, _, cal = real_setup()
        lab = to_lab(np.array(profile.appearance.rack_tile, np.uint8).reshape(1, 1, 3)).reshape(3)
        h, w = img.shape[:2]
        d: dict = {"blobs": []}
        f: dict = {"ring": [], "glyph": []}
        for b in find_tile_blobs(img, (0, 0, w, h), lab, 22.0, (20, 300)):
            glyph = extract_glyph(b.crop, b.ring)
            d["blobs"].append(
                [
                    b.rect,
                    _array_hash(b.crop),
                    None
                    if glyph is None
                    else [glyph.value_mark, glyph.touches_edge, _array_hash(glyph.vec)],
                    tile_value(img, b.rect),
                ]
            )
            f["ring"] += b.ring.tolist()
            if glyph is not None:
                f["glyph"] += [*glyph.ink.tolist(), glyph.ink_fraction]
        return merge([{"d": _j(d), "f": f}, one_read(cal, profile, img)])

    return run


@functools.cache
def auto_calibration(theme: str, name: str = "demo_garden") -> Calibration:
    """Calibrated on a few screens as a player would; a theme that cannot be calibrated this
    way reads with another theme's calibration (its views then mostly report no board)."""
    profile = with_appearance(demo_profile(name), theme)
    first = None
    for seed in (1000, 1001, 1002, 1003, 1004, 1005):
        scene = random_scene(seed, name, theme=theme, cell=40, tentative=False, min_turn=8)
        img = cached_frame(
            f"cal/{name}-{theme}-{seed}", lambda s=scene: render_view(s, ViewSpec())[0]
        )
        text = "".join(t for t in scene.rack if t)
        try:
            if first is None:
                first = calibrate_auto(img, profile, text)
            else:
                add_screen(first, img, profile, text)
        except CalibrationError:
            continue
    return first if first is not None else auto_calibration("meadow", name)


@functools.cache
def recorded_calibration(theme: str, cell: int, name: str = "demo_garden") -> Calibration:
    return calibrate_on(theme, [1000, 1001, 1002], cell, name)


def calibration_obs(cal: Calibration) -> dict:
    stores = (cal.templates, cal.rack_templates, cal.empty_glyphs)
    optional = (cal.blank_ink, cal.blank_value_ink, cal.label_ink)
    d = {
        "counts": [sorted((k, len(v)) for k, v in store.items()) for store in stores],
        "protos": [sorted(cal.empty), len(cal.tiles), [t.state for t in cal.tiles]],
        "flags": [cal.auto, cal.values_expected, [x is None for x in optional]],
        "frame": [cal.image_size, cal.rack_band, len(cal.tile_backgrounds)],
    }
    g = cal.grid
    protos = [cal.rack_proto, *cal.tiles, *(cal.empty[k] for k in sorted(cal.empty))]
    f = {
        "scalars": [cal.rack_tile, g.x0, g.y0, g.pitch_x, g.pitch_y, *cal.normal_ink],
        "protos": [x for p in protos for x in (*p.mean.tolist(), p.radius)],
        "vecs": [x for st in stores for k in sorted(st) for v in st[k] for x in v.tolist()],
        "colors": [x for t in cal.tile_backgrounds for x in t.tolist()]
        + [x for v in optional if v is not None for x in v.tolist()],
    }
    return {"d": _j(d), "f": {k: [float(x) for x in v] for k, v in f.items()}}


def case_calibration(theme: str, cell: int | None = None):
    def run(ctx: Ctx) -> dict:
        cal = recorded_calibration(theme, cell) if cell else auto_calibration(theme)
        return calibration_obs(cal)

    return run


def case_scene(theme: str, cell: int, seed: int, name: str = "demo_garden"):
    def run(ctx: Ctx) -> dict:
        img = ctx.frame(
            f"scene/{name}-{theme}-{cell}-{seed}",
            lambda: render(random_scene(seed, name, theme=theme, cell=cell))[0],
        )
        profile = with_appearance(demo_profile(name), theme)
        rec = recognizer_read(theme, cell, img, name)
        if name == "demo_garden":
            reader_cal = auto_calibration(theme)
        else:
            reader_cal = auto_calibration(theme, name)
        return merge([rec, one_read(reader_cal, profile, img)])

    return run


VIEW_SPECS = {
    "z075": lambda: ViewSpec(zoom=0.75),
    "z100": lambda: ViewSpec(),
    "z140": lambda: ViewSpec(zoom=1.4, center=(0.3, 0.6)),
    "z180": lambda: ViewSpec(zoom=1.8, center=(0.7, 0.4)),
    "z240": lambda: ViewSpec(zoom=2.4, center=(0.5, 0.5)),
    "wide": lambda: ViewSpec(size=(1280, 720), zoom=0.9),
    "small": lambda: ViewSpec(size=(800, 600), zoom=1.2, center=(0.45, 0.55)),
    "panel": lambda: ViewSpec(
        zoom=1.0,
        panels=[
            ((60, 80, 220, 90), (150, 70, 120), "Nice!"),
            ((700, 300, 200, 120), (60, 60, 60), ""),
        ],
    ),
    "rnd1": lambda: random_view(random.Random(901)),
    "rnd2": lambda: random_view(random.Random(902)),
}


def case_view(theme: str, spec_name: str, seed: int):
    def run(ctx: Ctx) -> dict:
        scene = random_scene(seed, theme=theme, cell=40, tentative=False)
        img = ctx.frame(
            f"view/{theme}-{spec_name}", lambda: render_view(scene, VIEW_SPECS[spec_name]())[0]
        )
        profile = with_appearance(demo_profile("demo_garden"), theme)
        return one_read(auto_calibration(theme), profile, img)

    return run


def with_extra_tiles(scene, count: int):
    free = [(r, c) for r in range(4, 9) for c in range(4, 9) if (r, c) not in scene.tiles][:count]
    tiles = dict(scene.tiles)
    for i, rc in enumerate(free):
        tiles[rc] = ("QZJ"[i % 3], False, COMMITTED)
    return dataclasses.replace(scene, tiles=tiles)


SEQUENCES = {
    "zoom": [ViewSpec(zoom=z) for z in (0.75, 0.75, 1.0, 1.4, 1.4, 1.8, 2.4, 1.4, 0.75)],
    "pan": [ViewSpec(zoom=1.8, center=(0.3 + 0.08 * i, 0.3 + 0.08 * i)) for i in range(7)],
    "resize": [ViewSpec(size=s) for s in ((960, 640), (960, 640), (800, 600), (1280, 720),
                                          (1280, 720), (640, 480), (960, 640))],
}  # fmt: skip


def case_sequence(theme: str, kind: str, step: float):
    def run(ctx: Ctx) -> dict:
        scene = random_scene(31, theme=theme, cell=40, tentative=False)
        profile = with_appearance(demo_profile("demo_garden"), theme)
        frames = []
        if kind == "tiles":
            for i in range(5):
                grown = with_extra_tiles(scene, min(i, 3))
                frames.append(
                    ctx.frame(
                        f"seq/{theme}-tiles-{i}",
                        lambda g=grown: render_view(g, ViewSpec(zoom=0.9))[0],
                    )
                )
        else:
            for i, spec in enumerate(SEQUENCES[kind]):
                frames.append(
                    ctx.frame(f"seq/{theme}-{kind}-{i}", lambda s=spec: render_view(scene, s)[0])
                )
        return sequence(auto_calibration(theme), profile, frames, step)

    return run


def stripes() -> np.ndarray:
    img = np.full((480, 640, 3), 40, np.uint8)
    img[:, ::40] = 220
    img[::40] = 220
    return img


def case_negative(kind: str):
    def run(ctx: Ctx) -> dict:
        profile, real, cal = real_setup()
        rng = np.random.default_rng(5)
        makers = {
            "dark": lambda: np.full((400, 600, 3), 40, np.uint8),
            "white": lambda: np.full((500, 500, 3), 255, np.uint8),
            "noise": lambda: rng.integers(0, 255, (480, 640, 3), dtype=np.uint8),
            "tiny": lambda: real[100:130, 100:140].copy(),
            "patch": lambda: real[300:520, 700:960].copy(),
            "stripes": stripes,
            "around": lambda: cv2.imread(str(DISCORD / "board-around.png")),
            "gray": lambda: cv2.cvtColor(
                cv2.cvtColor(real, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR
            ),
        }
        img = ctx.frame(f"neg/{kind}", makers[kind])
        return merge([locate_obs(img, profile), one_read(cal, profile, img)])

    return run


def exact(*parts) -> str:
    """Hash of values and arrays down to the last bit."""
    h = hashlib.sha1()
    for p in parts:
        if isinstance(p, np.ndarray):
            h.update(f"{p.shape}{p.dtype}".encode())
            h.update(np.ascontiguousarray(p).tobytes())
        else:
            h.update(repr(p).encode())
    return h.hexdigest()[:12]


def glyph_parts(g) -> tuple:
    if g is None:
        return ("no glyph",)
    return (g.vec, g.ink, g.value_mark, g.ink_fraction, g.value_ink, g.touches_edge, g.value_mask)


def prim_source(kind: str, ctx: Ctx):
    profile, img, _ = real_setup()
    if kind.startswith("real"):
        f = float(kind.split("-")[1])
        return profile, ctx.frame(f"real/{f}", lambda: resample(img, f) if f != 1.0 else img)
    if kind.startswith("fixture"):
        name = kind.split(":")[1]
        theme = name.split("-")[1]
        frame = ctx.frame(f"fixture/{name}", lambda: cv2.imread(str(SYNTHETIC / f"{name}.png")))
        return with_appearance(demo_profile("demo_garden"), theme), frame
    _, theme, cell = kind.split("-")
    scene = random_scene(21, "demo_garden", theme=theme, cell=int(cell))
    frame = ctx.frame(f"scene/demo_garden-{theme}-{cell}-21", lambda: render(scene)[0])
    return with_appearance(demo_profile("demo_garden"), theme), frame


def case_prim_cells(kind: str):
    def run(ctx: Ctx) -> dict:
        profile, frame = prim_source(kind, ctx)
        view = locate_board(frame, profile)
        board, grid = autoread.work_frame(frame, view.grid)
        samples, corners, glyphs, retried, scaled, marks = ([] for _ in range(6))
        for r, c in zip(*(a.tolist() for a in np.nonzero(view.visible)), strict=True):
            feat, crop_ = autoread.cell_sample(board, grid, r, c)
            samples.append(exact(feat, crop_))
            corners.append(autoread._corner_covered(crop_, feat))
            glyphs.append(exact(*glyph_parts(extract_glyph(crop_, feat))))
            retried.append(exact(*glyph_parts(extract_glyph(crop_, feat, retry=True))))
            scaled.append(exact(*glyph_parts(extract_glyph(crop_, feat, 0.55))))
            marks.append(exact(corner_mark_ink(crop_, feat)))
        d = {
            "samples": exact(*samples),
            "corners": exact(corners),
            "glyphs": exact(*glyphs),
            "retried": exact(*retried),
            "scaled": exact(*scaled),
            "marks": exact(*marks),
            "n": len(samples),
        }
        return {"d": d, "f": {}}

    return run


def case_prim_blobs(kind: str):
    def run(ctx: Ctx) -> dict:
        profile, frame = prim_source(kind, ctx)
        h, w = frame.shape[:2]
        view = locate_board(frame, profile)
        rng = np.random.default_rng(11)
        wanted = [profile.appearance.rack_tile, *list(profile.appearance.squares.values())]
        for _ in range(3):
            y, x = int(rng.integers(0, h)), int(rng.integers(0, w))
            wanted.append(tuple(int(v) for v in frame[y, x]))
        bands = [(0, 0, w, h), (0, round(0.35 * h), w, h), (w // 5, h // 3, w - w // 7, h - h // 9)]
        bands.append((-20, -20, w // 2, h // 2))
        bands.append((w, 0, w + 5, h))
        out = []
        for color in wanted:
            lab = to_lab(np.array(color, np.uint8).reshape(1, 1, 3)).reshape(3)
            for band in bands:
                for radius, native in ((22.0, None), (10.0, 40.0), (30.0, None)):
                    blobs = find_tile_blobs(frame, band, lab, radius, (0.01 * h, 0.14 * h), native)
                    out.append(exact(len(blobs), *((b.rect, b.crop, b.ring) for b in blobs)))
        rack = autoread.rack_tiles(frame, profile, view)
        return {
            "d": {"blobs": exact(*out), "rack": exact(*((b.rect, b.crop) for b in rack))},
            "f": {},
        }

    return run


def case_prim_measure(kind: str):
    def run(ctx: Ctx) -> dict:
        profile, frame = prim_source(kind, ctx)
        view = locate_board(frame, profile)
        edited = frame.copy()
        h, w = edited.shape[:2]
        cv2.rectangle(edited, (w // 3, h // 3), (w // 2, h // 2), (60, 40, 40), -1)
        out = [locate.measure_extent(f, profile, view) for f in (frame, edited)]
        return {"d": {"extent": _j(out)}, "f": {}}

    return run


def case_prim_phase(seed: int):
    def run(ctx: Ctx) -> dict:
        rng = np.random.default_rng(seed)
        out = []
        for _ in range(120):
            n = int(rng.integers(40, 1400))
            pitch = float(rng.uniform(8, min(300, n / 3)))
            kind = rng.integers(0, 3)
            x = np.arange(n, dtype=np.float32)
            comb = (np.cos(2 * np.pi * (x - rng.uniform(0, pitch)) / pitch) > 0.6).astype(
                np.float32
            )
            noise = rng.random(n).astype(np.float32)
            prof = (noise, comb + 0.2 * noise, np.round(comb * 3) / 3)[kind].astype(np.float32)
            prof = prof / (prof.mean() + 1e-6)
            step = float(rng.choice([0.25, 0.5, max(0.25, pitch / 40), max(0.5, pitch / 24)]))
            out.append(locate._best_phase(prof, pitch, step))
        flat = np.array(out, np.float64)
        return {"d": {"phase": exact(flat)}, "f": {}}

    return run


def case_prim_classify(seed: int):
    def run(ctx: Ctx) -> dict:
        rng = np.random.default_rng(seed)
        out = []
        for i in range(40):
            k = int(rng.integers(1, 9))
            palette = rng.uniform([0, -60, -60], [100, 60, 60], (k, 3)).astype(np.float32)
            if i % 3 == 0 and k > 2:
                palette[rng.integers(1, k)] = palette[0]
            shape = (int(rng.integers(1, 90)), int(rng.integers(1, 120)))
            lab = rng.uniform([0, -60, -60], [100, 60, 60], (*shape, 3)).astype(np.float32)
            pick = rng.integers(0, k, shape)
            near = rng.random(shape) < 0.5
            lab = np.where(near[..., None], palette[pick] + rng.normal(0, 7, (*shape, 3)), lab)
            out.append(exact(locate.classify_pixels(lab.astype(np.float32), palette)))
        profile, frame, _ = real_setup()
        _, palette = locate._palette(profile)
        for f in (0.5, 0.7, 1.0):
            lab = to_lab(resample(frame, f))
            out.append(exact(locate.classify_pixels(lab, palette)))
        return {"d": {"classes": exact(*out)}, "f": {}}

    return run


def all_cases() -> dict:
    cases: dict = {}
    for kind in (
        "real-1.0",
        "real-0.75",
        "real-1.3",
        "fixture:scene-ember-0900",
        "fixture:scene-harbor-0102",
        "scene-meadow-24",
        "scene-apricot-48",
        "scene-slate-36",
    ):
        cases[f"prim/cells-{kind}"] = case_prim_cells(kind)
        cases[f"prim/blobs-{kind}"] = case_prim_blobs(kind)
        cases[f"prim/measure-{kind}"] = case_prim_measure(kind)
    for seed in (1, 2, 3):
        cases[f"prim/phase-{seed}"] = case_prim_phase(seed)
        cases[f"prim/classify-{seed}"] = case_prim_classify(seed)
    for f in REAL_SCALES:
        cases[f"real/scale-{f}"] = case_real_scale(f)
    for name in ("pad", "cropl", "cropt", "cropr", "cropb", "cropall"):
        cases[f"real/{name}"] = case_real_variant(name)
    for kind in ("resize", "edit"):
        for step in (1.0, 0.01):
            cases[f"real/seq-{kind}-{step}"] = case_real_sequence(kind, step)
    for png in sorted(SYNTHETIC.glob("scene-*.png")):
        cases[f"fixture/{png.stem}"] = case_fixture(png.stem, png.name)
    for name in ("rack-vpoknlj", "rack-vyeizae"):
        cases[f"rack/{name}"] = case_rack_strip(name)
    for kind in ("dark", "white", "noise", "tiny", "patch", "stripes", "around", "gray"):
        cases[f"neg/{kind}"] = case_negative(kind)
    for theme in THEME_NAMES:
        cases[f"calib/auto-{theme}"] = case_calibration(theme)
        for cell in CELLS:
            cases[f"calib/rec-{theme}-{cell}"] = case_calibration(theme, cell)
            for seed in (21, 22):
                cases[f"scene/{theme}-{cell}-{seed}"] = case_scene(theme, cell, seed)
        for spec in VIEW_SPECS:
            cases[f"view/{theme}-{spec}"] = case_view(theme, spec, 41)
    for theme in ("meadow", "candy", "ember"):
        for cell in (28, 44):
            cases[f"scene/pocket-{theme}-{cell}"] = case_scene(theme, cell, 23, "demo_pocket")
    for theme in ("meadow", "paper", "ember"):
        for kind in ("zoom", "pan", "resize", "tiles"):
            for step in (1.0, 0.01):
                cases[f"seq/{theme}-{kind}-{step}"] = case_sequence(theme, kind, step)
    return cases


GROUPS = {
    "cells": "cells",
    "rack": "rack",
    "glyphs": "rack",
    "crops": "cells",
    "view": "view",
    "visible": "view",
    "count": "view",
    "used": "view",
    "extent": "view",
    "span": "view",
    "dims": "view",
    "locates": "view",
}


def compact(obs: dict) -> dict:
    parts: dict[str, dict] = {}
    for k, v in obs["d"].items():
        parts.setdefault(GROUPS.get(k, k), {})[k] = v
    d = {g: _sha(json.dumps(v, sort_keys=True, separators=(",", ":")), 6) for g, v in parts.items()}
    f: dict = {}
    for k, v in obs["f"].items():
        if len(v) <= LONG_GROUP:
            f[k] = [round(x, 6) for x in v]
        else:
            f[k] = {"n": len(v), "sum": round(sum(v), 6), "max": round(max(abs(x) for x in v), 6)}
    return {"d": d, "f": f}


def run_case(fn) -> tuple[dict, dict]:
    ctx = Ctx()
    try:
        obs = fn(ctx)
    except Exception as exc:
        obs = error_obs(exc)
    return {"in": _sha("".join(ctx.hashes)), **compact(obs)}, obs


def differences(want: dict, got: dict) -> list[str]:
    out = [
        f"{k} differs"
        for k in sorted(set(want["d"]) | set(got["d"]))
        if want["d"].get(k) != got["d"].get(k)
    ]
    for k in sorted(set(want["f"]) | set(got["f"])):
        a, b = want["f"].get(k), got["f"].get(k)
        if type(a) is not type(b):
            out.append(f"{k} changed shape")
        elif isinstance(a, list):
            if len(a) != len(b) or any(abs(x - y) > FLOAT_TOL for x, y in zip(a, b, strict=True)):
                out.append(f"{k}: {a} != {b}")
        elif a is not None:
            tol = FLOAT_TOL * (a["n"] + 2)
            if (
                a["n"] != b["n"]
                or abs(a["sum"] - b["sum"]) > tol
                or abs(a["max"] - b["max"]) > FLOAT_TOL
            ):
                out.append(f"{k}: {a} != {b}")
    return out


DIGEST_PLATFORM = ("Darwin", "arm64")
CASES = all_cases()
QUICK = [c for c in CASES if c.split("/")[0] in ("real", "fixture", "rack", "neg")]
BROAD = [c for c in CASES if c not in QUICK]


@pytest.fixture(scope="module")
def stored() -> dict:
    return json.loads(DIGESTS.read_text())["cases"]


def check(cid: str, stored: dict) -> None:
    if (platform.system(), platform.machine()) != DIGEST_PLATFORM:
        pytest.skip("the digest was made on macOS arm64; other CPUs round differently")
    want = stored[cid]
    got, _ = run_case(CASES[cid])
    if got["in"] != want["in"]:
        pytest.skip("the input is not bit-identical to the one the digest was made from")
    assert not differences(want, got), differences(want, got)


def _comb_reference(profile: np.ndarray, pitch: float, phase: float) -> float:
    grid = np.arange(len(profile), dtype=np.float32)
    on = np.interp(np.arange(phase, len(profile) - 1, pitch), grid, profile)
    off = np.interp(np.arange(phase + pitch / 2, len(profile) - 1, pitch), grid, profile)
    return float(on.mean() - off.mean()) if len(on) and len(off) else 0.0


def _best_phase_reference(profile: np.ndarray, pitch: float, step: float) -> tuple[float, float]:
    sm = locate._smooth(profile, pitch * 0.05)
    best = max((_comb_reference(sm, pitch, ph), ph) for ph in np.arange(0, pitch, step))
    return best[1], best[0]


@pytest.mark.parametrize("pitch", [12.0, 12.5, 16.0, 25.0, 32.0, 40.75, 50.0, 17.3, 23.456])
def test_best_phase_matches_scoring_every_phase(pitch):
    rng = np.random.default_rng(int(pitch * 100))
    for n in (61, 100, 199, 200, 201, 400, 513):
        for kind in range(3):
            x = np.arange(n, dtype=np.float32)
            comb = (np.cos(2 * np.pi * (x - rng.uniform(0, pitch)) / pitch) > 0.5).astype(
                np.float32
            )
            noise = rng.random(n).astype(np.float32)
            prof = (noise, comb + 0.1 * noise, np.round(comb * 2) / 2)[kind]
            prof = (prof / (prof.mean() + 1e-6)).astype(np.float32)
            for step in (0.25, 0.5, max(0.25, pitch / 40)):
                assert locate._best_phase(prof, pitch, step) == _best_phase_reference(
                    prof, pitch, step
                )


def test_digest_covers_exactly_the_cases(stored):
    assert sorted(stored) == sorted(CASES)


@pytest.mark.parametrize("cid", QUICK)
def test_fixture_outputs_are_unchanged(cid, stored):
    check(cid, stored)


@pytest.mark.slow
@pytest.mark.parametrize("cid", BROAD)
def test_generated_outputs_are_unchanged(cid, stored):
    check(cid, stored)


def main() -> int:
    global CACHE
    ap = argparse.ArgumentParser()
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--out", type=Path, default=DIGESTS, help="where --update writes the digests")
    ap.add_argument("--dump", type=Path)
    ap.add_argument("--cache", type=Path, help="directory that keeps the generated inputs")
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    CACHE = args.cache
    digests, full = {}, {}
    for cid, fn in CASES.items():
        if args.only not in cid:
            continue
        t0 = time.perf_counter()
        digests[cid], full[cid] = run_case(fn)
        err = full[cid]["d"].get("error")
        print(f"{cid:34s} {time.perf_counter() - t0:6.2f}s {err or ''}", file=sys.stderr)
    if args.update:
        env = {"cv2": cv2.__version__, "numpy": np.__version__}
        text = json.dumps({"env": env, "cases": digests}, separators=(",", ":"), sort_keys=True)
        args.out.write_text(text + "\n")
    if args.dump:
        args.dump.write_bytes(pickle.dumps(full))
    return 0


if __name__ == "__main__":
    sys.exit(main())

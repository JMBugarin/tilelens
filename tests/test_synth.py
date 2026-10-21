import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from tilelens.rules.profile import demo_profile
from tilelens.synth.render import COMMITTED, RECENT, TENTATIVE, Scene, layout, render
from tilelens.synth.scenes import random_scene, write_fixtures
from tilelens.synth.themes import DEV_THEMES, HELD_OUT_THEMES, THEMES


@pytest.fixture(scope="module")
def garden():
    return demo_profile("demo_garden")


def digest(img):
    return hashlib.sha256(img.tobytes()).hexdigest()


def inset_pixel(img, rect, fx=0.18, fy=0.2):
    x, y, w, h = rect
    return tuple(int(v) for v in img[round(y + h * fy), round(x + w * fx)])


def test_theme_split():
    assert HELD_OUT_THEMES == ["ember"]
    assert len(DEV_THEMES) >= 4
    assert len({t.font for t in THEMES.values()}) >= 4


@pytest.mark.parametrize("theme", list(THEMES))
def test_every_theme_renders_deterministically(theme):
    a, la = render(random_scene(5, theme=theme, cell=32))
    b, lb = render(random_scene(5, theme=theme, cell=32))
    assert digest(a) == digest(b) and la == lb
    assert a.shape[1::-1] == tuple(la["geometry"]["size"])
    assert la["held_out"] == THEMES[theme].held_out


def test_labels_match_scene(garden):
    scene = random_scene(11, cell=36, tentative=True)
    _, labels = render(scene)
    for r in range(garden.height):
        for c in range(garden.width):
            tile = scene.tiles.get((r, c))
            lab = labels["board"][r][c]
            if tile is None:
                assert lab is None
            else:
                assert (lab["letter"], lab["blank"], lab["state"]) == tile
    assert len(labels["rack"]) == garden.rack_size
    assert any(s["state"] == TENTATIVE for row in labels["board"] for s in row if s)


def test_geometry_cells_inside_board(garden):
    geo = layout(garden, 40)
    bx, by, bw, bh = geo.board
    for r, c in [(0, 0), (12, 12), (6, 3)]:
        x, y, w, h = geo.cell_rect(r, c)
        assert bx <= x and x + w <= bx + bw and by <= y and y + h <= by + bh
    rx, ry, rw, rh = geo.rack
    assert all(rx <= s[0] and s[0] + s[2] <= rx + rw for s in geo.rack_slots)


def test_tile_states_and_blanks_are_visible(garden):
    theme = THEMES["meadow"]
    scene = Scene(
        garden,
        tiles={
            (6, 5): ("A", False, COMMITTED),
            (6, 6): ("B", False, TENTATIVE),
            (6, 7): ("C", False, RECENT),
            (6, 8): ("D", True, COMMITTED),
        },
        rack=["E", None, "?"],
        theme=theme,
        cell=40,
    )
    img, labels = render(scene)
    geo = layout(garden, 40)
    gap = 40 * theme.gap * 0.6
    fills = [inset_pixel(img, geo.cell_rect(6, c, gap)) for c in (5, 7)]
    assert fills[0] == theme.tile and fills[1] == theme.recent_tile
    x, y, w, h = geo.cell_rect(6, 6, gap)
    edge = tuple(int(v) for v in img[y + h // 2, x + 1])
    assert edge == theme.tentative_edge

    def value_ink(c):
        x, y, w, h = geo.cell_rect(6, c, gap)
        patch = img[
            round(y + h * 0.7) : round(y + h * 0.9), round(x + w * 0.72) : round(x + w * 0.92)
        ]
        return np.abs(patch.astype(int) - np.array(theme.tile)).sum(axis=2).max()

    assert value_ink(5) > 100
    assert value_ink(8) < 30
    assert labels["rack"] == [
        {"letter": "E", "blank": False},
        None,
        {"letter": None, "blank": True},
        None,
        None,
        None,
        None,
    ]


def test_scaling(garden):
    base, lb = render(random_scene(3, theme="slate", cell=30))
    big, lg = render(random_scene(3, theme="slate", cell=30, scale=2.0))
    assert big.shape[0] == round(base.shape[0] * 2) and big.shape[1] == round(base.shape[1] * 2)
    assert lg["geometry"]["pitch"] == pytest.approx(lb["geometry"]["pitch"] * 2)


def test_drag_and_fade(garden):
    scene = random_scene(4, theme="candy", cell=32)
    plain, _ = render(scene)
    scene.drag = (200.0, 200.0, "Q")
    dragged, labels = render(scene)
    assert labels["dragging"] and digest(plain) != digest(dragged)
    scene.drag = None
    pos = next(iter(scene.tiles))
    scene.fade = {pos: 0.4}
    faded, _ = render(scene)
    assert digest(faded) != digest(plain)


def test_write_fixtures(tmp_path):
    paths = write_fixtures(tmp_path, [1, 2], theme="paper")
    assert len(paths) == 2 and all(p.is_file() for p in paths)
    labels = json.loads(paths[0].with_suffix(".json").read_text())
    assert labels["theme"] == "paper"


FIXTURES = sorted((Path(__file__).parent / "fixtures" / "synthetic").glob("*.png"))


@pytest.mark.parametrize("png", FIXTURES, ids=lambda p: p.stem)
def test_committed_fixtures_regenerate(png):
    labels = json.loads(png.with_suffix(".json").read_text())
    seed = int(png.stem.rsplit("-", 1)[1])
    img, fresh = render(random_scene(seed, theme=labels["theme"]))
    assert fresh == labels
    stored = cv2.imread(str(png))
    assert stored.shape == img.shape
    assert np.abs(stored.astype(int) - img.astype(int)).mean() < 2.0

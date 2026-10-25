"""The first round: nothing is on the board and a spinning star sits on the start square.

The star changes shape every frame and used to be read as an unreadable tile, which left the
start square unknown, so no first word was ever suggested until the animation stopped. An empty
start square now means nobody has played, whatever the star looks like.
"""

import copy
import math

import cv2
import numpy as np
import pytest
from test_tracking import make
from test_tracking import setup as tracking_setup

from tilelens.state.controller import Status
from tilelens.synth.views import ViewSpec, render_view
from tilelens.vision.autoread import has_star

VIOLET = (214, 90, 140)  # BGR
SPIN = [1.0, 0.8, 0.55, 0.3, 0.12, 0.3, 0.55, 0.8]


def with_star(img, labels, start, width, color=VIOLET):
    v = labels["view"]
    r, c = start
    cx = v["x0"] + (c + 0.5) * v["pitch"]
    cy = v["y0"] + (r + 0.5) * v["pitch"]
    pts = []
    for i in range(10):
        ang = -math.pi / 2 + i * math.pi / 5
        rad = (0.42 if i % 2 == 0 else 0.18) * v["pitch"]
        pts.append([cx + rad * math.cos(ang) * width, cy + rad * math.sin(ang)])
    out = img.copy()
    cv2.fillPoly(out, [np.array(pts, np.int32)], color, cv2.LINE_AA)
    return out


def empty_scene(scene):
    scene = copy.deepcopy(scene)
    scene.tiles = {}
    scene.rack = ["A", "E", "R", "T", "N", "S", "O"]
    return scene


@pytest.fixture(scope="module")
def start_setup():
    return tracking_setup.__wrapped__()


def make_with_clock(start_setup):
    ctrl, source, solver, scene = make(start_setup)
    t = [0.0]
    ctrl.clock = lambda: t[0]
    ctrl.tick_time = t
    return ctrl, source, solver, scene


def spin(ctrl, source, img, labels, start, n=24):
    snaps = []
    for i in range(n):
        source.frame = with_star(img, labels, start, SPIN[i % len(SPIN)])
        ctrl.tick_time[0] += 0.1
        ctrl.tick()
        snaps.append(ctrl.snapshot)
    return snaps


def test_a_spinning_star_does_not_stop_the_first_suggestions(start_setup):
    profile, scene, _ = start_setup
    ctrl, source, solver, _ = make_with_clock(start_setup)
    scene = empty_scene(scene)
    img, labels = render_view(scene, ViewSpec(zoom=0.75))
    start = profile.start_squares[0]
    snaps = spin(ctrl, source, img, labels, start)
    ready = [s for s in snaps if s.status == Status.READY]
    assert ready and snaps[-1].status == Status.READY, [s.detail for s in snaps]
    # once ready it stays ready while the star keeps spinning, and no search is repeated
    first = next(i for i, s in enumerate(snaps) if s.status == Status.READY)
    assert all(s.status == Status.READY for s in snaps[first:])
    assert len({s.pid for s in snaps[first:]}) == 1
    moves = snaps[-1].result.moves
    assert moves and all(any((t.row, t.col) == start for t in m.tiles) for m in moves), (
        "a first word has to cover the start square"
    )
    assert ctrl.last_reading.start_open and ctrl.last_reading.star_seen


def test_the_first_suggestions_do_not_wait_for_the_star_to_stop(start_setup):
    profile, scene, _ = start_setup
    ctrl, source, solver, _ = make_with_clock(start_setup)
    img, labels = render_view(empty_scene(scene), ViewSpec(zoom=0.75))
    snaps = spin(ctrl, source, img, labels, profile.start_squares[0], n=12)
    assert any(s.status == Status.READY for s in snaps)


def test_a_played_word_on_the_start_square_ends_the_starting_round(start_setup):
    profile, scene, _ = start_setup
    ctrl, source, solver, played = make_with_clock(start_setup)
    played.tiles[profile.start_squares[0]] = ("A", False, "committed")
    source.frame = render_view(played, ViewSpec(zoom=0.75))[0]
    for _ in range(8):
        ctrl.tick_time[0] += 0.1
        ctrl.tick()
    reading = ctrl.last_reading
    assert not reading.start_open and not reading.star_seen
    assert ctrl.snapshot.board is not None and ctrl.snapshot.board.tiles()


def test_tiles_seen_elsewhere_while_the_star_shows_are_not_played_tiles(start_setup):
    profile, scene, _ = start_setup
    ctrl, source, solver, played = make_with_clock(start_setup)
    start = profile.start_squares[0]
    played.tiles = {k: v for k, v in played.tiles.items() if k != start}  # start still open
    img, labels = render_view(played, ViewSpec(zoom=0.75))
    source.frame = with_star(img, labels, start, 1.0)
    for _ in range(6):
        ctrl.tick_time[0] += 0.1
        ctrl.tick()
    reading = ctrl.last_reading
    assert reading.start_open
    assert not reading.placed().tiles() and reading.tentative_squares()


def test_the_star_is_found_by_its_violet():
    beige = np.full((48, 48, 3), (200, 225, 250), np.uint8)
    assert not has_star(beige)
    star = beige.copy()
    cv2.fillPoly(
        star,
        [
            np.array(
                [
                    [24, 6],
                    [30, 20],
                    [42, 20],
                    [32, 28],
                    [36, 42],
                    [24, 33],
                    [12, 42],
                    [16, 28],
                    [6, 20],
                    [18, 20],
                ],
                np.int32,
            )
        ],
        VIOLET,
    )
    assert has_star(star)
    blue = beige.copy()
    blue[8:40, 8:40] = (250, 200, 140)  # a double-letter square's blue is not violet
    assert not has_star(blue)

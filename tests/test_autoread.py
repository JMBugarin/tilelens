import random

import numpy as np
import pytest

from tilelens.rules.profile import demo_profile
from tilelens.synth.scenes import random_scene
from tilelens.synth.views import ViewSpec, random_view, render_view, with_appearance
from tilelens.vision.autoread import ViewReader, add_screen, calibrate_auto
from tilelens.vision.locate import BoardNotFound, locate_board
from tilelens.vision.recognizer import UNSEEN, CalibrationError

THEME = "apricot"


@pytest.fixture(scope="module")
def profile():
    return with_appearance(demo_profile("demo_garden"), THEME)


def rack_text(labels):
    return "".join("?" if t["blank"] else t["letter"] for t in labels["rack"] if t)


@pytest.fixture(scope="module")
def calibration(profile):
    first = None
    for seed in (1000, 1001, 1002, 1003, 1004, 1005):
        scene = random_scene(seed, theme=THEME, cell=40, tentative=False, min_turn=8)
        img, labels = render_view(scene, ViewSpec())
        if first is None:
            first = calibrate_auto(img, profile, rack_text(labels))
        else:
            add_screen(first, img, profile, rack_text(labels))
    return first


def test_appearance_from_theme(profile):
    assert profile.appearance.squares["."] is not None
    assert len(profile.appearance.squares) == 1 + len(profile.raw["board"]["legend"])


@pytest.mark.parametrize("seed", range(24))
def test_locate_partial_views(profile, seed):
    rng = random.Random(seed)
    scene = random_scene(seed, theme=THEME, cell=40, tentative=False)
    img, labels = render_view(scene, random_view(rng))
    truth = labels["view"]
    try:
        view = locate_board(img, profile)
    except BoardNotFound:
        assert np.array(truth["visible"]).sum() < 40
        return
    assert abs(view.grid.pitch_x - truth["pitch"]) / truth["pitch"] < 0.02
    assert abs(view.grid.x0 - truth["x0"]) < 0.15 * truth["pitch"]
    assert abs(view.grid.y0 - truth["y0"]) < 0.15 * truth["pitch"]


def test_no_board_in_view(profile):
    with pytest.raises(BoardNotFound):
        locate_board(np.full((400, 600, 3), 40, np.uint8), profile)


def test_auto_calibration_learns_rack_and_labels(calibration):
    assert len(calibration.rack_templates) >= 10
    assert calibration.auto and calibration.empty_glyphs


def test_auto_calibration_rejects_wrong_rack(profile):
    scene = random_scene(1000, theme=THEME, cell=40, tentative=False, min_turn=8)
    img, labels = render_view(scene, ViewSpec())
    with pytest.raises(CalibrationError, match="rack tiles"):
        calibrate_auto(img, profile, rack_text(labels) + "Z")


@pytest.mark.parametrize("seed", range(16))
def test_reading_partial_views_never_guesses(profile, calibration, seed):
    rng = random.Random(100 + seed)
    scene = random_scene(seed, theme=THEME, cell=40, tentative=False)
    img, labels = render_view(scene, random_view(rng))
    reader = ViewReader(calibration, profile)
    reading = reader.read(img)
    truth_visible = np.array(labels["view"]["visible"])
    if not reading.visible:
        assert truth_visible.sum() < 40
        return
    silent = []
    seen = correct = 0
    for r, row in enumerate(labels["board"]):
        for c, truth in enumerate(row):
            got = reading.cells[r][c]
            if got.state == UNSEEN or got.uncertain:
                continue
            seen += 1
            if truth is None:
                ok = got.letter is None
            else:
                ok = got.letter == truth["letter"] and got.blank == truth["blank"]
            if ok:
                correct += 1
            else:
                silent.append((r, c, truth, got.letter, got.blank))
    assert not silent, silent
    assert seen > 0
    for t, got in zip([t for t in labels["rack"] if t], reading.rack, strict=False):
        if not got.uncertain:
            assert got.blank == t["blank"] and (t["blank"] or got.letter == t["letter"])


@pytest.mark.parametrize("seed", range(1100, 1112))
def test_unaided_calibration_is_right_or_asks(profile, seed):
    from tilelens.vision.autoread import NeedsLetters, calibrate_unaided

    scene = random_scene(seed, theme=THEME, cell=40, tentative=False, min_turn=8)
    img, labels = render_view(scene, ViewSpec())
    truth = rack_text(labels)
    try:
        cal = calibrate_unaided(img, profile)
    except NeedsLetters as exc:
        assert len(exc.guess) == len(truth) and exc.unsure
        return
    got = ViewReader(cal, profile).read(img)
    assert "".join("?" if t.blank else t.letter for t in got.rack) == truth

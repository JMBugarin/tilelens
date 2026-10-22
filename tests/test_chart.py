import random

import numpy as np
import pytest

from tilelens.rules.profile import demo_profile
from tilelens.synth.scenes import random_scene
from tilelens.synth.views import (
    ViewSpec,
    random_view,
    render_tile_chart,
    render_view,
    with_appearance,
)
from tilelens.vision.autoread import ViewReader, calibrate_auto
from tilelens.vision.chart import chart_tiles, learn_chart, looks_like_chart
from tilelens.vision.recognizer import UNSEEN, CalibrationError

THEME = "apricot"


@pytest.fixture(scope="module")
def profile():
    return with_appearance(demo_profile("demo_garden"), THEME)


def first_calibration(profile):
    scene = random_scene(1000, theme=THEME, cell=40, tentative=False, min_turn=8)
    img, labels = render_view(scene, ViewSpec(zoom=0.75))
    rack = "".join("?" if t["blank"] else t["letter"] for t in labels["rack"] if t)
    return calibrate_auto(img, profile, rack)


def test_chart_teaches_every_letter(profile):
    chart = render_tile_chart(profile, THEME)
    assert looks_like_chart(chart, profile)
    tiles = chart_tiles(chart, profile)
    assert len(tiles) == len(profile.alphabet) + 1
    cal = first_calibration(profile)
    assert learn_chart(cal, chart, profile) == len(profile.alphabet)
    assert set(cal.rack_templates) == set(profile.alphabet)


def test_partial_chart_is_refused(profile):
    chart = render_tile_chart(profile, THEME)
    cut = chart[: chart.shape[0] // 2].copy()
    assert not looks_like_chart(cut, profile)
    with pytest.raises(CalibrationError, match="tiles"):
        learn_chart(first_calibration(profile), cut, profile)
    with pytest.raises(CalibrationError):
        learn_chart(first_calibration(profile), np.full((300, 400, 3), 200, np.uint8), profile)


def test_reading_after_the_chart_is_accurate_and_never_wrong(profile):
    cal = first_calibration(profile)
    learn_chart(cal, render_tile_chart(profile, THEME), profile)
    tiles = read = wrong = 0
    for seed in range(24):
        scene = random_scene(seed, theme=THEME, cell=40, tentative=False)
        img, labels = render_view(scene, random_view(random.Random(500 + seed)))
        reading = ViewReader(cal, profile).read(img)
        if not reading.visible:
            continue
        for r, row in enumerate(labels["board"]):
            for c, truth in enumerate(row):
                got = reading.cells[r][c]
                if got.state == UNSEEN:
                    continue
                if truth is None:
                    wrong += bool(got.letter and not got.uncertain)
                    continue
                tiles += 1
                if got.uncertain:
                    continue
                if got.letter == truth["letter"] and got.blank == truth["blank"]:
                    read += 1
                else:
                    wrong += 1
    assert tiles > 300
    assert wrong == 0
    assert read / tiles > 0.75


def test_dialog_learns_from_a_chart(profile):
    from PySide6.QtWidgets import QApplication

    from tilelens.ui.auto_calibrate import AutoCalibrationDialog

    QApplication.instance() or QApplication([])
    chart = render_tile_chart(profile, THEME)
    dlg = AutoCalibrationDialog(chart, profile)
    assert "Tile chart found" in dlg.found.text()
    assert not dlg.run()  # needs a board calibration first
    dlg = AutoCalibrationDialog(chart, profile, previous=first_calibration(profile))
    assert dlg.run()
    assert set(dlg.result.rack_templates) == set(profile.alphabet)


def test_new_tile_colors_are_learned_from_one_frame(profile):
    cal = first_calibration(profile)
    learn_chart(cal, render_tile_chart(profile, THEME), profile)
    cal.tile_backgrounds.clear()
    scene = random_scene(3, theme=THEME, cell=40, tentative=False, min_turn=10)
    img, labels = render_view(scene, ViewSpec(zoom=0.75))
    reading = ViewReader(cal, profile).read(img)
    assert cal.tile_backgrounds  # white and highlighted tiles, learned on the spot
    flagged = [c for row in reading.cells for c in row if c.reason == "tile color not seen yet"]
    # only a color on fewer than three tiles waits for a confirmation
    assert len(flagged) <= 2


def test_a_panel_edge_across_a_tile_hides_it(profile):
    import cv2

    from tilelens.vision.autoread import _corner_covered
    from tilelens.vision.cells import PATCH, patch_features, to_lab

    tile = np.full((48, 48, 3), 250, np.uint8)
    feat = patch_features(to_lab(cv2.resize(tile, (PATCH, PATCH))))
    assert not _corner_covered(tile, feat)
    tile[42:, :] = (140, 60, 120)
    assert _corner_covered(tile, feat)

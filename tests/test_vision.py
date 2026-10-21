import copy

import numpy as np
import pytest

from tilelens.synth.render import TENTATIVE, render
from tilelens.synth.scenes import demo_engine, random_scene
from tilelens.vision.evaluate import (
    Score,
    calibrate_on,
    hint_from,
    score_reading,
    truth_rack,
    truth_rows,
)
from tilelens.vision.grid import GridNotFound, detect_grid
from tilelens.vision.recognizer import (
    COMMITTED,
    RECENT,
    SCALE_CHANGED,
    Calibration,
    CalibrationError,
    Recognizer,
    calibrate,
)

CAL_SEEDS = [1000, 1001, 1002]


@pytest.fixture(scope="module")
def profile():
    return demo_engine("demo_garden")[0]


@pytest.fixture(scope="module")
def calibrations():
    return {}


def recognizer_for(theme, profile, calibrations):
    if theme not in calibrations:
        calibrations[theme] = calibrate_on(theme, CAL_SEEDS, 36)
    return Recognizer(copy.deepcopy(calibrations[theme]), profile)


def evaluate(rec, theme, seeds, **scene_kwargs):
    total = Score()
    for seed in seeds:
        img, labels = render(random_scene(seed, theme=theme, cell=36, **scene_kwargs))
        total.merge(score_reading(rec.read(img), labels, f"{theme}:{seed}"))
    return total


@pytest.mark.parametrize("theme", ["meadow", "slate", "candy", "paper", "ember"])
@pytest.mark.parametrize(("cell", "scale"), [(30, 1.0), (44, 1.0), (36, 2.0)])
def test_grid_detection_from_rough_rectangle(theme, cell, scale):
    rng = np.random.default_rng(cell)
    img, labels = render(random_scene(3, theme=theme, cell=cell, scale=scale))
    x, y, w, h = hint_from(labels)
    p = labels["geometry"]["pitch"]
    jitter = rng.uniform(-0.3, 0.3, 4) * p
    grid = detect_grid(
        img, 13, 13, hint=(x + jitter[0], y + jitter[1], w + jitter[2], h + jitter[3])
    )
    assert abs(grid.x0 - x) / p < 0.12 and abs(grid.y0 - y) / p < 0.12
    assert abs(grid.pitch_x - p) / p < 0.01 and abs(grid.pitch_y - p) / p < 0.01


def test_grid_not_found_on_tiny_image():
    with pytest.raises(GridNotFound):
        detect_grid(np.zeros((40, 40, 3), np.uint8), 13, 13)


@pytest.mark.parametrize("theme", ["meadow", "slate", "candy", "paper"])
def test_dev_themes_have_no_silent_errors(theme, profile, calibrations):
    rec = recognizer_for(theme, profile, calibrations)
    s = evaluate(rec, theme, range(12), tentative=False).summary()
    assert s["cell_silent_error_pct"] == 0
    assert s["rack_silent_error_pct"] == 0
    assert s["cell_accuracy_pct"] >= 99.0


def test_held_out_theme(profile, calibrations):
    rec = recognizer_for("ember", profile, calibrations)
    s = evaluate(rec, "ember", range(12), tentative=False).summary()
    assert s["cell_silent_error_pct"] == 0 and s["rack_silent_error_pct"] == 0
    assert s["cell_accuracy_pct"] >= 99.0
    assert s["position_silent_error_pct"] == 0


@pytest.mark.parametrize("theme", ["meadow", "candy", "ember"])
def test_tentative_tiles_are_never_read_as_played(theme, profile, calibrations):
    rec = recognizer_for(theme, profile, calibrations)
    for seed in range(8):
        img, labels = render(random_scene(seed, theme=theme, cell=36, tentative=True))
        reading = rec.read(img)
        for r, row in enumerate(labels["board"]):
            for c, truth in enumerate(row):
                if truth and truth["state"] == TENTATIVE:
                    assert reading.cells[r][c].state not in (COMMITTED, RECENT)
        assert reading.placed().to_rows() != [] and not reading.confident


def _without_blanks(scene):
    scene.tiles = {k: (letter, False, st) for k, (letter, _, st) in scene.tiles.items()}
    scene.rack = [None if t == "?" else t for t in scene.rack]
    return scene


@pytest.mark.parametrize("theme", ["meadow", "paper"])
def test_unseen_blank_style_is_never_silently_a_normal_letter(theme, profile):
    samples = []
    hint = None
    for seed in CAL_SEEDS:
        img, labels = render(
            _without_blanks(random_scene(seed, theme=theme, cell=36, tentative=False, min_turn=8))
        )
        samples.append((img, truth_rows(labels), truth_rack(labels)))
        hint = hint or hint_from(labels)
    rec = Recognizer(calibrate(samples, profile, board_hint=hint), profile)
    assert rec.cal.blank_ink is None
    blanks_seen = 0
    for seed in range(20):
        img, labels = render(random_scene(seed, theme=theme, cell=36, tentative=False))
        reading = rec.read(img)
        for r, row in enumerate(labels["board"]):
            for c, truth in enumerate(row):
                if truth and truth["blank"]:
                    blanks_seen += 1
                    cell = reading.cells[r][c]
                    assert cell.blank or cell.uncertain
    assert blanks_seen > 0


def test_missing_letter_is_flagged_then_learned(profile, calibrations):
    rec = recognizer_for("slate", profile, calibrations)
    rec.cal.templates.pop("E")
    rec.cal.rack_templates.pop("E", None)
    rec._rebuild_templates()
    assert rec.missing_letters == ["E"]
    seed = next(
        s
        for s in range(40)
        if any(
            t and t["letter"] == "E" and t["state"] != TENTATIVE
            for row in render(random_scene(s, theme="slate", cell=36))[1]["board"]
            for t in row
        )
    )
    img, labels = render(random_scene(seed, theme="slate", cell=36))
    reading = rec.read(img)
    e_cells = [
        (r, c)
        for r, row in enumerate(labels["board"])
        for c, t in enumerate(row)
        if t and t["letter"] == "E" and t["state"] != TENTATIVE
    ]
    assert all(reading.cells[r][c].uncertain for r, c in e_cells)
    fixes = {(r, c): ("E", labels["board"][r][c]["blank"]) for r, c in e_cells}
    rec.learn_from_corrections(img, reading, fixes)
    again = rec.read(img)
    assert all(
        again.cells[r][c].letter == "E" and not again.cells[r][c].uncertain for r, c in e_cells
    )


def test_calibration_round_trip(profile, calibrations, tmp_path):
    rec = recognizer_for("meadow", profile, calibrations)
    path = tmp_path / "cal.json"
    rec.cal.save(path)
    back = Recognizer(Calibration.load(path), profile)
    img, _ = render(random_scene(7, theme="meadow", cell=36))
    assert back.read(img).signature() == rec.read(img).signature()


def test_calibration_rejects_bad_input(profile):
    img, labels = render(random_scene(1000, theme="slate", cell=36, tentative=False, min_turn=8))
    rows, rack, hint = truth_rows(labels), truth_rack(labels), hint_from(labels)
    with pytest.raises(CalibrationError, match="rows of"):
        calibrate([(img, rows[:-1], rack)], profile, board_hint=hint)
    with pytest.raises(CalibrationError, match="rack tiles"):
        calibrate([(img, rows, rack + "Z")], profile, board_hint=hint)
    with pytest.raises(CalibrationError):
        calibrate([(img, ["." * 13] * 13, rack)], profile, board_hint=hint)
    shifted = [r[1:] + "." for r in rows]
    with pytest.raises(CalibrationError):
        calibrate([(img, shifted, rack)], profile, board_hint=hint)


def test_scale_change_is_reported(profile, calibrations):
    rec = recognizer_for("meadow", profile, calibrations)
    img, _ = render(random_scene(5, theme="meadow", cell=36, scale=1.25, tentative=False))
    reading = rec.read(img)
    assert SCALE_CHANGED in reading.warnings and not reading.confident


def test_not_visible(profile, calibrations):
    rec = recognizer_for("meadow", profile, calibrations)
    w, h = rec.cal.image_size
    reading = rec.read(np.full((h, w, 3), 30, np.uint8))
    assert not reading.visible and not reading.confident


def test_reading_helpers(profile, calibrations):
    rec = recognizer_for("slate", profile, calibrations)
    for seed in range(20):
        img, labels = render(random_scene(seed, theme="slate", cell=36, tentative=False))
        reading = rec.read(img)
        if reading.confident:
            break
    assert reading.confident
    assert reading.placed().to_rows() == truth_rows(labels)
    assert "".join(sorted(reading.rack_counter().elements())) == "".join(sorted(truth_rack(labels)))
    assert reading.elapsed < 0.5


def test_calibrate_and_read_cli(tmp_path, capsys):
    import cv2

    from tilelens.app import main
    from tilelens.rules.profile import demo_profile_dir

    args = ["calibrate", "--profile", str(demo_profile_dir() / "demo_garden.toml")]
    for i, seed in enumerate(CAL_SEEDS):
        img, labels = render(
            random_scene(seed, theme="paper", cell=36, tentative=False, min_turn=8)
        )
        cv2.imwrite(str(tmp_path / f"{i}.png"), img)
        (tmp_path / f"{i}.txt").write_text("\n".join(truth_rows(labels)))
        args += ["--image", str(tmp_path / f"{i}.png"), "--board", str(tmp_path / f"{i}.txt")]
        args += ["--rack", truth_rack(labels)]
    x, y, w, h = hint_from(labels)
    args += ["--rect", f"{x + 5},{y - 4},{w - 3},{h + 6}", "--out", str(tmp_path / "cal.json")]
    assert main(args) == 0
    assert "saved calibration" in capsys.readouterr().out
    img, labels = render(random_scene(4, theme="paper", cell=36, tentative=False))
    cv2.imwrite(str(tmp_path / "x.png"), img)
    code = main(
        ["read", "--profile", str(demo_profile_dir() / "demo_garden.toml"),
         "--calibration", str(tmp_path / "cal.json"), "--image", str(tmp_path / "x.png")]
    )  # fmt: skip
    out = capsys.readouterr().out
    assert code in (0, 3)
    assert out.splitlines()[:13] == truth_rows(labels) or "!" in out

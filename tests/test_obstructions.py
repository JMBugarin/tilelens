"""Things that are not the game must never be read as tiles.

Discord's own interface, another app's window and the game's panels all lie over or beside the
board. Dark ones (Discord's dark theme, a code editor, the letterbox) used to be learned as a tile
color, after which every letter of their text read as a tile.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from tilelens.rules.lexicon import Lexicon
from tilelens.rules.profile import load_profile
from tilelens.vision.autoread import ObstructionMap, ViewReader
from tilelens.vision.lexfix import valid_word_cells
from tilelens.vision.recognizer import Calibration, tile_like

FIXTURES = Path(__file__).parent / "fixtures" / "discord"


@pytest.fixture(scope="module")
def profile():
    return load_profile(FIXTURES / "growing-profile.toml")


@pytest.fixture
def board():
    return cv2.imread(str(FIXTURES / "board-grown.png"))


def reader(profile):
    return ViewReader(Calibration.load(FIXTURES / "calibration.json"), profile)


def cover(img, x0, y0, x1, y1, lines):
    """A dark window with light text over part of the screen."""
    out = img.copy()
    out[y0:y1, x0:x1] = (30, 30, 30)
    for i, text in enumerate(lines):
        cv2.putText(out, text, (x0 + 10, y0 + 40 + i * 50), cv2.FONT_HERSHEY_SIMPLEX, 1.3,
                    (235, 235, 235), 2, cv2.LINE_AA)  # fmt: skip
    return out


def tiles_in(reading, x0, y0, x1, y1):
    g = reading.view.grid
    found = []
    for r, row in enumerate(reading.cells):
        for c, cell in enumerate(row):
            if cell.letter and cell.state in ("committed", "recent"):
                x, y, w, h = g.cell_rect(r, c)
                if x0 <= x + w / 2 <= x1 and y0 <= y + h / 2 <= y1:
                    found.append((r, c, cell.letter))
    return found


def test_dark_colors_are_never_a_tile_color():
    assert not tile_like(np.array([35.0, -2.0, -3.0]))  # charcoal
    assert not tile_like(np.array([7.0, 0.0, 0.0]))  # black
    assert tile_like(np.array([69.0, 33.0, 37.0])) and tile_like(np.array([100.0, 0.0, 0.0]))
    cal = Calibration.load(FIXTURES / "calibration.json")
    before = len(cal.tile_backgrounds)
    assert not cal.add_tile_background(np.array([30.0, 0.0, 0.0]))
    assert len(cal.tile_backgrounds) == before


def test_a_calibration_that_learned_a_dark_color_is_cleaned_when_loaded(tmp_path):
    cal = Calibration.load(FIXTURES / "calibration.json")
    cal.tile_backgrounds.append(np.array([35.0, -2.0, -3.0], np.float32))
    cal.tile_backgrounds.append(np.array([7.0, 0.0, 0.0], np.float32))
    path = tmp_path / "poisoned.json"
    path.write_text(cal.to_json())
    clean = Calibration.load(path)
    assert all(tile_like(t) for t in clean.tile_backgrounds)
    assert len(clean.tile_backgrounds) == len(cal.tile_backgrounds) - 2


def test_text_in_a_dark_window_over_the_board_is_not_read_as_tiles(profile, board):
    r = reader(profile)
    window = (1000, 280, 1560, 520)  # over empty squares on the left of the board
    covered = cover(board, *window, ["MMMM WWWW MMW", "DEMM MWMW WM"])
    # even a calibration that was taught the window's colors cannot be made to believe it
    r.cal.tile_backgrounds.append(np.array([11.0, 0.0, 0.0], np.float32))
    reading = r.read(covered)
    assert tiles_in(reading, *window) == []
    assert tiles_in(reading, 0, 0, 1e9, 1e9) != []  # the real tiles are still read


def test_the_real_tiles_beside_a_foreign_window_are_unharmed(profile, board):
    base = reader(profile).read(board)
    expected = {(t.row, t.col): t.letter for t in base.placed().tiles()}
    r = reader(profile)
    covered = cover(board, 270, 560, 700, 1000, ["MMMMMM", "WWWWWW", "MWMWMW"])
    for _ in range(3):
        reading = r.read(covered)
    got = {(t.row, t.col): t.letter for t in reading.placed().tiles()}
    assert got == expected


def test_a_window_that_stays_is_remembered_and_forgotten_when_it_goes(profile, board):
    r = reader(profile)
    window = (270, 140, 700, 480)
    covered = cover(board, *window, ["MMMMMM", "WWWWWW", "MWMWMW"])
    for _ in range(3):
        r.read(covered)
    assert r.obstructions.heat.max() >= 1.5
    flagged = sum(1 for row in r.read(covered).cells for c in row if c.foreign)
    assert flagged > 10
    # the game's own dark turn bar over the board's top row is foreign too, and stays so
    baseline = sum(1 for row in reader(profile).read(board).cells for c in row if c.foreign)
    for _ in range(40):
        reading = r.read(board)
    assert sum(1 for row in reading.cells for c in row if c.foreign) == baseline


def test_the_obstruction_map_needs_the_same_square_to_be_foreign_twice():
    m = ObstructionMap()
    rect, size = (100, 100, 50, 50), (3456, 2234)
    assert not m.blocked(rect, size)
    m.mark(rect, size)
    m.decay()
    assert not m.blocked(rect, size)
    m.mark(rect, size)
    assert m.blocked(rect, size)
    assert not m.blocked((1500, 900, 50, 50), size)


def test_only_tiles_inside_whole_words_are_learned_from(profile, board):
    reading = reader(profile).read(board)
    words = Lexicon.from_words(["FACIA", "DENIED", "UNROUGH", "AB"])
    cells = valid_word_cells(reading, words)
    letters = {reading.cells[r][c].letter for r, c in cells}
    assert len(cells) == 5 + 6 + 7 and letters >= set("FACIDENUROGH")
    assert valid_word_cells(reading, Lexicon.from_words(["ZZ"])) == set()

import copy
from collections import Counter

import pytest

from tilelens.rules.board import WALL_CELL, Board
from tilelens.state.controller import WAITING, LiveController, Status
from tilelens.state.memory import BoardMemory, Coverage
from tilelens.state.worker import InlineSolver
from tilelens.synth.render import COMMITTED, TENTATIVE
from tilelens.synth.scenes import demo_engine, random_scene
from tilelens.synth.views import ViewSpec, render_view, with_appearance
from tilelens.vision.autoread import ViewReader, calibrate_auto

THEME = "apricot"


def rows_board(rows):
    return Board.from_rows(rows)


def test_memory_tracks_until_the_board_can_change_out_of_view():
    mem = BoardMemory()
    full = rows_board(["A..", "B..", "..."])
    rack = Counter("XYZ")
    assert mem.update(full, rack) == (full, Coverage.FULL)
    partial = rows_board(["A.#", "B.#", "###"])
    assert mem.update(partial, rack) == (full, Coverage.TRACKING)
    # the player's own move: the others move next, maybe out of view
    moved = rows_board(["AX#", "B.#", "###"])
    after = Counter("YZQ")
    assert mem.update(moved, after) == (moved, Coverage.PARTIAL)
    assert mem.update(rows_board(["AX.", "B..", "..."]), after)[1] == Coverage.FULL
    assert mem.update(moved, after) == (moved, Coverage.PARTIAL)
    # someone else's move in view starts a new record, filled in by panning
    other = rows_board(["AX#", "BD#", "###"])
    assert mem.update(other, after) == (other, Coverage.PARTIAL)
    assert mem.update(rows_board(["###", "#D.", "#.C"]), after)[1] == Coverage.PARTIAL
    merged, cov = mem.update(rows_board(["AX.", "##.", ".##"]), after)
    assert cov == Coverage.TRACKING
    assert merged == rows_board(["AX.", "BD.", "..C"])


def test_memory_accumulates_squares_hidden_by_the_rack():
    mem = BoardMemory()
    rack = Counter("XY")
    top = rows_board(["A..", "B..", "###"])
    assert mem.update(top, rack) == (top, Coverage.PARTIAL)
    bottom = rows_board(["###", "B..", ".C."])
    assert mem.update(bottom, rack) == (rows_board(["A..", "B..", ".C."]), Coverage.TRACKING)


class FrameSource:
    def __init__(self):
        self.frame = None

    def grab(self):
        return self.frame


def rack_text(labels):
    return "".join("?" if t["blank"] else t["letter"] for t in labels["rack"] if t)


@pytest.fixture(scope="module")
def setup():
    profile = with_appearance(demo_engine("demo_garden")[0], THEME)
    scene = random_scene(7, theme=THEME, cell=40, tentative=False, min_turn=6)
    scene.rack = [t for t in scene.rack if t != "?"] or ["A"]
    img, labels = render_view(scene, WHOLE)
    cal = calibrate_auto(img, profile, rack_text(labels))
    reader = ViewReader(cal, profile)
    reading = reader.read(img)
    fixes = {
        (r, c): (t["letter"], t["blank"])
        for r, row in enumerate(labels["board"])
        for c, t in enumerate(row)
        if t is not None and reading.cells[r][c].state != "unseen"
    }
    reader.learn(img, reading, fixes)
    return profile, scene, cal


def make(setup):
    profile, scene, cal = setup
    source = FrameSource()
    gen = demo_engine("demo_garden")[2]
    solver = InlineSolver(gen)
    ctrl = LiveController(
        source, ViewReader(copy.deepcopy(cal), profile), solver, profile, "v1", stable_frames=2
    )
    return ctrl, source, solver, copy.deepcopy(scene)


def settle(ctrl, n=4):
    for _ in range(n):
        ctrl.tick()
    return ctrl.snapshot


WHOLE = ViewSpec(zoom=0.75)  # the floating rack clears the board
ZOOMED = ViewSpec(zoom=2.0, center=(0.35, 0.4))


def test_zooming_in_keeps_the_full_board_result(setup):
    ctrl, source, solver, scene = make(setup)
    source.frame = render_view(scene, WHOLE)[0]
    snap = settle(ctrl)
    assert snap.status == Status.READY, snap.detail
    assert snap.coverage == Coverage.FULL
    full_pid = snap.pid
    assert not snap.board.has_unknown()

    source.frame = render_view(scene, ZOOMED)[0]
    snap = settle(ctrl)
    assert snap.status == Status.READY, snap.detail
    assert snap.coverage == Coverage.TRACKING
    assert snap.pid == full_pid
    assert len(solver.submitted) == 1


def test_change_while_zoomed_limits_moves_to_visible_squares(setup):
    ctrl, source, solver, scene = make(setup)
    source.frame = render_view(scene, WHOLE)[0]
    settle(ctrl)
    zoomed, labels = render_view(scene, ZOOMED)
    visible = labels["view"]["visible"]
    spot = next(
        (r, c)
        for r, row in enumerate(visible)
        for c, v in enumerate(row)
        if v
        and (r, c) not in scene.tiles
        and 2 < r < 10
        and 2 < c < 10
        and any((r + dr, c + dc) in scene.tiles for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)))
    )
    known = {t for t in setup[2].templates}
    scene.tiles[spot] = (sorted(known)[0], False, COMMITTED)
    source.frame = render_view(scene, ZOOMED)[0]
    snap = settle(ctrl)
    assert snap.status == Status.READY, snap.detail
    assert snap.coverage == Coverage.PARTIAL
    assert snap.board.has_unknown()
    for r, row in enumerate(visible):
        for c, v in enumerate(row):
            if not v:
                assert snap.board.cells[r][c] == WALL_CELL
    for move in snap.result.moves:
        for t in move.tiles:
            assert not snap.board.is_unknown(t.row, t.col)
            assert visible[t.row][t.col]


def test_tiles_taken_from_the_rack_keep_the_suggestion(setup):
    ctrl, source, solver, scene = make(setup)
    source.frame = render_view(scene, WHOLE)[0]
    shown = settle(ctrl)
    assert shown.status == Status.READY
    letter = next(t for t in scene.rack if t)
    scene.rack[scene.rack.index(letter)] = None
    spot = next((r, c) for r in range(3, 10) for c in range(3, 10) if (r, c) not in scene.tiles)
    scene.tiles[spot] = (letter, False, TENTATIVE)
    source.frame = render_view(scene, WHOLE)[0]
    snap = settle(ctrl)
    assert (snap.status, snap.detail) == (Status.READY, WAITING)
    assert snap.pid == shown.pid and snap.result is shown.result
    assert len(solver.submitted) == 1


@pytest.mark.gui
def test_auto_calibration_dialog_learns_from_the_rack(setup):
    from PySide6.QtWidgets import QApplication

    from tilelens.ui.auto_calibrate import AutoCalibrationDialog

    QApplication.instance() or QApplication([])
    profile, scene, _ = setup
    img, labels = render_view(scene, ZOOMED)
    dlg = AutoCalibrationDialog(img, profile)
    assert "Board found" in dlg.found.text()
    assert not dlg.run()
    dlg.rack_edit.setText(rack_text(labels) + "Q")
    assert not dlg.run()
    assert "rack tiles" in dlg.status.text()
    dlg.rack_edit.setText(rack_text(labels))
    assert dlg.run()
    assert dlg.result.auto and dlg.result.rack_templates


def test_corrections_stick_across_frames(setup):
    profile, scene, _ = setup
    img, labels = render_view(scene, WHOLE)
    cal = calibrate_auto(img, profile, rack_text(labels))
    source = FrameSource()
    solver = InlineSolver(demo_engine("demo_garden")[2])
    ctrl = LiveController(
        source, ViewReader(cal, profile), solver, profile, "v1", stable_frames=2, soft=False
    )
    source.frame = img
    snap = settle(ctrl)
    assert snap.status == Status.CHECK, snap.detail
    fixes = {}
    for r, c in snap.reading.uncertain_cells:
        t = labels["board"][r][c]
        fixes[(r, c)] = (t["letter"], t["blank"]) if t else None
    rack_fixes = {
        i: ("?" if t["blank"] else t["letter"])
        for i, (t, got) in enumerate(
            zip([t for t in labels["rack"] if t], snap.reading.rack, strict=True)
        )
        if got.uncertain
    }
    assert ctrl.apply_corrections(fixes, rack_fixes=rack_fixes)
    pid = ctrl.snapshot.pid
    for _ in range(3):
        snap = settle(ctrl, 3)
        assert snap.status == Status.READY, snap.detail
        assert snap.pid == pid
    # a slightly different zoom changes every glyph a little
    source.frame = render_view(scene, ViewSpec(zoom=0.73, center=(0.51, 0.5)))[0]
    snap = settle(ctrl, 4)
    assert (snap.status, snap.pid) == (Status.READY, pid), snap.detail


def test_panning_and_zooming_never_drop_the_suggestion(setup):
    ctrl, source, solver, scene = make(setup)
    source.frame = render_view(scene, WHOLE)[0]
    shown = settle(ctrl)
    assert shown.status == Status.READY, shown.detail
    path = [(1.3, (0.4, 0.4)), (1.6, (0.45, 0.42)), (2.0, (0.5, 0.45)), (1.6, (0.55, 0.5))]
    for zoom, center in path + path[::-1]:
        source.frame = render_view(scene, ViewSpec(zoom=zoom, center=center))[0]
        for _ in range(3):
            ctrl.tick()
            snap = ctrl.snapshot
            assert snap.status == Status.READY, snap.detail
            assert snap.result is not None
    assert len(solver.submitted) == 1


def test_a_new_tile_in_view_clears_the_suggestion(setup):
    ctrl, source, solver, scene = make(setup)
    source.frame = render_view(scene, WHOLE)[0]
    settle(ctrl)
    known = sorted(setup[2].templates)
    spot = next((r, c) for r in range(3, 10) for c in range(3, 10) if (r, c) not in scene.tiles)
    scene.tiles[spot] = (known[0], False, COMMITTED)
    source.frame = render_view(scene, ZOOMED)[0]
    ctrl.tick()
    assert ctrl.snapshot.result is None

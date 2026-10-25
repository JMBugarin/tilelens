import copy
import subprocess
import sys
import textwrap
import time

import numpy as np
import pytest

pytestmark = pytest.mark.gui
pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QImage, QMouseEvent, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tilelens.capture.source import CallableSource  # noqa: E402
from tilelens.platforms.base import Region  # noqa: E402
from tilelens.state.controller import Snapshot, Status  # noqa: E402
from tilelens.synth.game import FakeGame, calibration_samples  # noqa: E402
from tilelens.ui.calibrate_dialog import CalibrationDialog, to_qimage  # noqa: E402
from tilelens.ui.cards import ResultCard, describe  # noqa: E402
from tilelens.ui.corrections import CorrectionDialog, parse_cell  # noqa: E402
from tilelens.ui.main_window import MainWindow  # noqa: E402
from tilelens.ui.overlay import OverlayWindow  # noqa: E402
from tilelens.ui.preview import CellMapper  # noqa: E402
from tilelens.ui.region_select import RegionSelector  # noqa: E402
from tilelens.ui.session import LiveSession, SuppressingSource  # noqa: E402
from tilelens.ui.settings import DEFAULT_SHORTCUTS, Settings  # noqa: E402
from tilelens.vision.recognizer import COMMITTED, RECENT, Recognizer, calibrate  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def calibration():
    samples, hint = calibration_samples(range(71, 79), theme="meadow", cell=36)
    return calibrate(samples, FakeGame(seed=71).profile, board_hint=hint)


def qimage_to_bgr(img: QImage) -> np.ndarray:
    img = img.convertToFormat(QImage.Format.Format_RGB888)
    w, h = img.width(), img.height()
    arr = np.frombuffer(img.constBits(), np.uint8, count=img.bytesPerLine() * h)
    arr = arr.reshape(h, img.bytesPerLine())[:, : w * 3].reshape(h, w, 3)
    return arr[:, :, ::-1].copy()


def make_session(qapp, calibration, tmp_path, game=None):
    game = game or FakeGame(seed=5)
    settings = Settings(calibration=str(tmp_path / "cal.json"))
    frame = {"img": game.frame()}
    region = Region(0, 0, frame["img"].shape[1], frame["img"].shape[0])
    session = LiveSession(
        settings,
        source=CallableSource(lambda: frame["img"]),
        region_provider=lambda: region,
        use_process=False,
    )
    session.set_calibration(copy.deepcopy(calibration))
    return session, game, frame


def run_until(qapp, session, statuses, limit=4.0):
    end = time.monotonic() + limit
    while time.monotonic() < end:
        qapp.processEvents()
        snap = session.snapshot
        if snap is not None and snap.status in statuses:
            return snap
        time.sleep(0.01)
    return session.snapshot


def test_settings_round_trip(tmp_path):
    s = Settings(hz=50, overlay_mode="bogus", shortcuts={"pause": "Ctrl+P", "junk": "X"})
    s.save(tmp_path / "s.json")
    back = Settings.load(tmp_path / "s.json")
    assert back.hz == 12 and back.overlay_mode == "exclude"
    assert back.shortcuts["pause"] == "Ctrl+P" and "junk" not in back.shortcuts
    assert set(back.shortcuts) == set(DEFAULT_SHORTCUTS)
    assert Settings.load(tmp_path / "missing.json").hz == 10


def test_parse_cell():
    assert parse_cell("") is None and parse_cell(".") is None
    assert parse_cell("a").blank and parse_cell("A").letter == "A"
    assert parse_cell("~B").played is False
    assert isinstance(parse_cell("AB"), str)


def test_overlay_is_click_through(qapp):
    ov = OverlayWindow()
    flags = ov.windowFlags()
    assert flags & Qt.WindowType.WindowTransparentForInput
    assert flags & Qt.WindowType.WindowStaysOnTopHint
    assert ov.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert ov.focusPolicy() == Qt.FocusPolicy.NoFocus
    ov.close()


def test_overlay_ghosts_align_with_board(qapp, calibration, tmp_path):
    session, game, frame = make_session(qapp, calibration, tmp_path)
    snap = run_until(qapp, session, {Status.READY, Status.CHECK})
    assert snap.status == Status.READY, snap.detail
    move = session.selected_move()
    mapper = session.overlay_mapper()
    geo = game.geometry
    for t in move.tiles:
        rect = mapper.rect(t.row, t.col)
        x, y, w, h = geo.cell_rect(t.row, t.col)
        assert abs(rect.center().x() - (x + w / 2)) < 2.5
        assert abs(rect.center().y() - (y + h / 2)) < 2.5
    session.stop()


def test_mapper_handles_retina_scale():
    from tilelens.vision.grid import Grid

    grid = Grid(100, 50, 72, 72, 13, 13)
    region = Region(10, 20, 500, 400)
    mapper = CellMapper.for_overlay(grid, (1000, 800), region)
    rect = mapper.rect(0, 0, inset=0)
    assert rect.x() == pytest.approx(50) and rect.width() == pytest.approx(36)


def test_overlay_leaking_into_capture_is_never_read_as_tiles(qapp, calibration, tmp_path):
    session, game, frame = make_session(qapp, calibration, tmp_path)
    snap = run_until(qapp, session, {Status.READY})
    session.refresh_overlay()
    ov = session.overlay
    ov.resize(frame["img"].shape[1], frame["img"].shape[0])
    composite = to_qimage(frame["img"])
    p = QPainter(composite)
    ov.render(p, QPoint(0, 0))
    p.end()
    leaked = qimage_to_bgr(composite)
    assert np.abs(leaked.astype(int) - frame["img"].astype(int)).sum() > 0
    rec = Recognizer(copy.deepcopy(calibration), game.profile)
    reading = rec.read(leaked)
    for t in snap.result.moves[0].tiles:
        cell = reading.cells[t.row][t.col]
        assert cell.uncertain or cell.state not in (COMMITTED, RECENT)
    assert not reading.confident
    session.stop()


def test_suppress_mode_hides_overlay_only_during_capture(qapp, calibration, tmp_path):
    session, game, frame = make_session(qapp, calibration, tmp_path)
    run_until(qapp, session, {Status.READY})
    session.refresh_overlay()
    ov = session.overlay
    states = []

    class Probe:
        def grab(self):
            states.append(ov.suppressed)
            return frame["img"]

    src = SuppressingSource(Probe(), ov)
    for _ in range(20):
        src.grab()
    assert all(states) and not ov.suppressed
    assert len(ov.suppress_log) == 20
    assert max(ov.suppress_log) < 0.05
    session.stop()


def test_region_selector_emits_region(qapp):
    sel = RegionSelector()
    got = []
    sel.selected.connect(got.append)
    sel.show()

    def mouse(kind, x, y):
        ev = QMouseEvent(
            kind,
            QPointF(x, y),
            QPointF(x, y),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        return ev

    sel.mousePressEvent(mouse(QMouseEvent.Type.MouseButtonPress, 100, 120))
    sel.mouseMoveEvent(mouse(QMouseEvent.Type.MouseMove, 400, 500))
    sel.mouseReleaseEvent(mouse(QMouseEvent.Type.MouseButtonRelease, 400, 500))
    assert got and got[0].width == 300 and got[0].height == 380


def test_calibration_dialog(qapp, tmp_path):
    game = FakeGame(seed=74, opening_turns=10)
    img = game.frame()
    geo = game.geometry
    dlg = CalibrationDialog(
        img, game.profile, board=game.board, rack="".join(t for t in game.rack if t)
    )
    assert not dlg.run()
    assert "rectangle" in dlg.status.text()
    dlg.picker.set_rect((geo.origin[0] + 4, geo.origin[1] - 3, 13 * geo.pitch, 13 * geo.pitch))
    dlg.add_sample()
    dlg.add_sample()
    assert len(dlg.samples) == 1
    assert dlg.run(), dlg.status.text()
    assert dlg.result is not None and dlg.result.templates
    bad = CalibrationDialog(img, game.profile, board=None, rack="ABC")
    bad.picker.set_rect((geo.origin[0], geo.origin[1], 13 * geo.pitch, 13 * geo.pitch))
    assert not bad.run() and "failed" in bad.status.text()


def test_cards_and_corrections(qapp, calibration, tmp_path):
    session, game, frame = make_session(qapp, calibration, tmp_path)
    snap = run_until(qapp, session, {Status.READY})
    move = snap.result.moves[0]
    d = describe(move)
    assert d["word"] == move.word and d["total"] == move.score
    card = ResultCard(0)
    card.set_move(move)
    assert move.word in card.title.text() and str(move.score) in card.title.text()
    card.set_move(None)
    assert not card.isEnabled()
    reading = Recognizer(copy.deepcopy(calibration), game.profile).read(frame["img"])
    dlg = CorrectionDialog(reading)
    r, c = next((r, c) for r in range(13) for c in range(13) if reading.cells[r][c].letter)
    dlg.edits[(r, c)].setText("")
    assert dlg.collect() is None
    assert dlg.fixes == {(r, c): None}
    dlg.edits[(0, 0)].setText("12")
    assert "row 1, col 1" in dlg.collect()
    session.stop()


@pytest.mark.parametrize(
    "status", [Status.READY, Status.SOLVING, Status.CHECK, Status.PAUSED, Status.NOT_VISIBLE]
)
def test_main_window_states(qapp, calibration, tmp_path, status):
    session, game, frame = make_session(qapp, calibration, tmp_path)
    win = MainWindow(session)
    snap = run_until(qapp, session, {Status.READY})
    if status != Status.READY:
        snap = Snapshot(status, "detail text")
    win.show_snapshot(snap)
    assert win.status.text() == status.value
    shown = [c.move_shown for c in win.cards if c.move_shown is not None]
    assert bool(shown) == (status == Status.READY)
    assert win.check_btn.isEnabled() is False
    img = win.grab()
    assert img.width() > 400
    win.close()
    session.stop()


def test_shortcuts_and_controls(qapp, calibration, tmp_path):
    session, game, frame = make_session(qapp, calibration, tmp_path)
    win = MainWindow(session)
    run_until(qapp, session, {Status.READY})
    assert set(win.shortcuts) == set(DEFAULT_SHORTCUTS)
    win.hide_btn.toggle()
    assert session.preview_hidden and session.overlay.move_shown is None
    win.hide_btn.toggle()
    win.next_card()
    assert session.selected == 1
    win.toggle_pause()
    assert run_until(qapp, session, {Status.PAUSED}).status == Status.PAUSED
    assert session.overlay.move_shown is None
    win.toggle_pause()
    assert run_until(qapp, session, {Status.READY}).status == Status.READY
    win.set_mode("panel_only")
    assert not session.overlay.isVisible()
    win.close()
    session.stop()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS window sharing")
def test_overlay_excluded_from_capture_on_macos():
    code = textwrap.dedent(
        """
        import sys
        from PySide6.QtWidgets import QApplication
        from tilelens.platforms import get_adapter
        from tilelens.ui.overlay import OverlayWindow
        app = QApplication([])
        ov = OverlayWindow()
        ov.setGeometry(0, 0, 10, 10)
        ok = ov.try_exclude_from_capture(get_adapter())
        print("EXCLUDED" if ok else "NOT_EXCLUDED")
        ov.close()
        """
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        env={**__import__("os").environ, "QT_QPA_PLATFORM": "cocoa"},
    )
    assert "EXCLUDED" in out.stdout and "NOT_EXCLUDED" not in out.stdout, out.stderr


def test_debug_dump_only_on_request(qapp, calibration, tmp_path, monkeypatch):
    monkeypatch.setenv("TILELENS_HOME", str(tmp_path / "home"))
    session, game, frame = make_session(qapp, calibration, tmp_path)
    run_until(qapp, session, {Status.READY})
    dumps = tmp_path / "home" / "debug_dumps"
    assert not dumps.exists()
    path = session.save_debug_dump()
    assert path.is_file() and path.with_suffix(".json").is_file()
    assert len(list(dumps.iterdir())) == 2
    session.stop()


def test_square_names():
    from tilelens.ui.glass import square_name

    assert square_name(0, 0) == "A1"
    assert square_name(7, 7) == "H8"
    assert square_name(18, 26) == "AA19"


def test_glass_scene_draws_frame_ghosts_and_rack(qapp, calibration, tmp_path):
    from tilelens.ui.glass import CYAN, GREEN

    session, game, frame = make_session(qapp, calibration, tmp_path)
    snap = run_until(qapp, session, {Status.READY})
    assert snap.status == Status.READY, snap.detail
    scene = session.overlay_scene()
    assert scene.board is not None and scene.move is not None
    h, w = frame["img"].shape[:2]
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    from tilelens.ui.glass import paint_scene

    paint_scene(p, scene)
    p.end()

    def near(x, y, color, radius=6):
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                c = img.pixelColor(int(x + dx), int(y + dy))
                if c.alpha() > 150 and abs(c.hue() - color.hue()) < 12 and c.saturation() > 90:
                    return True
        return False

    mapper = session.overlay_mapper()
    for t in scene.move.tiles:
        rect = mapper.rect(t.row, t.col)
        assert near(rect.left(), rect.center().y(), CYAN)
    assert near(scene.board.left(), scene.board.top(), CYAN)
    if scene.rack is not None:
        assert near(scene.rack.left() - 6, scene.rack.center().y(), GREEN)
    session.stop()


def test_glass_panel_cards_and_states(qapp, calibration, tmp_path):
    from tilelens.state.memory import Coverage
    from tilelens.ui.glass_panel import GlassPanel

    session, game, frame = make_session(qapp, calibration, tmp_path)
    snap = run_until(qapp, session, {Status.READY})
    panel = GlassPanel()
    panel.show_snapshot(snap, 1, 0.2)
    moves = snap.result.moves
    shown = [c for c in panel.cards if c.move_shown is not None]
    assert len(shown) == min(3, len(moves))
    assert shown[0].word.text() == moves[0].word
    assert shown[0].points.text() == f"{moves[0].score} pts"
    assert panel.cards[1].property("selected") is True
    assert panel.updated.text() == "Updated just now"
    got = []
    panel.card_clicked.connect(got.append)
    ev = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(5, 5),
        QPointF(5, 5),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    panel.cards[2].mousePressEvent(ev)
    assert got == [2]
    from dataclasses import replace

    partial = replace(snap, coverage=Coverage.PARTIAL)
    panel.show_snapshot(partial, 0)
    assert "in view" in panel.subtitle.text() and "Zoom out" in panel.message.text()
    panel.show_snapshot(Snapshot(Status.CHECK, "2 squares to check"), 0)
    assert not panel.check_btn.isHidden()
    assert all(c.move_shown is None for c in panel.cards)
    assert "CHECK" in panel.live.text()
    panel.close()
    session.stop()


def _paint(scene, w=400, h=300):
    from tilelens.ui.glass import paint_scene

    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    paint_scene(p, scene)
    p.end()
    return img


def _yellow_pixels(img, rect=None):
    from tilelens.ui.glass import YELLOW

    count = 0
    for x in range(img.width()):
        for y in range(img.height()):
            if rect is not None and not rect.contains(x, y):
                continue
            c = img.pixelColor(x, y)
            if (
                c.alpha() > 200
                and abs(c.red() - YELLOW.red()) < 12
                and abs(c.green() - YELLOW.green()) < 12
                and abs(c.blue() - YELLOW.blue()) < 40
            ):
                count += 1
    return count


def test_a_tile_to_confirm_is_outlined_in_yellow_not_in_the_suggestion_color(qapp):
    from PySide6.QtCore import QRectF

    from tilelens.ui.glass import OverlayScene

    tile = QRectF(150, 120, 60, 60)
    img = _paint(OverlayScene(unsure=[(tile, "M18: Y?", True)]))
    assert _yellow_pixels(img, tile.adjusted(-8, -8, 8, 8)) > 150  # a solid outline around it
    assert _yellow_pixels(img, QRectF(0, 0, 100, 100)) == 0  # and nowhere else
    assert OverlayScene(unsure=[(tile, "?", True)]).empty is False
    assert _yellow_pixels(_paint(OverlayScene())) == 0


def test_the_first_tile_asked_about_is_marked_more_strongly(qapp):
    from PySide6.QtCore import QRectF

    from tilelens.ui.glass import OverlayScene

    a, b = QRectF(40, 120, 60, 60), QRectF(250, 120, 60, 60)
    img = _paint(OverlayScene(unsure=[(a, "Tile 4: Y?", True), (b, "?", False)]))
    first = _yellow_pixels(img, a.adjusted(-10, -10, 10, 10))
    other = _yellow_pixels(img, b.adjusted(-10, -10, 10, 10))
    assert first > other > 50


def test_session_marks_the_rack_tile_and_the_board_square_it_asks_about(
    qapp, calibration, tmp_path
):
    from tilelens.state.controller import Snapshot

    session, game, frame = make_session(qapp, calibration, tmp_path)
    run_until(qapp, session, {Status.READY})
    ctrl, mapper = session.controller, session.overlay_mapper()
    region = session.region_provider()
    reading = ctrl.last_reading
    snap = Snapshot(
        Status.READY,
        unsure=(("rack", 1, "Y", ("Y", "V")), ("cell", (3, 4), "N", ("N", "H"))),
    )
    marks = session._unsure_marks(snap, reading, mapper, region, ctrl.last_frame_size)
    assert [m[2] for m in marks] == [True, False]  # the first is the one being asked about
    fw, _ = ctrl.last_frame_size
    x, y, w, h = reading.rack[1].rect
    scale = region.width / fw
    assert marks[0][0].x() == pytest.approx(x * scale) and marks[0][1] == "Tile 2: Y?"
    assert marks[1][0] == mapper.rect(3, 4, 0) and marks[1][1] == "?"
    asked_cell = Snapshot(Status.READY, unsure=(("cell", (3, 4), "N", ()),))
    label = session._unsure_marks(asked_cell, reading, mapper, region, ctrl.last_frame_size)[0][1]
    assert label == "E4: N?"
    held = Snapshot(Status.READY, held=0, unsure=asked_cell.unsure)
    assert session._unsure_marks(held, reading, mapper, region, ctrl.last_frame_size) == []

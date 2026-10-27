import copy
import os
import subprocess
import sys
import textwrap
import time
from dataclasses import replace

import numpy as np
import pytest

pytestmark = pytest.mark.gui
pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QPoint, QRect  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tilelens.capture.source import CallableSource  # noqa: E402
from tilelens.platforms.base import Region  # noqa: E402
from tilelens.state.controller import Status  # noqa: E402
from tilelens.synth.game import FakeGame, calibration_samples  # noqa: E402
from tilelens.ui import notify  # noqa: E402
from tilelens.ui.design import Appearance  # noqa: E402
from tilelens.ui.main_window import MainWindow  # noqa: E402
from tilelens.ui.session import LiveSession  # noqa: E402
from tilelens.ui.settings import Settings  # noqa: E402
from tilelens.vision.recognizer import calibrate  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def calibration():
    samples, hint = calibration_samples(range(71, 79), theme="meadow", cell=36)
    return calibrate(samples, FakeGame(seed=71).profile, board_hint=hint)


class Adapter:
    """Records what the session asks of the operating system."""

    name = "recording"

    def __init__(self):
        self.calls: list[tuple[str, int]] = []

    def show_on_all_spaces(self, win_id):
        self.calls.append(("spaces", win_id))
        return True

    def exclude_from_capture(self, win_id):
        self.calls.append(("exclude", win_id))
        return True

    def screen_permission(self):
        return True

    def request_permission(self):
        pass

    def close(self):
        pass


def make(qapp, calibration, tmp_path, *, offset=0, **settings):
    game = FakeGame(seed=5)
    cfg = Settings(calibration=str(tmp_path / "cal.json"), **settings)
    frame = {"img": game.frame()}
    h, w = frame["img"].shape[:2]
    region = {"r": Region(offset, offset, w, h)}
    session = LiveSession(
        cfg,
        source=CallableSource(lambda: frame["img"]),
        region_provider=lambda: region["r"],
        use_process=False,
    )
    session.set_calibration(copy.deepcopy(calibration))
    return session, game, frame, region


def until(qapp, session, statuses=(Status.READY,), limit=5.0):
    end = time.monotonic() + limit
    while time.monotonic() < end:
        qapp.processEvents()
        snap = session.snapshot
        if snap is not None and snap.status in statuses:
            return snap
        time.sleep(0.01)
    return session.snapshot


def overlaps(a: QRect, b) -> bool:
    return a.intersects(QRect(round(b.x()), round(b.y()), round(b.width()), round(b.height())))


def test_scene_carries_the_rank_the_total_and_the_guides_mode(qapp, calibration, tmp_path):
    session, _, _, _ = make(qapp, calibration, tmp_path, guides="always")
    snap = until(qapp, session)
    assert snap.status == Status.READY
    scene = session.overlay_scene()
    assert scene.rank == 1 and scene.total == min(3, len(snap.result.moves))
    assert scene.guides_mode == "always"
    session.select(1, hold=False)
    assert session.overlay_scene().rank == 2
    session.stop()


def test_scene_marks_no_read_tile_and_knows_which_squares_hold_tiles(qapp, calibration, tmp_path):
    session, _, _, _ = make(qapp, calibration, tmp_path)
    until(qapp, session)
    scene = session.overlay_scene()
    assert not hasattr(scene, "seen") and not hasattr(session, "_seen_tiles")
    reading = session.controller.last_reading
    letters = [
        (r, c) for r, row in enumerate(reading.cells) for c, cell in enumerate(row) if cell.letter
    ]
    assert letters and all(scene.occupied(r, c) for r, c in letters)
    assert not scene.occupied(-1, 0) and not scene.occupied(0, 999)
    session.stop()


def test_scene_lock_follows_the_board_not_its_jitter(qapp, calibration, tmp_path):
    session, _, _, region = make(qapp, calibration, tmp_path)
    until(qapp, session)
    first = session.overlay_scene()
    assert session.overlay_scene().lock == first.lock
    old = region["r"]
    region["r"] = Region(old.x, old.y, old.width + 40, old.height)
    assert session.overlay_scene().lock != first.lock
    session.stop()


def test_scene_asks_about_one_tile_only_without_a_hold(qapp, calibration, tmp_path):
    session, _, _, _ = make(qapp, calibration, tmp_path)
    until(qapp, session)
    ctrl = session.controller
    snap = ctrl.snapshot
    reading = ctrl.last_reading
    rack_rect = next(i for i, t in enumerate(reading.rack) if t.rect is not None)
    ctrl._snapshot = replace(snap, unsure=(("rack", rack_rect, "A", ("A", "O")),))
    asked = session.overlay_scene().asking
    assert len(asked) == 1 and asked[0].width() > 0
    ctrl._snapshot = replace(snap, unsure=(("cell", (6, 6), "A", ("A", "O")),))
    cell = session.overlay_scene().asking[0]
    assert cell.width() > 0 and cell.height() > 0
    ctrl._snapshot = replace(snap, unsure=(("cell", (6, 6), "A", ()),), held=0)
    assert session.overlay_scene().asking == ()
    ctrl._snapshot = snap
    assert session.overlay_scene().asking == ()
    session.stop()


def test_select_keeps_hold_semantics_and_ignores_plays_that_are_not_on_show(
    qapp, calibration, tmp_path
):
    session, _, _, _ = make(qapp, calibration, tmp_path)
    snap = until(qapp, session)
    shown = min(3, len(snap.result.moves))
    session.select(shown + 4)
    assert session.selected == 0 and session.controller.hold is None
    session.select(1)
    assert session.selected == 1 and session.controller.hold.index == 1
    session.select(1)
    assert session.controller.hold is None
    until(qapp, session)
    session.select(0, hold=False)
    assert session.controller.hold is None
    session.next_card()
    assert session.selected == 1 % shown
    session.stop()


def test_ctrl_shift_digits_pick_the_plays(qapp, calibration, tmp_path):
    session, _, _, _ = make(qapp, calibration, tmp_path)
    snap = until(qapp, session)
    win = MainWindow(session)
    assert [win.shortcuts[f"select_{n}"].key().toString() for n in (1, 2, 3)] == [
        "Ctrl+Shift+1",
        "Ctrl+Shift+2",
        "Ctrl+Shift+3",
    ]
    win.shortcuts["select_2"].activated.emit()
    assert session.selected == 1
    assert session.overlay.scene.rank == 2
    win.shortcuts["select_1"].activated.emit()
    assert session.selected == 0
    if len(snap.result.moves) >= 3:
        win.shortcuts["select_3"].activated.emit()
        assert session.selected == 2 and session.controller.hold.index == 2
    win.close()
    session.stop()


def test_the_panel_sits_beside_the_board_and_not_over_it(qapp, calibration, tmp_path, monkeypatch):
    from PySide6.QtGui import QGuiApplication

    class Screen:
        def availableGeometry(self):
            return QRect(0, 0, 1600, 900)

    monkeypatch.setattr(QGuiApplication, "screenAt", staticmethod(lambda _pt: Screen()))
    session, _, frame, _ = make(qapp, calibration, tmp_path)
    until(qapp, session)
    session.refresh_panel()
    board, rack = session._placement_rects(session.region_provider())
    glass = session.panel.glass_geometry()
    assert board is not None
    assert not overlaps(glass, board), (glass, board)
    if rack is not None:
        assert not overlaps(glass, rack)
    session.stop()


def test_a_dragged_panel_is_saved_relative_to_the_region_and_followed(qapp, calibration, tmp_path):
    session, _, _, region = make(qapp, calibration, tmp_path, offset=100)
    until(qapp, session)
    saved = []
    session.settings_changed.connect(saved.append)
    session.panel.move_glass_to(300, 260)
    session.panel.user_moved = True
    session.panel.moved_by_user.emit(QPoint(300, 260))
    assert session.settings.panel_offset == {"x": 200, "y": 160}
    session._offset_timer.timeout.emit()
    assert saved == ["panel_offset"]
    session.refresh_panel()
    assert session.panel.glass_geometry().topLeft() == QPoint(300, 260)
    old = region["r"]
    region["r"] = Region(old.x + 40, old.y + 20, old.width, old.height)
    session.refresh_panel()
    assert session.panel.glass_geometry().topLeft() == QPoint(340, 280)
    session.panel.reset_user_position()
    assert session.settings.panel_offset is None
    session.refresh_panel()
    assert session.panel.user_position is None
    session.stop()


def test_a_saved_offset_places_the_panel_at_startup(qapp, calibration, tmp_path):
    session, _, _, _ = make(
        qapp, calibration, tmp_path, offset=50, panel_offset={"x": 120, "y": 90}
    )
    until(qapp, session)
    assert session.panel.glass_geometry().topLeft() == QPoint(170, 140)
    session.stop()


def test_the_compact_flag_round_trips_through_the_settings(qapp, calibration, tmp_path):
    session, _, _, _ = make(qapp, calibration, tmp_path, panel_compact=True)
    until(qapp, session)
    assert session.panel.is_compact()
    saved = []
    session.settings_changed.connect(saved.append)
    session.panel.set_compact(False)
    assert session.settings.panel_compact is False and saved == ["panel_compact"]
    session.settings.panel_compact = True
    session.refresh_panel()
    assert session.panel.is_compact()
    session.stop()


def test_the_first_run_checklist_follows_the_session(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("TILELENS_HOME", str(tmp_path / "home"))
    session = LiveSession(
        Settings(), region_provider=lambda: Region(0, 0, 800, 600), use_process=False
    )
    capture, words, game = session.setup_state()
    assert capture is True and game is None
    assert words == (session.lexicon is not None)
    session.show_setup_panel()
    assert session.panel.view.setup
    session.stop()


def bright_frame(luma: int, size=(600, 800)):
    return np.full((*size, 3), luma, np.uint8)


def test_the_appearance_samples_the_backdrop_at_most_once_a_second(
    qapp, calibration, tmp_path, monkeypatch
):
    session, _, frame, _ = make(qapp, calibration, tmp_path, offset=0)
    until(qapp, session)
    appearance = Appearance("auto", qapp)
    session.appearance = appearance
    session.panel.show()
    assert session.panel.material is appearance.material
    fed = []
    monkeypatch.setattr(appearance, "sample", lambda bgr, rect: fed.append(rect))
    ctrl = session.controller
    ctrl.last_frame = frame["img"]
    clock = {"t": 1000.0}
    monkeypatch.setattr("tilelens.ui.session.time.monotonic", lambda: clock["t"])
    session._sampled_at = 0.0
    session._sample_backdrop()
    clock["t"] += 0.4
    session._sample_backdrop()
    assert len(fed) == 1
    clock["t"] += 0.7
    session._sample_backdrop()
    assert len(fed) == 2
    x, y, w, h = fed[0]
    assert w > 0 and h > 0
    session.stop()


def test_a_bright_backdrop_turns_an_auto_panel_light_after_a_few_readings(
    qapp, calibration, tmp_path
):
    session, _, frame, _ = make(qapp, calibration, tmp_path)
    until(qapp, session)
    appearance = Appearance("auto", qapp)
    session.appearance = appearance
    session.panel.show()
    assert appearance.material.dark
    frame["img"] = bright_frame(245, frame["img"].shape[:2])
    session.controller.last_frame = frame["img"]
    for _ in range(4):
        session._sampled_at = 0.0
        session._sample_backdrop()
    assert not appearance.material.dark
    assert not session.panel.material.dark
    assert not session.overlay.glass_material.dark
    session.stop()


def test_a_panel_outside_the_captured_frame_leaves_the_appearance_alone(
    qapp, calibration, tmp_path, monkeypatch
):
    session, _, _, region = make(qapp, calibration, tmp_path)
    until(qapp, session)
    appearance = Appearance("auto", qapp)
    session.appearance = appearance
    session.panel.show()
    region["r"] = Region(5000, 5000, 800, 600)
    fed = []
    monkeypatch.setattr(appearance, "sample", lambda bgr, rect: fed.append(rect))
    session._sampled_at = 0.0
    session._sample_backdrop()
    assert fed == []
    session.stop()


def test_confirmations_are_toasts_and_problems_stay_messages(
    qapp, calibration, tmp_path, monkeypatch
):
    session, _, _, _ = make(qapp, calibration, tmp_path)
    until(qapp, session)
    toasts, messages = [], []
    monkeypatch.setattr("tilelens.rules.profile.save_learned_values", lambda *a, **k: None)
    monkeypatch.setattr(notify, "show", lambda *a, **k: toasts.append(a))
    session.message.connect(messages.append)
    session.adopt_values({"A": 2})
    session.toast("Hello")
    assert [t[0] for t in toasts if t[0] == "Hello"] == ["Hello"]
    assert any("Learned tile values" in t[0] for t in toasts)
    assert not any("Learned" in m for m in messages)
    session.note("Confirm rack tile 2")
    assert messages == ["Confirm rack tile 2"]
    session.stop()


def test_a_toast_sits_under_the_panel_when_there_is_room(qapp, calibration, tmp_path):
    session, _, _, _ = make(qapp, calibration, tmp_path)
    until(qapp, session)
    session.panel.show()
    session.toast("Saved", ms=800)
    toast = notify.manager().toast
    assert toast is not None and toast.isVisible()
    panel, tg = session.panel.glass_geometry(), toast.glass_geometry()
    assert not tg.intersects(panel)
    notify.manager().clear()
    session.stop()


def test_windows_are_put_over_every_space_and_hidden_from_capture(qapp, calibration, tmp_path):
    game = FakeGame(seed=5)
    frame = game.frame()
    adapter = Adapter()
    session = LiveSession(
        Settings(calibration=str(tmp_path / "cal.json")),
        adapter=adapter,
        source=CallableSource(lambda: frame),
        region_provider=lambda: Region(0, 0, frame.shape[1], frame.shape[0]),
        use_process=False,
    )
    session.set_calibration(copy.deepcopy(calibration))
    until(qapp, session)
    ids = {int(session.panel.winId()), int(session.overlay.winId())}
    spaces = {w for kind, w in adapter.calls if kind == "spaces"}
    excluded = {w for kind, w in adapter.calls if kind == "exclude"}
    assert ids <= spaces and ids <= excluded
    adapter.calls.clear()
    session.panel.hide()
    session.panel.show()
    assert ("exclude", int(session.panel.winId())) in adapter.calls
    from tilelens.ui.glass_dialog import GlassDialog

    dialog = GlassDialog("Check")
    adapter.calls.clear()
    dialog.show()
    assert ("exclude", int(dialog.winId())) in adapter.calls
    dialog.close()
    session.settings.overlay_mode = "suppress"
    adapter.calls.clear()
    session.panel.hide()
    session.panel.show()
    assert ("spaces", int(session.panel.winId())) in adapter.calls
    assert not any(kind == "exclude" for kind, _ in adapter.calls)
    session.stop()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS window sharing")
def test_real_windows_stay_blurred_excluded_and_on_every_space():
    code = textwrap.dedent(
        """
        import copy, tempfile, time
        from pathlib import Path
        import objc
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from AppKit import (
            NSStatusWindowLevel, NSWindowCollectionBehaviorCanJoinAllSpaces, NSWindowSharingNone,
        )
        from tilelens.capture.source import CallableSource
        from tilelens.platforms import get_adapter
        from tilelens.platforms.base import Region
        from tilelens.synth.game import FakeGame, calibration_samples
        from tilelens.ui import notify
        from tilelens.ui.design import Appearance
        from tilelens.ui.session import LiveSession
        from tilelens.ui.settings import Settings
        from tilelens.vision.recognizer import calibrate

        samples, hint = calibration_samples(range(71, 79), theme="meadow", cell=36)
        cal = calibrate(samples, FakeGame(seed=71).profile, board_hint=hint)
        frame = FakeGame(seed=5).frame()
        region = Region(60, 60, frame.shape[1], frame.shape[0])
        settings = Settings(calibration=str(Path(tempfile.mkdtemp()) / "cal.json"))
        session = LiveSession(
            settings, adapter=get_adapter(), source=CallableSource(lambda: frame),
            region_provider=lambda: region, use_process=False,
        )
        appearance = Appearance("dark", app)
        session.appearance = appearance
        session.set_calibration(copy.deepcopy(cal))

        def pump(n=15):
            for _ in range(n):
                app.processEvents()
                time.sleep(0.03)

        def native(w):
            return objc.objc_object(c_void_p=int(w.winId())).window()

        def effects(w):
            content = native(w).contentView()
            return [v for v in content.superview().subviews() if v.identifier()]

        def good(w, blur=True):
            win = native(w)
            ok = [
                win.sharingType() == NSWindowSharingNone,
                bool(win.collectionBehavior() & NSWindowCollectionBehaviorCanJoinAllSpaces),
                win.level() == NSStatusWindowLevel,
            ]
            if blur:
                ok += [len(effects(w)) == 1, w.native_blur]
            return all(ok)

        end = time.monotonic() + 8
        while time.monotonic() < end and not (session.snapshot and session.snapshot.result):
            pump(3)
        pump()
        out = [good(session.panel), good(session.overlay, blur=False)]
        session.panel.hide(); pump(); session.panel.show(); pump()
        out.append(good(session.panel))
        appearance.set_mode("light"); pump(25)
        out += [good(session.panel), not session.panel.material.dark]
        session.panel.set_compact(True); pump()
        out.append(good(session.panel))
        session.panel.set_compact(False); pump()
        out.append(good(session.panel))
        session.panel._open_menu(); pump()
        out.append(good(session.panel.menu))
        session.panel.menu.hide()
        session.toast("Saved")
        pump(20)
        out.append(good(notify.manager().toast))
        print("RESULT", len(out), all(out), out)
        session.stop()
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "QT_QPA_PLATFORM": "cocoa"},
    )
    assert "RESULT 9 True" in proc.stdout, proc.stdout + proc.stderr

import dataclasses
import os

import pytest

pytestmark = pytest.mark.gui
pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tilelens.solver.movegen import SearchResult  # noqa: E402
from tilelens.state.controller import HELD, WAITING, Snapshot, Status  # noqa: E402
from tilelens.state.memory import Coverage  # noqa: E402
from tilelens.ui.design import DARK, LIGHT, SHADOW_BLEED, Appearance, set_reduce_motion  # noqa: E402
from tilelens.ui.glass_panel import GlassPanel, age_text, view_of  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def still(qapp):
    set_reduce_motion(True)
    yield
    set_reduce_motion(False)


@pytest.fixture(scope="module")
def plays():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    from ui_gallery import ctx

    return ctx.featured_moves()


def snap(plays, n=3, **kw) -> Snapshot:
    result = SearchResult(list(plays[:n]), True, 100, 0.01)
    base = dict(
        status=Status.READY,
        detail="",
        result=result,
        coverage=Coverage.FULL,
        published_at=1.0,
    )
    base.update(kw)
    return Snapshot(**base)


def test_view_maps_every_state(plays):
    ready = view_of(snap(plays))
    assert len(ready.moves) == 3 and ready.notice is None and not ready.skeleton
    assert view_of(None).setup
    reading = view_of(Snapshot(Status.READING, "Reading"))
    assert reading.skeleton and reading.working and not reading.moves
    solving = view_of(Snapshot(Status.SOLVING, "Searching"))
    assert solving.skeleton and solving.working
    empty = view_of(snap(plays, 0, detail="No legal placement"))
    assert empty.empty is not None and empty.empty[0] == "No legal placement"
    assert view_of(snap(plays, 0, detail="Rack is empty")).empty[0] == "Rack is empty"
    partial = view_of(snap(plays, coverage=Coverage.PARTIAL))
    assert partial.notice is not None and partial.notice.kind == "info"
    assert "Zoom out" in partial.notice.text
    assert "behind" in view_of(snap(plays, coverage=Coverage.PARTIAL), framed=True).notice.text
    check = view_of(Snapshot(Status.CHECK, "3 squares to check"))
    assert check.notice.do == "check" and check.notice.kind == "warning"
    perm = view_of(Snapshot(Status.NOT_VISIBLE, "Screen Recording permission is missing"))
    assert perm.notice.kind == "error" and perm.notice.do == "permission"
    assert view_of(Snapshot(Status.PAUSED, "Paused")).notice.do == "pause"
    held = view_of(snap(plays, detail=HELD, held=1))
    assert held.notice.do == "release" and held.held == 1
    stuck = view_of(Snapshot(Status.CHECK, "Solver stopped: boom"))
    assert stuck.notice.kind == "error" and stuck.tone == "error"
    assert view_of(snap(plays, detail=WAITING)).caption.startswith("This suggestion")


def test_notice_priority_is_error_then_answer_then_info(plays):
    unsure = (("rack", 6, "O", ("O", "Q")),)
    s = snap(plays, notice="looks like O", unsure=unsure, coverage=Coverage.PARTIAL)
    assert view_of(s).notice.kind == "warning"
    err = view_of(Snapshot(Status.NOT_VISIBLE, "permission"), note="Confirm rack tile 3")
    assert err.notice.kind == "error"
    assert view_of(None, note="Confirm rack tile 3").notice.kind == "warning"


def test_panel_cards_selection_and_signals(qapp, plays):
    panel = GlassPanel()
    panel.show_snapshot(snap(plays), 1, 0.2)
    panel.show()
    shown = [c for c in panel.cards if c.move_shown is not None]
    assert len(shown) == 3
    assert panel.cards[1].selected and not panel.cards[0].selected
    assert panel.cards[1].expanded and not panel.cards[0].expanded
    assert panel.updated.text() == "Updated just now"
    got = []
    panel.card_clicked.connect(got.append)
    panel.cards[2].click()
    assert got == [2]
    assert panel.height() >= panel.layout().minimumSize().height()
    panel.close()


def test_one_card_and_no_cards(qapp, plays):
    panel = GlassPanel()
    panel.show_snapshot(snap(plays, 1), 0, 0.1)
    assert [c.isHidden() for c in panel.cards] == [False, True, True]
    panel.show_snapshot(snap(plays, 0, detail="No legal placement"), 0)
    assert all(c.isHidden() for c in panel.cards) and not panel.empty.isHidden()
    panel.close()


def test_banner_actions_emit_the_right_signal(qapp, plays):
    panel = GlassPanel()
    seen = []
    for name in ("check_clicked", "calibrate_clicked", "release_clicked", "rescan_clicked"):
        getattr(panel, name).connect(lambda n=name: seen.append(n))
    panel.settings_clicked.connect(lambda: seen.append("settings_clicked"))

    panel.show_snapshot(Snapshot(Status.CHECK, "2 squares to check"), 0)
    panel.banner.action.click()
    panel.show_snapshot(snap(plays, detail=HELD, held=0), 0)
    panel.banner.action.click()
    panel.show_snapshot(Snapshot(Status.NOT_VISIBLE, "Board not found"), 0)
    panel.banner.action.click()
    panel.show_snapshot(Snapshot(Status.CHECK, "Board size changed; recalibrate"), 0)
    panel.banner.action.click()
    panel.show_snapshot(Snapshot(Status.NOT_VISIBLE, "Screen Recording permission is missing"), 0)
    panel.banner.action.click()
    assert seen == [
        "check_clicked",
        "release_clicked",
        "rescan_clicked",
        "calibrate_clicked",
        "settings_clicked",
    ]
    perm = []
    panel.permission_clicked.connect(lambda: perm.append(1))
    panel.banner.action.click()
    assert perm == [1] and seen.count("settings_clicked") == 1
    panel.close()


def test_pause_button_and_menu_actions(qapp, plays):
    panel = GlassPanel()
    panel.show_snapshot(snap(plays), 0, 0.1)
    seen = []
    panel.pause_clicked.connect(lambda: seen.append("pause"))
    panel.rescan_clicked.connect(lambda: seen.append("rescan"))
    panel.settings_clicked.connect(lambda: seen.append("settings"))
    panel.calibrate_clicked.connect(lambda: seen.append("calibrate"))
    panel.pause_btn.click()
    for key in ("rescan", "settings", "calibrate", "pause"):
        panel.menu.rows[key].click()
    assert seen == ["pause", "rescan", "settings", "calibrate", "pause"]
    panel.show_snapshot(Snapshot(Status.PAUSED, "Paused"), 0)
    assert panel.pause_btn.icon_name == "play"
    panel.close()


def test_letter_keys_answer_an_unsure_tile(qapp, plays):
    panel = GlassPanel()
    unsure = (("cell", (22, 33), "S", ("S", "B", "5")),)
    panel.show_snapshot(snap(plays, unsure=unsure, notice="Square looks like S"), 0, 0.1)
    keys = [k for k in panel.chip_btns if not k.isHidden()]
    assert [k.text() for k in keys] == ["S", "B", "5"]
    got = []
    panel.chip_clicked.connect(lambda *a: got.append(a))
    keys[1].clicked.emit()
    assert got == [("cell", (22, 33), "B")]
    panel.show_snapshot(snap(plays), 0, 0.1)
    assert panel.chip_row.isHidden()
    panel.close()


def test_compact_mode(qapp, plays):
    panel = GlassPanel()
    panel.show_snapshot(snap(plays), 0, 0.1)
    panel.show()
    full_height = panel.height()
    flips = []
    panel.compact_changed.connect(flips.append)
    panel.set_compact(True)
    panel.set_compact(True)
    assert panel.is_compact() and flips == [True]
    assert panel.height() < full_height
    assert panel.glass_size()[1] == 44
    got = []
    panel.card_clicked.connect(got.append)
    panel._bar.next.click()
    assert got == [1]
    panel.show_snapshot(snap(plays), 2, 0.1)
    panel._bar.next.click()
    panel._bar.prev.click()
    assert got == [1, 0, 1]
    assert "3 of 3" in panel._bar.where.text()
    panel.show_snapshot(Snapshot(Status.CHECK, "2 squares to check"), 0)
    assert panel.glass_size()[1] == 44 and not panel._bar.status.isHidden()
    panel.set_compact(False)
    assert not panel.is_compact() and flips == [True, False]
    assert panel.height() > 44 + 2 * 16
    panel.close()


def test_compact_with_a_single_play_has_no_arrows(qapp, plays):
    panel = GlassPanel()
    panel.show_snapshot(snap(plays, 1), 0, 0.1)
    panel.set_compact(True)
    assert not panel._bar.next.isEnabled() and not panel._bar.prev.isEnabled()
    panel.close()


def test_nothing_moves_while_a_result_shows(qapp, plays):
    set_reduce_motion(False)
    panel = GlassPanel()
    panel.show_snapshot(Snapshot(Status.READING, "Aligning"), 0)
    panel.show()
    assert panel.pill.spinning and not panel.calm
    panel.show_snapshot(snap(plays), 0, 0.1)
    assert not panel.pill.spinning and panel.calm
    assert not any(sk.isVisible() for sk in panel.skeletons)
    panel.show_snapshot(snap(plays, detail="Updating"), 0, 0.1)
    assert not panel.pill.spinning
    panel.close()


def test_long_words_and_three_digit_scores_do_not_widen_the_panel(qapp, plays):
    big = [
        dataclasses.replace(m, word=w, score=s)
        for m, w, s in zip(
            plays,
            ("ELECTROMAGNETIC", "COMPREHENSIVELY", "UNDERSTANDINGLY"),
            (128, 104, 99),
            strict=True,
        )
    ]
    panel = GlassPanel()
    width = panel.width()
    panel.show_snapshot(snap(big), 0, 0.1)
    panel.show()
    assert panel.width() == width
    card = panel.cards[0]
    assert card.points.geometry().right() <= card.width()
    assert card.word.geometry().right() < card.points.geometry().left()
    assert card.points.text() == "128 pts"
    panel.set_compact(True)
    assert panel.width() == width
    assert panel._bar.points.geometry().right() <= panel._bar.width()
    panel.close()


def test_scale_changes_the_width_and_keeps_the_contents_inside(qapp, plays):
    panel = GlassPanel()
    panel.show_snapshot(snap(plays), 0, 0.1)
    panel.show()
    base = panel.width()
    panel.set_scale(1.3)
    assert panel.scale == pytest.approx(1.3) and panel.width() > base
    assert panel.height() >= panel.layout().minimumSize().height()
    panel.set_scale(0.5)
    assert panel.scale == pytest.approx(0.85) and panel.width() < base
    panel.close()


def test_stale_age_changes_the_footer_tone(qapp, plays):
    panel = GlassPanel()
    panel.show_snapshot(snap(plays), 0, 0.5)
    panel.show()
    panel.set_age(30)
    assert panel.updated.text() == age_text(30) == "Updated 30 s ago"
    assert panel._footer.isVisibleTo(panel)
    assert age_text(0.2) == "Updated just now" and age_text(200) == "Updated 3 min ago"
    assert age_text(None) == ""
    panel.close()


def test_dismissed_notice_stays_dismissed_until_it_changes(qapp, plays):
    panel = GlassPanel()
    panel.show_snapshot(snap(plays, coverage=Coverage.PARTIAL), 0, 0.1)
    assert not panel.banner.isHidden()
    panel.banner.close_button.click()
    assert panel.banner.isHidden()
    panel.show_snapshot(snap(plays, coverage=Coverage.PARTIAL), 0, 0.2)
    assert panel.banner.isHidden()
    panel.show_snapshot(snap(plays), 0, 0.2)
    panel.show_snapshot(snap(plays, coverage=Coverage.PARTIAL), 0, 0.2)
    assert not panel.banner.isHidden()
    panel.close()


def test_setup_card_actions_follow_the_checklist(qapp):
    panel = GlassPanel()
    panel.show_snapshot(None, 0)
    assert not panel.setup.isHidden() and panel.pause_btn.isHidden()
    got = []
    panel.calibrate_clicked.connect(lambda: got.append("calibrate"))
    panel.settings_clicked.connect(lambda: got.append("settings"))
    panel.set_setup(True, True, None)
    panel.setup.button.click()
    panel.set_setup(True, False, None)
    panel.setup.button.click()
    panel.set_setup(False, True, None)
    panel.setup.button.click()
    assert got == ["calibrate", "settings", "settings"]
    assert panel.setup.rows["capture"].state is False
    panel.close()


def test_hooks_compose_and_follow_the_appearance(qapp, plays):
    panel = GlassPanel()
    assert panel.backdrop is not None
    shown = []
    panel.on_shown = shown.append
    asked = []
    panel.backdrop = lambda win_id, radius, dark: asked.append(dark) or True
    panel.show_snapshot(snap(plays), 0, 0.1)
    panel.show()
    assert shown and asked
    app = Appearance("dark")
    panel.bind_appearance(app)
    assert panel.material.name == "dark"
    app.set_mode("light")
    assert panel.material.name == LIGHT.name
    assert panel.glass_material.name == LIGHT.name
    app.set_mode("dark")
    assert panel.material is DARK
    panel.close()


def test_auto_place_beside_the_board_and_remembers_a_drag(qapp, plays):
    panel = GlassPanel()
    panel.show_snapshot(snap(plays), 0, 0.1)
    panel.show()
    region = (100, 100, 900, 700)
    board = (120, 140, 560, 480)
    rack = (160, 640, 480, 60)
    screen = (0, 0, 1600, 900)
    found = panel.auto_place(region, board, rack, screen)
    assert found.side == "right"
    gx, gy = panel.pos().x() + SHADOW_BLEED, panel.pos().y() + SHADOW_BLEED
    assert (gx, gy) == (found.x, found.y) and found.x >= 680
    panel.move(300, 300)
    panel.moved_by_user.emit(QPoint(316, 316))
    panel.user_moved = True
    again = panel.auto_place(region, board, rack, screen)
    assert again.side == "user" and (again.x, again.y) == (316, 316)
    panel.reset_user_position()
    assert not panel.user_moved
    panel.close()

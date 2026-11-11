import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.gui
pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tilelens.capture.source import CallableSource  # noqa: E402
from tilelens.platforms.base import Region  # noqa: E402
from tilelens.state.controller import Snapshot, Status  # noqa: E402
from tilelens.state.memory import Coverage  # noqa: E402
from tilelens.synth.game import FakeGame  # noqa: E402
from tilelens.ui.design import (  # noqa: E402
    DARK,
    reduce_motion,
    set_default_material,
    set_reduce_motion,
)
from tilelens.ui.main_window import (  # noqa: E402
    MODES,
    SHORTCUT_LABELS,
    MainWindow,
    shortcut_text,
    view_box,
)
from tilelens.ui.session import LiveSession  # noqa: E402
from tilelens.ui.settings import (  # noqa: E402
    DEFAULT_SHORTCUTS,
    Settings,
    clean_offset,
)

WORDS = Path(__file__).resolve().parents[1] / "profiles" / "demo_words.txt"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def calm(qapp):
    set_reduce_motion(True)
    set_default_material(DARK)
    yield
    set_reduce_motion(False)


@pytest.fixture
def session(qapp, tmp_path):
    settings = Settings(calibration=str(tmp_path / "none.json"), words=str(WORDS))
    s = LiveSession(
        settings,
        source=CallableSource(lambda: None),
        region_provider=lambda: Region(0, 0, 800, 600),
        use_process=False,
    )
    yield s
    s.stop()


@pytest.fixture(scope="module")
def ready_snapshot():
    game = FakeGame(seed=5)
    board, rack = game.position()
    return Snapshot(
        Status.READY,
        "",
        board=board,
        rack=rack,
        result=game.gen.solve(board, rack, top_k=3),
        published_at=100.0,
        confirmed_at=100.0,
        solve_ms=12.0,
        coverage=Coverage.FULL,
    )


@pytest.fixture
def toasts():
    return []


@pytest.fixture
def window(qapp, session, toasts):
    win = MainWindow(session, clock=lambda: 100.4, toaster=lambda *a: toasts.append(a))
    yield win
    win.close()


def set_up(win, *, region=True):
    win.session.recognizer = SimpleNamespace(cal=SimpleNamespace(auto=True))
    if region:
        win.session.settings.region = {"x": 0, "y": 0, "width": 800, "height": 600}
    win.refresh_settings()
    win.show_snapshot(win.session.snapshot)


def show_ready(win, snap):
    set_up(win)
    win.session.controller = SimpleNamespace(
        snapshot=snap,
        last_read_ms=18.0,
        hold=None,
        last_reading=None,
        last_grid=None,
        last_frame_size=None,
        paused=False,
        clock=lambda: 100.4,
        hold_move=lambda i: None,
        release_hold=lambda: None,
    )
    win.show_snapshot(snap)


# settings model


def test_defaults_include_the_new_fields():
    s = Settings()
    assert (s.appearance, s.guides, s.reduce_motion, s.panel_compact) == (
        "auto",
        "auto",
        False,
        False,
    )
    assert s.panel_offset is None
    for i in (1, 2, 3):
        assert DEFAULT_SHORTCUTS[f"select_{i}"] == f"Ctrl+Shift+{i}"


def test_old_settings_file_still_loads(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "profile": "x.toml",
                "hz": 9.0,
                "overlay_mode": "suppress",
                "shortcuts": {"pause": "Ctrl+P"},
            }
        )
    )
    s = Settings.load(path)
    assert s.profile == "x.toml" and s.overlay_mode == "suppress" and s.hz == 9.0
    assert s.shortcuts["pause"] == "Ctrl+P"
    assert s.shortcuts["select_2"] == DEFAULT_SHORTCUTS["select_2"]
    assert s.appearance == "auto" and s.panel_offset is None


@pytest.mark.parametrize(
    "key, bad, expected",
    [
        ("appearance", "sepia", "auto"),
        ("appearance", 3, "auto"),
        ("guides", "sometimes", "auto"),
        ("guides", None, "auto"),
        ("reduce_motion", "yes", False),
        ("reduce_motion", 1, False),
        ("panel_compact", "true", False),
        ("panel_offset", "left", None),
        ("panel_offset", {"x": 1}, None),
        ("panel_offset", {"x": "1", "y": 2}, None),
        ("panel_offset", {"x": float("nan"), "y": 2}, None),
        ("panel_offset", {"x": True, "y": 2}, None),
        ("overlay_mode", "everywhere", "exclude"),
    ],
)
def test_invalid_values_fall_back(tmp_path, key, bad, expected):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({key: bad}))
    assert getattr(Settings.load(path), key) == expected


def test_valid_values_survive_a_round_trip(tmp_path):
    path = tmp_path / "settings.json"
    s = Settings(
        appearance="dark",
        guides="always",
        reduce_motion=True,
        panel_compact=True,
        panel_offset={"x": 12.4, "y": -30},
    )
    s.save(path)
    back = Settings.load(path)
    assert (back.appearance, back.guides, back.reduce_motion, back.panel_compact) == (
        "dark",
        "always",
        True,
        True,
    )
    assert back.panel_offset == {"x": 12, "y": -30}


def test_clean_offset_rounds_and_rejects():
    assert clean_offset({"x": 1.6, "y": 2.2}) == {"x": 2, "y": 2}
    assert clean_offset(None) is None


# the window

PUBLIC = [
    "region_btn",
    "cal_btn",
    "pause_btn",
    "rescan_btn",
    "hide_btn",
    "check_btn",
    "words_btn",
    "profile_btn",
    "dump_btn",
    "mode",
    "status",
    "detail",
    "timing",
    "banner",
    "label",
    "cards",
    "preview",
    "profile_name",
    "shortcuts",
    "session",
]


def test_public_names_remain(window):
    for name in PUBLIC:
        assert hasattr(window, name), name
    for name in [
        "show_message",
        "show_snapshot",
        "refresh_selection",
        "update_timing",
        "select_card",
        "next_card",
        "toggle_pause",
        "toggle_hide",
        "set_mode",
        "open_corrections",
        "save_dump",
        "choose_words",
        "choose_profile",
    ]:
        assert callable(getattr(window, name)), name
    assert len(window.cards) == 3


def test_first_run_leads_with_the_checklist(window):
    assert window.needs_setup
    window.show()
    assert window.setup_view.isVisibleTo(window)
    assert not window.settings_view.isVisibleTo(window)
    keys = [st.key for st in window.setup_states()]
    assert keys[-1] == "learn" and "words" in keys


def test_checklist_marks_steps_and_has_one_primary_action(window):
    window.session.lexicon = None
    window.session.settings.words = None
    window.show_snapshot(None)
    window.show()
    rows = list(window._steps.values())
    states = [r.badge.state for r in rows]
    assert states[0] == "current"
    assert states.count("current") == 1
    primary = [r for r in rows if r.button.isVisibleTo(window) and r.button.variant == "prominent"]
    assert len(primary) == 1
    assert not rows[-1].button.isEnabled()


def test_checklist_steps_turn_into_check_marks(window):
    window.session.recognizer = SimpleNamespace(cal=SimpleNamespace(auto=True))
    window.session.settings.region = {"x": 0, "y": 0, "width": 8, "height": 8}
    window.show_snapshot(None)
    assert not window.needs_setup
    assert all(r.badge.state == "done" for r in window._steps.values())
    window.show()
    assert window.settings_view.isVisibleTo(window)
    assert not window.setup_view.isVisibleTo(window)


def test_permission_step_comes_first_when_the_system_refuses(window):
    window.session.adapter = SimpleNamespace(
        screen_permission=lambda: False,
        request_permission=lambda: None,
        permission_help=lambda: "Allow it in Privacy and Security",
        close=lambda: None,
    )
    window.show_snapshot(None)
    assert next(iter(window._steps)) == "permission"
    window._steps["permission"].button.click()
    assert "Privacy" in window.banner.text()
    assert window.banner.kind == "warning"
    window.session.adapter = SimpleNamespace(
        screen_permission=lambda: True, request_permission=lambda: None, close=lambda: None
    )
    window.show_snapshot(None)
    assert window._steps["permission"].badge.state == "done"


def test_skip_setup_shows_every_setting(window):
    window.show()
    window._skip_setup()
    assert window.settings_view.isVisibleTo(window)


def test_status_pill_and_timing(window, ready_snapshot):
    show_ready(window, ready_snapshot)
    assert window.status.text() == "Tracking" and window.status.tone == "ok"
    assert window.timing.text() == "read 18 ms · solved in 12 ms"
    assert [c.move_shown is not None for c in window.cards] == [True, True, True]
    assert window.pause_btn.text() == "Pause"
    window.show_snapshot(Snapshot(Status.PAUSED, "Paused"))
    assert window.pause_btn.text() == "Resume"
    assert all(c.move_shown is None for c in window.cards)


def test_plays_show_scores_and_selection(window, ready_snapshot):
    show_ready(window, ready_snapshot)
    moves = ready_snapshot.result.moves
    assert window.cards[0].score.score == moves[0].score
    window.select_card(1)
    assert window.session.selected == 1
    assert [c.selected for c in window.cards] == [False, True, False]
    window.next_card()
    assert window.session.selected == 2


def test_one_play_and_none(window, ready_snapshot):
    show_ready(window, ready_snapshot)
    one = Snapshot(
        **{
            **ready_snapshot.__dict__,
            "result": SimpleNamespace(
                moves=ready_snapshot.result.moves[:1], label="highest scoring"
            ),
        }
    )
    window.show_snapshot(one)
    assert [c.move_shown is not None for c in window.cards] == [True, False, False]
    assert not window.cards[1].isEnabled()
    window.show_snapshot(None)
    assert all(c.move_shown is None for c in window.cards)


def test_three_digit_scores_and_long_words_fit(window, ready_snapshot):
    import dataclasses

    show_ready(window, ready_snapshot)
    move = dataclasses.replace(ready_snapshot.result.moves[0], word="UNCOMPROMISINGLY", score=134)
    window.cards[0].set_move(move)
    window.show()
    window.fit()
    card = window.cards[0]
    assert card.score.sizeHint().width() <= card.width() // 3
    assert card.word.text() == "UNCOMPROMISINGLY"
    assert card.word.sizeHint().width() <= card.word.width() or card.word.width() > 100


def test_modes_map_to_settings_and_captions(window):
    assert [k for k, *_ in MODES] == ["exclude", "suppress", "panel_only"]
    assert [label for _, label, _ in MODES] == [
        "Hidden from capture",
        "Hide while capturing",
        "Panel only",
    ]
    seen = []
    window.settings_changed.connect(seen.append)
    for key, _, text in MODES:
        window.mode.set_current_key(key)
        assert window.session.settings.overlay_mode == key
        assert window.mode_caption.text() == text
    assert seen.count("overlay_mode") >= 2
    window.set_mode("nonsense")
    assert window.session.settings.overlay_mode == "panel_only"


def test_guides_theme_motion_and_compact_write_settings(window):
    seen = []
    window.settings_changed.connect(seen.append)
    st = window.session.settings
    window.guides.set_current_key("off")
    assert st.guides == "off"
    window.theme.set_current_key("light")
    assert st.appearance == "light"
    window.motion_switch.setChecked(True)
    assert st.reduce_motion is True and reduce_motion() is True
    window.motion_switch.setChecked(False)
    assert st.reduce_motion is False and reduce_motion() is False
    window.compact_switch.setChecked(True)
    assert st.panel_compact is True
    assert {"guides", "appearance", "reduce_motion", "panel_compact"} <= set(seen)


def test_refresh_settings_does_not_report_a_user_edit(window):
    seen = []
    window.settings_changed.connect(seen.append)
    st = window.session.settings
    st.appearance, st.guides, st.panel_compact = "dark", "always", True
    window.refresh_settings()
    assert seen == []
    assert window.theme.current_key() == "dark"
    assert window.guides.current_key() == "always"
    assert window.compact_switch.isChecked()


def test_panel_position_row_and_reset(window, toasts):
    set_up(window)
    window.session.settings.panel_offset = {"x": 10, "y": 20}
    window.refresh_settings()
    window.show()
    assert window.panel_row.isVisibleTo(window)
    window.reset_panel_position()
    assert window.session.settings.panel_offset is None
    assert not window.panel_row.isVisibleTo(window)
    assert toasts and toasts[-1][0].startswith("Panel moved back")


def test_shortcuts_cover_the_selection_keys(window, ready_snapshot):
    assert set(window.shortcuts) == set(DEFAULT_SHORTCUTS)
    assert set(SHORTCUT_LABELS) == set(DEFAULT_SHORTCUTS)
    show_ready(window, ready_snapshot)
    window.shortcuts["select_3"].activated.emit()
    assert window.session.selected == 2
    window.shortcuts["select_1"].activated.emit()
    assert window.session.selected == 0


def test_shortcut_rows_show_keys(window):
    window.show()
    assert set(window.shortcut_rows) == set(DEFAULT_SHORTCUTS)
    window.shortcuts_section.set_expanded(True)
    row = window.shortcut_rows["select_2"]
    assert row.keys.sequence == shortcut_text(DEFAULT_SHORTCUTS["select_2"])


def test_shortcut_text_names_keys_for_the_platform():
    assert shortcut_text("Ctrl+Shift+P", mac=True) == "⇧+⌘+P"
    assert shortcut_text("Ctrl+Shift+P", mac=False) == "Ctrl+Shift+P"
    assert shortcut_text("Ctrl+Shift+1", mac=False).endswith("1")


def test_messages_stay_in_a_banner_and_confirmations_toast(window, toasts):
    window.show()
    window.show_message("Screen Recording is off", "error")
    assert window.banner_box.isVisibleTo(window)
    assert window.banner.kind == "error"
    window.show_message("")
    assert not window.banner_box.isVisibleTo(window)
    window.session.save_debug_dump = lambda: Path("/tmp/a-dump.png")
    window.save_dump()
    assert toasts[-1][0] == "Debug dump saved: a-dump.png" and toasts[-1][1] == "success"
    window.session.save_debug_dump = lambda: None
    window.save_dump()
    assert toasts[-1][1] == "warning"


def test_choose_words_loads_the_list(window, toasts, monkeypatch):
    monkeypatch.setattr(
        "tilelens.ui.main_window.QFileDialog.getOpenFileName", lambda *a, **k: (str(WORDS), "")
    )
    window.choose_words()
    assert window.session.settings.words == str(WORDS)
    assert window.words_name.text() == WORDS.name
    assert toasts and toasts[-1][0].startswith("Word list loaded")


def test_long_names_are_elided_not_clipped(window):
    window.session.profile = SimpleNamespace(
        name="Letter League on Discord " * 6, source=None, appearance=None, word_list=None
    )
    window.refresh_settings()
    window.show()
    window.fit()
    assert window.profile_name.minimumSizeHint().width() < 200
    assert window.scroll.horizontalScrollBar().maximum() == 0
    assert window.content.sizeHint().width() <= window.scroll.viewport().width() + 40


def test_window_height_follows_content_and_screen(window):
    window.show()
    window.fit()
    cap = window._screen_cap()
    glass = window.height() - 2 * 36
    assert 0 < glass <= cap
    window.show_message("A long message " * 30, "warning")
    window.fit()
    assert window.height() - 72 <= cap


def test_escape_and_close_button_hide(window):
    window.show()
    window.close_btn.click()
    assert not window.isVisible()
    window.show()
    window.keyPressEvent(
        __import__("PySide6.QtGui", fromlist=["QKeyEvent"]).QKeyEvent(
            __import__("PySide6.QtCore", fromlist=["QEvent"]).QEvent.Type.KeyPress,
            Qt.Key.Key_Escape,
            Qt.KeyboardModifier.NoModifier,
        )
    )
    assert not window.isVisible()


def test_view_box_windows_a_large_board(ready_snapshot):
    board = ready_snapshot.board
    assert view_box(board, None) == (0, 0, board.height, board.width)
    big = SimpleNamespace(width=27, height=19, get=lambda r, c: "A" if (r, c) == (9, 13) else None)
    top, left, rows, cols = view_box(big, None)
    assert rows <= 19 and cols <= 27
    assert top <= 9 < top + rows and left <= 13 < left + cols
    assert rows < 19 or cols < 27


def test_gallery_states_register():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    try:
        from ui_gallery import registry, states_settings, states_window  # noqa: F401
    finally:
        sys.path.pop(0)
    names = set(registry.REGISTRY)
    assert {"window/main-setup-manual", "window/main-ready", "settings/populated"} <= names
    assert {f"toast/{k}" for k in ("info", "success", "warning", "error")} <= names

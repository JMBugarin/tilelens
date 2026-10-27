import gc
import os
import subprocess
import sys
import textwrap
import weakref
from types import SimpleNamespace

import numpy as np
import pytest

pytestmark = pytest.mark.gui
pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import (  # noqa: E402
    QEvent,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    Qt,
    qInstallMessageHandler,
)
from PySide6.QtGui import QFocusEvent, QImage, QMouseEvent, QPainter  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QRadioButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from tilelens.platforms import macos, windows  # noqa: E402
from tilelens.platforms.base import PlatformAdapter  # noqa: E402
from tilelens.ui.design import (  # noqa: E402
    DARK,
    ICON_NAMES,
    LIGHT,
    SHADOW_BLEED,
    Appearance,
    Banner,
    Divider,
    FloatAnimator,
    GlassButton,
    GlassCard,
    GlassStyle,
    GlassWindow,
    Glyph,
    IconButton,
    KeyCap,
    KeyCombo,
    Label,
    LoopAnimator,
    LumaSwitch,
    NativeBackdrop,
    RankBadge,
    ScoreBadge,
    SectionLabel,
    Segmented,
    Spinner,
    StatusPill,
    Switch,
    apply_app_style,
    backdrop_stats,
    build_qss,
    call_backdrop,
    luma_of,
    material_of,
    native_backdrop,
    paint_icon,
    reduce_motion,
    set_default_material,
    set_reduce_motion,
    strength_for,
)
from tilelens.ui.design import native as native_mod  # noqa: E402
from tilelens.ui.design import qss as qss_mod  # noqa: E402
from tilelens.ui.design.appearance import PIVOT, STRENGTH_MAX, STRENGTH_MIN  # noqa: E402
from tilelens.ui.design.native import resolve_backdrop, takes_inset  # noqa: E402
from tilelens.ui.design.paint import blend_materials  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def calm(qapp):
    set_reduce_motion(False)
    set_default_material(DARK)
    yield
    set_reduce_motion(False)
    set_default_material(DARK)


def pixels(widget, dpr: float = 1.0) -> np.ndarray:
    w, h = widget.width(), widget.height()
    img = QImage(round(w * dpr), round(h * dpr), QImage.Format.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(dpr)
    img.fill(Qt.GlobalColor.transparent)
    widget.render(img)
    arr = np.frombuffer(img.constBits(), np.uint8, count=img.bytesPerLine() * img.height())
    return arr.reshape(img.height(), img.bytesPerLine())[:, : img.width() * 4].copy()


def differs(a: np.ndarray, b: np.ndarray) -> bool:
    return a.shape != b.shape or bool(np.any(a != b))


def shown(widget, w: int | None = None, h: int | None = None):
    if w and h:
        widget.resize(w, h)
    else:
        widget.adjustSize()
    widget.show()
    QApplication.processEvents()
    return widget


def key(widget, k):
    QTest.keyClick(widget, k)


def mouse_event(kind, pos, global_pos, buttons=Qt.MouseButton.LeftButton):
    button = Qt.MouseButton.LeftButton if kind != QEvent.Type.MouseMove else Qt.MouseButton.NoButton
    return QMouseEvent(
        kind, QPointF(pos), QPointF(global_pos), button, buttons, Qt.KeyboardModifier.NoModifier
    )


def test_luma_switch_needs_three_samples_beyond_the_band():
    s = LumaSwitch()
    assert [s.update(0.9), s.update(0.9)] == [False, False]
    assert s.update(0.9) is True
    assert s.light
    assert [s.update(0.2), s.update(0.2)] == [False, False]
    assert s.update(0.2) is True
    assert not s.light


def test_luma_switch_streak_resets_inside_the_band():
    s = LumaSwitch()
    for v in (0.9, 0.9, 0.6, 0.9, 0.9):
        assert s.update(v) is False
    assert not s.light
    s.update(0.9)
    assert s.light
    for v in (0.1, 0.1, 0.5, 0.1, 0.1):
        assert s.update(v) is False
    assert s.light
    assert PIVOT == 0.55


def test_luma_switch_ignores_values_just_inside_the_band():
    s = LumaSwitch()
    for _ in range(10):
        assert s.update(PIVOT + 0.119) is False
    assert not s.light


def test_luma_of_regions_and_rect_forms():
    img = np.zeros((100, 200, 3), np.uint8)
    img[:, 100:] = 255
    assert luma_of(img, (0, 0, 100, 100)) == pytest.approx(0.0, abs=0.01)
    assert luma_of(img, (100, 0, 100, 100)) == pytest.approx(1.0, abs=0.01)
    assert luma_of(img, QRect(0, 0, 200, 100)) == pytest.approx(0.5, abs=0.05)

    class Box:
        x, y, width, height = 100, 0, 100, 100

    assert luma_of(img, Box) == pytest.approx(1.0, abs=0.01)
    assert luma_of(img, (500, 500, 10, 10)) == PIVOT
    assert luma_of(img[:, :, 0], (100, 0, 100, 100)) == pytest.approx(1.0, abs=0.01)


def test_luma_of_is_cheap_on_a_large_frame():
    img = np.full((2160, 3840, 3), 128, np.uint8)
    import time

    t = time.perf_counter()
    for _ in range(50):
        luma_of(img, (100, 100, 1500, 1500))
    assert (time.perf_counter() - t) / 50 < 0.002


def test_busy_backdrop_reads_a_spread():
    rng = np.random.default_rng(0)
    noisy = rng.integers(0, 255, (100, 100, 3), dtype=np.uint8)
    flat = np.full((100, 100, 3), 128, np.uint8)
    assert backdrop_stats(noisy, (0, 0, 100, 100))[1] > 0.15
    assert backdrop_stats(flat, (0, 0, 100, 100))[1] == pytest.approx(0.0, abs=1e-6)


def test_strength_stays_in_range_and_rises_with_conflict():
    for luma in (0.0, 0.3, 0.55, 0.8, 1.0):
        for busy in (0.0, 0.1, 0.3):
            for dark in (True, False):
                assert STRENGTH_MIN <= strength_for(luma, busy, dark) <= STRENGTH_MAX
    assert strength_for(0.9, 0.0, True) > strength_for(0.2, 0.0, True)
    assert strength_for(0.1, 0.0, False) > strength_for(0.85, 0.0, False)
    assert strength_for(0.2, 0.3, True) > strength_for(0.2, 0.0, True)


def test_appearance_auto_switches_with_hysteresis_and_signals():
    app = Appearance("auto")
    seen = []
    app.appearance_changed.connect(seen.append)
    assert app.material is DARK
    for v in (0.9, 0.9):
        app.feed(v)
    assert seen == []
    app.feed(0.9)
    assert seen == [LIGHT]
    app.feed(0.5)
    assert app.material is LIGHT
    for v in (0.1, 0.1, 0.1):
        app.feed(v)
    assert seen == [LIGHT, DARK]


def test_appearance_fixed_modes_ignore_the_backdrop():
    app = Appearance("dark")
    seen = []
    app.appearance_changed.connect(seen.append)
    for _ in range(6):
        app.feed(0.95)
    assert app.material is DARK and seen == []
    app.set_mode("light")
    assert seen == [LIGHT]
    for _ in range(6):
        app.feed(0.05)
    assert app.material is LIGHT
    app.set_mode("auto")
    assert app.material is LIGHT
    for _ in range(3):
        app.feed(0.05)
    assert app.material is DARK
    with pytest.raises(ValueError):
        app.set_mode("sepia")
    with pytest.raises(ValueError):
        Appearance("sepia")


def test_appearance_samples_an_image_and_adapts_strength():
    app = Appearance("auto")
    strengths = []
    app.strength_changed.connect(strengths.append)
    bright = np.full((200, 200, 3), 240, np.uint8)
    for _ in range(4):
        app.sample(bright, (0, 0, 200, 200))
    assert app.material is LIGHT
    dark = np.full((200, 200, 3), 20, np.uint8)
    for _ in range(6):
        app.sample(dark, QRect(0, 0, 200, 200))
    assert app.material is DARK
    assert strengths
    assert all(STRENGTH_MIN <= s <= STRENGTH_MAX for s in strengths)


def test_reduce_motion_applies_changes_at_once(qapp):
    host = shown(QWidget(), 80, 40)
    anim = FloatAnimator(host, 0.0, duration=400)
    set_reduce_motion(True)
    assert reduce_motion()
    anim.to(1.0)
    assert anim.value == 1.0 and not anim.running
    set_reduce_motion(False)
    anim.to(0.0)
    assert anim.running and anim.value > 0.0
    set_reduce_motion(True)
    assert anim.value == 0.0 and not anim.running


def test_animation_runs_when_visible_and_finishes(qapp):
    host = shown(QWidget(), 80, 40)
    anim = FloatAnimator(host, 0.0, duration=60)
    anim.to(1.0)
    assert anim.running and anim.value < 1.0
    QTest.qWait(200)
    assert anim.value == 1.0 and not anim.running


def test_no_animation_for_hidden_widgets(qapp):
    host = QWidget()
    anim = FloatAnimator(host, 0.0, duration=400)
    anim.to(1.0)
    assert anim.value == 1.0 and not anim.running


def test_loop_animator_follows_visibility_and_reduce_motion(qapp):
    host = QWidget()
    host.resize(40, 40)
    loop = LoopAnimator(host, 200)
    loop.start()
    assert not loop.running
    host.show()
    QApplication.processEvents()
    assert loop.running
    host.hide()
    QApplication.processEvents()
    assert not loop.running
    host.show()
    QApplication.processEvents()
    assert loop.running
    set_reduce_motion(True)
    assert not loop.running and loop.phase == 0.0
    set_reduce_motion(False)
    assert loop.running
    loop.stop()
    assert not loop.running


def test_spinner_runs_only_while_shown_and_is_static_under_reduce_motion(qapp):
    sp = Spinner(18)
    assert not sp.running
    shown(sp)
    assert sp.running
    sp.hide()
    QApplication.processEvents()
    assert not sp.running
    set_reduce_motion(True)
    sp.show()
    QApplication.processEvents()
    assert not sp.running
    a, b = pixels(sp), pixels(sp)
    assert not differs(a, b)


def test_status_pill_moves_only_when_busy_and_allowed(qapp):
    pill = shown(StatusPill("Scanning", "busy"))
    assert pill.animating
    pill.set_status("Ready", "ok")
    assert not pill.animating
    set_reduce_motion(True)
    pill.set_status("Scanning", "busy")
    assert not pill.animating
    with pytest.raises(ValueError):
        pill.set_status("x", "loud")


def components():
    def card():
        c = GlassCard()
        c.set_leading(RankBadge(2))
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(Label("QUIXOTIC", "word"))
        lay.addWidget(Label("H8 · Across", "footnote", "secondary"))
        c.set_body(body)
        c.set_trailing(ScoreBadge(96))
        c.resize(340, 70)
        return c

    def window():
        w = GlassWindow()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(w.content_margins(16))
        lay.addWidget(GlassButton("Pause"))
        lay.addWidget(Label("Plays", "headline"))
        w.set_glass_width(260)
        w.adjustSize()
        return w

    return {
        "window": window,
        "card": card,
        "button_plain": lambda: GlassButton("Pause", icon="pause"),
        "button_tinted": lambda: GlassButton("Rescan", variant="tinted", icon="refresh"),
        "button_prominent": lambda: GlassButton("Calibrate", variant="prominent", size="s"),
        "button_destructive": lambda: GlassButton("Reset", variant="destructive"),
        "icon_button": lambda: IconButton("gear", "Settings"),
        "icon_button_bare": lambda: IconButton("close", "Close", size="s", variant="bare"),
        "pill": lambda: StatusPill("Ready", "ok"),
        "segmented": lambda: Segmented(["Auto", "Dark", "Light"], 1),
        "switch": lambda: Switch(True, "Reduce motion"),
        "keycap": lambda: KeyCap("Shift"),
        "keycombo": lambda: KeyCombo("Ctrl+Shift+P"),
        "banner_info": lambda: Banner("Tracking resumed.", "info", dismissible=True),
        "banner_warning": lambda: Banner("Hidden squares.", "warning", action_text="Check"),
        "banner_error": lambda: Banner("Recording blocked.", "error"),
        "spinner": lambda: Spinner(20),
        "divider": lambda: Divider(),
        "section": lambda: SectionLabel("Capture"),
        "score": lambda: ScoreBadge(128),
        "rank": lambda: RankBadge(3, active=True),
        "label": lambda: Label("Hello", "title"),
        "glyph": lambda: Glyph("shield", 20),
    }


@pytest.mark.parametrize("dpr", [1.0, 2.0])
@pytest.mark.parametrize("name", list(components()))
def test_every_component_constructs_and_renders(qapp, name, dpr):
    widget = components()[name]()
    if name == "divider":
        widget.resize(120, 1)
    elif name not in ("window", "card"):
        widget.adjustSize()
    arr = pixels(widget, dpr)
    assert arr.shape[0] == round(widget.height() * dpr)
    assert arr[:, 3::4].max() > 0
    if name != "window":
        widget.setEnabled(False)
        assert pixels(widget, dpr).shape == arr.shape


@pytest.mark.parametrize("dpr", [1.0, 2.0])
@pytest.mark.parametrize("m", [DARK, LIGHT], ids=["dark", "light"])
def test_components_render_in_both_materials(qapp, m, dpr):
    win = GlassWindow(material=m)
    lay = QVBoxLayout(win)
    lay.setContentsMargins(win.content_margins(16))
    for name in ("card", "button_tinted", "segmented", "switch", "banner_warning", "pill"):
        lay.addWidget(components()[name]())
    win.adjustSize()
    arr = pixels(win, dpr)
    assert arr.shape == (round(win.height() * dpr), round(win.width() * dpr) * 4)
    inner = arr[
        round(SHADOW_BLEED * dpr) + 10 : -round(SHADOW_BLEED * dpr),
        round(SHADOW_BLEED * dpr) + 10 : -round(SHADOW_BLEED * dpr) * 4 : 4,
    ]
    assert inner.size > 0


def test_selected_and_disabled_visuals_differ(qapp):
    card = components()["card"]()
    base = pixels(card)
    card.set_selected(True)
    assert card.selected
    sel = pixels(card)
    assert differs(base, sel)
    card.setEnabled(False)
    assert differs(sel, pixels(card))
    badge = card._slots["leading"]
    assert badge._active

    btn = GlassButton("Pause")
    on = pixels(btn)
    btn.setEnabled(False)
    assert differs(on, pixels(btn))

    prom = GlassButton("Go", variant="prominent")
    on = pixels(prom)
    prom.setEnabled(False)
    assert differs(on, pixels(prom))

    sw = Switch(False)
    off = pixels(sw)
    sw.setChecked(True)
    assert differs(off, pixels(sw))

    seg = Segmented(["a", "b"], 0)
    first = pixels(seg)
    seg.set_current_index(1)
    assert differs(first, pixels(seg))

    ib = IconButton("gear", "x")
    a = pixels(ib)
    ib.setEnabled(False)
    assert differs(a, pixels(ib))


def test_card_slots_and_activation(qapp):
    card = GlassCard()
    clicks = []
    card.clicked.connect(lambda: clicks.append(1))
    first, second = RankBadge(1), RankBadge(2)
    card.set_leading(first)
    card.set_leading(second)
    assert card.row.count() == 1
    card.set_body(Label("x"))
    card.set_trailing(ScoreBadge(10))
    assert card.row.itemAt(0).widget() is second
    assert isinstance(card.row.itemAt(2).widget(), ScoreBadge)
    shown(card)
    QTest.mouseClick(card, Qt.MouseButton.LeftButton)
    assert clicks == [1]
    card.set_selected(True)
    assert second._active


def test_material_follows_the_window(qapp):
    win = GlassWindow(material=LIGHT)
    child = QWidget(win)
    assert material_of(child) is LIGHT
    assert material_of(QWidget()) is DARK
    set_default_material(LIGHT)
    assert material_of(QWidget()) is LIGHT


def test_light_and_dark_render_differently(qapp):
    btn = GlassButton("Pause")
    dark = pixels(btn)
    set_default_material(LIGHT)
    assert differs(dark, pixels(btn))


def test_segmented_keyboard(qapp):
    seg = shown(Segmented(["One", "Two", "Three"], 0))
    seen = []
    seg.current_changed.connect(seen.append)
    key(seg, Qt.Key.Key_Right)
    key(seg, Qt.Key.Key_Right)
    key(seg, Qt.Key.Key_Right)
    assert seg.current_index() == 2 and seen == [1, 2]
    key(seg, Qt.Key.Key_Left)
    assert seg.current_text() == "Two"
    key(seg, Qt.Key.Key_Home)
    assert seg.current_index() == 0
    key(seg, Qt.Key.Key_Left)
    assert seen == [1, 2, 1, 0]
    key(seg, Qt.Key.Key_End)
    assert seg.current_index() == 2
    assert "Three" in seg.accessibleDescription()


def test_segmented_mouse_and_validation(qapp):
    seg = shown(Segmented(["A", "B", "C", "D"], 0))
    QTest.mouseClick(seg, Qt.MouseButton.LeftButton, pos=QPoint(seg.width() - 10, 12))
    assert seg.current_index() == 3
    QTest.mouseClick(seg, Qt.MouseButton.LeftButton, pos=QPoint(6, 12))
    assert seg.current_index() == 0
    seg.setEnabled(False)
    QTest.mouseClick(seg, Qt.MouseButton.LeftButton, pos=QPoint(seg.width() - 10, 12))
    assert seg.current_index() == 0
    with pytest.raises(ValueError):
        Segmented(["only"])
    with pytest.raises(ValueError):
        Segmented(list("abcde"))


def test_segmented_thumb_reaches_its_index_without_motion(qapp):
    set_reduce_motion(True)
    seg = shown(Segmented(["A", "B", "C"], 0))
    seg.set_current_index(2)
    assert seg._thumb.value == 2.0


def test_switch_toggles_by_mouse_and_keyboard(qapp):
    sw = shown(Switch(False, "Overlay"))
    seen = []
    sw.toggled.connect(seen.append)
    QTest.mouseClick(sw, Qt.MouseButton.LeftButton)
    assert sw.isChecked() and seen == [True]
    key(sw, Qt.Key.Key_Space)
    assert not sw.isChecked() and seen == [True, False]
    sw.setEnabled(False)
    QTest.mouseClick(sw, Qt.MouseButton.LeftButton)
    assert not sw.isChecked()
    assert sw.accessibleName() == "Overlay"


def test_switch_thumb_travels_and_settles(qapp):
    sw = shown(Switch(False))
    sw.setChecked(True)
    assert sw._pos.running
    QTest.qWait(400)
    assert sw._pos.value == 1.0


def test_buttons_activate_with_keys_and_show_a_ring_for_keyboard_focus(qapp):
    btn = shown(GlassButton("Go"))
    hits = []
    btn.clicked.connect(lambda: hits.append(1))
    key(btn, Qt.Key.Key_Space)
    key(btn, Qt.Key.Key_Return)
    assert hits == [1, 1]
    plain = pixels(btn)
    QApplication.sendEvent(btn, QFocusEvent(QEvent.Type.FocusIn, Qt.FocusReason.MouseFocusReason))
    assert not differs(plain, pixels(btn))
    QApplication.sendEvent(btn, QFocusEvent(QEvent.Type.FocusIn, Qt.FocusReason.TabFocusReason))
    assert differs(plain, pixels(btn))
    QApplication.sendEvent(btn, QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.TabFocusReason))
    assert not differs(plain, pixels(btn))


def test_icon_button_names_itself_from_its_tooltip(qapp):
    ib = IconButton("gear", "Open settings")
    assert ib.toolTip() == "Open settings" == ib.accessibleName()
    ib.set_icon_name("play")
    assert ib.icon_name == "play"
    with pytest.raises(ValueError):
        IconButton("gear", "x", size="xl")


def test_button_validation(qapp):
    with pytest.raises(ValueError):
        GlassButton("x", variant="loud")
    with pytest.raises(ValueError):
        GlassButton("x", size="xl")
    small = GlassButton("Go", size="s")
    big = GlassButton("Go", size="m")
    assert small.sizeHint().height() < big.sizeHint().height()


def test_banner_dismiss_and_action(qapp):
    banner = shown(Banner("Hidden squares.", "warning", action_text="Check tiles"))
    dismissed, acted = [], []
    banner.dismissed.connect(lambda: dismissed.append(1))
    banner.action_clicked.connect(lambda: acted.append(1))
    banner.action.click()
    assert acted == [1] and banner.isVisible()
    banner.close_button.click()
    assert dismissed == [1] and not banner.isVisible()

    again = shown(Banner("Note", "info", dismissible=False))
    assert not again.close_button.isVisible()
    assert not again.action.isVisible()
    again.set_dismissible(True)
    again.dismiss()
    assert not again.isVisible()

    with pytest.raises(ValueError):
        Banner("x", "loud")
    assert banner.text() == "Hidden squares."


def test_banner_kinds_render_differently(qapp):
    shots = []
    for kind in ("info", "warning", "error"):
        b = Banner("Same words", kind)
        b.resize(320, 40)
        shots.append(pixels(b))
    assert differs(shots[0], shots[1]) and differs(shots[1], shots[2])


def test_label_wraps_and_reports_height_for_width(qapp):
    lab = Label("A long sentence that has to wrap onto several lines " * 3, wrap=True)
    assert lab.hasHeightForWidth()
    assert lab.heightForWidth(120) > lab.heightForWidth(400)
    plain = Label("Short", "title")
    assert not plain.hasHeightForWidth()
    assert plain.accessibleName() == "Short"
    plain.setText("Other")
    assert plain.text() == "Other"
    assert SectionLabel("capture")._display() == "CAPTURE"


def test_score_badge_keeps_a_stable_width(qapp):
    a, b = ScoreBadge(7), ScoreBadge(128)
    assert a.sizeHint().width() == b.sizeHint().width()
    assert ScoreBadge(1280).sizeHint().width() > b.sizeHint().width()
    b.set_score(None)
    assert b.accessibleName() == ""
    assert ScoreBadge(12).accessibleName() == "12 pts"


def test_key_combo_splits_keys(qapp):
    combo = KeyCombo("Ctrl+Shift+P")
    assert combo.layout().count() == 3
    combo.set_sequence("Esc")
    assert combo.layout().count() == 1 and combo.sequence == "Esc"


def test_icons_paint_and_validate(qapp):
    required = {
        "gear",
        "pause",
        "play",
        "refresh",
        "target",
        "check",
        "chevron-down",
        "chevron-right",
        "close",
        "minus",
        "ellipsis",
        "shield",
        "lock",
        "alert-triangle",
        "info",
        "keyboard",
        "folder",
        "sparkle",
        "wand",
        "spinner",
    }
    assert required <= set(ICON_NAMES)
    for name in ICON_NAMES:
        img = QImage(48, 48, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        paint_icon(p, name, QRectF(0, 0, 48, 48), DARK.text)
        p.end()
        arr = np.frombuffer(img.constBits(), np.uint8).reshape(48, img.bytesPerLine())
        assert arr[:, 3::4].max() > 200, name
    img = QImage(48, 48, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    with pytest.raises(ValueError):
        paint_icon(p, "nonsense", QRectF(0, 0, 48, 48), DARK.text)
    p.end()


def test_icon_weight_changes_the_stroke(qapp):
    def ink(weight):
        img = QImage(48, 48, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        paint_icon(p, "check", QRectF(0, 0, 48, 48), DARK.text, weight)
        p.end()
        arr = np.frombuffer(img.constBits(), np.uint8).reshape(48, img.bytesPerLine())
        return int(arr[:, 3::4].astype(np.int64).sum())

    assert ink(3.0) > ink(1.2)


def test_blend_materials_endpoints_and_midpoint():
    assert blend_materials(DARK, LIGHT, 0) is DARK
    assert blend_materials(DARK, LIGHT, 1) is LIGHT
    mid = blend_materials(DARK, LIGHT, 0.5)
    assert mid.text.red() not in (DARK.text.red(), LIGHT.text.red())
    assert mid.shadow == LIGHT.shadow
    assert mid.name == "light"


def test_glass_window_frame_and_geometry(qapp):
    win = GlassWindow()
    flags = win.windowFlags()
    assert flags & Qt.WindowType.FramelessWindowHint
    assert flags & Qt.WindowType.WindowStaysOnTopHint
    assert flags & Qt.WindowType.Tool
    assert win.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert win.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    focusable = GlassWindow(take_focus=True)
    assert not focusable.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

    win.set_glass_width(300)
    win.resize(win.width(), 200 + 2 * SHADOW_BLEED)
    assert win.width() == 300 + 2 * SHADOW_BLEED
    g = win.glass_rect()
    assert (g.x(), g.y(), g.width(), g.height()) == (SHADOW_BLEED, SHADOW_BLEED, 300, 200)
    m = win.content_margins(12, 10)
    assert (m.left(), m.top(), m.right(), m.bottom()) == (
        SHADOW_BLEED + 12,
        SHADOW_BLEED + 10,
        SHADOW_BLEED + 12,
        SHADOW_BLEED + 10,
    )
    assert win.content_margins().left() == SHADOW_BLEED + 16
    assert win.content_margins(1, 2, 3, 4).bottom() == SHADOW_BLEED + 4
    with pytest.raises(ValueError):
        win.content_margins(1, 2, 3)
    win.move_glass_to(100, 120)
    assert (win.x(), win.y()) == (100 - SHADOW_BLEED, 120 - SHADOW_BLEED)


def test_glass_window_asks_the_platform_for_a_backdrop(qapp, monkeypatch):
    calls = []

    def native(win_id, radius, dark, inset):
        calls.append((win_id, radius, dark, inset))
        return True

    monkeypatch.setattr(GlassWindow, "devicePixelRatioF", lambda self: 2.0)
    win = GlassWindow()
    win.resize(200, 200)
    assert not win.native_blur
    win.backdrop = native
    win.show()
    assert calls and calls[0][0] == int(win.winId())
    assert calls[0][1] == 26 and calls[0][2] is True
    assert calls[0][3] == SHADOW_BLEED
    assert win.native_blur
    win.set_material(LIGHT)
    assert calls[-1][2] is False and calls[-1][1] == 26 and calls[-1][3] == SHADOW_BLEED
    translucent = pixels(win)
    win.set_solid(True)
    assert not win.native_blur
    assert differs(translucent, pixels(win))
    win.hide()


def test_glass_window_still_takes_a_three_argument_hook(qapp):
    calls = []

    def older(win_id, radius_px, dark):
        calls.append((radius_px, dark))
        return True

    win = GlassWindow()
    win.resize(200, 200)
    win.backdrop = older
    win.show()
    assert calls == [(26, True)] and win.native_blur
    win.resize(240, 220)
    assert len(calls) == 2
    win.hide()


def test_a_type_error_inside_the_hook_is_not_mistaken_for_its_arity(qapp):
    calls = []

    def broken(win_id, radius, dark, inset):
        calls.append(1)
        raise TypeError("inside")

    win = GlassWindow()
    win.resize(200, 200)
    win.backdrop = broken
    win.show()
    assert calls == [1] and not win.native_blur
    win.hide()


def test_glass_window_asks_again_when_it_resizes_while_shown(qapp):
    calls = []
    win = GlassWindow()
    win.backdrop = lambda *a: calls.append(a) or True
    win.resize(200, 200)
    assert calls == []
    win.show()
    QApplication.processEvents()
    assert len(calls) == 1
    win.resize(300, 260)
    assert len(calls) == 2 and calls[-1][1:] == (26.0, True, float(SHADOW_BLEED))
    win.resize(300, 260)
    assert len(calls) == 2
    win.set_glass_width(400)
    assert len(calls) == 3 and win.native_blur
    win.hide()
    win.resize(500, 300)
    assert len(calls) == 3
    win.show()
    assert len(calls) == 4
    win.hide()


def test_a_resize_that_loses_the_blur_falls_back_to_the_solid_body(qapp):
    answers = iter([True, False, True])
    win = GlassWindow()
    win.backdrop = lambda *a: next(answers)
    win.resize(200, 200)
    win.show()
    assert win.native_blur
    win.resize(260, 200)
    assert not win.native_blur
    win.resize(300, 200)
    assert win.native_blur
    win.hide()


def test_set_solid_gives_the_blur_back_even_after_a_material_change(qapp):
    win = GlassWindow()
    win.backdrop = lambda *a: True
    win.resize(200, 200)
    win.show()
    win.set_solid(True)
    win.set_material(LIGHT)
    assert not win.native_blur
    win.set_solid(False)
    assert win.native_blur
    win.set_solid(False)
    win.hide()
    win.set_solid(True)
    win.set_solid(False)
    win.show()
    assert win.native_blur
    win.hide()


class RecordingAdapter(PlatformAdapter):
    def __init__(self, answer=True):
        self.calls = []
        self.answer = answer

    def capture(self, region):
        raise AssertionError

    def screen_permission(self):
        return None

    def apply_backdrop(self, win_id, radius, dark, inset=0.0):
        self.calls.append((win_id, radius, dark, inset))
        return self.answer


def test_glass_window_takes_an_adapter_object_or_its_bound_method(qapp):
    for use in (lambda a: a, lambda a: a.apply_backdrop):
        adapter = RecordingAdapter()
        win = GlassWindow()
        win.resize(200, 200)
        win.backdrop = use(adapter)
        win.show()
        assert adapter.calls == [(int(win.winId()), 26.0, True, float(SHADOW_BLEED))]
        assert win.native_blur
        win.hide()
    plain = RecordingAdapter(answer=False)
    win = GlassWindow()
    win.resize(200, 200)
    win.backdrop = plain
    win.show()
    assert plain.calls and not win.native_blur
    win.hide()


def test_glass_window_takes_module_level_functions_and_does_not_raise_offscreen(qapp):
    for source in (macos, windows, macos.apply_backdrop, windows.apply_backdrop):
        win = GlassWindow()
        win.resize(200, 200)
        win.backdrop = source
        win.show()
        win.resize(220, 210)
        assert not win.native_blur
        win.hide()
    assert call_backdrop(macos, 3, 26.0, True, 36.0) is False
    assert call_backdrop(windows, 3, 26.0, True, 36.0) is False


def test_the_class_level_hook_does_not_bind_to_the_window(qapp):
    calls = []
    try:
        GlassWindow.backdrop = SimpleNamespace(apply_backdrop=lambda *a: calls.append(a) or True)
        win = GlassWindow()
        win.resize(200, 200)
        win.show()
        assert len(calls) == 1 and len(calls[0]) == 4 and win.native_blur
        win.hide()
        GlassWindow.backdrop = native_backdrop()
        other = GlassWindow()
        other.resize(200, 200)
        other.show()
        assert not other.native_blur
        other.hide()
    finally:
        GlassWindow.backdrop = None


def test_call_backdrop_forms_and_arity(monkeypatch):
    seen = []

    def four(a, b, c, d):
        seen.append(("four", d))
        return 1

    def three(a, b, c):
        seen.append(("three",))
        return 1

    def defaulted(a, b, c, d=0.0):
        seen.append(("defaulted", d))
        return 1

    def varargs(*a):
        seen.append(("varargs", len(a)))
        return 1

    def keyword_only(a, b, c, d=0.0, *, api=None):
        seen.append(("keyword_only", d))
        return 1

    for fn in (four, three, defaulted, varargs, keyword_only):
        assert call_backdrop(fn, 1, 26.0, True, 36.0) is True
    assert seen == [
        ("four", 36.0),
        ("three",),
        ("defaulted", 36.0),
        ("varargs", 4),
        ("keyword_only", 36.0),
    ]
    assert takes_inset(four) and not takes_inset(three) and not takes_inset(len)

    def unsigned(fn):
        raise ValueError("no signature")

    monkeypatch.setattr(native_mod.inspect, "signature", unsigned)
    assert takes_inset(len)
    assert call_backdrop(None, 1, 26.0, True, 36.0) is False
    assert call_backdrop(object(), 1, 26.0, True, 36.0) is False
    assert resolve_backdrop(None) is None and resolve_backdrop(3) is None
    adapter = RecordingAdapter()
    assert resolve_backdrop(adapter) == adapter.apply_backdrop


def test_native_backdrop_is_a_no_op_without_a_native_window(qapp):
    assert QApplication.platformName() == "offscreen"
    for name in ("darwin", "win32", "windows", "linux", None):
        hook = native_backdrop(name)
        assert isinstance(hook, NativeBackdrop)
        assert hook(int(QWidget().winId()), 26.0, True, 36.0) is False
        assert hook(0, 26.0, True) is False
    assert not native_backdrop("linux").available
    assert native_backdrop("darwin").available and native_backdrop("win32").available


@pytest.mark.parametrize(
    ("platform", "qt_name", "module"),
    [("darwin", "cocoa", macos), ("win32", "windows", windows)],
)
def test_native_backdrop_delegates_to_the_platform_module(monkeypatch, platform, qt_name, module):
    calls = []
    monkeypatch.setattr(module, "apply_backdrop", lambda *a: calls.append(a) or True)
    monkeypatch.setattr(
        native_mod, "QGuiApplication", SimpleNamespace(platformName=lambda: qt_name)
    )
    hook = native_backdrop(platform)
    assert hook(0x20000, 26, True, 36) is True
    assert calls == [(0x20000, 26.0, True, 36.0)]
    assert hook(0x20000, 26, False) is True and calls[-1] == (0x20000, 26.0, False, 0.0)
    monkeypatch.setattr(native_mod, "QGuiApplication", SimpleNamespace(platformName=lambda: "xcb"))
    assert hook(0x20000, 26, True, 36) is False and len(calls) == 2


def test_native_backdrop_survives_a_platform_module_that_will_not_load(monkeypatch):
    def refuse(name):
        raise ImportError(name)

    monkeypatch.setattr(
        native_mod, "QGuiApplication", SimpleNamespace(platformName=lambda: "cocoa")
    )
    monkeypatch.setattr(native_mod.importlib, "import_module", refuse)
    assert native_backdrop("darwin")(0x20000, 26, True, 36) is False


@pytest.mark.skipif(sys.platform != "darwin", reason="needs the macOS window server")
def test_glass_window_blurs_a_real_macos_window_and_follows_its_resizes():
    pytest.importorskip("objc")
    code = textwrap.dedent(
        """
        import time
        import objc
        from PySide6.QtWidgets import QApplication
        from tilelens.ui.design import LIGHT, SHADOW_BLEED, GlassWindow, native_backdrop

        app = QApplication([])
        win = GlassWindow()
        win.backdrop = native_backdrop()
        win.resize(332, 160)
        win.show()

        def pump():
            for _ in range(5):
                app.processEvents()
                time.sleep(0.02)

        def effects():
            window = objc.objc_object(c_void_p=int(win.winId())).window()
            content = window.contentView()
            found = [v for v in content.superview().subviews() if v.identifier()]
            return content.frame(), found

        pump()
        content, found = effects()
        view = found[0]
        frame = view.frame()
        out = [win.native_blur, len(found) == 1, view.layer().cornerRadius() == 26.0]
        out += [(frame.origin.x, frame.origin.y) == (SHADOW_BLEED, SHADOW_BLEED)]
        win.resize(400, 260)
        pump()
        content, found = effects()
        frame = found[0].frame()
        out += [len(found) == 1, win.native_blur]
        out += [frame.size.width == content.size.width - 2 * SHADOW_BLEED]
        out += [frame.size.height == content.size.height - 2 * SHADOW_BLEED]
        win.set_material(LIGHT)
        pump()
        out += [len(effects()[1]) == 1, win.native_blur]
        print("RESULT", len(out), all(out), out)
        win.close()
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "QT_QPA_PLATFORM": "cocoa"},
    )
    assert "RESULT 10 True" in proc.stdout, proc.stdout + proc.stderr


def test_glass_window_falls_back_to_solid(qapp):
    for outcome in (False, "boom"):
        win = GlassWindow()
        win.resize(200, 200)
        if outcome == "boom":

            def native(*_):
                raise RuntimeError("no effect here")

        else:

            def native(*_):
                return False

        win.backdrop = native
        win.show()
        assert not win.native_blur
        assert pixels(win).shape[0] == 200
        win.hide()
    plain = GlassWindow()
    plain.resize(200, 200)
    solid = pixels(plain)
    plain.backdrop = lambda *_: True
    plain.show()
    assert differs(solid, pixels(plain))
    plain.hide()


def test_glass_window_on_shown_hook(qapp):
    seen = []
    win = GlassWindow()
    win.on_shown = seen.append
    win.resize(100, 100)
    win.show()
    assert seen == [int(win.winId())]
    win.hide()


def test_glass_window_material_switch_is_instant_with_reduce_motion(qapp):
    win = shown(GlassWindow(), 200, 200)
    changed = []
    win.material_changed.connect(changed.append)
    set_reduce_motion(True)
    win.set_material(LIGHT)
    assert win.glass_material is LIGHT and changed == [LIGHT]
    win.set_material(LIGHT)
    assert changed == [LIGHT]
    win.hide()


def test_glass_window_cross_fades_the_material(qapp):
    win = shown(GlassWindow(), 200, 200)
    win.set_material(LIGHT)
    assert win.glass_material is DARK
    QTest.qWait(70)
    mid = win.glass_material
    assert mid is not LIGHT and mid is not DARK
    assert win.material is LIGHT
    QTest.qWait(400)
    assert win.glass_material is LIGHT
    win.hide()


def test_glass_window_strength_changes_the_fill(qapp):
    win = GlassWindow()
    win.resize(200, 200)
    set_reduce_motion(True)
    base = pixels(win)
    win.set_strength(1.15)
    assert win.strength == pytest.approx(1.15)
    assert differs(base, pixels(win))
    win.set_strength(5)
    assert win.strength == pytest.approx(1.3)


def test_glass_window_binds_to_an_appearance(qapp):
    set_reduce_motion(True)
    win = GlassWindow()
    app = Appearance("dark")
    win.bind(app)
    app.set_mode("light")
    assert win.glass_material is LIGHT
    other = Appearance("dark")
    win.bind(other)
    app.set_mode("dark")
    assert win.glass_material is DARK
    other.set_mode("light")
    assert win.glass_material is LIGHT


def test_glass_window_drag_reports_user_moves(qapp):
    win = shown(GlassWindow(), 240, 160)
    win.move(50, 60)
    moves = []
    win.moved_by_user.connect(moves.append)
    inside = QPoint(SHADOW_BLEED + 20, SHADOW_BLEED + 20)
    start = win.mapToGlobal(inside)
    QApplication.sendEvent(win, mouse_event(QEvent.Type.MouseButtonPress, inside, start))
    QApplication.sendEvent(
        win,
        mouse_event(QEvent.Type.MouseMove, inside, start + QPoint(1, 1)),
    )
    assert moves == [] and not win.user_moved
    QApplication.sendEvent(
        win,
        mouse_event(QEvent.Type.MouseMove, inside, start + QPoint(30, 12)),
    )
    QApplication.sendEvent(
        win,
        mouse_event(
            QEvent.Type.MouseButtonRelease, inside, start + QPoint(30, 12), Qt.MouseButton.NoButton
        ),
    )
    assert win.user_moved and moves
    assert (win.x(), win.y()) == (80, 72)
    assert moves[-1] == QPoint(80 + SHADOW_BLEED, 72 + SHADOW_BLEED)
    win.move(0, 0)
    win.move_glass_to(moves[-1])
    assert (win.x(), win.y()) == (80, 72)
    win.hide()


def test_glass_window_ignores_drags_that_start_in_the_shadow(qapp):
    win = shown(GlassWindow(), 240, 160)
    win.move(50, 60)
    outside = QPoint(4, 4)
    start = win.mapToGlobal(outside)
    QApplication.sendEvent(win, mouse_event(QEvent.Type.MouseButtonPress, outside, start))
    QApplication.sendEvent(win, mouse_event(QEvent.Type.MouseMove, outside, start + QPoint(40, 40)))
    assert not win.user_moved
    win.hide()


def test_glass_window_extra_style_and_palette_follow_the_material(qapp):
    win = GlassWindow(material=DARK)
    label = QLabel("Plain", win)
    win.set_extra_qss("QLabel#note { color: red; }")
    assert "QLabel#note" in win.styleSheet()
    before = win.styleSheet()
    win.set_material(LIGHT)
    assert win.styleSheet() != before and "QLabel#note" in win.styleSheet()
    assert win.palette().windowText().color().red() < 100
    assert label.parent() is win


@pytest.mark.parametrize("m", [DARK, LIGHT], ids=["dark", "light"])
def test_qss_builds_for_each_material(m):
    qss = build_qss(m)
    assert qss.count("{") == qss.count("}")
    for selector in (
        "QDialog",
        "QLabel",
        "QLineEdit",
        "QComboBox",
        "QComboBox QAbstractItemView",
        "QCheckBox",
        "QRadioButton",
        "QSpinBox",
        "QSlider",
        "QScrollBar",
        "QToolTip",
        "QMenu",
        "QPushButton",
    ):
        assert selector in qss, selector
    assert "None" not in qss and "PySide" not in qss
    assert "rgba(" in qss


def test_qss_differs_between_materials():
    assert build_qss(DARK) != build_qss(LIGHT)


def test_standard_widgets_render_with_the_style(qapp):
    style = GlassStyle()
    for m in (DARK, LIGHT):
        dlg = QDialog()
        dlg.setStyle(style)
        dlg.setStyleSheet(build_qss(m))
        lay = QVBoxLayout(dlg)
        combo = QComboBox()
        combo.addItems(["one", "two"])
        check = QCheckBox("Check")
        check.setChecked(True)
        radio = QRadioButton("Radio")
        slider = QSlider(Qt.Orientation.Horizontal)
        for w in (QLabel("Text"), QLineEdit("abc"), combo, QSpinBox(), check, radio, slider):
            lay.addWidget(w)
        dlg.resize(260, 300)
        arr = pixels(dlg)
        assert arr.size > 0
        check.setChecked(False)
        assert differs(arr, pixels(dlg))
        menu = QMenu()
        menu.setStyleSheet(build_qss(m))
        menu.addAction("Pause")
        menu.addSeparator()
        menu.addAction("Quit").setEnabled(False)
        menu.adjustSize()
        assert pixels(menu).size > 0


@pytest.mark.parametrize("m", [DARK, LIGHT], ids=["dark", "light"])
def test_qss_parses_without_qt_warnings(qapp, m):
    seen = []
    previous = qInstallMessageHandler(lambda kind, ctx, msg: seen.append(msg))
    try:
        host = QWidget()
        host.setStyleSheet(build_qss(m))
        lay = QVBoxLayout(host)
        for w in (QLabel("x"), QLineEdit(), QComboBox(), QSpinBox(), QCheckBox("c"), QSlider()):
            lay.addWidget(w)
        host.ensurePolished()
        for child in host.findChildren(QWidget):
            child.ensurePolished()
        host.grab()
    finally:
        qInstallMessageHandler(previous)
    assert not [msg for msg in seen if "parse" in msg.lower() or "unknown property" in msg.lower()]


def test_apply_app_style_sets_font_palette_and_sheet(qapp):
    saved = (qapp.font(), qapp.palette(), qapp.styleSheet())
    try:
        apply_app_style(qapp, LIGHT, widget_style=False)
        assert qapp.styleSheet() == build_qss(LIGHT)
        assert qapp.font().pixelSize() == 13
        assert qapp.palette().windowText().color().red() < 100
        apply_app_style(qapp, DARK, widget_style=False)
        assert qapp.palette().windowText().color().red() > 200
    finally:
        qapp.setFont(saved[0])
        qapp.setPalette(saved[1])
        qapp.setStyleSheet(saved[2])


def test_unknown_icons_and_tones_fail_where_they_are_written(qapp):
    with pytest.raises(ValueError):
        GlassButton("x", icon="nope")
    with pytest.raises(ValueError):
        IconButton("nope", "x")
    with pytest.raises(ValueError):
        Glyph("nope")
    with pytest.raises(ValueError):
        Glyph("info", tone="loud")
    with pytest.raises(ValueError):
        Label("x", tone="loud")
    with pytest.raises(ValueError):
        Spinner(tone="loud")
    btn, ib, glyph, label = (
        GlassButton("x", icon="gear"),
        IconButton("gear", "x"),
        Glyph("info"),
        Label("x"),
    )
    for call in (
        lambda: btn.set_icon_name("nope"),
        lambda: ib.set_icon_name("nope"),
        lambda: glyph.set_name("nope"),
        lambda: glyph.set_tone("loud"),
        lambda: label.set_tone("loud"),
    ):
        with pytest.raises(ValueError):
            call()
    assert ib.icon_name == "gear"
    btn.set_icon_name(None)
    ib.set_icon_name("settings")
    for widget in (btn, ib, glyph, label):
        assert pixels(widget).size


def test_dropped_controls_are_released(qapp):
    makers = (
        lambda: GlassButton("x"),
        lambda: IconButton("gear", "x"),
        lambda: Switch(True, "x"),
        lambda: GlassCard(),
        lambda: Segmented(["a", "b"]),
        lambda: StatusPill("x", "busy"),
        lambda: Spinner(),
    )
    for make in makers:
        widget = make()
        ref = weakref.ref(widget)
        shown(widget)
        widget.hide()
        QApplication.processEvents()
        del widget
        gc.collect()
        assert ref() is None, make


def test_an_eliding_label_gives_way_in_a_narrow_layout(qapp):
    host = QWidget()
    lay = QHBoxLayout(host)
    long = Label("A very long label text that has to be cut short somewhere", elide=True)
    tail = Label("Tail")
    lay.addWidget(long)
    lay.addWidget(tail)
    shown(host, 160, 30)
    assert long.width() < long.sizeHint().width()
    assert long.width() + tail.width() <= 160
    assert host.minimumSizeHint().width() < long.sizeHint().width()

    rigid = QWidget()
    row = QHBoxLayout(rigid)
    row.addWidget(Label("A very long label text that must stay whole"))
    assert rigid.minimumSizeHint().width() >= row.itemAt(0).widget().sizeHint().width()


def test_segmented_drops_a_press_that_was_interrupted_by_disabling(qapp):
    seg = shown(Segmented(["A", "B", "C"], 0))
    at = QPoint(seg.width() - 10, 12)
    QTest.mousePress(seg, Qt.MouseButton.LeftButton, pos=at)
    seg.setEnabled(False)
    seg.setEnabled(True)
    QTest.mouseRelease(seg, Qt.MouseButton.LeftButton, pos=at)
    assert seg.current_index() == 0
    QTest.mouseClick(seg, Qt.MouseButton.LeftButton, pos=at)
    assert seg.current_index() == 2


def test_key_combo_reads_a_plus_key(qapp):
    texts = {
        "Ctrl+Shift+P": ["Ctrl", "Shift", "P"],
        "Ctrl++": ["Ctrl", "+"],
        "Ctrl+Shift++": ["Ctrl", "Shift", "+"],
        "+": ["+"],
        " Ctrl + P ": ["Ctrl", "P"],
        "Ctrl+": ["Ctrl"],
        "": [],
    }
    for sequence, keys in texts.items():
        combo = KeyCombo(sequence)
        assert [c.text() for c in combo.findChildren(KeyCap)] == keys, sequence
        assert combo.sequence == sequence


def test_key_combo_replaces_its_caps_at_once(qapp):
    combo = shown(KeyCombo("Ctrl+P"))
    old = combo.findChildren(KeyCap)
    assert len(old) == 2
    combo.set_sequence("A+B+C")
    assert [c.text() for c in combo.findChildren(KeyCap)] == ["A", "B", "C"]
    assert all(c.parent() is None for c in old)


def test_glyph_files_are_named_by_content_and_written_whole(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(qss_mod, "glyph_dir", lambda: tmp_path)
    monkeypatch.setattr(qss_mod, "_glyph_cache", {})
    url = qss_mod.glyph_url("chevron-down", DARK.text2, 12)
    assert url and os.path.exists(url) and os.path.exists(url[:-4] + "@2x.png")
    assert not list(tmp_path.glob("*.part"))
    files = sorted(tmp_path.iterdir())
    assert qss_mod.glyph_url("chevron-down", DARK.text2, 12) == url
    assert sorted(tmp_path.iterdir()) == files

    def redrawn(p, name, rect, color, weight=1.6):
        p.fillRect(rect, color)

    monkeypatch.setattr(qss_mod, "paint_icon", redrawn)
    monkeypatch.setattr(qss_mod, "_glyph_cache", {})
    other = qss_mod.glyph_url("chevron-down", DARK.text2, 12)
    assert other != url and os.path.exists(other)


def test_glyph_files_that_cannot_be_written_leave_the_arrows_to_the_style(
    qapp, tmp_path, monkeypatch
):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setattr(qss_mod, "glyph_dir", lambda: blocker / "sub")
    monkeypatch.setattr(qss_mod, "_glyph_cache", {})
    assert qss_mod.glyph_url("chevron-down", DARK.text2, 12) is None
    assert "down-arrow" not in build_qss(DARK)
    img = QImage(4, 4, QImage.Format.Format_ARGB32_Premultiplied)
    with pytest.raises(OSError):
        qss_mod._write_png(img, tmp_path / "missing" / "x.png")

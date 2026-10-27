import pytest
from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtWidgets import QApplication, QWidget

from tilelens.ui import notify
from tilelens.ui.design import (
    DARK,
    LIGHT,
    Appearance,
    set_default_material,
    set_reduce_motion,
)
from tilelens.ui.notify import MIN_VISIBLE_MS, Toast, ToastManager


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def calm(qapp):
    set_reduce_motion(True)
    set_default_material(DARK)
    yield
    set_reduce_motion(False)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def manager(qapp, clock):
    mgr = ToastManager(clock=clock)
    yield mgr
    mgr.clear()
    if mgr.toast is not None:
        mgr.toast.close()


def test_toast_never_takes_focus(qapp):
    toast = Toast("Saved", "success")
    assert toast.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert toast.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert toast.focusPolicy() == Qt.FocusPolicy.NoFocus
    toast.close()


def test_click_through_option(qapp):
    solid = Toast("Saved", click_through=False)
    through = Toast("Saved", click_through=True)
    assert not solid.click_through and through.click_through
    through.set_click_through(False)
    assert not through.click_through
    solid.close()
    through.close()


def test_kinds_set_icon_and_accessible_description(qapp):
    toast = Toast("x")
    for kind in notify.KINDS:
        toast.set_content("Something happened", kind)
        assert toast.kind == kind
        assert toast.accessibleDescription() == kind
        assert toast.accessibleName() == "Something happened"
    with pytest.raises(ValueError):
        toast.set_content("x", "debug")
    toast.close()


def test_long_text_wraps_inside_the_maximum_width(qapp):
    toast = Toast("short")
    short = toast.glass_size()
    toast.set_content("word " * 80, "info")
    wide = toast.glass_size()
    assert wide[0] <= notify.MAX_TEXT_WIDTH + 80
    assert wide[1] > short[1]
    toast.close()


def test_shows_one_at_a_time_in_order(manager, clock):
    assert manager.show("one", "info") is True
    assert manager.show("two", "success") is True
    assert manager.show("three", "warning") is True
    assert manager.current == ("one", "info")
    assert manager.pending == [("two", "success"), ("three", "warning")]
    assert manager.toast.isVisible()
    assert manager.toast.text == "one"
    clock.now = 5.0
    manager._dismiss()
    assert manager.current == ("two", "success")
    assert manager.toast.text == "two"
    manager._dismiss()
    assert manager.current == ("three", "warning")
    manager._dismiss()
    assert manager.current is None and not manager.toast.isVisible()


def test_duplicates_are_dropped_but_extend_the_current_one(manager):
    assert manager.show("Saved", "success", ms=1000)
    assert manager.show("Saved", "success", ms=4000) is False
    assert manager._dwell.remainingTime() > 1500
    manager.show("Other", "info")
    assert manager.show("Other", "info") is False
    assert manager.pending == [("Other", "info")]


def test_empty_text_is_ignored_and_whitespace_collapsed(manager):
    assert manager.show("   ", "info") is False
    assert manager.current is None
    manager.show("a  b\n c", "info")
    assert manager.current == ("a b c", "info")


def test_a_waiting_toast_shortens_the_visible_one(manager, clock):
    manager.show("first", "info", ms=10000)
    assert manager._dwell.remainingTime() > 5000
    clock.now = 0.2
    manager.show("second", "info")
    assert manager._dwell.remainingTime() <= MIN_VISIBLE_MS - 150


def test_queue_is_capped(manager):
    manager.show("current", "info")
    for i in range(10):
        manager.show(f"waiting {i}", "info")
    assert len(manager.pending) == notify.MAX_PENDING
    assert manager.pending[-1][0] == "waiting 9"


def test_unknown_kind_is_an_error(manager):
    with pytest.raises(ValueError):
        manager.show("x", "debug")


def test_clear_removes_everything(manager):
    manager.show("one")
    manager.show("two")
    manager.clear()
    assert manager.current is None and manager.pending == []
    assert not manager.toast.isVisible()


def test_reduce_motion_makes_the_fade_instant(manager):
    manager.show("hello", "info")
    assert manager.toast.opacity == 1.0
    manager._dismiss()
    assert manager.current is None and not manager.toast.isVisible()


def test_fade_is_at_most_one_hundred_eighty_ms(qapp):
    assert notify.FADE_MS <= 180
    set_reduce_motion(False)
    toast = Toast("Hello")
    toast.fade_in()
    assert toast._alpha._anim.duration() <= 180
    toast.close()


def test_placement_over_an_anchor_stays_on_screen(manager, qapp):
    anchor = QWidget()
    anchor.setGeometry(40, 40, 300, 200)
    anchor.show()
    manager.show("Over a window", "info", anchor=anchor)
    glass = manager.toast.glass_geometry()
    screen = qapp.primaryScreen().availableGeometry()
    assert screen.contains(glass)
    assert abs(glass.center().x() - anchor.geometry().center().x()) <= 3
    anchor.close()


@pytest.mark.parametrize("anchor", [None, QRect(10, 10, 100, 40), QRectF(5, 5, 50, 20)])
def test_placement_for_other_anchors(manager, qapp, anchor):
    manager.show("Placed", "success", anchor=anchor)
    assert qapp.primaryScreen().availableGeometry().contains(manager.toast.glass_geometry())


def test_a_hidden_anchor_falls_back_to_the_screen(manager, qapp):
    anchor = QWidget()
    manager.show("Placed", "info", anchor=anchor)
    assert qapp.primaryScreen().availableGeometry().contains(manager.toast.glass_geometry())
    anchor.close()


def test_module_level_show_uses_one_manager(qapp):
    first = notify.manager()
    assert notify.manager() is first
    assert notify.show("Hello from the module", "info") is True
    first.clear()
    if first.toast is not None:
        first.toast.close()


def test_toasts_follow_a_bound_appearance(manager):
    appearance = Appearance("light", initial=LIGHT)
    manager.bind(appearance)
    manager.show("Light", "info")
    assert manager.toast.material.name == LIGHT.name
    appearance.set_mode("dark")
    assert manager.toast.material.name == DARK.name

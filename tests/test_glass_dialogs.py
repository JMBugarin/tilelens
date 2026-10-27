import os
import sys
import time
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QRect, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from tilelens.ui.calibrate_dialog import CalibrationDialog, RectPicker
from tilelens.ui.corrections import CorrectionDialog
from tilelens.ui.design import DARK, LIGHT, GlassButton, set_reduce_motion
from tilelens.ui.glass_dialog import (
    GlassDialog,
    LetterField,
    Notice,
    RackField,
    Steps,
)
from tilelens.ui.region_select import RegionSelector

ROOM = QRect(0, 0, 1440, 900)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def still(qapp):
    saved = GlassDialog.appearance
    GlassDialog.appearance = None
    set_reduce_motion(True)
    yield
    set_reduce_motion(False)
    GlassDialog.appearance = saved


@pytest.fixture(scope="module")
def gallery():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    from ui_gallery import ctx

    return ctx


def roomy(dlg):
    dlg.available_geometry = lambda: ROOM
    return dlg


def key(widget, k, text="", mods=Qt.KeyboardModifier.NoModifier):
    ev = QKeyEvent(QKeyEvent.Type.KeyPress, k, mods, text)
    widget.keyPressEvent(ev)
    return ev


def mouse(kind, x, y, button=Qt.MouseButton.LeftButton):
    return QMouseEvent(
        kind, QPointF(x, y), QPointF(x, y), button, button, Qt.KeyboardModifier.NoModifier
    )


def press(w, x, y):
    w.mousePressEvent(mouse(QMouseEvent.Type.MouseButtonPress, x, y))


def move(w, x, y):
    w.mouseMoveEvent(mouse(QMouseEvent.Type.MouseMove, x, y))


def release(w, x, y):
    w.mouseReleaseEvent(mouse(QMouseEvent.Type.MouseButtonRelease, x, y))


class Sheet(GlassDialog):
    def __init__(self):
        super().__init__("Title", "Subtitle")
        self.picked = 0
        self.primary, self.secondary = self.add_footer("Go", "Cancel")
        self.primary.clicked.connect(self._go)

    def _go(self):
        self.picked += 1


def test_sheet_is_a_frameless_translucent_stay_on_top_dialog(qapp):
    dlg = Sheet()
    flags = dlg.windowFlags()
    assert flags & Qt.WindowType.FramelessWindowHint
    assert flags & Qt.WindowType.WindowStaysOnTopHint
    assert dlg.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert dlg.testAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
    assert dlg.title_label.text() == "Title" and dlg.subtitle_label.text() == "Subtitle"
    assert dlg.primary.variant == "prominent" and dlg.secondary.variant == "plain"
    assert dlg.close_button.accessibleName() == "Close"


def test_sheet_can_leave_the_game_focused(qapp):
    dlg = GlassDialog("Quiet", take_focus=False)
    assert dlg.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert not GlassDialog("Asks").testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)


def test_enter_presses_the_primary_button_only_when_it_is_enabled(qapp):
    dlg = Sheet()
    key(dlg, Qt.Key.Key_Return)
    assert dlg.picked == 1
    dlg.set_primary_enabled(False)
    key(dlg, Qt.Key.Key_Enter)
    assert dlg.picked == 1


def test_escape_rejects_and_a_busy_sheet_holds(qapp):
    dlg = Sheet()
    dlg.show()
    seen = []
    dlg.rejected.connect(lambda: seen.append("no"))
    dlg.set_busy("Working")
    dlg.reject()
    assert dlg.isVisible() and not seen
    dlg.set_busy(None)
    QTest.keyClick(dlg, Qt.Key.Key_Escape)
    assert seen == ["no"] and not dlg.isVisible()


def test_busy_swaps_the_footer_and_locks_the_form(qapp):
    dlg = Sheet()
    dlg.set_busy("Calibrating")
    assert dlg.busy
    assert not dlg.footer_actions.isEnabled() and not dlg.body_host.isEnabled()
    assert not dlg.close_button.isEnabled()
    assert dlg._status_row.label.text() == "Calibrating"
    dlg.set_busy(None)
    assert dlg.footer_actions.isEnabled() and dlg.close_button.isEnabled()


def test_run_blocking_returns_value_and_error(qapp):
    dlg = Sheet()
    assert dlg.run_blocking(lambda: 7) == 7
    dlg.show()
    assert dlg.run_blocking(lambda: 8, grace=0) == 8
    seen = []
    dlg.busy_changed.connect(seen.append)

    def slow():
        time.sleep(0.12)
        return "done"

    assert dlg.run_blocking(slow, "Slow", grace=10) == "done"
    assert seen == [True, False] and not dlg.busy and not dlg.working

    def fail():
        raise ValueError("no")

    with pytest.raises(ValueError, match="no"):
        dlg.run_blocking(fail)
    assert not dlg.working


def test_success_accepts_after_a_hold_and_not_at_all_when_told(qapp):
    dlg = Sheet()
    dlg.show_success("Done")
    assert dlg.result() == dlg.DialogCode.Accepted
    held = Sheet()
    held.show_success("Done", then_accept=False)
    assert held.success_shown and held.result() != held.DialogCode.Accepted
    held.clear_success()
    assert held.footer_actions.isEnabled()


def test_sheet_follows_the_material_and_a_native_blur(qapp):
    asked = []

    def blur(win_id, radius, dark, inset=0.0):
        asked.append((dark, inset))
        return True

    dlg = Sheet()
    dlg.backdrop = blur
    dlg.show()
    assert dlg.native_blur and asked
    dlg.set_material(LIGHT)
    assert dlg.material is LIGHT and asked[-1][0] is False
    dlg.set_solid(True)
    assert not dlg.native_blur
    plain = Sheet()
    plain.backdrop = lambda *a: False
    plain.show()
    assert not plain.native_blur
    assert not plain.grab().isNull()


def test_sheet_never_outgrows_the_screen_it_is_on(qapp):
    dlg = Sheet()
    dlg.set_glass_size(2000, 2000)
    dlg.available_geometry = lambda: QRect(0, 0, 1000, 700)
    dlg.show()
    glass = dlg.glass_rect()
    assert glass.width() <= 1000 - 32 and glass.height() <= 700 - 32


def test_steps_describe_themselves_and_reject_nonsense(qapp):
    steps = Steps(["One", "Two", "Three"])
    steps.set_active(1)
    assert steps.states == ["done", "active", "todo"]
    assert "2. Two (active)" in steps.accessibleDescription()
    with pytest.raises(ValueError):
        steps.set_states(["done"])
    with pytest.raises(ValueError):
        steps.set_states(["done", "later", "todo"])
    steps.resize(120, steps.height())
    assert not steps.grab().isNull()


def test_notice_keeps_its_height_when_it_empties(qapp):
    n = Notice()
    n.resize(300, 100)
    empty_h = n.minimumHeight()
    n.message("Something went wrong with the rack", "error")
    assert n.kind == "error" and n.text().startswith("Something")
    n.message("All good", "success")
    assert n.kind == "success"
    n.clear()
    assert n.empty and n.minimumHeight() == empty_h > 0
    with pytest.raises(ValueError):
        n.message("x", "loud")


def test_letter_field_notation_and_keys(qapp):
    f = LetterField()
    entered = []
    f.entered.connect(lambda: entered.append(1))
    key(f, Qt.Key.Key_A, "a")
    assert f.text() == "A" and entered
    key(f, Qt.Key.Key_Question, "?")
    assert f.text() == "a" and f.parts() == ("A", True, False, True)
    key(f, Qt.Key.Key_B, "B")
    assert f.text() == "b"
    key(f, Qt.Key.Key_AsciiTilde, "~")
    assert f.text() == "~b"
    key(f, Qt.Key.Key_Backspace)
    assert f.text() == ""
    f.setText("12")
    assert f.parts()[3] is False
    f.setText("~A")
    assert f.parts() == ("A", False, True, True)


def test_letter_field_lets_enter_tab_and_escape_through(qapp):
    f = LetterField()
    for k in (Qt.Key.Key_Return, Qt.Key.Key_Tab, Qt.Key.Key_Escape):
        assert not key(f, k, {Qt.Key.Key_Return: "\r"}.get(k, "")).isAccepted()
    assert key(f, Qt.Key.Key_5, "5").isAccepted()


def test_letter_field_user_edit_ends_a_guess(qapp):
    f = LetterField("R")
    f.set_guess(True)
    assert f.guess
    f.type_letter("e")
    assert f.text() == "E" and not f.guess


def test_rack_field_types_advances_and_grows_only_by_paste(qapp):
    r = RackField(7)
    changes = []
    r.text_changed.connect(changes.append)
    r.focus_slot(0)
    for ch in "cat":
        key(r, Qt.Key.Key_A, ch)
    assert r.text() == "CAT" and r.current == 3 and changes[-1] == "CAT"
    key(r, Qt.Key.Key_Backspace)
    assert r.text() == "CA" and r.current == 2
    key(r, Qt.Key.Key_Question, "?")
    assert r.text() == "CA?"
    r.setText("abcdefghijklmno")
    assert r.count() == 15 and r.text() == "ABCDEFGHIJKLMNO"
    assert r.flag_of(7) == "warning" and r.flag_of(6) == ""
    r.setText("ab")
    assert r.count() == 7 and r.slots()[:3] == ["A", "B", ""]


def test_rack_field_marks_guesses_until_they_are_typed_over(qapp):
    r = RackField(5)
    r.set_slots(list("ABCDE"), guess=[1, 3])
    assert r._guess == {1, 3}
    r.set_slot(1, "Z")
    assert r._guess == {3}
    r.set_slots(list("ABCDE"), guess=True)
    assert len(r._guess) == 5
    with pytest.raises(ValueError):
        r.set_flags({0: "loud"})


def test_rack_field_wraps_and_asks_for_the_height_it_needs(qapp):
    r = RackField(7)
    r.resize(312, 60)
    one_row = r.minimumSizeHint().height()
    r.setText("ABCDEFGHIJKLMNOPQRSTUVWX")
    r.resize(312, 60)
    assert r.minimumSizeHint().height() > 3 * one_row
    assert r.heightForWidth(312) == r.minimumSizeHint().height()
    r.resize(312, r.minimumSizeHint().height())
    last = r.slot_rect(23)
    assert last.bottom() <= r.height() and last.right() <= r.width()
    assert r.slot_at(last.center()) == 23


def frame_of(w=400, h=300):
    img = np.zeros((h, w, 3), np.uint8)
    img[:] = (90, 120, 150)
    return img


def test_rect_picker_draws_moves_and_resizes_in_frame_pixels(qapp):
    p = RectPicker(frame_of(), 13, 13)
    p.resize(420, 320)
    p.show()
    changes = []
    p.changed.connect(lambda: changes.append(1))
    a, b = p.to_widget(QPointF(100, 80)), p.to_widget(QPointF(300, 240))
    press(p, a.x(), a.y())
    move(p, b.x(), b.y())
    release(p, b.x(), b.y())
    r = p.rect_px
    assert (round(r.x()), round(r.y()), round(r.width()), round(r.height())) == (100, 80, 200, 160)
    assert changes
    inside = p.to_widget(QPointF(200, 160))
    press(p, inside.x(), inside.y())
    to = p.to_widget(QPointF(1000, 1000))
    move(p, to.x(), to.y())
    release(p, to.x(), to.y())
    r = p.rect_px
    assert r.right() <= 400 and r.bottom() <= 300 and round(r.width()) == 200
    corner = p.to_widget(r.bottomRight())
    press(p, corner.x(), corner.y())
    shrunk = p.to_widget(QPointF(r.x() + 50, r.y() + 40))
    move(p, shrunk.x(), shrunk.y())
    release(p, shrunk.x(), shrunk.y())
    assert round(p.rect_px.width()) == 50 and round(p.rect_px.height()) == 40


def test_rect_picker_drops_a_stray_click_and_nudges_with_keys(qapp):
    p = RectPicker(frame_of(), 13, 13)
    p.resize(420, 320)
    c = p.to_widget(QPointF(50, 50))
    press(p, c.x(), c.y())
    release(p, c.x(), c.y())
    assert p.rect_px is None
    p.set_rect((10, 10, 100, 80))
    key(p, Qt.Key.Key_Right)
    key(p, Qt.Key.Key_Down, "", Qt.KeyboardModifier.ControlModifier)
    r = p.rect_px
    assert (round(r.x()), round(r.y())) == (11, 20)
    key(p, Qt.Key.Key_Right, "", Qt.KeyboardModifier.ShiftModifier)
    assert round(p.rect_px.width()) == 101
    assert not p.grab().isNull()


def test_rect_picker_keeps_the_picture_undistorted(qapp):
    p = RectPicker(frame_of(400, 300), 13, 13)
    for size in ((420, 320), (900, 300), (300, 700)):
        p.resize(*size)
        r = p.image_rect()
        assert r.width() / r.height() == pytest.approx(400 / 300, rel=1e-3)
        assert p.rect().contains(r.toRect())


@pytest.fixture(scope="module")
def game():
    from tilelens.synth.game import FakeGame

    return FakeGame(seed=74, opening_turns=10)


def board_rect(game):
    geo = game.geometry
    return (geo.origin[0] + 4, geo.origin[1] - 3, 13 * geo.pitch, 13 * geo.pitch)


def test_manual_dialog_guides_validates_inline_and_confirms(qapp, game):
    dlg = roomy(
        CalibrationDialog(
            game.frame(), game.profile, board=game.board, rack="".join(t for t in game.rack if t)
        )
    )
    assert dlg.steps.states == ["active", "done", "done"]
    assert not dlg.run()
    assert dlg.status.kind == "warning" and "rectangle" in dlg.status.text()
    dlg.picker.set_rect(board_rect(game))
    assert dlg.steps.states == ["done", "done", "done"]
    assert dlg.size_label.text().endswith("px")
    dlg.add_sample()
    assert dlg.status.kind == "success" and dlg.count_label.text() == "1 screen added"
    accepted = []
    dlg.accepted.connect(lambda: accepted.append(1))
    assert dlg.run(), dlg.status.text()
    assert dlg.result is not None and dlg.result.templates
    assert accepted == [1] and dlg.success_shown


def test_manual_dialog_reports_a_failed_calibration_without_closing(qapp, game):
    dlg = roomy(CalibrationDialog(game.frame(), game.profile, board=None, rack="ABC"))
    dlg.picker.set_rect(board_rect(game))
    dlg.show()
    assert not dlg.run()
    assert dlg.status.kind == "error" and "failed" in dlg.status.text()
    assert dlg.isVisible() and dlg.result is None and not dlg.busy
    assert dlg.primary.isEnabled()


def test_manual_dialog_keeps_a_long_rack_and_message_inside_its_columns(qapp, game):
    dlg = roomy(CalibrationDialog(game.frame(), game.profile, board=None, rack=""))
    dlg.show()
    dlg.rack_edit.setText("ABCDEFGHIJKLMNO")
    dlg.status.message("Calibration failed: " + "the rack does not match the screen " * 4, "error")
    QApplication.processEvents()
    assert dlg.rack_edit.height() >= dlg.rack_edit.heightForWidth(dlg.rack_edit.width())
    assert (
        dlg.status.mapTo(dlg, dlg.status.rect().topLeft()).y()
        >= dlg.rack_edit.mapTo(dlg, dlg.rack_edit.rect().bottomLeft()).y()
    )
    label = dlg.status.label
    assert label.height() >= label.heightForWidth(label.width())
    assert not dlg.grab().isNull()


def test_manual_dialog_capture_swaps_the_screen_and_resets_the_inputs(qapp, game):
    other = game.frame().copy()
    dlg = roomy(CalibrationDialog(game.frame(), game.profile, grab_frame=lambda: other, rack="AB"))
    assert dlg.new_btn.isEnabled()
    dlg.picker.set_rect(board_rect(game))
    dlg.new_screen()
    assert dlg.picker.frame is other and dlg.picker.rect_px is not None
    assert dlg.rack_edit.text() == ""
    broken = CalibrationDialog(game.frame(), game.profile, grab_frame=lambda: None)
    broken.new_screen()
    assert broken.status.kind == "error" and "Capture failed" in broken.status.text()
    assert not CalibrationDialog(game.frame(), game.profile).new_btn.isEnabled()


def test_manual_dialog_guess_rectangle_reports_a_miss_as_a_warning(qapp, game):
    blank = np.full_like(game.frame(), 128)
    dlg = roomy(CalibrationDialog(blank, game.profile))
    dlg.auto_rect()
    assert dlg.status.kind == "warning" and "Drag the rectangle" in dlg.status.text()
    ok = roomy(CalibrationDialog(game.frame(), game.profile))
    ok.auto_rect()
    assert ok.picker.rect_px is not None and ok.status.kind == "info"


def test_auto_dialog_checks_inline_and_marks_the_rack_guess(qapp, gallery):
    dlg = roomy(gallery.make_auto_dialog(rack=gallery.RACK))
    assert dlg.found.kind == "success" and "Board found" in dlg.found.text()
    assert dlg.rack_edit.expected == len(dlg.preview.rack_rects) == 7
    assert dlg.rack_edit._guess and dlg.learn_btn.isEnabled()
    assert dlg.steps.states == ["done", "done", "active"]
    assert dlg.preview.board_box() is not None
    assert not dlg.grab().isNull()
    dlg.rack_edit.set_slot(0, "Z")
    assert 0 not in dlg.rack_edit._guess


def test_auto_dialog_asks_for_a_rack_before_it_learns(qapp, gallery):
    dlg = roomy(gallery.make_auto_dialog())
    assert not dlg.run()
    assert dlg.status.kind == "warning" and "rack" in dlg.status.text()
    assert dlg.rack_edit.flag_of(0) == "warning"
    dlg.rack_edit.set_slot(0, "A")
    assert dlg.rack_edit.flag_of(1) == ""
    dlg.rack_edit.setText(gallery.RACK + "Q")
    assert not dlg.run()
    assert dlg.status.kind == "error" and "rack tiles" in dlg.status.text()
    assert dlg.result is None


def test_auto_dialog_without_a_board_cannot_learn(qapp, gallery):
    frame = np.full((614, 928, 3), (60, 52, 50), np.uint8)
    dlg = roomy(gallery.make_auto_dialog(frame))
    assert dlg.found.kind == "error" and "Board not found" in dlg.found.text()
    assert not dlg.learn_btn.isEnabled()
    assert dlg.steps.states[0] == "active"


def test_auto_dialog_learns_and_confirms(qapp, gallery):
    dlg = roomy(gallery.make_auto_dialog(rack=gallery.RACK))
    accepted = []
    dlg.accepted.connect(lambda: accepted.append(1))
    assert dlg.run(), dlg.status.text()
    assert dlg.result is not None and dlg.result.auto and accepted == [1]


@pytest.fixture(scope="module")
def reading(gallery):
    return gallery.check_reading()


def test_corrections_list_one_row_per_unclear_square(qapp, reading):
    dlg = roomy(CorrectionDialog(reading))
    assert [row.key for row in dlg.rows] == reading.uncertain_cells and len(dlg.rows) == 3
    first = dlg.rows[0]
    assert (
        first.field.guess and first.field.text() == reading.cells[first.key[0]][first.key[1]].letter
    )
    assert [k.text() for k in first.keys][-1] == "Empty"
    assert first.keys[0].active and not first.keys[1].active
    assert dlg.primary.isEnabled()
    assert "3 squares to check" in dlg.subtitle_label.text()
    assert dlg.steps.states == ["done", "done", "active"]


def test_corrections_apply_waits_for_every_square_and_the_rack(qapp, reading):
    dlg = roomy(CorrectionDialog(reading))
    row = dlg.rows[0]
    row.field.setText("", user=True)
    row._touched = False
    row._refresh()
    dlg._update()
    assert not dlg.primary.isEnabled() and row.field.flag == "warning"
    assert "1 square still needs a letter" in dlg.subtitle_label.text()
    row.keys[1].pressed_key.emit(row.keys[1].text())
    assert row.field.text() == row.keys[1].text() and row.keys[1].active
    assert dlg.primary.isEnabled()
    dlg.rack_edit.set_slot(2, "")
    assert not dlg.primary.isEnabled() and dlg.rack_edit.flag_of(2) == "warning"
    assert "rack" in dlg.subtitle_label.text()
    dlg.rack_edit.set_slot(2, "e")
    assert dlg.primary.isEnabled()


def test_corrections_empty_is_an_answer_and_invalid_text_is_not(qapp, reading):
    dlg = roomy(CorrectionDialog(reading))
    row = dlg.rows[1]
    row.keys[-1].pressed_key.emit("Empty")
    assert row.field.text() == "" and row.answered and row.keys[-1].active
    row.field.setText("12", user=True)
    assert not dlg.primary.isEnabled()
    assert dlg.status.kind == "error" and "'12' is not a single letter" in dlg.status.text()
    dlg._accept()
    assert dlg.result() != dlg.DialogCode.Accepted
    row.field.setText("T", user=True)
    assert dlg.status.empty and dlg.primary.isEnabled()


def test_corrections_collect_keeps_the_old_contract(qapp, reading):
    dlg = roomy(CorrectionDialog(reading))
    r, c = dlg.rows[0].key
    assert dlg.collect() is None
    assert set(dlg.fixes) == set(reading.uncertain_cells)
    assert dlg.fixes[(r, c)].letter == reading.cells[r][c].letter
    assert dlg.rack == "".join(t.letter for t in reading.rack)
    other = next(
        (rr, cc)
        for rr in range(len(reading.cells))
        for cc in range(len(reading.cells[0]))
        if reading.cells[rr][cc].letter and not reading.cells[rr][cc].uncertain
    )
    assert other not in [row.key for row in dlg.rows]
    dlg.edits[other].setText("")
    assert dlg.collect() is None and dlg.fixes[other] is None
    dlg.edits[(0, 0)].setText("12")
    assert "row 1, col 1" in dlg.collect()
    with pytest.raises(KeyError):
        dlg.edits[(999, 0)]


def test_corrections_accept_on_apply_and_candidates_skip_non_letters(qapp, reading):
    dlg = roomy(CorrectionDialog(reading))
    dlg.show()
    QTest.keyClick(dlg, Qt.Key.Key_Return)
    assert dlg.result() == dlg.DialogCode.Accepted and dlg.fixes
    assert all(k.text().isalpha() for row in dlg.rows for k in row.keys[:-1])


def test_corrections_advance_with_typing_and_arrows(qapp, reading):
    dlg = roomy(CorrectionDialog(reading))
    dlg.show()
    first, second = dlg.rows[0].field, dlg.rows[1].field
    first.setFocus()
    key(first, Qt.Key.Key_E, "e")
    assert second.hasFocus() or dlg.focusWidget() is second
    key(second, Qt.Key.Key_Up)
    assert first.hasFocus() or dlg.focusWidget() is first


def test_corrections_with_nothing_unclear_still_apply(qapp, reading):
    for row in reading.cells:
        for cell in row:
            cell.uncertain = False
    for t in reading.rack:
        t.uncertain = False
    dlg = roomy(CorrectionDialog(reading))
    assert not dlg.rows and dlg.primary.isEnabled()
    assert not dlg.none_label.isHidden()
    assert dlg.collect() is None and dlg.fixes == {}


def test_every_dialog_paints_in_both_materials_at_a_phone_of_a_screen(qapp, gallery, game):
    made = [
        CalibrationDialog(game.frame(), game.profile, board=game.board, rack="ABCDEFG"),
        gallery.make_auto_dialog(rack=gallery.RACK),
        CorrectionDialog(gallery.check_reading()),
    ]
    for dlg in made:
        for m in (DARK, LIGHT):
            dlg.set_material(m)
            dlg.show()
            img = dlg.grab().toImage()
            assert img.width() > 300 and img.height() > 300
            assert dlg.minimumSizeHint().width() <= 960 + 72
        dlg.close()


def test_region_selector_emits_the_dragged_region_in_screen_points(qapp):
    sel = RegionSelector()
    got, gone = [], []
    sel.selected.connect(got.append)
    sel.cancelled.connect(lambda: gone.append(1))
    sel.show()
    press(sel, 100, 120)
    move(sel, 400, 500)
    assert sel.selection().size().width() == 300
    release(sel, 400, 500)
    origin = sel.mapToGlobal(sel.rect().topLeft())
    assert got and not gone and not sel.isVisible()
    assert (got[0].x, got[0].y, got[0].width, got[0].height) == (
        origin.x() + 100,
        origin.y() + 120,
        300,
        380,
    )


def test_region_selector_cancels_on_escape_and_on_a_tiny_drag(qapp):
    sel = RegionSelector()
    gone = []
    sel.cancelled.connect(lambda: gone.append(1))
    sel.show()
    QTest.keyClick(sel, Qt.Key.Key_Escape)
    assert gone == [1] and not sel.isVisible()
    tiny = RegionSelector()
    got = []
    tiny.selected.connect(got.append)
    tiny.cancelled.connect(lambda: gone.append(2))
    tiny.show()
    press(tiny, 10, 10)
    move(tiny, 30, 30)
    release(tiny, 30, 30)
    assert gone == [1, 2] and not got


def test_region_selector_ends_on_every_display_when_one_finishes(qapp):
    root = RegionSelector()
    peer = RegionSelector(_root=root)
    root._peers.append(peer)
    root.show()
    peer.show()
    got, gone = [], []
    root.selected.connect(got.append)
    root.cancelled.connect(lambda: gone.append(1))
    peer.selected.connect(lambda r: gone.append("peer"))
    QTest.keyClick(peer, Qt.Key.Key_Escape)
    assert gone == [1] and not root.isVisible() and not peer.isVisible()
    root.show()
    peer.show()
    press(peer, 20, 20)
    move(peer, 220, 180)
    release(peer, 220, 180)
    assert len(got) == 1 and got[0].width == 200 and not peer.isVisible()


def test_region_selector_paints_every_state(qapp):
    sel = RegionSelector()
    sel.resize(900, 600)
    sel.show()
    assert not sel.grab().isNull()
    for end in ((500, 400), (130, 130), (890, 598)):
        press(sel, 100, 100)
        move(sel, *end)
        assert not sel.grab().isNull()
        sel._start = sel._end = None
    sel.close()


def test_no_widget_in_the_dialogs_carries_its_own_stylesheet(qapp, gallery, game):
    made = [
        CalibrationDialog(game.frame(), game.profile),
        gallery.make_auto_dialog(),
        CorrectionDialog(gallery.check_reading()),
    ]
    for dlg in made:
        own = [
            w
            for w in dlg.findChildren(QWidget)
            if w.styleSheet() and not isinstance(w, GlassButton)
        ]
        assert not own

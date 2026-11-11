"""Calibration and correction dialogs."""

from __future__ import annotations

import functools

import cv2
import numpy as np
from PySide6.QtCore import QRect

from ui_gallery import ctx
from ui_gallery.registry import register

LONG_RACK = "ABCDEFGHIJKLMNO"
ROOM = QRect(0, 0, 1440, 900)


def dialog(name: str):
    """register(), with the sheet sized for a 1440 x 900 display: the offscreen screen is a few
    hundred points, and the sheet would shrink to fit it."""

    def wrap(fn):
        @functools.wraps(fn)
        def build():
            dlg = fn()
            dlg.available_geometry = lambda: ROOM
            return dlg

        return register(name, kind="dialog")(build)

    return wrap


@dialog("dialog/calibrate-manual")
def calibrate_manual():
    """Typed calibration, nothing drawn yet"""
    return ctx.make_calibration_dialog()[0]


@dialog("dialog/calibrate-manual-rect")
def calibrate_manual_rect():
    """The board rectangle drawn, with its grid lines and handles"""
    return ctx.make_calibration_dialog(rect=True)[0]


@dialog("dialog/calibrate-manual-guess")
def calibrate_manual_guess():
    """After Guess rectangle"""
    dlg = ctx.make_calibration_dialog()[0]
    dlg.auto_rect()
    return dlg


@dialog("dialog/calibrate-manual-added")
def calibrate_manual_added():
    """After Add this screen"""
    dlg = ctx.make_calibration_dialog(rect=True)[0]
    dlg.add_sample()
    return dlg


@dialog("dialog/calibrate-manual-no-rect")
def calibrate_manual_no_rect():
    """Calibrate pressed with no rectangle"""
    dlg = ctx.make_calibration_dialog()[0]
    dlg.run()
    return dlg


@dialog("dialog/calibrate-manual-failed")
def calibrate_manual_failed():
    """Calibration failed because the typed rack does not match"""
    dlg = ctx.make_calibration_dialog(rect=True, rack=False)[0]
    dlg.rack_edit.setText("ABC")
    dlg.run()
    return dlg


@dialog("dialog/calibrate-manual-busy")
def calibrate_manual_busy():
    """Calibrating: spinner in the footer, the form held still"""
    dlg = ctx.make_calibration_dialog(rect=True)[0]
    dlg.set_busy("Calibrating")
    return dlg


@dialog("dialog/calibrate-manual-success")
def calibrate_manual_success():
    """Calibrated: the confirmation before the sheet closes"""
    dlg = ctx.make_calibration_dialog(rect=True)[0]
    dlg.show_success("Calibrated", then_accept=False)
    return dlg


@dialog("dialog/calibrate-manual-long")
def calibrate_manual_long():
    """A rack of fifteen letters and a long failure message"""
    dlg = ctx.make_calibration_dialog(rect=True, rack=False)[0]
    dlg.rack_edit.setText(LONG_RACK)
    dlg.status.message(
        "Calibration failed: the typed rack has 15 tiles but the screen shows 7, and row 3 of "
        "the board has 14 squares where the profile has 13",
        "error",
    )
    return dlg


@dialog("dialog/calibrate-auto")
def calibrate_auto():
    """Board and rack found on the real game"""
    return ctx.make_auto_dialog(rack=ctx.RACK)


@dialog("dialog/calibrate-auto-no-rack")
def calibrate_auto_no_rack():
    """Learn pressed with no rack typed"""
    dlg = ctx.make_auto_dialog()
    dlg.run()
    return dlg


@dialog("dialog/calibrate-auto-not-found")
def calibrate_auto_not_found():
    """A screen with no board on it"""
    frame = np.full((614, 928, 3), (60, 52, 50), np.uint8)
    cv2.rectangle(frame, (80, 60), (520, 400), (86, 76, 72), -1)
    return ctx.make_auto_dialog(frame)


@dialog("dialog/calibrate-auto-mismatch")
def calibrate_auto_mismatch():
    """The typed rack has more letters than the screen has tiles"""
    dlg = ctx.make_auto_dialog(rack=ctx.RACK + "Q")
    dlg.run()
    return dlg


@dialog("dialog/calibrate-auto-busy")
def calibrate_auto_busy():
    """Learning the tiles: spinner in the footer"""
    dlg = ctx.make_auto_dialog(rack=ctx.RACK)
    dlg.set_busy("Learning the tiles")
    return dlg


@dialog("dialog/calibrate-auto-success")
def calibrate_auto_success():
    """Tiles learned: the confirmation before the sheet closes"""
    dlg = ctx.make_auto_dialog(rack=ctx.RACK)
    dlg.show_success("Tiles learned", then_accept=False)
    return dlg


@dialog("dialog/calibrate-auto-long")
def calibrate_auto_long():
    """A rack of fifteen letters typed over the guess"""
    dlg = ctx.make_auto_dialog(rack=ctx.RACK)
    dlg.rack_edit.setText(LONG_RACK)
    return dlg


@dialog("dialog/corrections")
def corrections():
    """Check tiles: unclear squares with their likely letters, rack below"""
    return ctx.make_corrections_dialog()


def _first_row(dlg):
    return dlg.rows[0]


@dialog("dialog/corrections-error")
def corrections_error():
    """A square that is not a single letter"""
    dlg = ctx.make_corrections_dialog()
    _first_row(dlg).field.setText("12")
    dlg._accept()
    return dlg


@dialog("dialog/corrections-complete")
def corrections_complete():
    """A candidate picked on the first square: its keycap is lit, Apply is ready"""
    dlg = ctx.make_corrections_dialog()
    row = _first_row(dlg)
    row._pressed(row.keys[1].text())
    return dlg


@dialog("dialog/corrections-unanswered")
def corrections_unanswered():
    """A square with no guess and a rack tile left empty: Apply waits"""
    dlg = ctx.make_corrections_dialog()
    row = _first_row(dlg)
    row.field.setText("", user=True)
    row._touched = False
    row._refresh()
    dlg.rack_edit.set_slot(2, "")
    return dlg


@dialog("dialog/corrections-many")
def corrections_many():
    """Twenty unclear squares: the list scrolls, the rack stays in view"""
    from tilelens.ui.corrections import CorrectionDialog

    reading = ctx.check_reading()
    marked = 0
    for row in reading.cells:
        for cell in row:
            if cell.letter and marked < 20:
                cell.uncertain = True
                cell.reason = (
                    "letter shape unclear" if marked % 3 else "a premium square may be hiding it"
                )
                cell.candidates = tuple(c for c in "AEOIRSTLN"[marked % 5 : marked % 5 + 3])
                marked += 1
    return CorrectionDialog(reading)


@dialog("dialog/corrections-busy")
def corrections_busy():
    """Applying the corrections: spinner in the footer"""
    dlg = ctx.make_corrections_dialog()
    dlg.set_busy("Applying")
    return dlg

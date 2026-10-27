"""Calibration and correction dialogs."""

from __future__ import annotations

import cv2
import numpy as np

from ui_gallery import ctx
from ui_gallery.registry import register


@register("dialog/calibrate-manual", kind="dialog")
def calibrate_manual():
    """Typed calibration, nothing drawn yet"""
    return ctx.make_calibration_dialog()[0]


@register("dialog/calibrate-manual-rect", kind="dialog")
def calibrate_manual_rect():
    """The board rectangle drawn, with its grid lines"""
    return ctx.make_calibration_dialog(rect=True)[0]


@register("dialog/calibrate-manual-guess", kind="dialog")
def calibrate_manual_guess():
    """After Guess rectangle"""
    dlg = ctx.make_calibration_dialog()[0]
    dlg.auto_rect()
    return dlg


@register("dialog/calibrate-manual-added", kind="dialog")
def calibrate_manual_added():
    """After Add this screen"""
    dlg = ctx.make_calibration_dialog(rect=True)[0]
    dlg.add_sample()
    return dlg


@register("dialog/calibrate-manual-no-rect", kind="dialog")
def calibrate_manual_no_rect():
    """Calibrate pressed with no rectangle"""
    dlg = ctx.make_calibration_dialog()[0]
    dlg.run()
    return dlg


@register("dialog/calibrate-manual-failed", kind="dialog")
def calibrate_manual_failed():
    """Calibration failed because the typed rack does not match"""
    dlg = ctx.make_calibration_dialog(rect=True, rack=False)[0]
    dlg.rack_edit.setText("ABC")
    dlg.run()
    return dlg


@register("dialog/calibrate-auto", kind="dialog")
def calibrate_auto():
    """Board and rack found on the real game"""
    return ctx.make_auto_dialog(rack=ctx.RACK)


@register("dialog/calibrate-auto-no-rack", kind="dialog")
def calibrate_auto_no_rack():
    """Learn pressed with no rack typed"""
    dlg = ctx.make_auto_dialog()
    dlg.run()
    return dlg


@register("dialog/calibrate-auto-not-found", kind="dialog")
def calibrate_auto_not_found():
    """A screen with no board on it"""
    frame = np.full((614, 928, 3), (60, 52, 50), np.uint8)
    cv2.rectangle(frame, (80, 60), (520, 400), (86, 76, 72), -1)
    return ctx.make_auto_dialog(frame)


@register("dialog/corrections", kind="dialog")
def corrections():
    """Check tiles: unclear squares highlighted, rack below"""
    return ctx.make_corrections_dialog()


@register("dialog/corrections-error", kind="dialog")
def corrections_error():
    """A square that is not a single letter"""
    dlg = ctx.make_corrections_dialog()
    dlg.edits[(0, 0)].setText("12")
    dlg._accept()
    return dlg

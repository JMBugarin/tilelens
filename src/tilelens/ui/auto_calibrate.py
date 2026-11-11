from __future__ import annotations

import copy

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from tilelens.rules.profile import Profile
from tilelens.ui.calibrate_dialog import to_qimage
from tilelens.ui.design import GlassButton, Label, SectionLabel, Space, material_of, type_font
from tilelens.ui.design.material import paint_wash
from tilelens.ui.glass_dialog import (
    FrameView,
    GlassDialog,
    Notice,
    RackField,
    paint_chip,
    paint_frame_line,
)
from tilelens.vision.autoread import add_screen, calibrate_auto, rack_tiles
from tilelens.vision.chart import chart_tiles, learn_chart, looks_like_chart
from tilelens.vision.locate import BoardNotFound, BoardView, locate_board
from tilelens.vision.recognizer import Calibration, CalibrationError

STEPS = ("Check the screen", "Confirm the rack", "Learn")
RACK_TIP = (
    "Have a screen that lists every tile, A to Z? Open it in the game, press Capture again "
    "and Learn: every letter is learned at once."
)


class FramePreview(FrameView):
    """The captured screen with the board outlined once and the rack tiles numbered."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.view: BoardView | None = None
        self.rack_rects: list[tuple[int, int, int, int]] = []
        self.numbered = True
        self.placeholder = "No screen captured"

    def set_frame(self, frame, view=None, rack_rects=()) -> None:
        self.view = view
        self.rack_rects = sorted(rack_rects, key=lambda r: r[0])
        super().set_frame(frame)

    def board_box(self) -> tuple[float, float, float, float] | None:
        """The squares in view as one rectangle in frame pixels."""
        if self.view is None:
            return None
        rows, cols = np.nonzero(self.view.visible)
        if not len(rows):
            return None
        g = self.view.grid
        x0, y0, _, _ = g.cell_rect(int(rows.min()), int(cols.min()))
        x1, y1, w, h = g.cell_rect(int(rows.max()), int(cols.max()))
        return x0, y0, x1 + w - x0, y1 + h - y0

    def paint_overlay(self, p: QPainter, image: QRectF) -> None:
        m = material_of(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        box = self.board_box()
        if box is not None:
            wr = self.rect_to_widget(*box)
            paint_frame_line(p, wr, m.accent, 6.0)
            above = wr.top() - image.top() > 28
            paint_chip(
                p, QPointF(wr.left(), wr.top() if above else wr.top() + 28), "Board", m, above
            )
        if not self.numbered:
            return
        font = type_font("caption", tabular=True)
        for i, rect in enumerate(self.rack_rects):
            wr = self.rect_to_widget(*rect)
            badge = QRectF(wr.center().x() - 9, wr.bottom() + 4, 18, 18)
            if badge.bottom() > image.bottom() - 2:
                badge.moveBottom(wr.bottom() - 4)
            paint_wash(p, badge, 9, m.accent)
            p.setFont(font)
            p.setPen(m.on_accent)
            p.drawText(badge, int(Qt.AlignmentFlag.AlignCenter.value), str(i + 1))


class AutoCalibrationDialog(GlassDialog):
    def __init__(
        self,
        frame: np.ndarray,
        profile: Profile,
        grab_frame=None,
        rack: str = "",
        previous: Calibration | None = None,
        parent=None,
    ):
        super().__init__(
            "Calibrate",
            "TileLens found the board. Confirm the rack letters so it can learn the tiles.",
            parent,
        )
        self.profile = profile
        self.grab_frame = grab_frame
        self.previous = previous if previous is not None and previous.auto else None
        self.frame = frame
        self.chart = False
        self.result: Calibration | None = None
        self.learned: Calibration | None = None
        self._ready = False
        self._guess = rack
        self.set_steps(STEPS)

        columns = QHBoxLayout()
        columns.setSpacing(Space.L)
        left = QVBoxLayout()
        left.setSpacing(Space.M)
        self.preview = FramePreview()
        left.addWidget(self.preview, 1)
        tools = QHBoxLayout()
        self.new_btn = GlassButton("Capture again", icon="refresh", size="s")
        self.new_btn.setToolTip("Grab the game again")
        self.new_btn.clicked.connect(self.new_screen)
        self.new_btn.setEnabled(grab_frame is not None)
        tools.addWidget(self.new_btn)
        tools.addStretch(1)
        left.addLayout(tools)
        columns.addLayout(left, 1)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(Space.S)
        self.found = Notice()
        right.addWidget(self.found)
        right.addSpacing(Space.S)
        right.addWidget(SectionLabel("Rack"))
        self.rack_hint = Label(
            "Left to right, matching the numbers on the picture. A question mark is a blank.",
            "footnote",
            "secondary",
            wrap=True,
        )
        right.addWidget(self.rack_hint)
        self.rack_edit = RackField(profile.rack_size, label="Rack, left to right")
        if rack:
            self.rack_edit.set_slots(list(rack), guess=True)
        right.addWidget(self.rack_edit)
        self.guess_hint = Label(
            "Highlighted letters are guesses. Type over any that are wrong.",
            "footnote",
            "secondary",
            wrap=True,
        )
        self.guess_hint.setVisible(bool(rack))
        right.addWidget(self.guess_hint)
        right.addSpacing(Space.S)
        right.addWidget(Label(RACK_TIP, "footnote", "secondary", wrap=True))
        right.addStretch(1)
        self.status = Notice()
        right.addWidget(self.status)
        right_host = QWidget()
        right_host.setLayout(right)
        right_host.setFixedWidth(320)
        columns.addWidget(right_host)
        self.body.addLayout(columns, 1)

        self.learn_btn, cancel = self.add_footer("Learn", "Cancel")
        self.learn_btn.clicked.connect(self.run)
        self.focus_chain(self.new_btn, self.rack_edit, cancel, self.learn_btn)
        self.rack_edit.text_changed.connect(self._on_rack)
        self.show_frame(frame)
        self.set_glass_size(920, 612)
        self.set_glass_minimum(760, 520)
        self.rack_edit.setFocus()

    def _on_rack(self, *_) -> None:
        self.rack_edit.set_flags({})
        self._sync()

    def _sync(self) -> None:
        done = [self._ready, self.chart or bool(self.rack_edit.text())]
        active = next((i for i, d in enumerate(done) if not d), len(done))
        self.steps.set_states(
            [
                "done" if i < len(done) and done[i] else "active" if i == active else "todo"
                for i in range(len(STEPS))
            ]
        )
        self.learn_btn.setEnabled(self._ready)

    def _analyse(self, frame: np.ndarray):
        """The slow part of looking at a screen; it touches no Qt object."""
        if looks_like_chart(frame, self.profile):
            return "chart", chart_tiles(frame, self.profile), None
        try:
            view = locate_board(frame, self.profile)
        except BoardNotFound as exc:
            return "missing", str(exc), None
        return "board", view, rack_tiles(frame, self.profile, view)

    def show_frame(self, frame: np.ndarray) -> None:
        self.frame = frame
        kind, found, blobs = self.run_blocking(
            lambda: self._analyse(frame), "Looking for the board"
        )
        self.chart = kind == "chart"
        self._ready = kind != "missing"
        self.preview.numbered = kind == "board"
        if kind == "chart":
            self.preview.set_frame(frame, None, [t.rect for t in found])
            self.found.message(
                f"Tile chart found: {len(found)} tiles. Press Learn to learn every letter.",
                "success",
            )
        elif kind == "missing":
            self.preview.set_frame(frame, None, [])
            self.found.message(
                f"Board not found: {found}. Open the game's board and capture again.", "error"
            )
        else:
            self.preview.set_frame(frame, found, [b.rect for b in blobs])
            self.found.message(
                f"Board found: {found.visible_count} of "
                f"{self.profile.width * self.profile.height} squares in view. "
                f"Rack: {len(blobs)} tiles.",
                "success",
            )
            if blobs:
                self.rack_edit.set_expected(len(blobs))
        self.rack_edit.setEnabled(kind == "board")
        self.rack_hint.setVisible(kind == "board")
        self.guess_hint.setVisible(kind != "chart" and bool(self._guess))
        self._sync()

    def new_screen(self) -> None:
        frame = self.grab_frame() if self.grab_frame else None
        if frame is None:
            self.status.message("Capture failed.", "error")
            return
        self.rack_edit.clear()
        self._guess = ""
        self.status.clear()
        self.show_frame(frame)

    def _fail(self, text: str, kind: str = "error") -> bool:
        self.status.message(text, kind)
        return False

    def run(self) -> bool:
        if self.chart:
            if self.previous is None:
                return self._fail(
                    "Calibrate on the board first (type your rack), then learn from the chart.",
                    "warning",
                )
            cal = copy.deepcopy(self.previous)
            try:
                self.run_blocking(
                    lambda: learn_chart(cal, self.frame, self.profile), "Learning every letter"
                )
            except CalibrationError as exc:
                return self._fail(f"Could not read the chart: {exc}")
            self.result = cal
            self.show_success("Every letter learned")
            return True
        rack = self.rack_edit.text().strip()
        if not rack:
            slots = self.rack_edit.slots()
            self.rack_edit.set_flags({i: "warning" for i in range(len(slots))})
            self.rack_edit.focus_slot(0)
            return self._fail("Type the rack first.", "warning")
        try:
            if self.previous is not None:
                cal = copy.deepcopy(self.previous)
                self.run_blocking(
                    lambda: add_screen(cal, self.frame, self.profile, rack), "Learning the tiles"
                )
            else:
                cal = self.run_blocking(
                    lambda: calibrate_auto(self.frame, self.profile, rack), "Learning the tiles"
                )
        except CalibrationError as exc:
            return self._fail(f"Calibration failed: {exc}")
        self.result = cal
        self.show_success("Tiles learned")
        return True


__all__ = ["AutoCalibrationDialog", "FramePreview", "to_qimage"]

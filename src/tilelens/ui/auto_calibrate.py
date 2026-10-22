from __future__ import annotations

import copy

import numpy as np
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from tilelens.rules.profile import Profile
from tilelens.ui.calibrate_dialog import to_qimage
from tilelens.vision.autoread import add_screen, calibrate_auto, rack_tiles
from tilelens.vision.chart import chart_tiles, learn_chart, looks_like_chart
from tilelens.vision.locate import BoardNotFound, BoardView, locate_board
from tilelens.vision.recognizer import Calibration, CalibrationError

STEPS = (
    "TileLens finds the board from its colors, so it does not need to be centered or whole.\n"
    "Type the letters in your rack from left to right ('?' for a blank) and press Learn.\n"
    "Then, if the game has a screen listing every tile (one per letter, A to Z), open it, "
    "press Capture again and Learn: every letter is learned at once."
)


class FramePreview(QWidget):
    """The captured screen with the board squares and rack tiles that were found."""

    def __init__(self):
        super().__init__()
        self.pixmap: QPixmap | None = None
        self.view: BoardView | None = None
        self.rack_rects: list[tuple[int, int, int, int]] = []
        self.setMinimumSize(420, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_frame(self, frame, view, rack_rects) -> None:
        self.pixmap = QPixmap.fromImage(to_qimage(frame))
        self.view = view
        self.rack_rects = rack_rects
        self.update()

    def paintEvent(self, event) -> None:
        if self.pixmap is None:
            return
        p = QPainter(self)
        fw, fh = self.pixmap.width(), self.pixmap.height()
        s = min(self.width() / fw, self.height() / fh)
        ox, oy = (self.width() - fw * s) / 2, (self.height() - fh * s) / 2
        p.drawPixmap(QRectF(ox, oy, fw * s, fh * s), self.pixmap, QRectF(self.pixmap.rect()))
        if self.view is not None:
            p.setPen(QPen(QColor(0, 220, 255, 170), 1))
            g = self.view.grid
            for r, c in zip(*np.nonzero(self.view.visible), strict=True):
                x, y, w, h = g.cell_rect(int(r), int(c))
                p.drawRect(QRectF(ox + x * s, oy + y * s, w * s, h * s))
        p.setPen(QPen(QColor(40, 220, 120), 2))
        for x, y, w, h in self.rack_rects:
            p.drawRect(QRectF(ox + x * s, oy + y * s, w * s, h * s))
        p.end()


class AutoCalibrationDialog(QDialog):
    def __init__(
        self,
        frame: np.ndarray,
        profile: Profile,
        grab_frame=None,
        rack: str = "",
        previous: Calibration | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Calibrate")
        self.profile = profile
        self.grab_frame = grab_frame
        self.previous = previous if previous is not None and previous.auto else None
        self.frame = frame
        self.result: Calibration | None = None
        self.learned: Calibration | None = None
        lay = QHBoxLayout(self)
        left = QVBoxLayout()
        self.preview = FramePreview()
        left.addWidget(self.preview, 1)
        self.new_btn = QPushButton("Capture again")
        self.new_btn.clicked.connect(self.new_screen)
        self.new_btn.setEnabled(grab_frame is not None)
        left.addWidget(self.new_btn)
        lay.addLayout(left, 3)

        right = QVBoxLayout()
        steps = QLabel(STEPS)
        steps.setWordWrap(True)
        right.addWidget(steps)
        self.found = QLabel()
        self.found.setWordWrap(True)
        right.addWidget(self.found)
        self.rack_edit = QLineEdit(rack)
        self.rack_edit.setPlaceholderText("Rack, left to right")
        right.addWidget(self.rack_edit)
        self.status = QLabel()
        self.status.setWordWrap(True)
        right.addWidget(self.status)
        right.addStretch(1)
        buttons = QHBoxLayout()
        self.learn_btn = QPushButton("Learn")
        self.learn_btn.setDefault(True)
        self.learn_btn.clicked.connect(self.run)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(self.learn_btn)
        buttons.addWidget(cancel)
        right.addLayout(buttons)
        lay.addLayout(right, 2)
        self.show_frame(frame)
        self.resize(1000, 620)

    def show_frame(self, frame: np.ndarray) -> None:
        self.frame = frame
        self.chart = looks_like_chart(frame, self.profile)
        if self.chart:
            tiles = chart_tiles(frame, self.profile)
            self.preview.set_frame(frame, None, [t.rect for t in tiles])
            self.found.setText(
                f"Tile chart found: {len(tiles)} tiles. Press Learn to learn every letter."
            )
            self.rack_edit.setEnabled(False)
            return
        self.rack_edit.setEnabled(True)
        try:
            view = locate_board(frame, self.profile)
        except BoardNotFound as exc:
            self.preview.set_frame(frame, None, [])
            self.found.setText(f"Board not found: {exc}.")
            return
        blobs = rack_tiles(frame, self.profile, view)
        self.preview.set_frame(frame, view, [b.rect for b in blobs])
        self.found.setText(
            f"Board found: {view.visible_count} of {self.profile.width * self.profile.height} "
            f"squares in view. Rack: {len(blobs)} tiles."
        )

    def new_screen(self) -> None:
        frame = self.grab_frame() if self.grab_frame else None
        if frame is None:
            self.status.setText("Capture failed.")
            return
        self.rack_edit.clear()
        self.show_frame(frame)

    def run(self) -> bool:
        if self.chart:
            if self.previous is None:
                self.status.setText(
                    "Calibrate on the board first (type your rack), then learn from the chart."
                )
                return False
            cal = copy.deepcopy(self.previous)
            try:
                learn_chart(cal, self.frame, self.profile)
            except CalibrationError as exc:
                self.status.setText(f"Could not read the chart: {exc}")
                return False
            self.result = cal
            self.accept()
            return True
        rack = self.rack_edit.text().strip()
        if not rack:
            self.status.setText("Type the rack first.")
            return False
        try:
            if self.previous is not None:
                cal = copy.deepcopy(self.previous)
                add_screen(cal, self.frame, self.profile, rack)
            else:
                cal = calibrate_auto(self.frame, self.profile, rack)
        except CalibrationError as exc:
            self.status.setText(f"Calibration failed: {exc}")
            return False
        self.result = cal
        self.accept()
        return True

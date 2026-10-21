from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from tilelens.rules.board import Board
from tilelens.rules.profile import Profile
from tilelens.vision.grid import GridNotFound, detect_grid
from tilelens.vision.recognizer import Calibration, CalibrationError, calibrate

STEPS = (
    "1. Drag a rectangle around the board grid (the squares only).\n"
    "2. Type the board: one line per row, '.' for empty, uppercase for a tile, lowercase for "
    "a blank.\n"
    "3. Type the rack from left to right, '?' for a blank.\n"
    "Add more screens for letters this one does not show, then press Calibrate."
)


def to_qimage(frame: np.ndarray) -> QImage:
    rgb = np.ascontiguousarray(frame[:, :, ::-1])
    h, w = rgb.shape[:2]
    return QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()


class RectPicker(QWidget):
    """Shows a frame scaled to fit and lets the user drag a rectangle in frame pixels."""

    changed = Signal()

    def __init__(self, frame: np.ndarray, rows: int, cols: int):
        super().__init__()
        self.frame = frame
        self.rows, self.cols = rows, cols
        self.pixmap = QPixmap.fromImage(to_qimage(frame))
        self.rect_px: QRectF | None = None
        self._start: QPointF | None = None
        self.setMinimumSize(420, 320)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def _scale(self) -> tuple[float, float, float]:
        fw, fh = self.pixmap.width(), self.pixmap.height()
        s = min(self.width() / fw, self.height() / fh)
        return s, (self.width() - fw * s) / 2, (self.height() - fh * s) / 2

    def _to_px(self, p: QPointF) -> QPointF:
        s, ox, oy = self._scale()
        return QPointF((p.x() - ox) / s, (p.y() - oy) / s)

    def set_rect(self, rect: tuple[float, float, float, float] | None) -> None:
        self.rect_px = QRectF(*rect) if rect else None
        self.update()
        self.changed.emit()

    def mousePressEvent(self, e) -> None:
        self._start = self._to_px(e.position())

    def mouseMoveEvent(self, e) -> None:
        if self._start is not None:
            self.rect_px = QRectF(self._start, self._to_px(e.position())).normalized()
            self.update()

    def mouseReleaseEvent(self, e) -> None:
        if self._start is not None:
            self.rect_px = QRectF(self._start, self._to_px(e.position())).normalized()
            self._start = None
            self.update()
            self.changed.emit()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        s, ox, oy = self._scale()
        p.drawPixmap(
            QRectF(ox, oy, self.pixmap.width() * s, self.pixmap.height() * s),
            self.pixmap,
            QRectF(self.pixmap.rect()),
        )
        if self.rect_px is not None:
            r = QRectF(
                ox + self.rect_px.x() * s,
                oy + self.rect_px.y() * s,
                self.rect_px.width() * s,
                self.rect_px.height() * s,
            )
            p.setPen(QPen(QColor(255, 60, 60), 2))
            p.drawRect(r)
            p.setPen(QPen(QColor(255, 60, 60, 110), 1))
            for i in range(1, self.cols):
                x = r.x() + r.width() * i / self.cols
                p.drawLine(QPointF(x, r.y()), QPointF(x, r.bottom()))
            for i in range(1, self.rows):
                y = r.y() + r.height() * i / self.rows
                p.drawLine(QPointF(r.x(), y), QPointF(r.right(), y))
        p.end()


class CalibrationDialog(QDialog):
    def __init__(
        self,
        frame: np.ndarray,
        profile: Profile,
        grab_frame=None,
        board: Board | None = None,
        rack: str = "",
        previous: Calibration | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Calibrate")
        self.profile = profile
        self.grab_frame = grab_frame
        self.previous = previous
        self.samples: list[tuple[np.ndarray, list[str], str]] = []
        self._current_added = False
        self.result: Calibration | None = None
        lay = QHBoxLayout(self)
        left = QVBoxLayout()
        self.picker = RectPicker(frame, profile.height, profile.width)
        left.addWidget(self.picker, 1)
        row = QHBoxLayout()
        self.auto_btn = QPushButton("Guess rectangle")
        self.auto_btn.clicked.connect(self.auto_rect)
        self.new_btn = QPushButton("Capture new screen")
        self.new_btn.clicked.connect(self.new_screen)
        self.new_btn.setEnabled(grab_frame is not None)
        row.addWidget(self.auto_btn)
        row.addWidget(self.new_btn)
        left.addLayout(row)
        lay.addLayout(left, 3)

        right = QVBoxLayout()
        steps = QLabel(STEPS)
        steps.setWordWrap(True)
        right.addWidget(steps)
        self.board_edit = QPlainTextEdit()
        mono = QFont("Menlo")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.board_edit.setFont(mono)
        rows = board.to_rows() if board else ["." * profile.width] * profile.height
        self.board_edit.setPlainText("\n".join(rows))
        right.addWidget(self.board_edit, 1)
        self.rack_edit = QLineEdit(rack)
        self.rack_edit.setPlaceholderText("Rack, left to right")
        right.addWidget(self.rack_edit)
        self.status = QLabel()
        self.status.setWordWrap(True)
        right.addWidget(self.status)
        buttons = QHBoxLayout()
        self.add_btn = QPushButton("Add this screen")
        self.add_btn.clicked.connect(self.add_sample)
        self.cal_btn = QPushButton("Calibrate")
        self.cal_btn.setDefault(True)
        self.cal_btn.clicked.connect(self.run)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(self.add_btn)
        buttons.addWidget(self.cal_btn)
        buttons.addWidget(cancel)
        right.addLayout(buttons)
        lay.addLayout(right, 2)
        if previous is not None:
            self.picker.set_rect(previous.grid.rect)
        self.resize(1000, 640)

    def auto_rect(self) -> None:
        try:
            g = detect_grid(self.picker.frame, self.profile.height, self.profile.width)
        except GridNotFound as exc:
            self.status.setText(f"Could not guess: {exc}. Drag the rectangle instead.")
            return
        self.picker.set_rect(g.rect)
        self.status.setText("Check the red grid lines sit on the board; drag to fix if not.")

    def new_screen(self) -> None:
        frame = self.grab_frame() if self.grab_frame else None
        if frame is None:
            self.status.setText("Capture failed.")
            return
        rect = self.picker.rect_px
        self.picker.frame = frame
        self.picker.pixmap = QPixmap.fromImage(to_qimage(frame))
        self.picker.set_rect((rect.x(), rect.y(), rect.width(), rect.height()) if rect else None)
        self.board_edit.setPlainText("\n".join(["." * self.profile.width] * self.profile.height))
        self.rack_edit.clear()
        self._current_added = False

    def current_sample(self):
        rows = [ln.strip() for ln in self.board_edit.toPlainText().splitlines() if ln.strip()]
        return (self.picker.frame, rows, self.rack_edit.text().strip())

    def add_sample(self) -> None:
        if self._current_added:
            self.samples[-1] = self.current_sample()
        else:
            self.samples.append(self.current_sample())
        self._current_added = True
        self.status.setText(f"{len(self.samples)} screen(s) added. Capture another or calibrate.")

    def hint(self):
        r = self.picker.rect_px
        return None if r is None else (r.x(), r.y(), r.width(), r.height())

    def run(self) -> bool:
        if self.hint() is None:
            self.status.setText("Drag a rectangle around the board first.")
            return False
        samples = list(self.samples)
        if self._current_added:
            samples[-1] = self.current_sample()
        else:
            samples.append(self.current_sample())
        try:
            cal = calibrate(samples, self.profile, board_hint=self.hint())
        except (CalibrationError, GridNotFound) as exc:
            self.status.setText(f"Calibration failed: {exc}")
            return False
        if self.previous is not None:
            cal.masks = list(self.previous.masks)
        self.result = cal
        self.accept()
        return True

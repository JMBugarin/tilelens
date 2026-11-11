from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QFontDatabase, QKeyEvent, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QPlainTextEdit, QVBoxLayout, QWidget

from tilelens.rules.board import Board
from tilelens.rules.profile import Profile
from tilelens.ui.design import (
    GlassButton,
    Label,
    SectionLabel,
    Space,
    material_of,
    rgba,
    with_alpha,
)
from tilelens.ui.glass_dialog import (
    FrameView,
    GlassDialog,
    Notice,
    RackField,
    edge_cursor,
    paint_chip,
    paint_frame_line,
    paint_scrim_hint,
    to_qimage,
)
from tilelens.vision.grid import GridNotFound, detect_grid
from tilelens.vision.recognizer import Calibration, CalibrationError, calibrate

HANDLES = ("nw", "n", "ne", "e", "se", "s", "sw", "w")
HANDLE_RADIUS = 5.5
HANDLE_REACH = 11.0
MIN_SIDE = 8.0

STEPS = ("Draw the board", "Type the board", "Type the rack")
HINT = "Drag around the board squares"


class RectPicker(FrameView):
    """A frame scaled to fit, undistorted; drag a rectangle over it, in frame pixels.

    Drag on empty space to draw, inside the rectangle to move it and on a handle to resize it.
    The arrow keys nudge it by a pixel, with Shift they resize it, and Ctrl takes ten.
    """

    changed = Signal()

    def __init__(self, frame: np.ndarray, rows: int, cols: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.rows, self.cols = rows, cols
        self.rect_px: QRectF | None = None
        self._drag: tuple | None = None
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Board rectangle")
        self.set_frame(frame)

    def set_frame(self, frame: np.ndarray | None) -> None:
        super().set_frame(frame)
        self._describe()

    def set_rect(self, rect: tuple[float, float, float, float] | None) -> None:
        self.rect_px = QRectF(*rect) if rect else None
        self._describe()
        self.update()
        self.changed.emit()

    def _describe(self) -> None:
        r = self.rect_px
        if r is None:
            said = "not drawn yet. Drag on the picture."
        else:
            said = f"{round(r.width())} by {round(r.height())} pixels"
        self.setAccessibleDescription(said)

    def _bounds(self) -> QRectF:
        fw, fh = self.frame_size()
        return QRectF(0, 0, fw, fh)

    def _clamped(self, p: QPointF) -> QPointF:
        b = self._bounds()
        return QPointF(min(max(p.x(), b.left()), b.right()), min(max(p.y(), b.top()), b.bottom()))

    def _handle_points(self, r: QRectF) -> dict[str, QPointF]:
        c = r.center()
        return {
            "nw": r.topLeft(),
            "n": QPointF(c.x(), r.top()),
            "ne": r.topRight(),
            "e": QPointF(r.right(), c.y()),
            "se": r.bottomRight(),
            "s": QPointF(c.x(), r.bottom()),
            "sw": r.bottomLeft(),
            "w": QPointF(r.left(), c.y()),
        }

    def _widget_rect(self) -> QRectF | None:
        r = self.rect_px
        return None if r is None else self.rect_to_widget(r.x(), r.y(), r.width(), r.height())

    def _handle_at(self, pos: QPointF) -> str | None:
        wr = self._widget_rect()
        if wr is None or wr.width() < 1:
            return None
        best, best_d = None, HANDLE_REACH
        for name, pt in self._handle_points(wr).items():
            d = max(abs(pt.x() - pos.x()), abs(pt.y() - pos.y()))
            if d <= best_d:
                best, best_d = name, d
        return best

    def _cursor_at(self, pos: QPointF) -> Qt.CursorShape:
        if self.frame is None:
            return Qt.CursorShape.ArrowCursor
        handle = self._handle_at(pos)
        if handle is not None:
            return edge_cursor(handle)
        wr = self._widget_rect()
        if wr is not None and wr.contains(pos):
            return Qt.CursorShape.SizeAllCursor
        return Qt.CursorShape.CrossCursor

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() != Qt.MouseButton.LeftButton or self.frame is None:
            super().mousePressEvent(e)
            return
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        pos = e.position()
        here = self._clamped(self.to_frame(pos))
        handle = self._handle_at(pos)
        wr = self._widget_rect()
        if handle is not None and self.rect_px is not None:
            self._drag = ("size", handle, QRectF(self.rect_px))
        elif wr is not None and wr.contains(pos) and self.rect_px is not None:
            self._drag = ("move", here - self.rect_px.topLeft())
        else:
            self._drag = ("new", here)
        e.accept()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        pos = e.position()
        if self._drag is None:
            self.setCursor(self._cursor_at(pos))
            super().mouseMoveEvent(e)
            return
        here = self._clamped(self.to_frame(pos))
        kind = self._drag[0]
        if kind == "new":
            self.rect_px = QRectF(self._drag[1], here).normalized()
        elif kind == "move" and self.rect_px is not None:
            b = self._bounds()
            tl = here - self._drag[1]
            w, h = self.rect_px.width(), self.rect_px.height()
            x = min(max(tl.x(), b.left()), b.right() - w)
            y = min(max(tl.y(), b.top()), b.bottom() - h)
            self.rect_px = QRectF(x, y, w, h)
        elif kind == "size":
            _, handle, start = self._drag
            left, top, right, bottom = start.left(), start.top(), start.right(), start.bottom()
            if "w" in handle:
                left = here.x()
            if "e" in handle:
                right = here.x()
            if "n" in handle:
                top = here.y()
            if "s" in handle:
                bottom = here.y()
            self.rect_px = QRectF(QPointF(left, top), QPointF(right, bottom)).normalized()
        self._describe()
        self.update()
        e.accept()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if self._drag is None:
            super().mouseReleaseEvent(e)
            return
        self._drag = None
        r = self.rect_px
        if r is not None and (r.width() < MIN_SIDE or r.height() < MIN_SIDE):
            self.rect_px = None
        self._describe()
        self.setCursor(self._cursor_at(e.position()))
        self.update()
        self.changed.emit()
        e.accept()

    def keyPressEvent(self, e: QKeyEvent) -> None:
        step = {
            Qt.Key.Key_Left: (-1, 0),
            Qt.Key.Key_Right: (1, 0),
            Qt.Key.Key_Up: (0, -1),
            Qt.Key.Key_Down: (0, 1),
        }.get(e.key())
        r = self.rect_px
        if step is None or r is None:
            super().keyPressEvent(e)
            return
        k = 10 if e.modifiers() & Qt.KeyboardModifier.ControlModifier else 1
        dx, dy = step[0] * k, step[1] * k
        b = self._bounds()
        if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            w = min(max(MIN_SIDE, r.width() + dx), b.right() - r.x())
            h = min(max(MIN_SIDE, r.height() + dy), b.bottom() - r.y())
            self.rect_px = QRectF(r.x(), r.y(), w, h)
        else:
            x = min(max(r.x() + dx, b.left()), b.right() - r.width())
            y = min(max(r.y() + dy, b.top()), b.bottom() - r.height())
            self.rect_px = QRectF(x, y, r.width(), r.height())
        self._describe()
        self.update()
        self.changed.emit()
        e.accept()

    def paint_overlay(self, p: QPainter, image: QRectF) -> None:
        m = material_of(self)
        wr = self._widget_rect()
        if wr is None:
            paint_scrim_hint(p, image, HINT)
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        dark, light = rgba(0, 0, 0, 0.34), with_alpha(m.accent, 0.9)
        for i in range(1, self.cols):
            x = wr.x() + wr.width() * i / self.cols
            p.setPen(QPen(dark, 3.0))
            p.drawLine(QPointF(x, wr.y()), QPointF(x, wr.bottom()))
            p.setPen(QPen(light, 1.0))
            p.drawLine(QPointF(x, wr.y()), QPointF(x, wr.bottom()))
        for i in range(1, self.rows):
            y = wr.y() + wr.height() * i / self.rows
            p.setPen(QPen(dark, 3.0))
            p.drawLine(QPointF(wr.x(), y), QPointF(wr.right(), y))
            p.setPen(QPen(light, 1.0))
            p.drawLine(QPointF(wr.x(), y), QPointF(wr.right(), y))
        paint_frame_line(p, wr, m.accent, 3.0)
        label = f"Board  {self.cols} × {self.rows}"
        above = wr.top() - image.top() > 30
        paint_chip(
            p, QPointF(wr.left() + 12, wr.top() if above else wr.top() + 28), label, m, above
        )
        for pt in self._handle_points(wr).values():
            p.setPen(QPen(rgba(0, 0, 0, 0.55), 1.5))
            p.setBrush(rgba(255, 255, 255, 0.98))
            p.drawEllipse(pt, HANDLE_RADIUS, HANDLE_RADIUS)


class CalibrationDialog(GlassDialog):
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
        super().__init__(
            "Calibrate",
            "Mark the board on a captured screen and type what is on it.",
            parent,
        )
        self.profile = profile
        self.grab_frame = grab_frame
        self.previous = previous
        self.samples: list[tuple[np.ndarray, list[str], str]] = []
        self._current_added = False
        self.result: Calibration | None = None
        self.set_steps(STEPS)

        columns = QHBoxLayout()
        columns.setSpacing(Space.L)
        left = QVBoxLayout()
        left.setSpacing(Space.M)
        self.picker = RectPicker(frame, profile.height, profile.width)
        left.addWidget(self.picker, 1)
        tools = QHBoxLayout()
        tools.setSpacing(Space.S)
        self.auto_btn = GlassButton("Guess rectangle", icon="wand", size="s")
        self.auto_btn.setToolTip("Look for the board in this screen")
        self.auto_btn.clicked.connect(self.auto_rect)
        self.new_btn = GlassButton("Capture new screen", icon="refresh", size="s")
        self.new_btn.setToolTip("Grab the game again, for letters this screen does not show")
        self.new_btn.clicked.connect(self.new_screen)
        self.new_btn.setEnabled(grab_frame is not None)
        self.size_label = Label("", "caption", "secondary", tabular=True)
        tools.addWidget(self.auto_btn)
        tools.addWidget(self.new_btn)
        tools.addStretch(1)
        tools.addWidget(self.size_label)
        left.addLayout(tools)
        columns.addLayout(left, 1)

        right = QVBoxLayout()
        right.setSpacing(Space.S)
        right.addWidget(SectionLabel("Board"))
        right.addWidget(
            Label(
                "One row per line. A capital letter is a tile, a small one a blank tile "
                "and a dot an empty square.",
                "footnote",
                "secondary",
                wrap=True,
            )
        )
        self.board_edit = QPlainTextEdit()
        self.board_edit.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.board_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.board_edit.setTabChangesFocus(True)
        self.board_edit.setMinimumHeight(140)
        self.board_edit.setAccessibleName("Board, one row per line")
        rows = board.to_rows() if board else ["." * profile.width] * profile.height
        self.board_edit.setPlainText("\n".join(rows))
        right.addWidget(self.board_edit, 1)
        right.addSpacing(Space.S)
        right.addWidget(SectionLabel("Rack"))
        right.addWidget(
            Label("Left to right. A question mark is a blank.", "footnote", "secondary")
        )
        self.rack_edit = RackField(profile.rack_size, label="Rack, left to right")
        if rack:
            self.rack_edit.set_slots(list(rack), guess=True)
        right.addWidget(self.rack_edit)
        right_host = QWidget()
        right_host.setLayout(right)
        right_host.setFixedWidth(312)
        right.setContentsMargins(0, 0, 0, 0)
        columns.addWidget(right_host)
        self.body.addLayout(columns, 1)
        self.status = Notice()
        self.body.addWidget(self.status)

        self.count_label = Label("", "footnote", "secondary")
        self.add_btn = self.add_footer_action("Add this screen", icon="plus")
        self.add_btn.setToolTip("Keep this screen and capture another one")
        self.add_btn.clicked.connect(self.add_sample)
        self.footer_left.addWidget(self.count_label)
        self.footer_left.addStretch(1)
        self.cal_btn, cancel = self.add_footer("Calibrate", "Cancel")
        self.cal_btn.clicked.connect(self.run)
        self.focus_chain(
            self.picker,
            self.auto_btn,
            self.new_btn,
            self.board_edit,
            self.rack_edit,
            self.add_btn,
            cancel,
            self.cal_btn,
        )
        self.picker.changed.connect(self._sync)
        self.board_edit.textChanged.connect(self._sync)
        self.rack_edit.text_changed.connect(self._sync)
        if previous is not None:
            self.picker.set_rect(previous.grid.rect)
        self._sync()
        self.set_glass_size(960, 664)
        self.set_glass_minimum(780, 560)
        self.picker.setFocus()

    def _sync(self, *_) -> None:
        r = self.picker.rect_px
        self.size_label.set_text(
            "" if r is None else f"{round(r.width())} × {round(r.height())} px"
        )
        board = self.board_edit.toPlainText()
        done = [
            r is not None,
            any(ch.isalpha() for ch in board),
            bool(self.rack_edit.text()),
        ]
        active = next((i for i, d in enumerate(done) if not d), len(done))
        self.steps.set_states(
            ["done" if d else "active" if i == active else "todo" for i, d in enumerate(done)]
        )
        n = len(self.samples)
        self.count_label.set_text(f"{n} screen{'s' if n != 1 else ''} added" if n else "")

    def auto_rect(self) -> None:
        frame = self.picker.frame
        try:
            g = self.run_blocking(
                lambda: detect_grid(frame, self.profile.height, self.profile.width),
                "Looking for the board",
            )
        except GridNotFound as exc:
            self.status.message(f"Could not guess: {exc}. Drag the rectangle instead.", "warning")
            return
        self.picker.set_rect(g.rect)
        self.status.message(
            "Check that the lines sit on the board squares. Drag the corners to fix them.",
            "info",
        )

    def new_screen(self) -> None:
        frame = self.grab_frame() if self.grab_frame else None
        if frame is None:
            self.status.message("Capture failed.", "error")
            return
        rect = self.picker.rect_px
        self.picker.set_frame(frame)
        self.picker.set_rect((rect.x(), rect.y(), rect.width(), rect.height()) if rect else None)
        self.board_edit.setPlainText("\n".join(["." * self.profile.width] * self.profile.height))
        self.rack_edit.clear()
        self._current_added = False
        self.status.message("New screen captured. Type what is on it.", "info")

    def current_sample(self):
        rows = [ln.strip() for ln in self.board_edit.toPlainText().splitlines() if ln.strip()]
        return (self.picker.frame, rows, self.rack_edit.text().strip())

    def add_sample(self) -> None:
        if self._current_added:
            self.samples[-1] = self.current_sample()
        else:
            self.samples.append(self.current_sample())
        self._current_added = True
        self._sync()
        self.status.message(
            f"{len(self.samples)} screen(s) added. Capture another or calibrate.", "success"
        )

    def hint(self):
        r = self.picker.rect_px
        return None if r is None else (r.x(), r.y(), r.width(), r.height())

    def run(self) -> bool:
        hint = self.hint()
        if hint is None:
            self.status.message("Drag a rectangle around the board first.", "warning")
            self.picker.setFocus()
            return False
        samples = list(self.samples)
        if self._current_added:
            samples[-1] = self.current_sample()
        else:
            samples.append(self.current_sample())
        try:
            cal = self.run_blocking(
                lambda: calibrate(samples, self.profile, board_hint=hint), "Calibrating"
            )
        except (CalibrationError, GridNotFound) as exc:
            self.status.message(f"Calibration failed: {exc}", "error")
            return False
        if self.previous is not None:
            cal.masks = list(self.previous.masks)
        self.result = cal
        self.show_success("Calibrated")
        return True


__all__ = ["CalibrationDialog", "RectPicker", "to_qimage"]

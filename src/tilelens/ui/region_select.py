from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFontMetricsF,
    QGuiApplication,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QWidget

from tilelens.platforms.base import Region
from tilelens.ui.design import (
    RAISED,
    Material,
    Space,
    material_of,
    paint_glass,
    paint_shadow,
    paint_wash,
    rgba,
    type_font,
)
from tilelens.ui.design.paint import flags, paint_control_rim

SCRIM = rgba(6, 8, 16, 0.52)
CUTOUT_RADIUS = 6.0
HANDLE_RADIUS = 5.5
MIN_SIDE = 40
HINT = "Drag around the board and rack"
HINT_TAIL = "to cancel"
PILL_HEIGHT = 40.0
READOUT_HEIGHT = 30.0


def screen_under_cursor():
    return QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()


class RegionSelector(QWidget):
    """Dimmed layer over a display; drag to choose the capture region. Created without a
    screen it covers every display, and a drag on any of them, or Esc, ends it on all."""

    selected = Signal(object)
    cancelled = Signal()

    def __init__(self, screen=None, *, _root: RegionSelector | None = None):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Choose the capture region")
        self.setAccessibleDescription("Drag around the board and rack. Escape cancels.")
        self._root = _root
        self._peers: list[RegionSelector] = []
        self._all_displays = screen is None and _root is None
        self._screen = screen or screen_under_cursor()
        self.setScreen(self._screen)
        self.setGeometry(self._screen.geometry())
        self._start: QPoint | None = None
        self._end: QPoint | None = None

    def selection(self) -> QRect | None:
        if self._start is None or self._end is None:
            return None
        a, b = self._start, self._end
        return QRect(min(a.x(), b.x()), min(a.y(), b.y()), abs(b.x() - a.x()), abs(b.y() - a.y()))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._all_displays and not self._peers:
            for other in QGuiApplication.screens():
                if other is not self._screen:
                    peer = RegionSelector(other, _root=self)
                    self._peers.append(peer)
                    peer.show()
            self.raise_()
        self.activateWindow()
        self.setFocus()

    def closeEvent(self, event) -> None:
        for peer in self._peers:
            peer.close()
        super().closeEvent(event)

    def _finish(self, region: Region | None) -> None:
        root = self._root or self
        for win in (root, *root._peers):
            win.close()
        if region is None:
            root.cancelled.emit()
        else:
            root.selected.emit(region)

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self._finish(None)
            return
        super().keyPressEvent(e)

    def mousePressEvent(self, e: QMouseEvent) -> None:
        self._start = self._end = e.position().toPoint()
        self.activateWindow()
        self.update()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._start is not None:
            self._end = e.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if self._start is None:
            return
        self._end = e.position().toPoint()
        rect = self.selection()
        if rect is None or rect.width() < MIN_SIDE or rect.height() < MIN_SIDE:
            self._finish(None)
            return
        top_left = self.mapToGlobal(rect.topLeft())
        self._finish(Region(top_left.x(), top_left.y(), rect.width(), rect.height()))

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        m = material_of(self)
        dpr = float(p.device().devicePixelRatioF())
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.fillRect(self.rect(), SCRIM)
        sel = self.selection()
        if sel is None:
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            self._paint_hint(p, m, dpr)
            p.end()
            return
        frame = QRectF(sel)
        hole = QPainterPath()
        hole.addRoundedRect(frame, CUTOUT_RADIUS, CUTOUT_RADIUS)
        p.fillPath(hole, QColor(0, 0, 0, 2))
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        self._paint_frame(p, m, frame)
        self._paint_readout(p, m, frame, dpr)
        p.end()

    def _paint_frame(self, p: QPainter, m: Material, frame: QRectF) -> None:
        outer = frame.adjusted(-1.0, -1.0, 1.0, 1.0)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(rgba(0, 0, 0, 0.45), 4.0))
        p.drawRoundedRect(outer, CUTOUT_RADIUS + 1, CUTOUT_RADIUS + 1)
        p.setPen(QPen(m.accent, 2.0))
        p.drawRoundedRect(outer, CUTOUT_RADIUS + 1, CUTOUT_RADIUS + 1)
        points = [outer.topLeft(), outer.topRight(), outer.bottomLeft(), outer.bottomRight()]
        if outer.width() > 96:
            points += [
                QPointF(outer.center().x(), outer.top()),
                QPointF(outer.center().x(), outer.bottom()),
            ]
        if outer.height() > 96:
            points += [
                QPointF(outer.left(), outer.center().y()),
                QPointF(outer.right(), outer.center().y()),
            ]
        p.setPen(QPen(rgba(0, 0, 0, 0.55), 1.5))
        p.setBrush(rgba(255, 255, 255, 0.98))
        for pt in points:
            p.drawEllipse(pt, HANDLE_RADIUS, HANDLE_RADIUS)

    def _paint_pill(self, p: QPainter, m: Material, rect: QRectF, dpr: float) -> None:
        radius = rect.height() / 2
        paint_shadow(p, rect, radius, RAISED, m, dpr)
        paint_glass(p, rect, radius, m, blur=False)

    def _paint_hint(self, p: QPainter, m: Material, dpr: float) -> None:
        font = type_font("label")
        small = type_font("caption")
        fm, sm = QFontMetricsF(font), QFontMetricsF(small)
        gap = Space.S
        cap_w = sm.horizontalAdvance("Esc") + 14
        parts = [
            fm.horizontalAdvance(HINT),
            fm.horizontalAdvance("·"),
            cap_w,
            fm.horizontalAdvance(HINT_TAIL),
        ]
        width = sum(parts) + gap * (len(parts) - 1) + 2 * Space.XL
        top = self.rect().top() + 56
        rect = QRectF((self.width() - width) / 2, top, width, PILL_HEIGHT)
        self._paint_pill(p, m, rect, dpr)
        x = rect.left() + Space.XL
        mid = flags(Qt.AlignmentFlag.AlignLeft, Qt.AlignmentFlag.AlignVCenter)
        p.setFont(font)
        for i, w in enumerate(parts):
            cell = QRectF(x, rect.top(), w + 1, rect.height())
            if i == 2:
                cap = QRectF(x, rect.center().y() - 11, w, 22)
                paint_wash(p, cap, 7, m.fill_hover)
                paint_control_rim(p, cap, 7, m)
                p.setFont(small)
                p.setPen(m.text)
                p.drawText(cap, int(Qt.AlignmentFlag.AlignCenter.value), "Esc")
                p.setFont(font)
            else:
                p.setPen(m.text if i == 0 else m.text2)
                text = (HINT, "·", "", HINT_TAIL)[i]
                p.drawText(cell, mid, text)
            x += w + gap

    def _paint_readout(self, p: QPainter, m: Material, frame: QRectF, dpr: float) -> None:
        small = frame.width() < MIN_SIDE or frame.height() < MIN_SIDE
        text = "Too small" if small else f"{round(frame.width())} × {round(frame.height())}"
        font = type_font("label", tabular=True)
        w = QFontMetricsF(font).horizontalAdvance(text) + 2 * Space.L
        rect = QRectF(0, 0, w, READOUT_HEIGHT)
        margin = 10.0
        x = min(max(frame.right() - w, margin), self.width() - w - margin)
        below = frame.bottom() + margin
        if below + READOUT_HEIGHT <= self.height() - margin:
            rect.moveTopLeft(QPointF(x, below))
        elif frame.top() - margin - READOUT_HEIGHT >= margin:
            rect.moveTopLeft(QPointF(x, frame.top() - margin - READOUT_HEIGHT))
        else:
            rect.moveTopLeft(QPointF(x, frame.bottom() - READOUT_HEIGHT - margin))
        self._paint_pill(p, m, rect, dpr)
        p.setFont(font)
        p.setPen(m.warning if small else m.text)
        p.drawText(rect, int(Qt.AlignmentFlag.AlignCenter.value), text)


__all__ = ["RegionSelector", "screen_under_cursor"]

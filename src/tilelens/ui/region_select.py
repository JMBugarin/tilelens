from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget

from tilelens.platforms.base import Region


class RegionSelector(QWidget):
    """Full-screen dimmed layer on one display; drag to choose the capture region."""

    selected = Signal(object)
    cancelled = Signal()

    def __init__(self, screen=None):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        screen = screen or QGuiApplication.screenAt(
            QGuiApplication.primaryScreen().geometry().center()
        )
        self.setGeometry(screen.geometry())
        self._start: QPoint | None = None
        self._end: QPoint | None = None

    def selection(self) -> QRect | None:
        if self._start is None or self._end is None:
            return None
        a, b = self._start, self._end
        return QRect(min(a.x(), b.x()), min(a.y(), b.y()), abs(b.x() - a.x()), abs(b.y() - a.y()))

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            self.close()

    def mousePressEvent(self, e) -> None:
        self._start = self._end = e.position().toPoint()
        self.update()

    def mouseMoveEvent(self, e) -> None:
        if self._start is not None:
            self._end = e.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, e) -> None:
        self._end = e.position().toPoint()
        rect = self.selection()
        self.close()
        if rect is None or rect.width() < 40 or rect.height() < 40:
            self.cancelled.emit()
            return
        top_left = self.mapToGlobal(rect.topLeft())
        self.selected.emit(Region(top_left.x(), top_left.y(), rect.width(), rect.height()))

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 90))
        rect = self.selection()
        if rect is not None:
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            p.fillRect(rect, Qt.GlobalColor.transparent)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            p.setPen(QPen(QColor(255, 196, 0), 2))
            p.drawRect(rect)
        p.setPen(QColor(255, 255, 255))
        p.drawText(20, 30, "Drag around the game's board and rack. Esc cancels.")
        p.end()

from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget

from tilelens.platforms.base import Region
from tilelens.solver.movegen import Move
from tilelens.ui.glass import OverlayScene, paint_scene
from tilelens.ui.preview import CellMapper


class OverlayWindow(QWidget):
    """Transparent, click-through window over the capture region.

    Draws the board frame, the rack outline and ghost tiles for the selected play.

    It never receives input and never sends any; previewing a placement places nothing.
    """

    def __init__(self):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.scene = OverlayScene()
        self.excluded = False
        self.suppressed = False
        self.suppress_log: list[float] = []
        self._suppress_started: float | None = None

    on_shown = None  # called with the native window id each time the window is shown

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.on_shown is not None:
            self.on_shown(int(self.winId()))

    def place(self, region: Region) -> None:
        self.setGeometry(region.x, region.y, region.width, region.height)

    @property
    def move_shown(self) -> Move | None:
        return self.scene.move

    def show_scene(self, scene: OverlayScene) -> None:
        self.scene = scene
        self.update()

    def show_move(self, move: Move | None, mapper: CellMapper | None) -> None:
        self.show_scene(OverlayScene(move, mapper.rect if mapper else None))

    def clear(self) -> None:
        self.show_scene(OverlayScene())

    def try_exclude_from_capture(self, adapter) -> bool:
        if not self.isVisible():
            self.show()
        adapter.show_on_all_spaces(int(self.winId()))
        self.excluded = bool(adapter.exclude_from_capture(int(self.winId())))
        return self.excluded

    def begin_capture(self) -> None:
        """Hide drawing for one capture when the window cannot be excluded from capture."""
        if self.scene.empty:
            return
        self.suppressed = True
        self._suppress_started = time.perf_counter()
        self.setWindowOpacity(0.0)

    def end_capture(self) -> None:
        if not self.suppressed:
            return
        self.suppressed = False
        self.setWindowOpacity(1.0)
        if self._suppress_started is not None:
            self.suppress_log.append(time.perf_counter() - self._suppress_started)
            self._suppress_started = None

    def paintEvent(self, event) -> None:
        if self.scene.empty or self.suppressed:
            return
        painter = QPainter(self)
        paint_scene(painter, self.scene)
        painter.end()

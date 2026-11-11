from __future__ import annotations

import time

from PySide6.QtCore import QEasingCurve, QRectF, Qt, QTimer
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget

from tilelens import telemetry
from tilelens.platforms.base import Region
from tilelens.solver.movegen import Move
from tilelens.ui.design.appearance import Appearance, default_material
from tilelens.ui.design.motion import FloatAnimator
from tilelens.ui.design.tokens import Material, Motion
from tilelens.ui.glass import GUIDE_HOLD_MS, OverlayScene, paint_scene
from tilelens.ui.preview import CellMapper


class OverlayWindow(QWidget):
    """Transparent, click-through window over the capture region.

    Draws the suggested play over the board. The alignment guides and the rack outline fade in
    when the lock changes, fade out after a moment, and stay only while something needs attention.

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
        self._key = self.scene.key()
        self._lock = self.scene.lock_key
        self._mode = self.scene.guides_mode
        self._material: Material | None = None
        self._strength = 1.0
        self._appearance: Appearance | None = None
        self._guides = FloatAnimator(
            self, 0.0, duration=Motion.BASE, easing=QEasingCurve.Type.OutCubic
        )
        self._hold = QTimer(self)
        self._hold.setSingleShot(True)
        self._hold.setInterval(GUIDE_HOLD_MS)
        self._hold.timeout.connect(self._release_guides)

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

    @property
    def glass_material(self) -> Material:
        return self._material if self._material is not None else default_material()

    @property
    def guide_alpha(self) -> float:
        return self._guides.value

    @property
    def guides_pending(self) -> bool:
        """True while the guides are visible or on their way."""
        return self._guides.value > 0.0 or self._guides.target > 0.0

    def set_material(self, material: Material) -> None:
        if self._material is not None and material.name == self._material.name:
            return
        self._material = material
        self.update()

    def set_strength(self, strength: float) -> None:
        strength = max(0.6, min(1.3, float(strength)))
        if abs(strength - self._strength) < 1e-3:
            return
        self._strength = strength
        self.update()

    def bind(self, appearance: Appearance) -> None:
        if self._appearance is not None:
            self._appearance.appearance_changed.disconnect(self.set_material)
            self._appearance.strength_changed.disconnect(self.set_strength)
        self._appearance = appearance
        self.set_material(appearance.material)
        self.set_strength(appearance.strength)
        appearance.appearance_changed.connect(self.set_material)
        appearance.strength_changed.connect(self.set_strength)

    def show_scene(self, scene: OverlayScene) -> None:
        key = scene.key()
        changed = key != self._key
        self.scene = scene
        self._key = key
        self._sync_guides(scene)
        if changed:
            self.update()

    def show_move(self, move: Move | None, mapper: CellMapper | None) -> None:
        self.show_scene(OverlayScene(move, mapper.rect if mapper else None))

    def clear(self) -> None:
        self.show_scene(OverlayScene())

    def _sync_guides(self, scene: OverlayScene) -> None:
        lock = scene.lock_key
        locked_before, self._lock = self._lock, lock
        mode_before, self._mode = self._mode, scene.guides_mode
        if scene.guide_alpha is not None:
            self._hold.stop()
            self._guides.jump(scene.guide_alpha)
            return
        if scene.guides_mode == "off" or not scene.has_guides:
            self._hold.stop()
            self._guides.to(0.0)
            return
        if scene.guides_mode == "always" or scene.needs_attention:
            self._hold.stop()
            self._guides.to(1.0)
            return
        if lock != locked_before or mode_before != "auto":
            self._guides.to(1.0)
            self._hold.start()
        elif not self._hold.isActive():
            self._guides.to(0.0)

    def _release_guides(self) -> None:
        scene = self.scene
        if scene.guide_alpha is None and scene.guides_mode == "auto" and not scene.needs_attention:
            self._guides.to(0.0)

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
        timed = telemetry.enabled()
        started = time.perf_counter() if timed else 0.0
        painter = QPainter(self)
        paint_scene(
            painter,
            self.scene,
            guides=self._guides.value,
            material=self.glass_material,
            strength=self._strength,
            bounds=QRectF(self.rect()),
        )
        painter.end()
        if timed:
            telemetry.emit(
                "paint", paint_ms=round((time.perf_counter() - started) * 1000, 3), kind="overlay"
            )

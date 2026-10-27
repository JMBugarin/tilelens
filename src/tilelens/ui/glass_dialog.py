"""The glass sheet the dialogs are built on, and the inputs and canvases they share.

GlassDialog paints like design.GlassWindow (shadow bleed, glass body, native backdrop with a
solid fallback) but is a QDialog, so exec() works and the dialog stays above a full-screen game.
It follows the application's material, never takes focus unless asked to, and leaves the game's
layout alone: nothing it shows moves a control that is already on screen.
"""

from __future__ import annotations

import contextlib
import itertools
import math
import threading
from collections.abc import Callable, Iterable, Sequence
from typing import Any

import numpy as np
from PySide6.QtCore import (
    QEvent,
    QEventLoop,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFontMetricsF,
    QGuiApplication,
    QImage,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QSizePolicy,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from tilelens.ui.design import (
    FLOATING,
    SHADOW_BLEED,
    Appearance,
    Banner,
    Divider,
    Elevation,
    FloatAnimator,
    GlassButton,
    GlassWindow,
    Glyph,
    IconButton,
    Label,
    Material,
    Motion,
    Radius,
    Space,
    Spinner,
    call_backdrop,
    default_material,
    material_of,
    mix,
    native_backdrop,
    paint_glass,
    paint_icon,
    paint_shadow,
    paint_wash,
    reduce_motion,
    rgba,
    type_font,
    with_alpha,
)
from tilelens.ui.design.components import _Interactive
from tilelens.ui.design.paint import (
    blend_materials,
    dim,
    flags,
    paint_control_rim,
    paint_focus_ring,
    scaled_alpha,
)
from tilelens.ui.design.qss import build_qss, palette_for

SUCCESS_HOLD_MS = 450
BUSY_GRACE_MS = 120
FLAGS = ("", "warning", "error")
_NATIVE = native_backdrop()


def _is_typing(event: QKeyEvent) -> bool:
    """A printable key: swallowed by a field. Enter, Tab and Escape belong to the dialog."""
    text = event.text()
    return bool(text) and text.isprintable() and not text.isspace()


def to_qimage(frame: np.ndarray) -> QImage:
    rgb = np.ascontiguousarray(frame[:, :, ::-1])
    h, w = rgb.shape[:2]
    return QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()


class _StatusRow(QWidget):
    """Spinner or check and a line of text, for the footer while something runs or worked."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(Space.S)
        self.spinner = Spinner(16)
        self.check = Glyph("check", 16, "success", 2.2)
        self.label = Label("", "label", "secondary", elide=True)
        lay.addWidget(self.spinner, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.check, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.label, 1, Qt.AlignmentFlag.AlignVCenter)
        self.show_kind("busy")

    def show_kind(self, kind: str) -> None:
        busy = kind == "busy"
        self.spinner.setVisible(busy)
        self.check.setVisible(not busy)
        if busy:
            self.spinner.start()
        else:
            self.spinner.stop()
        self.label.set_tone("secondary" if busy else "primary")

    def set_text(self, text: str) -> None:
        self.label.set_text(text)


class GlassDialog(QDialog):
    """Modal sheet: header (title, subtitle, close), body, footer with a primary and a plain button.

    Subclasses fill `body`, call add_footer() and connect their own handlers. Enter presses the
    primary button when it is enabled, Escape closes, and a long step runs through run_blocking
    so the sheet can show a spinner without freezing.
    """

    backdrop = None
    on_shown = None
    appearance: Appearance | None = None

    material_changed = Signal(object)
    busy_changed = Signal(bool)

    def __init__(
        self,
        title: str = "",
        subtitle: str = "",
        parent: QWidget | None = None,
        *,
        material: Material | None = None,
        take_focus: bool = True,
        closable: bool = True,
        elevation: Elevation = FLOATING,
        radius: float = Radius.SHEET,
    ):
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)
        if not take_focus:
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setWindowTitle(title or "TileLens")
        self._elevation = elevation
        self._radius = float(radius)
        shared = type(self).appearance
        start = material or (shared.material if shared is not None else default_material())
        self._target = start
        self._from = start
        self._shown = start
        self._strength = shared.strength if shared is not None else 1.0
        self._strength_from = self._strength_to = self._strength
        self._solid = False
        self._native = False
        self._cache: QPixmap | None = None
        self._cache_key: tuple | None = None
        self._bound: Appearance | None = None
        self._drag_offset: QPoint | None = None
        self._wanted: QSize | None = None
        self._busy = False
        self._working = False
        self._success = False
        self._closing_hold = False
        self.user_moved = False
        self._fade = FloatAnimator(self, 1.0, duration=Motion.BASE, on_change=self._on_fade)
        self._strength_fade = FloatAnimator(
            self, 1.0, duration=Motion.SLOW, on_change=self._on_strength_fade
        )
        self._apply_style(start)
        self._build(title, subtitle, closable)
        if shared is not None:
            self.bind(shared)
        else:
            hints = QGuiApplication.styleHints()
            hints.colorSchemeChanged.connect(self._follow_default)
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self.activate_primary)
        QShortcut(QKeySequence("Ctrl+Enter"), self, activated=self.activate_primary)

    def _build(self, title: str, subtitle: str, closable: bool) -> None:
        k = SHADOW_BLEED
        root = QVBoxLayout(self)
        root.setContentsMargins(k + Space.XXL, k + Space.XL, k + Space.XXL, k + Space.L)
        root.setSpacing(0)
        self.header = QWidget(self)
        head = QHBoxLayout(self.header)
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(Space.M)
        titles = QVBoxLayout()
        titles.setSpacing(Space.XS)
        self.title_label = Label(title, "title", elide=True)
        self.subtitle_label = Label(subtitle, "body", "secondary", wrap=True)
        self.subtitle_label.setVisible(bool(subtitle))
        titles.addWidget(self.title_label)
        titles.addWidget(self.subtitle_label)
        head.addLayout(titles, 1)
        self.close_button = IconButton("close", "Close", size="s", variant="bare")
        self.close_button.clicked.connect(self.reject)
        self.close_button.setVisible(closable)
        head.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignTop)
        root.addWidget(self.header)
        self.steps_slot = QVBoxLayout()
        self.steps_slot.setContentsMargins(0, 0, 0, 0)
        root.addLayout(self.steps_slot)
        root.addSpacing(Space.L)
        self.body_host = QWidget(self)
        self.body = QVBoxLayout(self.body_host)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(Space.M)
        root.addWidget(self.body_host, 1)
        root.addSpacing(Space.L)
        root.addWidget(Divider())
        root.addSpacing(Space.M)

        self.footer = QWidget(self)
        foot = QHBoxLayout(self.footer)
        foot.setContentsMargins(0, 0, 0, 0)
        foot.setSpacing(Space.S)
        self._lead_stack_host = QWidget(self.footer)
        self._lead_stack = QStackedLayout(self._lead_stack_host)
        self._lead_stack.setContentsMargins(0, 0, 0, 0)
        self.lead_actions = QWidget(self._lead_stack_host)
        self.footer_left = QHBoxLayout(self.lead_actions)
        self.footer_left.setContentsMargins(0, 0, 0, 0)
        self.footer_left.setSpacing(Space.S)
        self._status_row = _StatusRow(self._lead_stack_host)
        self._lead_stack.addWidget(self.lead_actions)
        self._lead_stack.addWidget(self._status_row)
        self.footer_actions = QWidget(self.footer)
        self.footer_right = QHBoxLayout(self.footer_actions)
        self.footer_right.setContentsMargins(0, 0, 0, 0)
        self.footer_right.setSpacing(Space.S)
        foot.addWidget(self._lead_stack_host, 1)
        foot.addWidget(self.footer_actions, 0)
        root.addWidget(self.footer)
        self.primary: GlassButton | None = None
        self.secondary: GlassButton | None = None

    def set_title(self, text: str) -> None:
        self.title_label.set_text(text)

    def set_subtitle(self, text: str) -> None:
        self.subtitle_label.set_text(text)
        self.subtitle_label.setVisible(bool(text))

    def set_steps(self, items: Sequence[str], active: int = 0) -> Steps:
        steps = Steps(items)
        steps.set_active(active)
        self.steps_slot.addSpacing(Space.M)
        self.steps_slot.addWidget(steps)
        self.steps = steps
        return steps

    def add_footer(
        self,
        primary: str = "",
        secondary: str = "Cancel",
        *,
        icon: str | None = None,
        variant: str = "prominent",
    ) -> tuple[GlassButton | None, GlassButton | None]:
        if secondary:
            self.secondary = GlassButton(secondary)
            self.secondary.clicked.connect(self.reject)
            self.secondary.setAccessibleName(secondary)
            self.footer_right.addWidget(self.secondary)
        if primary:
            self.primary = GlassButton(primary, variant=variant, icon=icon)
            self.primary.setAccessibleName(primary)
            self.footer_right.addWidget(self.primary)
        return self.primary, self.secondary

    def add_footer_action(
        self, text: str, *, icon: str | None = None, variant: str = "plain"
    ) -> GlassButton:
        button = GlassButton(text, variant=variant, icon=icon)
        button.setAccessibleName(text)
        self.footer_left.addWidget(button)
        return button

    def activate_primary(self) -> None:
        button = self.primary
        if button is not None and button.isEnabled() and not self._busy and not self._working:
            button.click()

    def set_primary_enabled(self, enabled: bool) -> None:
        if self.primary is not None:
            self.primary.setEnabled(enabled)

    def focus_chain(self, *widgets: QWidget) -> None:
        for a, b in itertools.pairwise(widgets):
            QWidget.setTabOrder(a, b)

    def set_glass_size(self, width: int, height: int) -> None:
        self._wanted = QSize(width, height)
        k = 2 * SHADOW_BLEED
        self.resize(width + k, height + k)

    def set_glass_minimum(self, width: int, height: int) -> None:
        k = 2 * SHADOW_BLEED
        self.setMinimumSize(width + k, height + k)

    def glass_rect(self) -> QRectF:
        b = float(SHADOW_BLEED)
        return QRectF(b, b, self.width() - 2 * b, self.height() - 2 * b)

    @property
    def glass_material(self) -> Material:
        return self._shown

    @property
    def material(self) -> Material:
        return self._target

    @property
    def native_blur(self) -> bool:
        return self._native and not self._solid

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def working(self) -> bool:
        return self._working

    @property
    def success_shown(self) -> bool:
        return self._success

    def set_material(self, m: Material) -> None:
        if m.name == self._target.name:
            return
        self._from = self._shown
        self._target = m
        self._apply_style(m)
        if self.isVisible():
            self._ask_backdrop()
        self._fade.jump(0.0)
        self._fade.to(1.0)
        self.material_changed.emit(m)

    def set_strength(self, strength: float) -> None:
        strength = max(0.6, min(1.3, float(strength)))
        if abs(strength - self._strength_to) < 1e-3:
            return
        self._strength_from = self._strength
        self._strength_to = strength
        self._strength_fade.jump(0.0)
        self._strength_fade.to(1.0)

    def set_solid(self, solid: bool) -> None:
        solid = bool(solid)
        if solid == self._solid:
            return
        self._solid = solid
        if self.isVisible():
            self._ask_backdrop()
        else:
            self._invalidate()
        self.update()

    def bind(self, appearance: Appearance) -> None:
        if self._bound is not None:
            with contextlib.suppress(RuntimeError, TypeError):
                self._bound.appearance_changed.disconnect(self.set_material)
                self._bound.strength_changed.disconnect(self.set_strength)
        self._bound = appearance
        self.set_material(appearance.material)
        self.set_strength(appearance.strength)
        appearance.appearance_changed.connect(self.set_material)
        appearance.strength_changed.connect(self.set_strength)

    def _follow_default(self, *_) -> None:
        if self._bound is None:
            QTimer.singleShot(0, self._adopt_default)

    def _adopt_default(self) -> None:
        with contextlib.suppress(RuntimeError):
            self.set_material(default_material())

    def _apply_style(self, m: Material) -> None:
        self.setPalette(palette_for(m))
        self.setStyleSheet(build_qss(m) + "\nQDialog { background: transparent; }\n")

    def _invalidate(self) -> None:
        self._cache = None
        self._cache_key = None

    def _on_fade(self) -> None:
        t = self._fade.value
        self._shown = self._target if t >= 1.0 else blend_materials(self._from, self._target, t)
        self._invalidate()
        self.update()

    def _on_strength_fade(self) -> None:
        t = self._strength_fade.value
        self._strength = self._strength_from + (self._strength_to - self._strength_from) * t
        self._invalidate()
        self.update()

    def _backdrop_source(self):
        if self.backdrop is not None:
            return self.backdrop
        if GlassWindow.backdrop is not None:
            return GlassWindow.backdrop
        return _NATIVE

    def _ask_backdrop(self) -> None:
        ok = False
        if not self._solid:
            try:
                ok = call_backdrop(
                    self._backdrop_source(),
                    int(self.winId()),
                    self._radius,
                    self._target.dark,
                    float(SHADOW_BLEED),
                )
            except Exception:
                ok = False
        self._native = ok
        self._invalidate()

    def _target_screen(self):
        parent = self.parentWidget()
        if parent is not None and parent.isVisible():
            screen = parent.screen()
            if screen is not None:
                return screen
        return QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()

    def available_geometry(self) -> QRect | None:
        screen = self._target_screen()
        return None if screen is None else screen.availableGeometry()

    def _place(self) -> None:
        avail = self.available_geometry()
        if avail is None:
            return
        k = 2 * SHADOW_BLEED
        if self._wanted is not None:
            w = min(self._wanted.width(), max(320, avail.width() - 32))
            h = min(self._wanted.height(), max(240, avail.height() - 32))
            self.resize(w + k, h + k)
        else:
            self.adjustSize()
        if not self.testAttribute(Qt.WidgetAttribute.WA_Moved):
            self.move(
                avail.center().x() - self.width() // 2, avail.center().y() - self.height() // 2
            )

    def setVisible(self, visible: bool) -> None:
        if visible and not self.isVisible():
            self._place()
        super().setVisible(visible)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.on_shown is not None:
            with contextlib.suppress(Exception):
                self.on_shown(int(self.winId()))
        self._ask_backdrop()

    def resizeEvent(self, event) -> None:
        self._invalidate()
        super().resizeEvent(event)
        if self.isVisible():
            self._ask_backdrop()

    def _background(self, dpr: float) -> QPixmap:
        key = (self.width(), self.height(), dpr, self.native_blur)
        if self._cache is not None and self._cache_key == key:
            return self._cache
        pm = QPixmap(round(self.width() * dpr), round(self.height() * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        q = QPainter(pm)
        r = self.glass_rect()
        paint_shadow(q, r, self._radius, self._elevation, self._shown, dpr)
        paint_glass(q, r, self._radius, self._shown, blur=self.native_blur, strength=self._strength)
        q.end()
        self._cache, self._cache_key = pm, key
        return pm

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        dpr = float(p.device().devicePixelRatioF())
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.drawPixmap(0, 0, self._background(dpr))
        p.end()

    def _in_header(self, pos: QPointF) -> bool:
        top = self.glass_rect().top()
        bottom = self.mapFromGlobal(self.header.mapToGlobal(QPoint(0, self.header.height()))).y()
        return top <= pos.y() <= bottom + Space.S and self.glass_rect().contains(pos)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._in_header(event.position()):
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is None:
            super().mouseMoveEvent(event)
            return
        self.move(event.globalPosition().toPoint() - self._drag_offset)
        self.user_moved = True
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.activate_primary()
            event.accept()
            return
        super().keyPressEvent(event)

    def reject(self) -> None:
        if self._closing_hold:
            self.accept()
            return
        if self._busy or self._working:
            return
        super().reject()

    def closeEvent(self, event) -> None:
        if self._busy or self._working:
            event.ignore()
            return
        super().closeEvent(event)

    def set_busy(self, text: str | None) -> None:
        on = bool(text)
        if on:
            self._status_row.show_kind("busy")
            self._status_row.set_text(text or "")
            self._lead_stack.setCurrentWidget(self._status_row)
        elif not self._success:
            self._lead_stack.setCurrentWidget(self.lead_actions)
        changed = on != self._busy
        self._busy = on
        self.body_host.setEnabled(not on)
        self.footer_actions.setEnabled(not on)
        self.lead_actions.setEnabled(not on)
        self.close_button.setEnabled(not on)
        if changed:
            self.busy_changed.emit(on)

    def run_blocking(self, fn: Callable[[], Any], text: str = "Working", grace: int | None = None):
        """fn() off the interface thread while the sheet is on screen, so a spinner can turn.

        fn must not touch Qt. Its result, or the exception it raised, comes back to the caller.
        Short work never flashes the spinner; hidden dialogs and tests simply call fn().
        """
        if not self.isVisible() or self._working:
            return fn()
        box: dict[str, Any] = {}
        finished = threading.Event()

        def target() -> None:
            try:
                box["value"] = fn()
            except BaseException as exc:
                box["error"] = exc
            finally:
                finished.set()

        self._working = True
        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        loop = QEventLoop(self)
        poll = QTimer(self)
        poll.setInterval(8)
        poll.timeout.connect(lambda: loop.quit() if finished.is_set() else None)
        late = QTimer(self)
        late.setSingleShot(True)
        late.setInterval(BUSY_GRACE_MS if grace is None else grace)
        late.timeout.connect(lambda: self.set_busy(text))
        poll.start()
        late.start()
        try:
            if not finished.is_set():
                loop.exec()
        finally:
            poll.stop()
            late.stop()
            thread.join()
            self._working = False
            if self._busy:
                self.set_busy(None)
        if "error" in box:
            raise box["error"]
        return box.get("value")

    def show_success(self, text: str, *, hold: bool = True, then_accept: bool = True) -> None:
        """Confirm in the footer; the dialog accepts itself a moment later unless told not to."""
        self._success = True
        self._status_row.show_kind("success")
        self._status_row.set_text(text)
        self._lead_stack.setCurrentWidget(self._status_row)
        self.body_host.setEnabled(False)
        self.footer_actions.setEnabled(False)
        self.lead_actions.setEnabled(False)
        if not then_accept:
            return
        if not hold or not self.isVisible() or reduce_motion():
            self.accept()
            return
        self._closing_hold = True
        QTimer.singleShot(SUCCESS_HOLD_MS, self._finish_success)

    def _finish_success(self) -> None:
        with contextlib.suppress(RuntimeError):
            self._closing_hold = False
            self.accept()

    def clear_success(self) -> None:
        self._success = False
        self._closing_hold = False
        self._lead_stack.setCurrentWidget(self.lead_actions)
        self.body_host.setEnabled(True)
        self.footer_actions.setEnabled(True)
        self.lead_actions.setEnabled(True)


class Steps(QWidget):
    """The numbered "what to do" line: one short phrase per step, the current one in focus."""

    HEIGHT = 24
    BADGE = 20
    GAP = 6
    SEP = 20

    def __init__(self, items: Sequence[str], parent: QWidget | None = None):
        super().__init__(parent)
        self._items = list(items)
        self._states = ["todo"] * len(self._items)
        self.setFont(type_font("footnote"))
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._describe()

    @property
    def states(self) -> list[str]:
        return list(self._states)

    def set_active(self, index: int) -> None:
        self.set_states(
            [
                "done" if i < index else "active" if i == index else "todo"
                for i in range(len(self._items))
            ]
        )

    def set_states(self, states: Sequence[str]) -> None:
        states = list(states)
        if len(states) != len(self._items) or any(
            s not in ("todo", "active", "done") for s in states
        ):
            raise ValueError("one of todo, active or done per step")
        self._states = states
        self._describe()
        self.update()

    def _describe(self) -> None:
        parts = [
            f"{i + 1}. {t} ({s})"
            for i, (t, s) in enumerate(zip(self._items, self._states, strict=True))
        ]
        self.setAccessibleName("Steps")
        self.setAccessibleDescription("; ".join(parts))

    def _font_for(self, state: str):
        return type_font("label", 0.92) if state == "active" else type_font("footnote")

    def _natural(self) -> list[float]:
        return [
            QFontMetricsF(self._font_for(s)).horizontalAdvance(t)
            for t, s in zip(self._items, self._states, strict=True)
        ]

    def sizeHint(self) -> QSize:
        text = sum(self._natural())
        fixed = len(self._items) * (self.BADGE + self.GAP) + max(0, len(self._items) - 1) * self.SEP
        return QSize(math.ceil(text + fixed), self.HEIGHT)

    def minimumSizeHint(self) -> QSize:
        return QSize(self.BADGE * len(self._items) + 40, self.HEIGHT)

    def paintEvent(self, event) -> None:
        if not self._items:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        m = material_of(self)
        natural = self._natural()
        n = len(self._items)
        fixed = n * (self.BADGE + self.GAP) + (n - 1) * self.SEP
        room = max(0.0, self.width() - fixed)
        total = sum(natural) or 1.0
        shrink = min(1.0, room / total)
        x = 0.0
        cy = self.height() / 2
        for i, (text, state) in enumerate(zip(self._items, self._states, strict=True)):
            badge = QRectF(x, cy - self.BADGE / 2, self.BADGE, self.BADGE)
            self._paint_badge(p, m, badge, i + 1, state)
            font = self._font_for(state)
            width = natural[i] * shrink
            fm = QFontMetricsF(font)
            p.setFont(font)
            p.setPen(m.text if state == "active" else m.text2)
            label = fm.elidedText(text, Qt.TextElideMode.ElideRight, width + 0.5)
            p.drawText(
                QRectF(badge.right() + self.GAP, 0, width + 1, self.height()),
                flags(Qt.AlignmentFlag.AlignLeft, Qt.AlignmentFlag.AlignVCenter),
                label,
            )
            x = badge.right() + self.GAP + width
            if i < n - 1:
                mid = x + self.SEP / 2
                p.setPen(QPen(m.divider, 1.0))
                p.drawLine(QPointF(mid, cy - 6), QPointF(mid, cy + 6))
                x += self.SEP
        p.end()

    @staticmethod
    def _paint_badge(p: QPainter, m: Material, r: QRectF, number: int, state: str) -> None:
        radius = r.height() / 2
        if state == "active":
            paint_wash(p, r, radius, m.accent)
            p.setPen(m.on_accent)
        elif state == "done":
            paint_wash(p, r, radius, with_alpha(m.success, 0.20 if m.dark else 0.14))
            paint_icon(p, "check", r.adjusted(4.5, 4.5, -4.5, -4.5), m.success, 2.8)
            return
        else:
            paint_wash(p, r, radius, m.fill_hover)
            paint_control_rim(p, r, radius, m)
            p.setPen(m.text2)
        p.setFont(type_font("caption", tabular=True))
        p.drawText(
            r.adjusted(0, -0.5, 0, -0.5), int(Qt.AlignmentFlag.AlignCenter.value), str(number)
        )


class Pane(QWidget):
    """A flat tinted surface for a group of controls inside the sheet."""

    def __init__(self, parent: QWidget | None = None, *, radius: float = Radius.CARD):
        super().__init__(parent)
        self._radius = float(radius)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        m = material_of(self)
        r = QRectF(self.rect())
        paint_wash(p, r, self._radius, dim(m.fill, self.isEnabled()))
        paint_control_rim(p, r, self._radius, m, 1.0 if self.isEnabled() else 0.5)
        p.end()


class Notice(Banner):
    """An inline message that keeps its space when empty, so showing one moves nothing."""

    MIN_HEIGHT = 48
    KINDS = ("info", "warning", "error", "success")

    def __init__(self, parent: QWidget | None = None, *, reserve: bool = True):
        super().__init__("", "info", dismissible=False, parent=parent)
        self._empty = True
        self._reserve = reserve
        self.setMinimumHeight(self.MIN_HEIGHT if reserve else 0)
        centered = Qt.AlignmentFlag.AlignVCenter
        self.layout().setAlignment(self.glyph, centered)
        self.layout().setAlignment(self.label, centered)
        self._sync()

    def set_kind(self, kind: str) -> None:
        if kind == "success":
            self._kind = kind
            self.glyph.set_name("check")
            self.glyph.set_tone("success")
            self.setAccessibleDescription(kind)
            self.update()
            return
        super().set_kind(kind)

    def _tone(self, m: Material) -> QColor:
        if self._kind == "success":
            return m.success
        return super()._tone(m)

    def setText(self, text: str, kind: str | None = None) -> None:
        if kind is not None:
            self.set_kind(kind)
        self.set_text(text)
        self._empty = not text
        self._sync()

    def clear(self) -> None:
        self.setText("")

    def message(self, text: str, kind: str = "info") -> None:
        self.setText(text, kind)

    @property
    def empty(self) -> bool:
        return self._empty

    def _sync(self) -> None:
        shown = not self._empty
        self.glyph.setVisible(shown)
        self.label.setVisible(shown)
        if not self._reserve:
            self.setVisible(shown)
        self.updateGeometry()
        self.update()

    def paintEvent(self, event) -> None:
        if self._empty:
            return
        super().paintEvent(event)


class CandidateKey(_Interactive, QWidget):
    """A letter keycap you can press, to answer a question with one of its likely answers."""

    pressed_key = Signal(str)

    def __init__(self, text: str, parent: QWidget | None = None, *, label: str | None = None):
        super().__init__(parent)
        self._text = text
        self._active = False
        self._pressing = False
        self._init_interactive()
        self.setFont(type_font("label"))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(label or text)

    def text(self) -> str:
        return self._text

    @property
    def active(self) -> bool:
        return self._active

    def set_active(self, active: bool) -> None:
        if active != self._active:
            self._active = active
            self.update()

    def sizeHint(self) -> QSize:
        w = QFontMetricsF(self.font()).horizontalAdvance(self._text)
        return QSize(max(30, math.ceil(w) + 20), 30)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if not event.isAutoRepeat():
                self.pressed_key.emit(self._text)
            event.accept()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self._pressing = True
            self._press.to(1.0)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        was, self._pressing = self._pressing, False
        self._press.to(0.0)
        if was and self.rect().contains(event.position().toPoint()):
            self.pressed_key.emit(self._text)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        m = material_of(self)
        en = self.isEnabled()
        full = QRectF(self.rect())
        cap = QRectF(full.left(), full.top(), full.width(), full.height() - 2)
        radius = 9.0
        lip = QPainterPath()
        lip.addRoundedRect(full.adjusted(0, 2, 0, 0), radius, radius)
        face = QPainterPath()
        face.addRoundedRect(cap, radius, radius)
        p.fillPath(
            lip.subtracted(face),
            dim(rgba(0, 0, 0, 0.30) if m.dark else rgba(20, 30, 60, 0.20), en),
        )
        base = rgba(255, 255, 255, 0.13) if m.dark else rgba(255, 255, 255, 0.85)
        hover = rgba(255, 255, 255, 0.22) if m.dark else rgba(255, 255, 255, 1.0)
        fill = mix(mix(base, hover, self._hover_value()), m.fill_press, self._press_value() * 0.6)
        paint_wash(p, cap, radius, dim(fill, en))
        if self._active:
            paint_wash(
                p,
                cap,
                radius,
                with_alpha(m.accent, 0.28 if m.dark else 0.16),
                with_alpha(m.accent, 0.9),
            )
        else:
            paint_control_rim(p, cap, radius, m, 0.9 if en else 0.5)
        p.setFont(self.font())
        p.setPen(dim(m.text if not self._active else mix(m.text, m.accent, 0.5), en))
        p.drawText(cap, int(Qt.AlignmentFlag.AlignCenter.value), self._text)
        if self._ring:
            paint_focus_ring(p, cap, radius, scaled_alpha(self._ring_color(m), 0.9))
        p.end()


def paint_tile(
    p: QPainter,
    m: Material,
    rect: QRectF,
    *,
    text: str = "",
    focus: bool = False,
    hover: float = 0.0,
    flag: str = "",
    guess: bool = False,
    blank: bool = False,
    pending: bool = False,
    enabled: bool = True,
    caret: bool = False,
    font=None,
) -> None:
    """One tile-shaped input cell: the letter large and tabular, a ring for focus or a flag."""
    radius = Radius.CONTROL - 2.0
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    fill = mix(m.fill, m.fill_hover, 0.5 + 0.5 * hover)
    if focus:
        fill = mix(fill, m.fill_press, 0.5)
    paint_wash(p, rect, radius, dim(fill, enabled))
    tone = {"warning": m.warning, "error": m.danger}.get(flag)
    if tone is not None:
        paint_wash(p, rect, radius, with_alpha(tone, 0.14 if m.dark else 0.10))
    if blank and not focus and tone is None:
        pen = QPen(dim(m.text2, enabled), 1.3, Qt.PenStyle.CustomDashLine)
        pen.setDashPattern([2.4, 2.2])
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(pen)
        p.drawRoundedRect(rect.adjusted(0.65, 0.65, -0.65, -0.65), radius - 0.65, radius - 0.65)
    else:
        paint_control_rim(p, rect, radius, m, 1.0 if enabled else 0.5)
    if tone is not None and not focus:
        paint_wash(p, rect, radius, QColor(0, 0, 0, 0), dim(with_alpha(tone, 0.85), enabled))
    if focus:
        paint_focus_ring(p, rect, radius, scaled_alpha(tone or m.accent, 0.95))
    if text:
        p.setFont(font or type_font("word", tabular=True))
        color = m.text
        if guess:
            color = m.accent if m.dark else mix(m.accent, m.text, 0.15)
        p.setPen(dim(color, enabled))
        p.drawText(rect.adjusted(0, -1, 0, -1), int(Qt.AlignmentFlag.AlignCenter.value), text)
    elif focus and caret:
        c = rect.center()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(dim(m.accent, enabled))
        p.drawRoundedRect(QRectF(c.x() - 1.0, c.y() - 9, 2.0, 18), 1.0, 1.0)
    if pending:
        p.setFont(type_font("caption"))
        p.setPen(dim(m.text2, enabled))
        p.drawText(
            rect.adjusted(0, 3, -5, 0),
            flags(Qt.AlignmentFlag.AlignRight, Qt.AlignmentFlag.AlignTop),
            "~",
        )
    p.restore()


class _TileInput(_Interactive, QWidget):
    """Shared plumbing of the tile-shaped inputs: focus, hover and the keys that bubble up."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._init_interactive()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, False)
        self.setCursor(Qt.CursorShape.IBeamCursor)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self._ring = False
        self.update()


class LetterField(_TileInput):
    """One square's answer: a letter, or nothing. "~" before it means placed but not played yet,
    lowercase means a blank tile. text() and setText() speak the same notation as before."""

    text_changed = Signal(str)
    entered = Signal()
    stepped = Signal(int)

    SIZE = QSize(44, 48)

    def __init__(self, text: str = "", parent: QWidget | None = None, *, label: str = "Letter"):
        super().__init__(parent)
        self._text = ""
        self._flag = ""
        self._guess = False
        self.setFixedSize(self.SIZE)
        self.setAccessibleName(label)
        self.setText(text)

    def text(self) -> str:
        return self._text

    def setText(self, text: str, *, user: bool = False) -> None:
        if text == self._text:
            return
        self._text = text
        if user:
            self._guess = False
        self._describe()
        self.update()
        self.text_changed.emit(text)

    def set_flag(self, flag: str) -> None:
        if flag not in FLAGS:
            raise ValueError(f"flag must be one of {FLAGS}")
        if flag != self._flag:
            self._flag = flag
            self.update()

    @property
    def flag(self) -> str:
        return self._flag

    def set_guess(self, guess: bool) -> None:
        if guess != self._guess:
            self._guess = guess
            self.update()

    @property
    def guess(self) -> bool:
        return self._guess

    def parts(self) -> tuple[str, bool, bool, bool]:
        """letter (upper case), blank, pending, valid"""
        return self._parts()

    def _parts(self) -> tuple[str, bool, bool, bool]:
        """letter, blank, pending, valid"""
        t = self._text.strip()
        pending = t.startswith("~")
        core = t[1:] if pending else t
        if core in ("", "."):
            return "", False, pending, not pending
        if len(core) == 1 and core.isalpha():
            return core.upper(), core.islower(), pending, True
        return core, False, pending, False

    def _describe(self) -> None:
        letter, blank, pending, valid = self._parts()
        if not valid:
            said = f"not a letter: {self._text}"
        elif not letter:
            said = "empty"
        else:
            said = letter + (", blank" if blank else "") + (", not played yet" if pending else "")
        self.setAccessibleDescription(said)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        mods = event.modifiers()
        if mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
            super().keyPressEvent(event)
            return
        if key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
            if self._text:
                self.setText("", user=True)
                self.entered.emit()
            else:
                self.stepped.emit(-1)
            event.accept()
            return
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self.stepped.emit(-1 if key == Qt.Key.Key_Up else 1)
            event.accept()
            return
        char = event.text()
        letter, blank, pending, valid = self._parts()
        if char == "~":
            if not valid:
                letter, blank = "", False
            self.setText(
                ("" if pending else "~") + (letter.lower() if blank else letter), user=True
            )
            event.accept()
            return
        if char == "?":
            if valid and letter:
                self.setText(
                    ("~" if pending else "") + (letter.upper() if blank else letter.lower()),
                    user=True,
                )
            event.accept()
            return
        if len(char) == 1 and char.isalpha():
            self.type_letter(char)
            event.accept()
            return
        event.accept() if _is_typing(event) else event.ignore()

    def type_letter(self, char: str) -> None:
        """What typing a letter does: keeps a blank a blank and a pending tile pending."""
        letter, blank, pending, valid = self._parts()
        keep_blank = valid and blank
        self.setText(
            ("~" if pending and valid else "") + (char.lower() if keep_blank else char.upper()),
            user=True,
        )
        self.entered.emit()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        m = material_of(self)
        letter, blank, pending, valid = self._parts()
        flag = self._flag or ("" if valid else "error")
        shown = letter
        if not valid and len(shown) > 2:
            shown = shown[:1] + "…"
        paint_tile(
            p,
            m,
            QRectF(self.rect()),
            text=shown,
            focus=self.hasFocus(),
            hover=self._hover_value(),
            flag=flag,
            guess=self._guess and valid,
            blank=blank,
            pending=pending,
            enabled=self.isEnabled(),
            caret=True,
        )
        p.end()


class RackField(_TileInput):
    """The rack as one tile-shaped field per tile: type to fill and advance, arrows to move."""

    text_changed = Signal(str)
    textChanged = text_changed

    TILE = QSize(38, 44)
    GAP = 6
    MAX = 24

    def __init__(self, count: int = 7, parent: QWidget | None = None, *, label: str = "Rack"):
        super().__init__(parent)
        self._expected = max(1, int(count))
        self._slots: list[str] = [""] * self._expected
        self._flags: dict[int, str] = {}
        self._guess: set[int] = set()
        self._cur = 0
        self._over = -1
        self.setMouseTracking(True)
        self.setAccessibleName(label)
        sp = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)
        self._describe()

    @property
    def expected(self) -> int:
        return self._expected

    def count(self) -> int:
        return len(self._slots)

    def set_expected(self, count: int) -> None:
        self._expected = max(1, int(count))
        self._fit()
        self.updateGeometry()
        self.update()

    def _fit(self) -> None:
        n = max(self._expected, self._last_filled() + 1)
        n = min(n, self.MAX)
        if n > len(self._slots):
            self._slots += [""] * (n - len(self._slots))
        elif n < len(self._slots):
            del self._slots[n:]
        self._cur = min(self._cur, len(self._slots) - 1)

    def _last_filled(self) -> int:
        for i in range(len(self._slots) - 1, -1, -1):
            if self._slots[i]:
                return i
        return -1

    def text(self) -> str:
        return "".join(s for s in self._slots if s)

    def setText(self, text: str) -> None:
        letters = [ch.upper() for ch in text if not ch.isspace()][: self.MAX]
        self.set_slots(letters)

    def clear(self) -> None:
        self.set_slots([])

    def slots(self) -> list[str]:
        return list(self._slots)

    def set_slots(self, slots: Sequence[str], *, guess: bool | Iterable[int] = False) -> None:
        """guess: True marks every letter as a guess, or give the indices of the ones that are."""
        letters = [s[:1].upper() if s else "" for s in slots][: self.MAX]
        self._slots = letters + [""] * max(0, self._expected - len(letters))
        if guess is True:
            self._guess = {i for i, s in enumerate(self._slots) if s}
        else:
            self._guess = {i for i in (guess or ()) if 0 <= i < len(self._slots) and self._slots[i]}
        self._fit()
        self._after_change()

    def set_slot(self, index: int, letter: str, *, user: bool = True) -> None:
        if not 0 <= index < len(self._slots):
            return
        self._slots[index] = letter[:1].upper() if letter else ""
        if user:
            self._guess.discard(index)
        self._after_change()

    def set_flags(self, flags_by_slot: dict[int, str]) -> None:
        for flag in flags_by_slot.values():
            if flag not in FLAGS:
                raise ValueError(f"flag must be one of {FLAGS}")
        self._flags = dict(flags_by_slot)
        self.update()

    def flag_of(self, index: int) -> str:
        if index >= self._expected and self._slots[index]:
            return self._flags.get(index) or "warning"
        return self._flags.get(index, "")

    def clear_flag(self, index: int) -> None:
        if self._flags.pop(index, None) is not None:
            self.update()

    def focus_slot(self, index: int) -> None:
        self._cur = max(0, min(len(self._slots) - 1, index))
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.update()

    @property
    def current(self) -> int:
        return self._cur

    def _after_change(self) -> None:
        self._describe()
        self.updateGeometry()
        self.update()
        self.text_changed.emit(self.text())

    def _describe(self) -> None:
        shown = " ".join(s or "empty" for s in self._slots)
        self.setAccessibleDescription(f"{len(self._slots)} tiles: {shown}")

    def _per_row(self, width: int) -> int:
        w = self.TILE.width()
        return max(1, int((width + self.GAP) // (w + self.GAP)))

    def slot_rect(self, i: int, width: int | None = None) -> QRectF:
        per = self._per_row(self.width() if width is None else width)
        row, col = divmod(i, per)
        w, h = self.TILE.width(), self.TILE.height()
        return QRectF(col * (w + self.GAP), row * (h + self.GAP), w, h)

    def slot_at(self, pos: QPointF) -> int:
        for i in range(len(self._slots)):
            if (
                self.slot_rect(i)
                .adjusted(-self.GAP / 2, -self.GAP / 2, self.GAP / 2, self.GAP / 2)
                .contains(pos)
            ):
                return i
        return -1

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        per = self._per_row(width)
        rows = math.ceil(len(self._slots) / per)
        return rows * self.TILE.height() + (rows - 1) * self.GAP

    def sizeHint(self) -> QSize:
        n = len(self._slots)
        w = n * self.TILE.width() + (n - 1) * self.GAP
        return QSize(w, self.TILE.height())

    def minimumSizeHint(self) -> QSize:
        # nested layouts do not pass height-for-width on, so the rows are counted here
        width = self.width() if self.width() >= self.TILE.width() * 3 else self.sizeHint().width()
        return QSize(self.TILE.width(), self.heightForWidth(width))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.updateGeometry()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        mods = event.modifiers()
        last = len(self._slots) - 1
        if event.matches(QKeySequence.StandardKey.Paste):
            self._paste(QApplication.clipboard().text())
            event.accept()
            return
        if mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
            super().keyPressEvent(event)
            return
        moves = {
            Qt.Key.Key_Left: self._cur - 1,
            Qt.Key.Key_Right: self._cur + 1,
            Qt.Key.Key_Home: 0,
            Qt.Key.Key_End: last,
        }
        if key in moves:
            self._cur = max(0, min(last, moves[key]))
            self.update()
            event.accept()
            return
        if key == Qt.Key.Key_Backspace:
            if self._slots[self._cur]:
                self.set_slot(self._cur, "")
            elif self._cur > 0:
                self._cur -= 1
                self.set_slot(self._cur, "")
            event.accept()
            return
        if key == Qt.Key.Key_Delete:
            self.set_slot(self._cur, "")
            event.accept()
            return
        char = event.text()
        if len(char) == 1 and (char.isalpha() or char == "?"):
            self.set_slot(self._cur, char)
            self._cur = min(last, self._cur + 1)
            self.update()
            event.accept()
            return
        if key == Qt.Key.Key_Space:
            self._cur = min(last, self._cur + 1)
            self.update()
            event.accept()
            return
        event.accept() if _is_typing(event) else event.ignore()

    def _paste(self, text: str) -> None:
        letters = [ch.upper() for ch in text if ch.isalpha() or ch == "?"]
        if not letters:
            return
        start = self._cur
        for k, ch in enumerate(letters):
            i = start + k
            while i >= len(self._slots) and len(self._slots) < self.MAX:
                self._slots.append("")
            if i >= len(self._slots):
                break
            self._slots[i] = ch
            self._guess.discard(i)
            self._cur = min(len(self._slots) - 1, i + 1)
        self._after_change()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            i = self.slot_at(event.position())
            if i >= 0:
                self._cur = i
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        i = self.slot_at(event.position())
        if i != self._over:
            self._over = i
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self._over = -1
        self.update()
        super().leaveEvent(event)

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.Type.EnabledChange:
            self.update()
        super().changeEvent(event)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        m = material_of(self)
        focused = self.hasFocus()
        for i, ch in enumerate(self._slots):
            blank = ch == "?"
            paint_tile(
                p,
                m,
                self.slot_rect(i),
                text=ch,
                focus=focused and i == self._cur,
                hover=1.0 if i == self._over and self.isEnabled() else 0.0,
                flag=self.flag_of(i),
                guess=i in self._guess,
                blank=blank,
                enabled=self.isEnabled(),
                caret=True,
            )
        p.end()


class FrameView(QWidget):
    """A captured screen scaled to fit, undistorted, in a rounded pane with a quiet inner rim."""

    PAD = Space.S
    INNER = Radius.CARD - Space.S

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.frame: np.ndarray | None = None
        self._image: QImage | None = None
        self._scaled: QPixmap | None = None
        self._scaled_key: tuple | None = None
        self.placeholder = "Nothing captured yet"
        self.setMinimumSize(280, 200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_frame(self, frame: np.ndarray | None) -> None:
        self.frame = frame
        self._image = None if frame is None else to_qimage(frame)
        self._scaled = None
        self._scaled_key = None
        self.updateGeometry()
        self.update()

    def frame_size(self) -> tuple[int, int]:
        if self._image is None:
            return 0, 0
        return self._image.width(), self._image.height()

    def image_rect(self) -> QRectF:
        """Where the frame sits in the widget, in widget points."""
        fw, fh = self.frame_size()
        area = QRectF(self.rect()).adjusted(self.PAD, self.PAD, -self.PAD, -self.PAD)
        if fw <= 0 or fh <= 0 or area.width() <= 0 or area.height() <= 0:
            return area
        s = min(area.width() / fw, area.height() / fh)
        w, h = fw * s, fh * s
        return QRectF(area.center().x() - w / 2, area.center().y() - h / 2, w, h)

    def scale(self) -> float:
        fw, _ = self.frame_size()
        return self.image_rect().width() / fw if fw else 1.0

    def to_frame(self, p: QPointF) -> QPointF:
        r, s = self.image_rect(), self.scale()
        return QPointF((p.x() - r.x()) / s, (p.y() - r.y()) / s)

    def to_widget(self, p: QPointF) -> QPointF:
        r, s = self.image_rect(), self.scale()
        return QPointF(r.x() + p.x() * s, r.y() + p.y() * s)

    def rect_to_widget(self, x: float, y: float, w: float, h: float) -> QRectF:
        a = self.to_widget(QPointF(x, y))
        return QRectF(a.x(), a.y(), w * self.scale(), h * self.scale())

    def paint_overlay(self, p: QPainter, image: QRectF) -> None:
        """Subclasses draw on top of the frame; p is clipped to the pane."""

    def _pixmap_for(self, target: QRectF, dpr: float) -> QPixmap | None:
        if self._image is None:
            return None
        size = QSize(max(1, round(target.width() * dpr)), max(1, round(target.height() * dpr)))
        key = (size.width(), size.height())
        if self._scaled is None or self._scaled_key != key:
            if size.width() >= self._image.width():
                self._scaled = QPixmap.fromImage(self._image)
            else:
                scaled = self._image.scaled(
                    size,
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self._scaled = QPixmap.fromImage(scaled)
            self._scaled_key = key
        return self._scaled

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        m = material_of(self)
        full = QRectF(self.rect())
        paint_wash(p, full, Radius.CARD, dim(m.fill, self.isEnabled()))
        paint_control_rim(p, full, Radius.CARD, m)
        if self._image is None:
            p.setFont(type_font("body"))
            p.setPen(m.text2)
            p.drawText(full, int(Qt.AlignmentFlag.AlignCenter.value), self.placeholder)
            p.end()
            return
        image = self.image_rect()
        dpr = float(p.device().devicePixelRatioF())
        pix = self._pixmap_for(image, dpr)
        clip = QPainterPath()
        clip.addRoundedRect(image, self.INNER, self.INNER)
        p.save()
        p.setClipPath(clip)
        if pix is not None:
            p.drawPixmap(image, pix, QRectF(pix.rect()))
        p.restore()
        paint_control_rim(p, image, self.INNER, m, 0.9)
        p.save()
        pane = QPainterPath()
        pane.addRoundedRect(full, Radius.CARD, Radius.CARD)
        p.setClipPath(pane)
        self.paint_overlay(p, image)
        p.restore()
        if not self.isEnabled():
            paint_wash(p, full, Radius.CARD, rgba(0, 0, 0, 0.18))
        p.end()


def paint_frame_line(p: QPainter, rect: QRectF, accent: QColor, radius: float = 4.0) -> None:
    """An outline that reads on any background: a dark casing under a bright line."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(rgba(0, 0, 0, 0.55), 4.0))
    p.drawRoundedRect(rect, radius, radius)
    p.setPen(QPen(accent, 2.0))
    p.drawRoundedRect(rect, radius, radius)
    p.restore()


def paint_chip(p: QPainter, anchor: QPointF, text: str, m: Material, above: bool = True) -> None:
    """A small dark label with white text, for naming what an outline marks."""
    font = type_font("caption")
    fm = QFontMetricsF(font)
    w, h = fm.horizontalAdvance(text) + 14, 20.0
    x = anchor.x()
    y = anchor.y() - h - 4 if above else anchor.y() + 4
    rect = QRectF(x, y, w, h)
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    paint_wash(p, rect, h / 2, rgba(0, 0, 0, 0.66), rgba(255, 255, 255, 0.22))
    p.setFont(font)
    p.setPen(rgba(255, 255, 255, 0.96))
    p.drawText(rect, int(Qt.AlignmentFlag.AlignCenter.value), text)
    p.restore()


def paint_scrim_hint(p: QPainter, area: QRectF, text: str) -> None:
    """A quiet line over the frame while nothing is drawn yet."""
    font = type_font("label")
    fm = QFontMetricsF(font)
    w, h = fm.horizontalAdvance(text) + 28, 32.0
    rect = QRectF(area.center().x() - w / 2, area.top() + Space.M, w, h)
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    paint_wash(p, rect, h / 2, rgba(0, 0, 0, 0.66), rgba(255, 255, 255, 0.22))
    p.setFont(font)
    p.setPen(rgba(255, 255, 255, 0.96))
    p.drawText(rect, int(Qt.AlignmentFlag.AlignCenter.value), text)
    p.restore()


def edge_cursor(name: str) -> Qt.CursorShape:
    return {
        "nw": Qt.CursorShape.SizeFDiagCursor,
        "se": Qt.CursorShape.SizeFDiagCursor,
        "ne": Qt.CursorShape.SizeBDiagCursor,
        "sw": Qt.CursorShape.SizeBDiagCursor,
        "n": Qt.CursorShape.SizeVerCursor,
        "s": Qt.CursorShape.SizeVerCursor,
        "e": Qt.CursorShape.SizeHorCursor,
        "w": Qt.CursorShape.SizeHorCursor,
    }[name]


__all__ = [
    "BUSY_GRACE_MS",
    "SUCCESS_HOLD_MS",
    "CandidateKey",
    "FrameView",
    "GlassDialog",
    "LetterField",
    "Notice",
    "Pane",
    "RackField",
    "Steps",
    "edge_cursor",
    "paint_chip",
    "paint_frame_line",
    "paint_scrim_hint",
    "paint_tile",
    "to_qimage",
]

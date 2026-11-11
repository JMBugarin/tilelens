"""Toasts: short confirmations that fade in beside what the player is doing and leave by
themselves. They never take focus, can let clicks through, show one at a time and wait their
turn politely. Anything that has to stay until it is dealt with belongs in a Banner instead.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QObject, QPoint, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QFontMetricsF, QGuiApplication
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLayout, QWidget

from tilelens.ui.design import (
    RAISED,
    SHADOW_BLEED,
    Appearance,
    FloatAnimator,
    GlassWindow,
    Glyph,
    Label,
    Motion,
    Radius,
    Space,
    native_backdrop,
    type_font,
)

KINDS = ("info", "success", "warning", "error")
ICONS = {"info": "info", "success": "check", "warning": "alert-triangle", "error": "alert-triangle"}
TONES = {"info": "accent", "success": "success", "warning": "warning", "error": "danger"}
FADE_MS = Motion.BASE
MIN_VISIBLE_MS = 900
MIN_DURATION_MS = 800
MAX_PENDING = 4
MAX_TEXT_WIDTH = 340
EDGE_MARGIN = 8


class Toast(GlassWindow):
    """One small pane of glass with an icon and a line or two of text."""

    finished = Signal()

    def __init__(self, text: str = "", kind: str = "info", *, click_through: bool = True):
        super().__init__(
            None,
            elevation=RAISED,
            radius=Radius.CARD,
            take_focus=False,
            draggable=False,
        )
        if type(self).backdrop is None:
            self.backdrop = native_backdrop()
        self._kind = "info"
        self._text = ""
        self._leaving = False
        self.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.set_click_through(click_through)
        self._alpha = FloatAnimator(self, 1.0, duration=FADE_MS, on_change=self._apply_opacity)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(self.content_margins(Space.L, 10))
        lay.setSpacing(10)
        lay.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
        self.glyph = Glyph("info", 16, "accent", 2.2)
        self.label = Label("", "body", "primary", wrap=True)
        lay.addWidget(self.glyph, 0, Qt.AlignmentFlag.AlignTop)
        lay.addWidget(self.label, 0, Qt.AlignmentFlag.AlignTop)
        self.set_content(text, kind)

    @property
    def kind(self) -> str:
        return self._kind

    @property
    def text(self) -> str:
        return self._text

    @property
    def click_through(self) -> bool:
        return bool(self.windowFlags() & Qt.WindowType.WindowTransparentForInput)

    @property
    def opacity(self) -> float:
        return self._alpha.value

    def set_click_through(self, on: bool) -> None:
        if on == self.click_through:
            return
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, on)
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, on)

    def set_content(self, text: str, kind: str = "info") -> None:
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}")
        self._text = text
        self._kind = kind
        self.glyph.set_name(ICONS[kind])
        self.glyph.set_tone(TONES[kind])
        self.setAccessibleName(text)
        self.setAccessibleDescription(kind)
        width = min(
            float(MAX_TEXT_WIDTH), QFontMetricsF(type_font("body")).horizontalAdvance(text) + 2
        )
        self.label.set_text(text)
        self.label.setFixedWidth(int(width) + 1)
        self.label.setFixedHeight(self.label.heightForWidth(int(width) + 1))
        self.layout().invalidate()
        self.layout().activate()
        self.adjustSize()

    def glass_size(self) -> tuple[int, int]:
        return self.width() - 2 * SHADOW_BLEED, self.height() - 2 * SHADOW_BLEED

    def fade_in(self) -> None:
        self._leaving = False
        self._alpha.jump(0.0)
        self.show()
        self.raise_()
        self._alpha.to(1.0)

    def fade_out(self) -> None:
        if not self.isVisible():
            self.finished.emit()
            return
        self._leaving = True
        self._alpha.to(0.0)

    def hide_now(self) -> None:
        self._leaving = False
        self._alpha.jump(1.0)
        self.hide()

    def _apply_opacity(self) -> None:
        self.setWindowOpacity(max(0.0, min(1.0, self._alpha.value)))
        if self._leaving and self._alpha.value <= 0.0 and self._alpha.target <= 0.0:
            self._leaving = False
            self.hide()
            self._alpha.jump(1.0)
            self.finished.emit()


@dataclass
class _Item:
    text: str
    kind: str
    ms: int
    anchor: object
    click_through: bool


def _anchor_rect(anchor) -> QRect | None:
    """Where the toast should sit: the glass of a window, a widget's area or a plain rectangle,
    in screen points; None when the anchor is gone or hidden."""
    if anchor is None:
        return None
    try:
        if isinstance(anchor, QRectF):
            return anchor.toRect()
        if isinstance(anchor, QRect):
            return QRect(anchor)
        if isinstance(anchor, QWidget):
            if not anchor.isVisible():
                return None
            glass = getattr(anchor, "glass_geometry", None)
            if callable(glass):
                return glass()
            return QRect(anchor.mapToGlobal(QPoint(0, 0)), anchor.size())
    except RuntimeError:
        return None
    return None


class ToastManager(QObject):
    """Shows toasts one at a time, in order. A newer toast shortens the one on screen to a
    short minimum so a burst of confirmations never keeps the player waiting."""

    shown = Signal(str, str)

    on_shown: Callable[[int], object] | None = None

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        factory: Callable[[], Toast] = Toast,
    ):
        super().__init__(parent)
        self._clock = clock
        self._factory = factory
        self._toast: Toast | None = None
        self._appearance: Appearance | None = None
        self._current: _Item | None = None
        self._pending: list[_Item] = []
        self._shown_at = 0.0
        self._dwell = QTimer(self)
        self._dwell.setSingleShot(True)
        self._dwell.timeout.connect(self._dismiss)

    @property
    def toast(self) -> Toast | None:
        return self._toast

    @property
    def current(self) -> tuple[str, str] | None:
        return None if self._current is None else (self._current.text, self._current.kind)

    @property
    def pending(self) -> list[tuple[str, str]]:
        return [(i.text, i.kind) for i in self._pending]

    def show(
        self,
        text: str,
        kind: str = "info",
        ms: int = 2500,
        anchor=None,
        click_through: bool = True,
    ) -> bool:
        """Queue a toast. False when it was dropped: empty text, or the same message is already
        on screen or waiting."""
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}")
        text = " ".join(text.split())
        if not text:
            return False
        item = _Item(text, kind, max(MIN_DURATION_MS, int(ms)), anchor, click_through)
        cur = self._current
        if cur is not None and (cur.text, cur.kind) == (text, kind):
            if self._dwell.isActive():
                self._dwell.start(item.ms)
            return False
        if any((i.text, i.kind) == (text, kind) for i in self._pending):
            return False
        if cur is None:
            self._start(item)
            return True
        self._pending.append(item)
        del self._pending[:-MAX_PENDING]
        self._hurry()
        return True

    def clear(self) -> None:
        """Drop everything waiting and take the visible toast away at once."""
        self._pending.clear()
        self._dwell.stop()
        self._current = None
        if self._toast is not None:
            self._toast.hide_now()

    def bind(self, appearance: Appearance) -> None:
        """Toasts follow this appearance (light, dark and fill strength)."""
        self._appearance = appearance
        if self._toast is not None:
            self._toast.bind(appearance)

    def _ensure_toast(self) -> Toast:
        if self._toast is None:
            self._toast = self._factory()
            self._toast.finished.connect(self._finished)
            self._toast.on_shown = lambda win_id: self.on_shown(win_id) if self.on_shown else None
            if self._appearance is not None:
                self._toast.bind(self._appearance)
        return self._toast

    def _start(self, item: _Item) -> None:
        toast = self._ensure_toast()
        self._current = item
        toast.set_click_through(item.click_through)
        toast.set_content(item.text, item.kind)
        self._place(toast, item.anchor)
        self._shown_at = self._clock()
        toast.fade_in()
        self._dwell.start(item.ms)
        self.shown.emit(item.text, item.kind)

    def _hurry(self) -> None:
        elapsed_ms = (self._clock() - self._shown_at) * 1000
        wait = max(0, round(MIN_VISIBLE_MS - elapsed_ms))
        if not self._dwell.isActive() or self._dwell.remainingTime() > wait:
            self._dwell.start(wait)

    def _dismiss(self) -> None:
        if self._toast is None or self._current is None:
            return
        self._toast.fade_out()

    def _finished(self) -> None:
        self._current = None
        if self._pending:
            self._start(self._pending.pop(0))

    def _place(self, toast: Toast, anchor) -> None:
        """An anchor may be ("below", rect): the toast sits under the rectangle, or above it
        where the screen ends."""
        w, h = toast.glass_size()
        edge = "inside"
        if isinstance(anchor, tuple) and len(anchor) == 2 and isinstance(anchor[0], str):
            edge, anchor = anchor
        rect = _anchor_rect(anchor)
        screen = QGuiApplication.primaryScreen()
        if rect is not None:
            screen = QGuiApplication.screenAt(rect.center()) or screen
            x = rect.center().x() - w // 2
            y = rect.bottom() - Space.XL - h
            if edge == "below":
                y = rect.bottom() + Space.S
                if screen is not None and y + h > screen.availableGeometry().bottom() - EDGE_MARGIN:
                    y = rect.top() - Space.S - h
        else:
            area = screen.availableGeometry() if screen is not None else QRect(0, 0, 1280, 800)
            x = area.center().x() - w // 2
            y = area.bottom() - 56 - h
        if screen is not None:
            area = screen.availableGeometry()
            x = max(area.left() + EDGE_MARGIN, min(x, area.right() - w - EDGE_MARGIN))
            y = max(area.top() + EDGE_MARGIN, min(y, area.bottom() - h - EDGE_MARGIN))
        toast.move_glass_to(int(x), int(y))


_state: dict[str, object] = {}


def manager() -> ToastManager:
    """The application's toast manager, made on first use."""
    app = QApplication.instance()
    current = _state.get("manager")
    if current is None or _state.get("app") is not app:
        current = ToastManager(app)
        _state["manager"], _state["app"] = current, app
    return current  # type: ignore[return-value]


def show(
    text: str, kind: str = "info", ms: int = 2500, anchor=None, click_through: bool = True
) -> bool:
    return manager().show(text, kind, ms, anchor, click_through)


def bind(appearance: Appearance) -> None:
    manager().bind(appearance)


__all__ = ["KINDS", "Toast", "ToastManager", "bind", "manager", "show"]

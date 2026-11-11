"""Quiet motion: a float eased toward a target, plus a global reduce-motion switch.

Nothing animates while the owning widget is hidden, and with reduce-motion on every change is
applied at once, so a state is always reachable without waiting for a timer.
"""

from __future__ import annotations

import math
import weakref
from collections.abc import Callable

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QVariantAnimation
from PySide6.QtWidgets import QWidget

from tilelens.ui.design.tokens import Motion

_state = {"reduce": False}
_live: weakref.WeakSet[QObject] = weakref.WeakSet()


def reduce_motion() -> bool:
    return _state["reduce"]


def set_reduce_motion(on: bool) -> None:
    """Global switch. Turning it on settles every running animation where it is headed."""
    _state["reduce"] = bool(on)
    for anim in list(_live):
        try:
            anim.on_reduce_changed()  # type: ignore[attr-defined]
        except RuntimeError:
            _live.discard(anim)


def can_animate(widget: QWidget | None) -> bool:
    return not _state["reduce"] and widget is not None and widget.isVisible()


class FloatAnimator(QObject):
    """One float that eases toward a target (hover wash, press, selection, thumb position)."""

    def __init__(
        self,
        owner: QWidget,
        value: float = 0.0,
        *,
        duration: int = Motion.FAST,
        easing: QEasingCurve.Type = QEasingCurve.Type.OutCubic,
        on_change: Callable[[], None] | None = None,
    ):
        super().__init__(owner)
        self._owner = owner
        self._value = float(value)
        self._target = float(value)
        self._duration = duration
        self._on_change = on_change or owner.update
        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(QEasingCurve(easing))
        self._anim.valueChanged.connect(self._step)
        _live.add(self)

    @property
    def value(self) -> float:
        return self._value

    @property
    def target(self) -> float:
        return self._target

    @property
    def running(self) -> bool:
        return self._anim.state() == QVariantAnimation.State.Running

    def to(self, target: float, duration: int | None = None) -> None:
        target = float(target)
        if target == self._target and (self.running or self._value == target):
            return
        self._target = target
        d = self._duration if duration is None else duration
        if d <= 0 or not can_animate(self._owner):
            self.settle()
            return
        self._anim.stop()
        self._anim.setDuration(d)
        self._anim.setStartValue(self._value)
        self._anim.setEndValue(target)
        self._anim.start()

    def jump(self, value: float) -> None:
        self._target = float(value)
        self.settle()

    def settle(self) -> None:
        self._anim.stop()
        changed = self._value != self._target
        self._value = self._target
        if changed:
            self._on_change()

    def on_reduce_changed(self) -> None:
        if _state["reduce"] and self.running:
            self.settle()

    def _step(self, v) -> None:
        self._value = float(v)
        self._on_change()


class LoopAnimator(QObject):
    """A phase that repeats while wanted and shown (spinner, busy dot).

    It follows the owner's show and hide events, and stays at rest under reduce-motion.
    """

    def __init__(
        self,
        owner: QWidget,
        duration: int = 900,
        *,
        on_tick: Callable[[], None] | None = None,
    ):
        super().__init__(owner)
        self._owner = owner
        self._wanted = False
        self._phase = 0.0
        self._on_tick = on_tick or owner.update
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(duration)
        self._anim.setLoopCount(-1)
        self._anim.setEasingCurve(QEasingCurve(QEasingCurve.Type.Linear))
        self._anim.valueChanged.connect(self._step)
        owner.installEventFilter(self)
        _live.add(self)

    @property
    def phase(self) -> float:
        return self._phase

    @property
    def wave(self) -> float:
        """0..1 sine of the phase, for a slow fade in and out."""
        return 0.5 - 0.5 * math.cos(self._phase * math.tau)

    @property
    def running(self) -> bool:
        return self._anim.state() == QVariantAnimation.State.Running

    @property
    def wanted(self) -> bool:
        return self._wanted

    def start(self) -> None:
        self._wanted = True
        self._sync()

    def stop(self) -> None:
        self._wanted = False
        self._sync()

    def on_reduce_changed(self) -> None:
        self._sync()

    def eventFilter(self, obj, event) -> bool:
        # during teardown Qt may call this on a wrapper that has already lost its state
        owner = getattr(self, "_owner", None)
        if owner is not None and obj is owner:
            try:
                if event.type() in (QEvent.Type.Show, QEvent.Type.Hide):
                    self._sync()
            except RuntimeError:
                pass
        return False

    def _sync(self) -> None:
        if self._wanted and can_animate(self._owner):
            if not self.running:
                self._anim.start()
        elif self.running:
            self._anim.stop()
            self._phase = 0.0
            self._on_tick()
        elif self._phase != 0.0:
            self._phase = 0.0
            self._on_tick()

    def _step(self, v) -> None:
        self._phase = float(v)
        self._on_tick()


__all__ = [
    "FloatAnimator",
    "LoopAnimator",
    "can_animate",
    "reduce_motion",
    "set_reduce_motion",
]

"""Glass components: the floating window, the controls that sit in it and the text around them.

Everything paints through material.py with the material of the nearest GlassWindow (or the
default one), so a material switch repaints the whole tree. Controls keep a two pixel focus ring
inside their bounds, drawn only after keyboard focus.
"""

from __future__ import annotations

import contextlib
import math
from collections.abc import Sequence

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QMargins,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QKeyEvent,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QSizePolicy, QWidget

from tilelens.ui.design.appearance import Appearance, material_of
from tilelens.ui.design.icons import check_icon, paint_icon
from tilelens.ui.design.material import (
    FLOATING,
    SHADOW_BLEED,
    Elevation,
    paint_glass,
    paint_shadow,
    paint_wash,
)
from tilelens.ui.design.motion import FloatAnimator, LoopAnimator
from tilelens.ui.design.native import call_backdrop
from tilelens.ui.design.paint import (
    FOCUS_WIDTH,
    blend_materials,
    check_tone,
    dim,
    flags,
    paint_control_rim,
    paint_focus_ring,
    scaled_alpha,
    tone_color,
)
from tilelens.ui.design.qss import build_qss, palette_for
from tilelens.ui.design.tokens import (
    DARK,
    Material,
    Motion,
    Radius,
    Space,
    mix,
    rgba,
    type_font,
    with_alpha,
)

BUTTON_HEIGHT = {"s": 26, "m": 32}
ICON_BUTTON_SIZE = {"s": 24, "m": 32}
VARIANTS = ("plain", "tinted", "prominent", "destructive")
TONES = ("ok", "busy", "warn", "idle", "error")
KINDS = ("info", "warning", "error")

_WHITE = rgba(255, 255, 255)
_BLACK = rgba(0, 0, 0)

_KEYBOARD_REASONS = (
    Qt.FocusReason.TabFocusReason,
    Qt.FocusReason.BacktabFocusReason,
    Qt.FocusReason.ShortcutFocusReason,
)


class _Interactive:
    """Hover and press easing plus the keyboard-only focus ring, shared by the controls."""

    def _init_interactive(self, duration: int = Motion.FAST) -> None:
        self._hover = FloatAnimator(self, 0.0, duration=duration)
        self._press = FloatAnimator(self, 0.0, duration=duration)
        self._ring = False

    def _hover_value(self) -> float:
        return self._hover.value if self.isEnabled() else 0.0

    def _press_value(self) -> float:
        return self._press.value if self.isEnabled() else 0.0

    def _ring_color(self, m: Material) -> QColor:
        return with_alpha(m.accent, 0.9)

    def enterEvent(self, event) -> None:
        if self.isEnabled():
            self._hover.to(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover.to(0.0)
        super().leaveEvent(event)

    def hideEvent(self, event) -> None:
        self._hover.jump(0.0)
        self._press.jump(0.0)
        super().hideEvent(event)

    def focusInEvent(self, event) -> None:
        self._ring = event.reason() in _KEYBOARD_REASONS
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:
        self._ring = False
        self.update()
        super().focusOutEvent(event)

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.Type.EnabledChange:
            self._hover.jump(0.0)
            self._press.jump(0.0)
        super().changeEvent(event)


class _ButtonBase(_Interactive, QAbstractButton):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._init_interactive()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.pressed.connect(self._on_pressed)
        self.released.connect(self._on_released)

    def _on_pressed(self) -> None:
        self._press.to(1.0)

    def _on_released(self) -> None:
        self._press.to(0.0)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.isAutoRepeat():
            self.click()
            return
        super().keyPressEvent(event)


def _variant_colors(m: Material, variant: str) -> tuple[QColor, QColor, QColor, QColor]:
    """Resting, hover and pressed fill and the content colour of a button variant."""
    if variant == "plain":
        return m.fill, m.fill_hover, m.fill_press, m.text
    if variant == "prominent":
        return (
            m.accent,
            mix(m.accent, _WHITE, 0.14),
            mix(m.accent, _BLACK, 0.14),
            m.on_accent,
        )
    base = m.accent if variant == "tinted" else m.danger
    a = (0.20, 0.30, 0.38) if m.dark else (0.13, 0.20, 0.27)
    return (
        with_alpha(base, a[0]),
        with_alpha(base, a[1]),
        with_alpha(base, a[2]),
        base,
    )


def _paint_button_body(
    p: QPainter, rect: QRectF, radius: float, m: Material, variant: str, fill: QColor, en: bool
) -> None:
    if variant == "prominent":
        grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        grad.setColorAt(0, dim(mix(fill, _WHITE, 0.12), en, 0.35))
        grad.setColorAt(1, dim(fill, en, 0.35))
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawRoundedRect(rect, radius, radius)
        p.restore()
        inset = rect.adjusted(0.5, 0.5, -0.5, -0.5)
        hi = QLinearGradient(inset.topLeft(), inset.bottomLeft())
        hi.setColorAt(0, rgba(255, 255, 255, 0.34 if m.dark else 0.30))
        hi.setColorAt(0.5, rgba(255, 255, 255, 0.0))
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(hi, 1.0))
        p.drawRoundedRect(inset, radius - 0.5, radius - 0.5)
        p.restore()
        return
    paint_wash(p, rect, radius, dim(fill, en))
    paint_control_rim(p, rect, radius, m, 1.0 if en else 0.5)


class GlassButton(_ButtonBase):
    """Capsule button. Variants: plain, tinted, prominent, destructive. Sizes: s, m."""

    def __init__(
        self,
        text: str = "",
        *,
        variant: str = "plain",
        size: str = "m",
        icon: str | None = None,
        parent: QWidget | None = None,
    ):
        if icon is not None:
            check_icon(icon)
        super().__init__(parent)
        self._variant = "plain"
        self._size = "m"
        self._icon = icon
        self.set_variant(variant)
        self.set_size(size)
        self.setText(text)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)

    @property
    def variant(self) -> str:
        return self._variant

    def set_variant(self, variant: str) -> None:
        if variant not in VARIANTS:
            raise ValueError(f"variant must be one of {VARIANTS}")
        self._variant = variant
        self.update()

    def set_size(self, size: str) -> None:
        if size not in BUTTON_HEIGHT:
            raise ValueError(f"size must be one of {tuple(BUTTON_HEIGHT)}")
        self._size = size
        self.setFont(type_font("label", 1.0 if size == "m" else 0.92))
        self.updateGeometry()
        self.update()

    def set_icon_name(self, name: str | None) -> None:
        self._icon = None if name is None else check_icon(name)
        self.updateGeometry()
        self.update()

    def _metrics(self) -> tuple[float, float, float]:
        m = self._size == "m"
        return (16.0 if m else 12.0), (16.0 if m else 14.0), 6.0

    def sizeHint(self) -> QSize:
        pad, isz, gap = self._metrics()
        text = self.text()
        w = QFontMetricsF(self.font()).horizontalAdvance(text) if text else 0.0
        if self._icon:
            w += isz + (gap if text else 0)
        h = BUTTON_HEIGHT[self._size]
        return QSize(max(h, math.ceil(w + 2 * pad)), h)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        m = material_of(self)
        en = self.isEnabled()
        rest, hov, prs, fg = _variant_colors(m, self._variant)
        fill = mix(mix(rest, hov, self._hover_value()), prs, self._press_value())
        r = QRectF(self.rect())
        radius = r.height() / 2
        _paint_button_body(p, r, radius, m, self._variant, fill, en)
        pad, isz, gap = self._metrics()
        fm = QFontMetricsF(self.font())
        text = self.text()
        tw = fm.horizontalAdvance(text) if text else 0.0
        avail = r.width() - 2 * pad - (isz + gap if self._icon else 0.0)
        if tw > avail > 0:
            text = fm.elidedText(text, Qt.TextElideMode.ElideRight, avail)
            tw = fm.horizontalAdvance(text)
        total = tw + (isz + (gap if text else 0) if self._icon else 0.0)
        x = r.center().x() - total / 2
        color = dim(fg, en)
        if self._icon:
            paint_icon(p, self._icon, QRectF(x, r.center().y() - isz / 2, isz, isz), color, 1.9)
            x += isz + (gap if text else 0)
        if text:
            p.setFont(self.font())
            p.setPen(color)
            p.drawText(
                QRectF(x, 0, tw + 1, r.height()),
                flags(Qt.AlignmentFlag.AlignLeft, Qt.AlignmentFlag.AlignVCenter),
                text,
            )
        if self._ring:
            ring = m.text if self._variant == "prominent" else self._ring_color(m)
            paint_focus_ring(p, r, radius, scaled_alpha(ring, 0.9))
        p.end()


class IconButton(_ButtonBase):
    """Round icon button. Its tooltip is also its accessible name."""

    VARIANTS = ("plain", "bare", "tinted", "prominent", "destructive")

    def __init__(
        self,
        icon: str,
        tooltip: str = "",
        *,
        size: str = "m",
        variant: str = "plain",
        parent: QWidget | None = None,
    ):
        check_icon(icon)
        if size not in ICON_BUTTON_SIZE:
            raise ValueError(f"size must be one of {tuple(ICON_BUTTON_SIZE)}")
        super().__init__(parent)
        self._icon = icon
        self._size = size
        self._variant = "plain"
        self.set_variant(variant)
        self.set_tooltip(tooltip)
        d = ICON_BUTTON_SIZE[size]
        self.setFixedSize(d, d)

    def set_variant(self, variant: str) -> None:
        if variant not in self.VARIANTS:
            raise ValueError(f"variant must be one of {self.VARIANTS}")
        self._variant = variant
        self.update()

    def set_icon_name(self, name: str) -> None:
        self._icon = check_icon(name)
        self.update()

    @property
    def icon_name(self) -> str:
        return self._icon

    def set_tooltip(self, text: str) -> None:
        self.setToolTip(text)
        self.setAccessibleName(text or self._icon)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        m = material_of(self)
        en = self.isEnabled()
        bare = self._variant == "bare"
        variant = "plain" if bare else self._variant
        if self.isCheckable() and self.isChecked() and variant == "plain":
            variant = "tinted"
        rest, hov, prs, fg = _variant_colors(m, variant)
        if bare:
            rest = with_alpha(rest, 0.0)
            fg = m.text2 if not self._hover_value() else m.text
        fill = mix(mix(rest, hov, self._hover_value()), prs, self._press_value())
        r = QRectF(self.rect())
        radius = r.height() / 2
        if bare:
            if fill.alpha():
                paint_wash(p, r, radius, dim(fill, en))
        else:
            _paint_button_body(p, r, radius, m, variant, fill, en)
        isz = r.height() * (0.56 if self._size == "m" else 0.6)
        paint_icon(
            p,
            self._icon,
            QRectF(r.center().x() - isz / 2, r.center().y() - isz / 2, isz, isz),
            dim(fg, en),
            1.9,
        )
        if self._ring:
            ring = m.text if variant == "prominent" else self._ring_color(m)
            paint_focus_ring(p, r, radius, scaled_alpha(ring, 0.9))
        p.end()


class Switch(_ButtonBase):
    """Toggle. Use toggled(bool) from QAbstractButton; label is the accessible name."""

    WIDTH = 44
    HEIGHT = 26

    def __init__(self, checked: bool = False, label: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self._pos = FloatAnimator(self, 1.0 if checked else 0.0, duration=Motion.BASE)
        self.toggled.connect(self._on_toggled)
        self.setAccessibleName(label)
        self.setToolTip(label)

    def _on_toggled(self, on: bool) -> None:
        self._pos.to(1.0 if on else 0.0)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        m = material_of(self)
        en = self.isEnabled()
        t = self._pos.value
        r = QRectF(self.rect())
        radius = r.height() / 2
        off = mix(m.fill_hover, m.fill_press, 0.55)
        track = mix(off, m.accent, t)
        paint_wash(p, r, radius, dim(track, en))
        paint_control_rim(p, r, radius, m, (1.0 - 0.6 * t) * (1.0 if en else 0.5))
        d = r.height() - 4 + 3 * self._press_value()
        x = 2 + t * (r.width() - 4 - (r.height() - 4)) - (d - (r.height() - 4)) * t
        thumb = QRectF(x, 2, d, r.height() - 4)
        for dy, a in ((1.5, 0.10), (0.5, 0.16)):
            paint_wash(
                p,
                thumb.translated(0, dy),
                thumb.height() / 2,
                rgba(0, 0, 0, a * (1 if en else 0.4)),
            )
        paint_wash(
            p,
            thumb,
            thumb.height() / 2,
            dim(rgba(255, 255, 255, 0.98), en, 0.7),
            rgba(0, 0, 0, 0.10 if m.dark else 0.12),
        )
        if self._ring:
            paint_focus_ring(p, r, radius, scaled_alpha(self._ring_color(m), 0.95))
        p.end()


class Segmented(_Interactive, QWidget):
    """2 to 4 exclusive options with a sliding thumb. Left/Right/Home/End move the selection."""

    current_changed = Signal(int)

    HEIGHT = 30
    PAD = 2

    def __init__(self, options: Sequence[str], current: int = 0, parent: QWidget | None = None):
        super().__init__(parent)
        options = list(options)
        if not 2 <= len(options) <= 4:
            raise ValueError("Segmented takes 2 to 4 options")
        self._options = options
        self._index = max(0, min(len(options) - 1, current))
        self._init_interactive()
        self._thumb = FloatAnimator(self, float(self._index), duration=Motion.BASE)
        self._over = -1
        self.setFont(type_font("label", 0.92))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(self.HEIGHT)
        self._pressed_index = -1
        self._describe()

    @property
    def options(self) -> list[str]:
        return list(self._options)

    def current_index(self) -> int:
        return self._index

    def current_text(self) -> str:
        return self._options[self._index]

    def set_current_index(self, index: int) -> None:
        index = max(0, min(len(self._options) - 1, index))
        if index == self._index:
            return
        self._index = index
        self._thumb.to(float(index))
        self._describe()
        self.current_changed.emit(index)

    def _describe(self) -> None:
        self.setAccessibleDescription(f"Selected: {self._options[self._index]}")

    def _segment_width(self) -> float:
        return (self.width() - 2 * self.PAD) / len(self._options)

    def _index_at(self, x: float) -> int:
        i = int((x - self.PAD) // max(1.0, self._segment_width()))
        return max(0, min(len(self._options) - 1, i))

    def sizeHint(self) -> QSize:
        fm = QFontMetricsF(self.font())
        widest = max(fm.horizontalAdvance(o) for o in self._options)
        return QSize(math.ceil((widest + 28) * len(self._options) + 2 * self.PAD), self.HEIGHT)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        step = {
            Qt.Key.Key_Left: -1,
            Qt.Key.Key_Up: -1,
            Qt.Key.Key_Right: 1,
            Qt.Key.Key_Down: 1,
        }.get(Qt.Key(event.key()))
        if step is not None:
            self.set_current_index(self._index + step)
        elif event.key() == Qt.Key.Key_Home:
            self.set_current_index(0)
        elif event.key() == Qt.Key.Key_End:
            self.set_current_index(len(self._options) - 1)
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self._pressed_index = self._index_at(event.position().x())
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        pressed, self._pressed_index = self._pressed_index, -1
        if pressed >= 0 and self.isEnabled():
            if self._index_at(event.position().x()) == pressed:
                self.set_current_index(pressed)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.Type.EnabledChange:
            self._pressed_index = -1
            self._over = -1
        super().changeEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        i = self._index_at(event.position().x()) if self.isEnabled() else -1
        if i != self._over:
            self._over = i
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self._over = -1
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        m = material_of(self)
        en = self.isEnabled()
        r = QRectF(self.rect())
        radius = r.height() / 2
        paint_wash(p, r, radius, dim(m.fill, en))
        paint_control_rim(p, r, radius, m, 1.0 if en else 0.5)
        seg = self._segment_width()
        inner = r.height() - 2 * self.PAD
        pos = self._thumb.value
        thumb = QRectF(self.PAD + pos * seg, self.PAD, seg, inner)
        if m.dark:
            paint_wash(
                p, thumb, inner / 2, dim(rgba(255, 255, 255, 0.18), en), rgba(255, 255, 255, 0.20)
            )
        else:
            paint_wash(p, thumb.translated(0, 1), inner / 2, rgba(20, 30, 60, 0.10 if en else 0.04))
            paint_wash(
                p, thumb, inner / 2, dim(rgba(255, 255, 255, 0.96), en, 0.6), rgba(20, 30, 60, 0.08)
            )
        p.setFont(self.font())
        fm = QFontMetricsF(self.font())
        for i, text in enumerate(self._options):
            near = max(0.0, 1.0 - abs(pos - i))
            base = mix(m.text2, m.text, near)
            if i == self._over and near < 0.5:
                base = mix(base, m.text, 0.6)
            cell = QRectF(self.PAD + i * seg, 0, seg, r.height())
            label = fm.elidedText(text, Qt.TextElideMode.ElideRight, seg - 12)
            p.setPen(dim(base, en))
            p.drawText(cell, int(Qt.AlignmentFlag.AlignCenter.value), label)
        if self._ring:
            paint_focus_ring(p, r, radius, scaled_alpha(self._ring_color(m), 0.95))
        p.end()


class GlassCard(_ButtonBase):
    """Selectable row surface: a leading slot (rank badge), a body and a trailing slot.

    Selected cards get a brighter lens and an accent rim. Children that define set_active(bool)
    (RankBadge, ScoreBadge) follow the selection.
    """

    SLOTS = ("leading", "body", "trailing")

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._selected = False
        self._sel = FloatAnimator(self, 0.0, duration=Motion.BASE)
        self._slots: dict[str, QWidget | None] = dict.fromkeys(self.SLOTS)
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(Space.M, 10, Space.M, 10)
        self.row.setSpacing(Space.M)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

    @property
    def selected(self) -> bool:
        return self._selected

    def set_selected(self, selected: bool) -> None:
        if selected == self._selected:
            return
        self._selected = selected
        self._sel.to(1.0 if selected else 0.0)
        for w in self._slots.values():
            if w is not None and hasattr(w, "set_active"):
                w.set_active(selected)

    def set_leading(self, widget: QWidget | None) -> None:
        self._set_slot("leading", widget, 0, Qt.AlignmentFlag.AlignTop)

    def set_body(self, widget: QWidget | None) -> None:
        self._set_slot("body", widget, 1, Qt.AlignmentFlag(0))

    def set_trailing(self, widget: QWidget | None) -> None:
        self._set_slot("trailing", widget, 0, Qt.AlignmentFlag.AlignTop)

    def _set_slot(self, slot: str, widget: QWidget | None, stretch: int, align) -> None:
        old = self._slots[slot]
        if old is not None:
            self.row.removeWidget(old)
            old.setParent(None)
            old.deleteLater()
        self._slots[slot] = widget
        if widget is None:
            return
        index = sum(1 for s in self.SLOTS[: self.SLOTS.index(slot)] if self._slots[s] is not None)
        self.row.insertWidget(index, widget, stretch, align)
        if hasattr(widget, "set_active"):
            widget.set_active(self._selected)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        m = material_of(self)
        en = self.isEnabled()
        r = QRectF(self.rect())
        radius = min(float(Radius.CARD), r.height() / 2)
        sel = self._sel.value
        wash = mix(
            mix(m.fill, m.fill_hover, self._hover_value()), m.fill_press, self._press_value()
        )
        rest = self._hover_value() + self._press_value()
        paint_wash(p, r, radius, dim(scaled_alpha(wash, min(1.0, rest * 1.5)), en))
        if sel > 0.0:
            lens = rgba(255, 255, 255, 0.08) if m.dark else rgba(255, 255, 255, 0.55)
            paint_wash(p, r, radius, dim(scaled_alpha(lens, sel), en))
            paint_wash(
                p, r, radius, dim(with_alpha(m.accent, (0.14 if m.dark else 0.07) * sel), en)
            )
            sheen = QLinearGradient(r.topLeft(), QPointF(r.left(), r.top() + r.height() * 0.6))
            sheen.setColorAt(0, rgba(255, 255, 255, (0.12 if m.dark else 0.55) * sel))
            sheen.setColorAt(1, rgba(255, 255, 255, 0.0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(sheen)
            p.drawRoundedRect(r, radius, radius)
        if sel > 0.0:
            paint_wash(
                p,
                r,
                radius,
                QColor(0, 0, 0, 0),
                dim(with_alpha(m.accent, (0.55 if m.dark else 0.50) * sel), en),
            )
        if self._ring:
            paint_focus_ring(p, r, radius, scaled_alpha(m.text, 0.7))
        p.end()


class Label(QWidget):
    """Text in a type role and a tone, painted with the current material (follows light/dark)."""

    def __init__(
        self,
        text: str = "",
        role: str = "body",
        tone: str = "primary",
        *,
        wrap: bool = False,
        elide: bool = False,
        align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft,
        tabular: bool = False,
        parent: QWidget | None = None,
    ):
        check_tone(tone)
        super().__init__(parent)
        self._text = text
        self._role = role
        self._tone = tone
        self._color: QColor | None = None
        self._wrap = wrap
        self._elide = elide
        self._align = align
        self._tabular = tabular
        self._apply_font()
        self._apply_policy()
        self.setAccessibleName(text)

    def text(self) -> str:
        return self._text

    def set_text(self, text: str) -> None:
        if text == self._text:
            return
        self._text = text
        self.setAccessibleName(text)
        self.updateGeometry()
        self.update()

    setText = set_text

    def set_role(self, role: str) -> None:
        self._role = role
        self._apply_font()

    def set_tone(self, tone: str) -> None:
        self._tone = check_tone(tone)
        self._color = None
        self.update()

    def set_color(self, color: QColor | None) -> None:
        self._color = color
        self.update()

    def set_wrap(self, wrap: bool) -> None:
        self._wrap = wrap
        self._apply_policy()
        self.updateGeometry()
        self.update()

    def _apply_font(self) -> None:
        self.setFont(type_font(self._role, tabular=self._tabular))
        self.updateGeometry()
        self.update()

    def _apply_policy(self) -> None:
        # a Minimum policy would make the layout refuse to go below the hint, so an eliding
        # label has to be able to shrink
        grows = self._wrap or self._elide
        sp = QSizePolicy(
            QSizePolicy.Policy.Preferred if grows else QSizePolicy.Policy.Minimum,
            QSizePolicy.Policy.Minimum,
        )
        sp.setHeightForWidth(self._wrap)
        self.setSizePolicy(sp)

    def _display(self) -> str:
        return self._text

    def _flag_bits(self) -> int:
        bits = self._align.value | Qt.AlignmentFlag.AlignVCenter.value
        if self._wrap:
            bits = (
                self._align.value | Qt.AlignmentFlag.AlignTop.value | Qt.TextFlag.TextWordWrap.value
            )
        return bits

    def hasHeightForWidth(self) -> bool:
        return self._wrap

    def heightForWidth(self, w: int) -> int:
        if not self._wrap:
            return self.sizeHint().height()
        rect = QFontMetricsF(self.font()).boundingRect(
            QRectF(0, 0, max(1, w), 100000), self._flag_bits(), self._display()
        )
        return math.ceil(rect.height())

    def sizeHint(self) -> QSize:
        fm = QFontMetricsF(self.font())
        adv = fm.horizontalAdvance(self._display())
        if self._wrap:
            w = math.ceil(min(adv, 320.0))
            return QSize(w, self.heightForWidth(w))
        return QSize(math.ceil(adv) + 1, math.ceil(fm.height()))

    def minimumSizeHint(self) -> QSize:
        fm = QFontMetricsF(self.font())
        if self._wrap:
            return QSize(24, math.ceil(fm.height()))
        if self._elide:
            return QSize(16, math.ceil(fm.height()))
        return self.sizeHint()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        m = material_of(self)
        color = self._color or tone_color(m, self._tone)
        p.setFont(self.font())
        p.setPen(dim(color, self.isEnabled(), 0.5))
        text = self._display()
        r = QRectF(self.rect())
        if self._elide and not self._wrap:
            text = QFontMetricsF(self.font()).elidedText(
                text, Qt.TextElideMode.ElideRight, r.width()
            )
        p.drawText(r, self._flag_bits(), text)
        p.end()


class SectionLabel(Label):
    """Small tracked caps above a group of rows."""

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(text, "caption", "tertiary", parent=parent)

    def _apply_font(self) -> None:
        f = type_font("caption")
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.7)
        self.setFont(f)
        self.updateGeometry()
        self.update()

    def _display(self) -> str:
        return self._text.upper()


class Glyph(QWidget):
    """A named icon as a widget."""

    def __init__(
        self,
        name: str,
        size: int = 16,
        tone: str = "secondary",
        weight: float = 1.8,
        parent: QWidget | None = None,
    ):
        check_icon(name)
        check_tone(tone)
        super().__init__(parent)
        self._name = name
        self._tone = tone
        self._weight = weight
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_name(self, name: str) -> None:
        self._name = check_icon(name)
        self.update()

    def set_tone(self, tone: str) -> None:
        self._tone = check_tone(tone)
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        m = material_of(self)
        paint_icon(
            p,
            self._name,
            QRectF(self.rect()),
            dim(tone_color(m, self._tone), self.isEnabled(), 0.5),
            self._weight,
        )
        p.end()


class Spinner(QWidget):
    """Arc spinner. It runs only while shown and not under reduce-motion."""

    def __init__(
        self,
        size: int = 16,
        tone: str = "accent",
        weight: float = 2.2,
        parent: QWidget | None = None,
    ):
        check_tone(tone)
        super().__init__(parent)
        self._tone = tone
        self._weight = weight
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAccessibleName("Working")
        self._loop = LoopAnimator(self, 900)
        self._loop.start()

    @property
    def running(self) -> bool:
        return self._loop.running

    def start(self) -> None:
        self._loop.start()

    def stop(self) -> None:
        self._loop.stop()

    def set_tone(self, tone: str) -> None:
        self._tone = check_tone(tone)
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        m = material_of(self)
        r = QRectF(self.rect())
        p.translate(r.center())
        p.rotate(self._loop.phase * 360.0)
        p.translate(-r.center())
        paint_icon(p, "spinner", r, tone_color(m, self._tone), self._weight)
        p.end()


class Divider(QWidget):
    """One device pixel line."""

    def __init__(self, vertical: bool = False, inset: int = 0, parent: QWidget | None = None):
        super().__init__(parent)
        self._vertical = vertical
        self._inset = inset
        if vertical:
            self.setFixedWidth(1)
            self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        else:
            self.setFixedHeight(1)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        m = material_of(self)
        t = 1.0 / max(1.0, p.device().devicePixelRatioF())
        r = QRectF(self.rect())
        if self._vertical:
            r = QRectF(
                r.center().x() - t / 2, r.top() + self._inset, t, r.height() - 2 * self._inset
            )
        else:
            r = QRectF(
                r.left() + self._inset, r.center().y() - t / 2, r.width() - 2 * self._inset, t
            )
        p.fillRect(r, m.divider)
        p.end()


class StatusPill(QWidget):
    """Dot and label in a tinted capsule. Only the busy tone moves, and only when allowed."""

    def __init__(self, text: str = "", tone: str = "idle", parent: QWidget | None = None):
        super().__init__(parent)
        self._text = text
        self._tone = "idle"
        self.setFont(type_font("caption"))
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(22)
        self._loop = LoopAnimator(self, 1800)
        self.set_status(text, tone)

    def text(self) -> str:
        return self._text

    @property
    def tone(self) -> str:
        return self._tone

    @property
    def animating(self) -> bool:
        return self._loop.running

    def set_status(self, text: str, tone: str) -> None:
        if tone not in TONES:
            raise ValueError(f"tone must be one of {TONES}")
        self._text = text
        self._tone = tone
        self.setAccessibleName(text)
        self.setAccessibleDescription(f"Status: {tone}")
        if tone == "busy":
            self._loop.start()
        else:
            self._loop.stop()
        self.updateGeometry()
        self.update()

    def set_text(self, text: str) -> None:
        self.set_status(text, self._tone)

    def set_tone(self, tone: str) -> None:
        self.set_status(self._text, tone)

    def sizeHint(self) -> QSize:
        fm = QFontMetricsF(self.font())
        return QSize(math.ceil(fm.horizontalAdvance(self._text) + 10 + 7 + 6 + 11), 22)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def _tone_color(self, m: Material) -> QColor:
        return {
            "ok": m.success,
            "busy": m.accent,
            "warn": m.warning,
            "idle": m.text3,
            "error": m.danger,
        }[self._tone]

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        m = material_of(self)
        c = self._tone_color(m)
        r = QRectF(self.rect())
        radius = r.height() / 2
        idle = self._tone == "idle"
        paint_wash(
            p,
            r,
            radius,
            with_alpha(c, (0.10 if idle else 0.16) if m.dark else (0.09 if idle else 0.12)),
            with_alpha(c, 0.0 if idle else 0.30),
        )
        if idle:
            paint_control_rim(p, r, radius, m, 0.8)
        dot = 7.0
        alpha = 1.0
        if self._loop.running:
            alpha = 0.42 + 0.58 * self._loop.wave
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(scaled_alpha(c, alpha))
        p.drawEllipse(QRectF(10, r.center().y() - dot / 2, dot, dot))
        p.setFont(self.font())
        p.setPen(m.text if not idle else m.text2)
        p.drawText(
            QRectF(10 + dot + 6, 0, r.width() - 10 - dot - 6 - 10, r.height()),
            flags(Qt.AlignmentFlag.AlignLeft, Qt.AlignmentFlag.AlignVCenter),
            self._text,
        )
        p.end()


class KeyCap(QWidget):
    """One key of a keyboard shortcut."""

    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._text = text
        self.setFont(type_font("caption"))
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(21)
        self.setAccessibleName(text)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def text(self) -> str:
        return self._text

    def sizeHint(self) -> QSize:
        w = QFontMetricsF(self.font()).horizontalAdvance(self._text)
        return QSize(max(22, math.ceil(w) + 14), 21)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        m = material_of(self)
        full = QRectF(self.rect())
        cap = QRectF(full.left(), full.top(), full.width(), full.height() - 1.5)
        lip = QPainterPath()
        lip.addRoundedRect(full.adjusted(0, 1.5, 0, 0), 6, 6)
        face = QPainterPath()
        face.addRoundedRect(cap, 6, 6)
        p.fillPath(lip.subtracted(face), rgba(0, 0, 0, 0.30) if m.dark else rgba(20, 30, 60, 0.20))
        paint_wash(p, cap, 6, rgba(255, 255, 255, 0.13) if m.dark else rgba(255, 255, 255, 0.85))
        paint_control_rim(p, cap, 6, m, 0.9)
        p.setFont(self.font())
        p.setPen(m.text2)
        p.drawText(cap, int(Qt.AlignmentFlag.AlignCenter.value), self._text)
        p.end()


def split_keys(sequence: str) -> list[str]:
    """The keys of "Ctrl+Shift+P"; a plus at the very end ("Ctrl++") is the key itself."""
    text = sequence.strip()
    plus = text == "+" or text.endswith("++")
    if plus:
        text = text[:-1]
    keys = [k.strip() for k in text.split("+") if k.strip()]
    return [*keys, "+"] if plus else keys


class KeyCombo(QWidget):
    """A shortcut such as "Ctrl+Shift+P" as a row of KeyCaps."""

    def __init__(self, sequence: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        self._lay = lay
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.set_sequence(sequence)

    def set_sequence(self, sequence: str) -> None:
        while self._lay.count():
            item = self._lay.takeAt(0)
            cap = item.widget()
            if cap is not None:
                cap.setParent(None)
                cap.deleteLater()
        self._sequence = sequence
        for key in split_keys(sequence):
            self._lay.addWidget(KeyCap(key))
        self.setAccessibleName(sequence)

    @property
    def sequence(self) -> str:
        return self._sequence


class RankBadge(QWidget):
    """Circle with the rank number; filled with the accent when active."""

    SIZE = 24

    def __init__(self, rank: int = 1, active: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self._rank = rank
        self._active = active
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setFont(type_font("label", 0.92, tabular=True))
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAccessibleName(f"Rank {rank}")
        self._fill = FloatAnimator(self, 1.0 if active else 0.0, duration=Motion.BASE)

    @property
    def rank(self) -> int:
        return self._rank

    def set_rank(self, rank: int) -> None:
        self._rank = rank
        self.setAccessibleName(f"Rank {rank}")
        self.update()

    def set_active(self, active: bool) -> None:
        self._active = active
        self._fill.to(1.0 if active else 0.0)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        m = material_of(self)
        t = self._fill.value
        r = QRectF(self.rect())
        paint_wash(p, r, r.height() / 2, mix(m.fill_hover, m.accent, t))
        paint_control_rim(p, r, r.height() / 2, m, 1.0 - t)
        p.setFont(self.font())
        p.setPen(mix(m.text, m.on_accent, t))
        p.drawText(
            r.adjusted(0, -0.5, 0, -0.5), int(Qt.AlignmentFlag.AlignCenter.value), str(self._rank)
        )
        p.end()


class ScoreBadge(QWidget):
    """Large tabular score with a small unit, right aligned so a column of cards lines up."""

    def __init__(
        self,
        score: int | None = None,
        unit: str = "pts",
        active: bool = False,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._score = score
        self._unit = unit
        self._active = active
        self.setFont(type_font("score", tabular=True))
        self._unit_font = type_font("caption")
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._sync_name()

    @property
    def score(self) -> int | None:
        return self._score

    def set_score(self, score: int | None) -> None:
        self._score = score
        self._sync_name()
        self.updateGeometry()
        self.update()

    def set_active(self, active: bool) -> None:
        self._active = active
        self.update()

    def _sync_name(self) -> None:
        self.setAccessibleName("" if self._score is None else f"{self._score} {self._unit}")

    def _number(self) -> str:
        return "-" if self._score is None else str(self._score)

    def _widths(self) -> tuple[float, float]:
        num = QFontMetricsF(self.font()).horizontalAdvance(self._number())
        floor = QFontMetricsF(self.font()).horizontalAdvance("000")
        return max(num, floor), QFontMetricsF(self._unit_font).horizontalAdvance(self._unit)

    def sizeHint(self) -> QSize:
        n, u = self._widths()
        return QSize(math.ceil(n + 3 + u) + 1, math.ceil(QFontMetricsF(self.font()).height()))

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        m = material_of(self)
        r = QRectF(self.rect())
        n_w, u_w = self._widths()
        baseline = (
            r.top()
            + QFontMetricsF(self.font()).ascent()
            + (r.height() - QFontMetricsF(self.font()).height()) / 2
        )
        x_unit = r.right() - u_w
        p.setFont(self._unit_font)
        p.setPen(m.text3 if not self._active else m.text2)
        p.drawText(QPointF(x_unit, baseline), self._unit)
        p.setFont(self.font())
        p.setPen(mix(m.text, m.accent, 0.55) if self._active else m.text)
        num = self._number()
        adv = QFontMetricsF(self.font()).horizontalAdvance(num)
        p.drawText(QPointF(x_unit - 3 - adv, baseline), num)
        p.end()


class Banner(QWidget):
    """Inline notice: icon, text, an optional action and a dismiss button."""

    action_clicked = Signal()
    dismissed = Signal()

    def __init__(
        self,
        text: str = "",
        kind: str = "info",
        *,
        action_text: str = "",
        dismissible: bool = True,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._kind = "info"
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 8, 8)
        lay.setSpacing(10)
        self.glyph = Glyph("info", 16, "accent")
        self.label = Label(text, "footnote", "primary", wrap=True)
        self.action = GlassButton(action_text, size="s")
        self.close_button = IconButton("close", "Dismiss", size="s", variant="bare")
        lay.addWidget(self.glyph, 0, Qt.AlignmentFlag.AlignTop)
        lay.addWidget(self.label, 1)
        lay.addWidget(self.action, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self.action.clicked.connect(self.action_clicked)
        self.close_button.clicked.connect(self.dismiss)
        self.set_kind(kind)
        self.set_action(action_text)
        self.set_dismissible(dismissible)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

    @property
    def kind(self) -> str:
        return self._kind

    def text(self) -> str:
        return self.label.text()

    def set_text(self, text: str) -> None:
        self.label.set_text(text)
        self.setAccessibleName(text)

    def set_kind(self, kind: str) -> None:
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}")
        self._kind = kind
        self.glyph.set_name("info" if kind == "info" else "alert-triangle")
        self.glyph.set_tone({"info": "accent", "warning": "warning", "error": "danger"}[kind])
        self.setAccessibleDescription(kind)
        self.update()

    def set_action(self, text: str) -> None:
        self.action.setText(text)
        self.action.setVisible(bool(text))
        self.action.updateGeometry()

    def set_dismissible(self, dismissible: bool) -> None:
        self.close_button.setVisible(dismissible)

    def dismiss(self) -> None:
        self.hide()
        self.dismissed.emit()

    def _tone(self, m: Material) -> QColor:
        return {"info": m.accent, "warning": m.warning, "error": m.danger}[self._kind]

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        m = material_of(self)
        c = self._tone(m)
        paint_wash(
            p,
            QRectF(self.rect()),
            Radius.CONTROL,
            with_alpha(c, 0.15 if m.dark else 0.11),
            with_alpha(c, 0.32 if m.dark else 0.30),
        )
        p.end()


class GlassWindow(QWidget):
    """Frameless, translucent, always-on-top window painted as one pane of glass.

    The window is SHADOW_BLEED larger than the glass on every side; lay content out with
    content_margins() and place the glass with move_glass_to() / glass_geometry();
    moved_by_user carries the glass's top-left, the point move_glass_to() takes.

    `backdrop` puts a native blur behind the glass. Set it on an instance, or on the class as
    native_backdrop() or staticmethod(fn). It is callable(win_id, radius, dark, inset) -> bool
    with radius and inset in points (inset is SHADOW_BLEED); a callable that takes only the
    first three, or an adapter object with apply_backdrop, works too. It is asked again on every
    resize and material change. Without it, or when it returns False, the glass is drawn in its
    more opaque solid form.
    """

    moved_by_user = Signal(QPoint)
    material_changed = Signal(object)

    backdrop = None
    on_shown = None

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        material: Material = DARK,
        elevation: Elevation = FLOATING,
        radius: float = Radius.PANEL,
        take_focus: bool = False,
        draggable: bool = True,
    ):
        super().__init__(
            parent,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)
        if not take_focus:
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self._elevation = elevation
        self._radius = float(radius)
        self._draggable = draggable
        self._target = material
        self._from = material
        self._shown = material
        self._strength = 1.0
        self._strength_from = 1.0
        self._strength_to = 1.0
        self._solid = False
        self._native = False
        self._extra_qss = ""
        self._appearance: Appearance | None = None
        self._cache: QPixmap | None = None
        self._cache_key: tuple | None = None
        self._drag_offset: QPoint | None = None
        self._drag_start = QPoint()
        self._dragging = False
        self.user_moved = False
        self._fade = FloatAnimator(
            self,
            1.0,
            duration=Motion.BASE,
            easing=QEasingCurve.Type.InOutSine,
            on_change=self._on_fade,
        )
        self._strength_fade = FloatAnimator(
            self,
            1.0,
            duration=Motion.SLOW,
            easing=QEasingCurve.Type.InOutSine,
            on_change=self._on_strength_fade,
        )
        self._apply_style(material)

    @property
    def glass_material(self) -> Material:
        return self._shown

    @property
    def material(self) -> Material:
        return self._target

    @property
    def strength(self) -> float:
        return self._strength_to

    @property
    def native_blur(self) -> bool:
        return self._native and not self._solid

    @property
    def radius(self) -> float:
        return self._radius

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

    def bind(self, appearance: Appearance) -> None:
        if self._appearance is not None:
            self._appearance.appearance_changed.disconnect(self.set_material)
            self._appearance.strength_changed.disconnect(self.set_strength)
        self._appearance = appearance
        self.set_material(appearance.material)
        self.set_strength(appearance.strength)
        appearance.appearance_changed.connect(self.set_material)
        appearance.strength_changed.connect(self.set_strength)

    def set_solid(self, solid: bool) -> None:
        """Force the opaque form (reduce transparency, or no native blur wanted)."""
        solid = bool(solid)
        if solid == self._solid:
            return
        self._solid = solid
        if self.isVisible():
            self._ask_backdrop()
        else:
            self._invalidate()
        self.update()

    def set_extra_qss(self, qss: str) -> None:
        """Style rules appended to the window's own; do not call setStyleSheet on the window."""
        self._extra_qss = qss
        self._apply_style(self._target)

    def glass_rect(self) -> QRectF:
        b = float(SHADOW_BLEED)
        return QRectF(b, b, self.width() - 2 * b, self.height() - 2 * b)

    def glass_geometry(self) -> QRect:
        """The glass in screen coordinates."""
        top_left = self.mapToGlobal(QPoint(SHADOW_BLEED, SHADOW_BLEED))
        return QRect(
            top_left,
            QSize(self.width() - 2 * SHADOW_BLEED, self.height() - 2 * SHADOW_BLEED),
        )

    def move_glass_to(self, x: int | QPoint, y: int | None = None) -> None:
        pt = x if isinstance(x, QPoint) else QPoint(x, y or 0)
        self.move(pt.x() - SHADOW_BLEED, pt.y() - SHADOW_BLEED)

    def set_glass_width(self, width: int) -> None:
        self.setFixedWidth(width + 2 * SHADOW_BLEED)

    def content_margins(self, *pad: int) -> QMargins:
        """Layout margins that keep content inside the glass: 0, 1, 2 (x, y) or 4 paddings."""
        if len(pad) == 0:
            pad = (Space.L,)
        if len(pad) == 1:
            left = top = right = bottom = pad[0]
        elif len(pad) == 2:
            left = right = pad[0]
            top = bottom = pad[1]
        elif len(pad) == 4:
            left, top, right, bottom = pad
        else:
            raise ValueError("content_margins takes 0, 1, 2 or 4 values")
        k = SHADOW_BLEED
        return QMargins(k + left, k + top, k + right, k + bottom)

    def _apply_style(self, m: Material) -> None:
        self.setPalette(palette_for(m))
        self.setStyleSheet(build_qss(m) + self._extra_qss)

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

    def _ask_backdrop(self) -> None:
        ok = False
        if self.backdrop is not None and not self._solid:
            try:
                ok = call_backdrop(
                    self.backdrop,
                    int(self.winId()),
                    self._radius,
                    self._target.dark,
                    float(SHADOW_BLEED),
                )
            except Exception:
                ok = False
        self._native = ok
        self._invalidate()

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

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if (
            self._draggable
            and event.button() == Qt.MouseButton.LeftButton
            and self.glass_rect().contains(event.position())
        ):
            self._drag_start = event.globalPosition().toPoint()
            self._drag_offset = self._drag_start - self.frameGeometry().topLeft()
            self._dragging = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is None:
            super().mouseMoveEvent(event)
            return
        here = event.globalPosition().toPoint()
        if not self._dragging and (here - self._drag_start).manhattanLength() < 3:
            return
        self._dragging = True
        self.move(here - self._drag_offset)
        self.user_moved = True
        self.moved_by_user.emit(self.pos() + QPoint(SHADOW_BLEED, SHADOW_BLEED))
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_offset = None
        self._dragging = False
        super().mouseReleaseEvent(event)


__all__ = [
    "BUTTON_HEIGHT",
    "FOCUS_WIDTH",
    "ICON_BUTTON_SIZE",
    "KINDS",
    "TONES",
    "VARIANTS",
    "Banner",
    "Divider",
    "GlassButton",
    "GlassCard",
    "GlassWindow",
    "Glyph",
    "IconButton",
    "KeyCap",
    "KeyCombo",
    "Label",
    "RankBadge",
    "ScoreBadge",
    "SectionLabel",
    "Segmented",
    "Spinner",
    "StatusPill",
    "Switch",
    "blend_materials",
    "dim",
    "paint_control_rim",
    "paint_focus_ring",
    "tone_color",
]

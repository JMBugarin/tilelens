"""Small painting helpers shared by the components and the widget style."""

from __future__ import annotations

from dataclasses import fields

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen

from tilelens.ui.design.tokens import Material, mix, rgba, with_alpha

FOCUS_WIDTH = 2.0


def flags(*parts) -> int:
    out = 0
    for part in parts:
        out |= part.value
    return out


def dim(color: QColor, enabled: bool = True, factor: float = 0.4) -> QColor:
    return color if enabled else with_alpha(color, color.alphaF() * factor)


def scaled_alpha(color: QColor, k: float) -> QColor:
    return with_alpha(color, color.alphaF() * k)


def blend_materials(a: Material, b: Material, t: float) -> Material:
    """Colour-wise blend of two materials. The shadow snaps to b so its cached tiles stay few."""
    if t <= 0:
        return a
    if t >= 1:
        return b
    vals = {}
    for f in fields(Material):
        va, vb = getattr(a, f.name), getattr(b, f.name)
        if f.name != "shadow" and isinstance(va, QColor):
            vals[f.name] = mix(va, vb, t)
        else:
            vals[f.name] = vb if (f.name == "shadow" or t >= 0.5) else va
    return Material(**vals)


LABEL_TONES = (
    "primary",
    "secondary",
    "tertiary",
    "accent",
    "success",
    "warning",
    "danger",
    "on-accent",
)


def check_tone(tone: str) -> str:
    """The tone itself, or ValueError: a typo should fail where it is written, not on paint."""
    if tone not in LABEL_TONES:
        raise ValueError(f"tone must be one of {LABEL_TONES}")
    return tone


def tone_color(m: Material, tone: str) -> QColor:
    return {
        "primary": m.text,
        "secondary": m.text2,
        "tertiary": m.text3,
        "accent": m.accent,
        "success": m.success,
        "warning": m.warning,
        "danger": m.danger,
        "on-accent": m.on_accent,
    }[tone]


def paint_control_rim(
    p: QPainter, rect: QRectF, radius: float, m: Material, strength: float = 1.0
) -> None:
    """Hairline for a control resting in glass: lit along the top, quiet below."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    inset = rect.adjusted(0.5, 0.5, -0.5, -0.5)
    grad = QLinearGradient(inset.topLeft(), inset.bottomLeft())
    if m.dark:
        grad.setColorAt(0, rgba(255, 255, 255, 0.26 * strength))
        grad.setColorAt(1, rgba(255, 255, 255, 0.05 * strength))
    else:
        grad.setColorAt(0, rgba(255, 255, 255, 0.95 * strength))
        grad.setColorAt(1, scaled_alpha(m.divider, 1.2 * strength))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(grad, 1.0))
    p.drawRoundedRect(inset, max(0.0, radius - 0.5), max(0.0, radius - 0.5))
    p.restore()


def paint_focus_ring(p: QPainter, rect: QRectF, radius: float, color: QColor) -> None:
    """Keyboard focus: a stroke just inside the control's bounds."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(color, FOCUS_WIDTH))
    inset = rect.adjusted(1, 1, -1, -1)
    p.drawRoundedRect(inset, max(0.0, radius - 1), max(0.0, radius - 1))
    p.restore()


__all__ = [
    "FOCUS_WIDTH",
    "LABEL_TONES",
    "blend_materials",
    "check_tone",
    "dim",
    "flags",
    "paint_control_rim",
    "paint_focus_ring",
    "scaled_alpha",
    "tone_color",
]

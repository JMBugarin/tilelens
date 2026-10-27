"""The region selector: the dimmed layer, and a drag around the board and rack."""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QMouseEvent

from ui_gallery import ctx
from ui_gallery.registry import register


def _mouse(kind, x: float, y: float) -> QMouseEvent:
    return QMouseEvent(
        kind,
        QPointF(x, y),
        QPointF(x, y),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


def _selector(start: tuple[float, float] | None = None, end: tuple[float, float] | None = None):
    from tilelens.ui.region_select import RegionSelector

    sel = RegionSelector()
    if start is not None and end is not None:
        sel.mousePressEvent(_mouse(QMouseEvent.Type.MouseButtonPress, *start))
        sel.mouseMoveEvent(_mouse(QMouseEvent.Type.MouseMove, *end))
    return sel


def _around_board() -> tuple[tuple[float, float], tuple[float, float]]:
    sc = ctx.scenario()
    a = sc.board_rect.topLeft()
    b = sc.rack_rect.bottomRight()
    return (a.x() - 14, a.y() - 14), (b.x() + 14, b.y() + 14)


@register("region/selector", kind="overlay")
def selector():
    """The dimmed layer before any drag, with the hint"""
    return _selector()


@register("region/selector-drag", kind="overlay")
def selector_drag():
    """Mid-drag around the board and rack, with the size readout"""
    return _selector(*_around_board())


@register("region/selector-small", kind="overlay")
def selector_small():
    """A drag that is too small to keep"""
    return _selector((420, 260), (440, 280))


@register("region/selector-corner", kind="overlay")
def selector_corner():
    """A drag that ends at the bottom edge of the screen, so the readout moves above it"""
    sc = ctx.scenario()
    w, h = sc.size_pt
    return _selector((w * 0.18, h * 0.45), (w * 0.82, h - 2))

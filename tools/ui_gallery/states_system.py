"""Region selector, tray menu and tooltips."""

from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QWidget

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


def _selector(drag: bool):
    from tilelens.ui.region_select import RegionSelector

    sel = RegionSelector()
    if drag:
        sel.mousePressEvent(_mouse(QMouseEvent.Type.MouseButtonPress, 150, 60))
        sel.mouseMoveEvent(_mouse(QMouseEvent.Type.MouseMove, 700, 500))
    return sel


@register("system/region-selector", kind="overlay")
def region_selector():
    """The dimmed layer before any drag"""
    return _selector(False)


@register("system/region-selector-drag", kind="overlay")
def region_selector_drag():
    """Mid-drag around the board"""
    return _selector(True)


@register("system/tray-menu", kind="dialog")
def tray_menu():
    """Text of the tray menu (the real one is drawn by the operating system)"""
    from tilelens.ui.app_main import App

    shell = SimpleNamespace(
        session=SimpleNamespace(toggle_pause=lambda: None, rescan=lambda: None),
        show_settings=lambda: None,
    )
    App._make_tray(shell)
    ctx.defer(shell.tray.hide)
    shell._menu.adjustSize()
    return shell._menu


def _tip(owner, anchor: QWidget, text: str | None = None):
    return ctx.tooltip(owner, anchor, text)


@register("system/tooltip-panel-calibrate", kind="window")
def tooltip_panel_calibrate():
    """Calibrate button in the panel"""
    panel = ctx.make_panel(ctx.snapshot("ready"))
    return _tip(panel, panel.cal_btn)


@register("system/tooltip-panel-settings", kind="window")
def tooltip_panel_settings():
    """Settings button in the panel"""
    panel = ctx.make_panel(ctx.snapshot("ready"))
    return _tip(panel, panel.settings_btn)


@register("system/tooltip-main-region", kind="dialog")
def tooltip_main_region():
    """Why Select region is disabled"""
    win = ctx.make_main_window(None, profile="auto")
    return _tip(win, win.region_btn)


@register("system/tooltip-main-overlay", kind="dialog")
def tooltip_main_overlay():
    """What the overlay modes mean"""
    win = ctx.make_main_window(ctx.demo_snapshot())
    return _tip(win, win.mode)


@register("system/tooltip-main-dump", kind="dialog")
def tooltip_main_dump():
    """Save debug dump button"""
    win = ctx.make_main_window(ctx.demo_snapshot())
    return _tip(win, win.dump_btn)


@register("system/tooltip-corrections-cell", kind="dialog")
def tooltip_corrections_cell():
    """Why a square needs a look"""
    dlg = ctx.make_corrections_dialog()
    cell = next(e for e in dlg.edits.values() if e.toolTip())
    return _tip(dlg, cell)

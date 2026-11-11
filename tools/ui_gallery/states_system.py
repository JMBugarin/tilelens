"""Tray menu and tooltips. The region selector lives in states_region."""

from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtCore import QRect
from PySide6.QtWidgets import QWidget

from ui_gallery import ctx
from ui_gallery.registry import register


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


@register("system/tooltip-panel-pause", kind="window")
def tooltip_panel_pause():
    """Pause button in the panel"""
    panel = ctx.make_panel(ctx.snapshot("ready"))
    return _tip(panel, panel.pause_btn)


@register("system/tooltip-panel-more", kind="window")
def tooltip_panel_more():
    """Overflow button in the panel"""
    panel = ctx.make_panel(ctx.snapshot("ready"))
    return _tip(panel, panel.more_btn)


@register("system/tooltip-main-overlay", kind="dialog")
def tooltip_main_overlay():
    """What the overlay modes mean"""
    from ui_gallery.states_window import window

    win = window(ctx.demo_snapshot())
    win.scroll.ensureWidgetVisible(win.mode, 0, 120)
    return _tip(win, win.mode)


@register("system/tooltip-main-dump", kind="dialog")
def tooltip_main_dump():
    """Save debug dump button"""
    from ui_gallery.states_window import window

    win = window(ctx.demo_snapshot(), open_sections=("diagnostics",))
    win.scroll.ensureWidgetVisible(win.dump_btn, 0, 160)
    return _tip(win, win.dump_btn)


@register("system/tooltip-corrections-cell", kind="dialog")
def tooltip_corrections_cell():
    """Why a square needs a look"""
    dlg = ctx.make_corrections_dialog()
    dlg.available_geometry = lambda: QRect(0, 0, 1440, 900)
    cell = next(e for e in dlg.edits.values() if e.toolTip())
    return _tip(dlg, cell)

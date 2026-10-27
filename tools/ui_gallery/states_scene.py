"""The panel and the preview together on the real game, as a player sees them."""

from __future__ import annotations

import math

from tilelens.state.memory import Coverage
from ui_gallery import ctx
from ui_gallery.registry import register

GAP = 16  # points between the board and the glass of the panel


def _glass_inset(panel) -> int:
    """Points of shadow room around the glass of a glass window; 0 for a plain window."""
    rect = getattr(panel, "glass_rect", None)
    return round(rect().x()) if callable(rect) else 0


def _scene(snap: str, selected: int = 0, move: int | None = 0, **scene):
    """The overlay and the panel placed the way the session places them: beside the board."""
    from tilelens.ui.panel_placement import panel_scale

    panel = ctx.make_panel(ctx.snapshot(snap), selected=selected, framed=scene.get("framed", False))
    base = ctx.scenario()
    panel.set_scale(panel_scale(base.size_pt[1]))
    panel.ensurePolished()
    glass_w, glass_h = panel.glass_size()
    # the game's own background is extended on the right until the panel fits beside the board
    need = base.board_rect.right() + GAP + glass_w + ctx.PANEL_INSET[0]
    wide = max(0, math.ceil((need - base.size_pt[0]) / 8) * 8)
    sc = ctx.scenario(wide)
    screen = (0, 0, sc.size_pt[0], max(sc.size_pt[1], glass_h + 2 * GAP))
    found = panel.auto_place(sc.region, sc.board_rect, sc.rack_rect, screen_rect=screen)
    overlay = ctx.make_overlay(ctx.overlay_scene(move=move, wide_pt=wide, **scene))
    assert found.overlap == 0, f"the panel would cover the board ({found})"
    return ctx.stage(sc.size_pt, (overlay, (0, 0), sc.size_pt), (panel, (panel.x(), panel.y())))


@register("scene/play", kind="overlay")
def play():
    """Tracking: the best play shown on the board, the panel beside it"""
    return _scene("ready")


@register("scene/play-second", kind="overlay")
def play_second():
    """The second play selected: blank tile and a crossing word"""
    return _scene("ready", selected=1, move=1)


@register("scene/held", kind="overlay")
def held():
    """A play held while the player places its tiles"""
    return _scene("held", selected=1, move=1)


@register("scene/partial", kind="overlay")
def partial():
    """Part of the board hidden: dashed frame and a note in the panel"""
    return _scene("ready-partial", coverage=Coverage.PARTIAL, guide_alpha=1.0)


@register("scene/unsure", kind="overlay")
def unsure():
    """An unclear rack tile: the panel asks, the rack outline turns amber"""
    return _scene("unsure-rack", rack_read=(6, 7), guide_alpha=1.0, asking=True)

"""The floating results panel in every state the tracker can put it in."""

from __future__ import annotations

import dataclasses

from PySide6.QtCore import QPoint

from tilelens.solver.movegen import SearchResult
from tilelens.state.controller import Snapshot, Status
from tilelens.ui.design import SHADOW_BLEED
from ui_gallery import ctx
from ui_gallery.registry import register


def _panel(name: str, snap: str, doc: str, **kwargs) -> None:
    def build():
        return _build(ctx.snapshot(snap), **kwargs)

    build.__doc__ = doc
    register(f"panel/{name}", kind="window")(build)


def _build(
    snap,
    compact: bool = False,
    setup: tuple | None = None,
    scale: float | None = None,
    **kwargs,
):
    panel = ctx.make_panel(snap, **kwargs)
    if setup is not None:
        panel.set_setup(*setup)
    if scale is not None:
        panel.set_scale(scale)
    if compact:
        panel.set_compact(True)
    return panel


def _plays(words: tuple[str, ...], scores: tuple[int, ...]) -> Snapshot:
    base = ctx.snapshot("ready")
    moves = [
        dataclasses.replace(m, word=w, score=s)
        for m, w, s in zip(ctx.featured_moves(), words, scores, strict=False)
    ]
    return dataclasses.replace(base, result=SearchResult(moves, True, 4180, 0.041))


_panel("setup", "setup", "First run: nothing checked yet", age=None)
_panel(
    "setup-permission",
    "setup",
    "First run: Screen Recording is off",
    age=None,
    setup=(False, True, None),
)
_panel(
    "setup-game",
    "setup",
    "First run: capture and word list are fine, the game is not in view",
    age=None,
    setup=(True, True, None),
)
_panel(
    "setup-words",
    "setup",
    "First run: no word list",
    age=None,
    setup=(True, False, None),
)
_panel(
    "setup-note",
    "setup",
    "Calibration trouble the player must act on",
    age=None,
    note="Confirm rack tile 3: open Calibrate and check the letters",
)
_panel("reading", "reading", "Reading the rack: three still placeholders", age=None)
_panel("aligning", "aligning", "Finding the board", age=None)
_panel("solving", "solving", "Searching for plays", age=None)
_panel("ready", "ready", "Three plays, the best one selected")
_panel("ready-second", "ready", "The second play selected", selected=1)
_panel("ready-third", "ready", "The third play selected", selected=2)
_panel("ready-old", "ready", "A result that is several seconds old", age=12.0)
_panel("ready-tracking", "ready-tracking", "Part of the board is out of view but remembered")
_panel("ready-partial", "ready-partial", "Some squares are unknown: zoom out")
_panel(
    "ready-partial-framed",
    "ready-partial",
    "Unknown squares are behind the game's own panels",
    framed=True,
)
_panel("ready-incomplete", "ready-incomplete", "The search was cut short")
_panel("one-play", "one-play", "Only one legal play")
_panel("no-move", "no-move", "No legal placement, and why")
_panel("placing", "placing", "The player is placing tiles")
_panel("updating", "updating", "New squares seen, results refreshing")
_panel("notice", "notice", "A letter was settled by the word list")
_panel("unsure-rack", "unsure-rack", "An unclear rack tile with letter keys")
_panel("unsure-cell", "unsure-cell", "An unclear board square with letter keys")
_panel("held", "held", "A play held while the tiles are placed", selected=1)
_panel("check", "check", "Tiles that need a human look")
_panel("check-scale", "check-scale", "The board changed size")
_panel("paused", "paused", "Tracking paused")
_panel("obscured", "obscured", "The board cannot be found")
_panel("covered", "covered", "Something covers the game")
_panel("permission", "permission", "Screen recording is not allowed: an action to fix it")
_panel("scale-small", "ready", "A small game window: the panel shrinks", scale=0.85)
_panel("scale-large", "ready", "A large display: the panel grows", scale=1.35)

_panel("compact", "ready", "Compact bar: the best play", compact=True)
_panel("compact-second", "ready", "Compact bar: the second play", compact=True, selected=1)
_panel("compact-one", "one-play", "Compact bar: a single play, arrows off", compact=True)
_panel("compact-held", "held", "Compact bar: a held play", compact=True, selected=1)
_panel("compact-reading", "reading", "Compact bar: reading", compact=True, age=None)
_panel("compact-check", "check", "Compact bar: tiles need a look", compact=True)
_panel("compact-permission", "permission", "Compact bar: no screen access", compact=True)
_panel("compact-no-move", "no-move", "Compact bar: no legal placement", compact=True)


@register("panel/long-word", kind="window")
def long_word():
    """Fifteen-letter plays and a long crossing word"""
    snap = _plays(("ELECTROMAGNETIC", "COMPREHENSIVELY", "UNDERSTANDINGLY"), (96, 88, 84))
    return _build(snap)


@register("panel/long-word-compact", kind="window")
def long_word_compact():
    """Compact bar with a fifteen-letter word"""
    snap = _plays(("ELECTROMAGNETIC", "COMPREHENSIVELY", "UNDERSTANDINGLY"), (96, 88, 84))
    return _build(snap, compact=True)


@register("panel/score-3-digit", kind="window")
def score_three_digit():
    """Three-digit scores line up in the column"""
    snap = _plays(("TORNADO", "OCEAN", "AT"), (128, 104, 9))
    return _build(snap, selected=0)


@register("panel/score-3-digit-small", kind="window")
def score_three_digit_small():
    """Three-digit score at the smallest scale"""
    snap = _plays(("ELECTROMAGNETIC", "OCEAN", "AT"), (128, 104, 9))
    return _build(snap, scale=0.85)


@register("panel/error-search", kind="window")
def error_search():
    """The search stopped: an error with a way to retry"""
    snap = Snapshot(Status.CHECK, "Solver stopped unexpectedly")
    return _build(snap, age=None)


@register("panel/empty-rack", kind="window")
def empty_rack():
    """Nothing in the rack"""
    base = ctx.snapshot("no-move")
    snap = dataclasses.replace(base, detail="Rack is empty")
    return _build(snap)


@register("panel/many-notices", kind="window")
def many_notices():
    """An unsure tile and a notice at once: one banner, the answer first"""
    base = ctx.snapshot("unsure-rack")
    five = (("rack", 6, "O", ("O", "Q", "G", "D", "C")),)
    snap = dataclasses.replace(base, unsure=five)
    return _build(snap)


@register("panel/menu", kind="overlay")
def menu():
    """The overflow menu open under the more button"""
    panel = ctx.make_panel(ctx.snapshot("ready"))
    panel.ensurePolished()
    panel.menu.set_material(panel.material)
    panel.menu.configure(
        paused=False,
        has_state=True,
        check=False,
        moved=True,
        shortcuts=panel._shortcuts,
    )
    anchor = panel.more_btn.mapTo(panel, QPoint(panel.more_btn.width(), panel.more_btn.height()))
    gw = panel.menu.width() - 2 * SHADOW_BLEED
    x = anchor.x() - gw - SHADOW_BLEED
    y = anchor.y() + 6 - SHADOW_BLEED
    size = (
        max(panel.width(), x + panel.menu.width()) + 8,
        max(panel.height(), y + panel.menu.height()) + 8,
    )
    return ctx.stage(size, (panel, (0, 0)), (panel.menu, (x, y)))

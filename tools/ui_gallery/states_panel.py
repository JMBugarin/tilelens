"""The floating results panel in every state the tracker can put it in."""

from __future__ import annotations

from ui_gallery import ctx
from ui_gallery.registry import register


def _panel(name: str, snap: str, doc: str, **kwargs) -> None:
    def build():
        return ctx.make_panel(ctx.snapshot(snap), **kwargs)

    build.__doc__ = doc
    register(f"panel/{name}", kind="window")(build)


_panel("setup", "setup", "First launch: nothing read yet", age=None)
_panel(
    "setup-note",
    "setup",
    "Calibration trouble the player must act on",
    age=None,
    note="Confirm rack tile 3: open Calibrate and check the letters",
)
_panel("reading", "reading", "Reading the rack", age=None)
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
_panel("no-move", "no-move", "No legal placement")
_panel("placing", "placing", "The player is placing tiles")
_panel("updating", "updating", "New squares seen, results refreshing")
_panel("notice", "notice", "A letter was settled by the word list")
_panel("unsure-rack", "unsure-rack", "An unclear rack tile with letter chips")
_panel("unsure-cell", "unsure-cell", "An unclear board square with letter chips")
_panel("held", "held", "A play held while the tiles are placed", selected=1)
_panel("check", "check", "Tiles that need a human look")
_panel("check-scale", "check-scale", "The board changed size")
_panel("paused", "paused", "Tracking paused")
_panel("obscured", "obscured", "The board cannot be found")
_panel("covered", "covered", "Something covers the game")
_panel("permission", "permission", "Screen recording is not allowed")

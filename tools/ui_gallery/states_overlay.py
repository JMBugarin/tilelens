"""The click-through preview drawn on the game: board frame, rack outline and ghost tiles."""

from __future__ import annotations

from tilelens.state.memory import Coverage
from ui_gallery import ctx
from ui_gallery.registry import register


def _overlay(name: str, doc: str, **scene) -> None:
    def build():
        return ctx.make_overlay(ctx.overlay_scene(**scene))

    build.__doc__ = doc
    register(f"overlay/{name}", kind="overlay")(build)


_overlay("ghosts", "The best play: ghost tiles, frame, confirmed rack")
_overlay("ghosts-blank", "A play that uses a blank and forms a crossing word", move=1)
_overlay("ghosts-crossing", "A play that forms two crossing words", move=2)
_overlay("frame-only", "Aligned board, no play shown", move=None)
_overlay("no-rack", "Board and play, rack not found", rack=False)
_overlay("rack-unsure", "Only part of the rack was read", rack_read=(5, 7))
_overlay("tracking", "Partial view that is tracked", coverage=Coverage.TRACKING)
_overlay("partial", "Partial view, zoom out to check", coverage=Coverage.PARTIAL)
_overlay(
    "partial-framed", "Squares hidden by the game's panels", coverage=Coverage.PARTIAL, framed=True
)
_overlay("play-only", "Ghost tiles with no frame and no rack", board=False, rack=False)

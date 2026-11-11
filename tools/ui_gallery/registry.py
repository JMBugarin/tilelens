"""The state registry. A state is a function that builds one thing the user can see.

    from ui_gallery.registry import register

    @register("panel/ready", kind="window")
    def panel_ready():
        return ctx.make_panel(ctx.snapshot("ready"))

kind decides how the result is staged:

    window   a top-level window, centered on a backdrop with room around it
    overlay  a full-stage translucent layer (the preview, the region selector), at the origin
    dialog   a window that is normally opaque; rendered once, without a backdrop

A window of any kind that sets WA_TranslucentBackground is composited onto every backdrop; an
opaque one is rendered once as backdrop "none".

The function returns one of

    a QWidget                  grabbed as it is
    (QWidget, scene)           widget.show_scene(scene) is called first
    a QImage or QPixmap        used as it is (tooltips, popups)
    ctx.stage(...)             several windows and images laid out on one stage

The module must be named tools/ui_gallery/states_*.py; the runner imports all of them. Names are
`surface/state` in lower-case kebab-case. Use ctx.defer(fn) for cleanup that must run after
the grab (stopping a session, closing a popup). A dialog or menu is shown with show() or
popup(); one run with exec() is dismissed by the runner and fails its state.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

KINDS = ("window", "overlay", "dialog")
NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*(/[a-z0-9]+(-[a-z0-9]+)*)+$")


@dataclass(frozen=True)
class State:
    name: str
    kind: str
    build: Callable
    stage: tuple[int, int] | None = None
    backdrops: tuple[str, ...] | None = None
    settle: int | None = None
    doc: str = ""


REGISTRY: dict[str, State] = {}


def register(
    name: str,
    kind: str = "window",
    *,
    stage: tuple[int, int] | None = None,
    backdrops: tuple[str, ...] | None = None,
    settle: int | None = None,
):
    """stage: size in points for overlay kinds (default: the real game frame).
    backdrops: restrict to these backdrops. settle: milliseconds to let animations finish."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}, not {kind!r}")
    if not NAME.match(name):
        raise ValueError(f"state name {name!r} must look like 'surface/state' in kebab-case")

    def wrap(fn: Callable) -> Callable:
        if name in REGISTRY:
            raise ValueError(f"state {name!r} is registered twice")
        doc = (fn.__doc__ or "").strip().splitlines()
        REGISTRY[name] = State(name, kind, fn, stage, backdrops, settle, doc[0] if doc else "")
        return fn

    return wrap


def select(patterns: list[str] | None) -> list[State]:
    states = sorted(REGISTRY.values(), key=lambda s: s.name)
    if not patterns:
        return states
    needles = [p.strip().lower() for part in patterns for p in part.split(",") if p.strip()]
    return [s for s in states if any(n in s.name for n in needles)]

"""The settings window, which is also the first thing a new player sees."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

from tilelens.state.controller import Status
from ui_gallery import ctx
from ui_gallery.registry import register

REGION = {"x": 0, "y": 0, "width": 800, "height": 600}


def window(
    snap,
    *,
    calibrated: bool = True,
    region: bool = True,
    settings: dict | None = None,
    open_sections: tuple[str, ...] = (),
    scroll: float = 0.0,
    **kw,
):
    """The main window with the session in the state of a player who is set up: a word list, a
    region and a calibration, each of which can be taken away."""
    win = ctx.make_main_window(snap, **kw)
    win._screen_cap = lambda: 780
    session = win.session
    session.recognizer = SimpleNamespace(cal=SimpleNamespace(auto=True)) if calibrated else None
    if region:
        session.settings.region = dict(REGION)
    for key, value in (settings or {}).items():
        setattr(session.settings, key, value)
    win.refresh_settings()
    win.show_snapshot(snap)
    for name in open_sections:
        getattr(win, f"{name}_section").set_expanded(True)
    win.show()
    win.fit()
    if scroll:
        bar = win.scroll.verticalScrollBar()
        bar.setValue(round(bar.maximum() * scroll))
    return win


def long_plays(snap, words: tuple[str, ...], scores: tuple[int, ...]):
    moves = [
        dataclasses.replace(m, word=w, score=s)
        for m, w, s in zip(snap.result.moves, words, scores, strict=False)
    ]
    return dataclasses.replace(snap, result=dataclasses.replace(snap.result, moves=moves))


@register("window/main-setup-manual", kind="window")
def setup_manual():
    """First launch with a profile that needs a region and typed calibration"""
    return window(None, calibrated=False, region=False, words=False)


@register("window/main-setup-auto", kind="window")
def setup_auto():
    """First launch with a profile that finds the board by color"""
    return window(None, calibrated=False, region=False, profile="auto")


@register("window/main-setup-no-words", kind="window")
def setup_no_words():
    """No word list chosen yet"""
    return window(None, calibrated=False, region=False, profile="auto", words=False)


@register("window/main-setup-partly", kind="window")
def setup_partly():
    """Word list and region done, the game still to be learned"""
    return window(None, calibrated=False, region=True)


@register("window/main-setup-permission", kind="window")
def setup_permission():
    """Screen Recording permission is the first step"""
    win = window(None, calibrated=False, region=False, profile="auto", words=False)
    win.session.adapter = SimpleNamespace(
        screen_permission=lambda: False,
        request_permission=lambda: None,
        permission_help=lambda: "",
        close=lambda: None,
    )
    win._sync_setup()
    win.fit()
    return win


@register("window/main-ready", kind="window")
def ready():
    """Three plays and the board preview"""
    return window(ctx.demo_snapshot())


@register("window/main-ready-second", kind="window")
def ready_second():
    """The second play selected"""
    return window(ctx.demo_snapshot(), selected=1)


@register("window/main-ready-middle", kind="window")
def ready_middle():
    """The same window scrolled halfway"""
    return window(ctx.demo_snapshot(), scroll=0.5)


@register("window/main-ready-bottom", kind="window")
def ready_bottom():
    """The same window scrolled to its end"""
    return window(ctx.demo_snapshot(), scroll=1.0)


@register("window/main-long-plays", kind="window")
def long_words():
    """A 15-letter word and three-digit scores"""
    snap = long_plays(
        ctx.demo_snapshot(),
        ("UNCOMPROMISINGLY", "INTERNATIONALIZE", "QUIZ"),
        (134, 108, 100),
    )
    return window(snap)


@register("window/main-one-play", kind="window")
def one_play():
    """A single play"""
    snap = ctx.demo_snapshot()
    snap = dataclasses.replace(
        snap, result=dataclasses.replace(snap.result, moves=snap.result.moves[:1])
    )
    return window(snap)


@register("window/main-no-play", kind="window")
def no_play():
    """The board is read and nothing can be played"""
    snap = ctx.demo_snapshot()
    snap = dataclasses.replace(
        snap, detail="No legal placement", result=dataclasses.replace(snap.result, moves=[])
    )
    return window(snap)


@register("window/main-banner", kind="window")
def banner():
    """A message under the header"""
    return window(ctx.demo_snapshot(), message="Region saved. Now calibrate.")


@register("window/main-banner-long", kind="window")
def banner_long():
    """A long message that wraps"""
    return window(
        ctx.demo_snapshot(),
        message=(
            "Screen Recording permission is missing. Open System Settings, Privacy and "
            "Security, Screen Recording, switch TileLens on and restart the application."
        ),
    )


@register("window/main-reading", kind="window")
def reading():
    """Reading the screen"""
    return window(ctx.demo_snapshot(Status.READING, "Reading"))


@register("window/main-solving", kind="window")
def solving():
    """Searching for plays"""
    return window(ctx.demo_snapshot(Status.SOLVING, "Searching"))


@register("window/main-check", kind="window")
def check():
    """Tiles to check, with the Check tiles button enabled"""
    return window(
        ctx.demo_snapshot(
            Status.CHECK, "3 squares to check; rack to check", reading=ctx.check_reading()
        )
    )


@register("window/main-paused", kind="window")
def paused():
    """Tracking paused"""
    return window(ctx.demo_snapshot(Status.PAUSED, "Paused"))


@register("window/main-not-visible", kind="window")
def not_visible():
    """The game is not visible"""
    return window(ctx.demo_snapshot(Status.NOT_VISIBLE, "Board not found"))

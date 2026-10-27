"""The settings window, which is also the first thing a new player sees."""

from __future__ import annotations

from tilelens.state.controller import Status
from ui_gallery import ctx
from ui_gallery.registry import register


@register("window/main-setup-manual", kind="window")
def setup_manual():
    """First launch with a profile that needs a region and typed calibration"""
    return ctx.make_main_window(None)


@register("window/main-setup-auto", kind="window")
def setup_auto():
    """First launch with a profile that finds the board by color"""
    return ctx.make_main_window(None, profile="auto")


@register("window/main-setup-no-words", kind="window")
def setup_no_words():
    """No word list chosen yet"""
    return ctx.make_main_window(None, profile="auto", words=False)


@register("window/main-ready", kind="window")
def ready():
    """Three plays and the board preview"""
    return ctx.make_main_window(ctx.demo_snapshot())


@register("window/main-ready-second", kind="window")
def ready_second():
    """The second play selected"""
    return ctx.make_main_window(ctx.demo_snapshot(), selected=1)


@register("window/main-ready-wide", kind="window")
def ready_wide():
    """The window at its own default size, before the application shrinks it"""
    return ctx.make_main_window(ctx.demo_snapshot(), size=(820, 560))


@register("window/main-banner", kind="window")
def banner():
    """A message under the status line"""
    return ctx.make_main_window(ctx.demo_snapshot(), message="Region saved. Now calibrate.")


@register("window/main-banner-long", kind="window")
def banner_long():
    """A long message that wraps"""
    return ctx.make_main_window(
        ctx.demo_snapshot(),
        message=(
            "Screen Recording permission is missing. Open System Settings, Privacy and "
            "Security, Screen Recording, switch TileLens on and restart the application."
        ),
    )


@register("window/main-reading", kind="window")
def reading():
    """Reading the screen"""
    return ctx.make_main_window(ctx.demo_snapshot(Status.READING, "Reading"))


@register("window/main-solving", kind="window")
def solving():
    """Searching for plays"""
    return ctx.make_main_window(ctx.demo_snapshot(Status.SOLVING, "Searching"))


@register("window/main-check", kind="window")
def check():
    """Tiles to check, with the Check tiles button enabled"""
    return ctx.make_main_window(
        ctx.demo_snapshot(
            Status.CHECK, "3 squares to check; rack to check", reading=ctx.check_reading()
        )
    )


@register("window/main-paused", kind="window")
def paused():
    """Tracking paused"""
    return ctx.make_main_window(ctx.demo_snapshot(Status.PAUSED, "Paused"))


@register("window/main-not-visible", kind="window")
def not_visible():
    """The game is not visible"""
    return ctx.make_main_window(ctx.demo_snapshot(Status.NOT_VISIBLE, "Board not found"))

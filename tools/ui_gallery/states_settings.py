"""Settings in full, long names, toasts and the application icon."""

from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtCore import QPointF
from PySide6.QtGui import QImage, QPainter

from ui_gallery import ctx
from ui_gallery.registry import register
from ui_gallery.states_window import window

LONG_PROFILE = "Letter League on Discord, 2560 by 1440 display, dark theme, large board"
LONG_WORDS = "/Users/josh/Documents/word lists/collins scrabble words 2019 extended.txt"


@register("settings/populated", kind="window")
def populated():
    """Every control set to something, every section open"""
    win = window(
        ctx.demo_snapshot(),
        settings={
            "overlay_mode": "suppress",
            "guides": "always",
            "appearance": "dark",
            "reduce_motion": True,
            "panel_compact": True,
            "panel_offset": {"x": 640, "y": 80},
            "words": str(ctx.WORDS),
        },
        open_sections=("shortcuts", "diagnostics"),
        scroll=0.0,
    )
    return win


@register("settings/populated-bottom", kind="window")
def populated_bottom():
    """The end of the same window"""
    return window(
        ctx.demo_snapshot(),
        settings={"panel_offset": {"x": 640, "y": 80}},
        open_sections=("shortcuts", "diagnostics"),
        scroll=1.0,
    )


@register("settings/long-names", kind="window")
def long_names():
    """A profile name and a word list path that do not fit"""
    win = window(ctx.demo_snapshot(), settings={"words": LONG_WORDS}, scroll=0.55)
    win.session.profile = SimpleNamespace(
        name=LONG_PROFILE,
        source=LONG_PROFILE,
        appearance=win.session.profile.appearance,
        word_list=None,
    )
    win.refresh_settings()
    return win


@register("settings/panel-only", kind="window")
def panel_only():
    """Panel only: nothing is drawn over the game"""
    return window(ctx.demo_snapshot(), settings={"overlay_mode": "panel_only"}, scroll=0.45)


@register("settings/not-calibrated", kind="window")
def not_calibrated():
    """Game area not set and nothing learned yet, setup skipped"""
    win = window(ctx.demo_snapshot(), calibrated=False, region=False, scroll=0.7)
    win._skip_setup()
    return win


def _toast(text: str, kind: str):
    from tilelens.ui.notify import Toast

    toast = Toast(text, kind, click_through=False)
    toast.show()
    ctx.defer(toast.close)
    return toast


@register("toast/info", kind="window")
def toast_info():
    """A neutral confirmation"""
    return _toast("Panel moved back beside the board", "info")


@register("toast/success", kind="window")
def toast_success():
    """A confirmation that something worked"""
    return _toast("Debug dump saved: dump-2026-10-27-0719.png", "success")


@register("toast/warning", kind="window")
def toast_warning():
    """Something to know, nothing broken"""
    return _toast("Nothing captured yet", "warning")


@register("toast/error", kind="window")
def toast_error():
    """Something failed"""
    return _toast("Could not save the debug dump: the disk is full", "error")


@register("toast/long", kind="window")
def toast_long():
    """Text that wraps to three lines"""
    return _toast(
        "Word list loaded: collins scrabble words 2019 extended with proper nouns and "
        "abbreviations removed.txt",
        "success",
    )


@register("toast/over-window", kind="window")
def toast_over_window():
    """A toast over the settings window, as it appears after Save debug dump"""
    win = window(ctx.demo_snapshot(), scroll=0.0)
    toast = _toast("Debug dump saved: dump-0719.png", "success")
    return ctx.stage(
        (win.width() + 80, win.height() + 80),
        (win, (40, 40)),
        (toast, (40 + (win.width() - toast.width()) // 2, win.height() - toast.height() - 20)),
    )


@register("icon/app-sizes", kind="dialog")
def app_icon_sizes():
    """The application icon at the sizes the system asks for, dark and light"""
    from tilelens.ui.app_icon import render_app_icon

    sizes = (16, 24, 32, 48, 64, 128)
    pad = 16
    scale = 2.0
    width = pad + sum(s + pad for s in sizes)
    height = 2 * (128 + pad) + pad
    canvas = QImage(
        int(width * scale), int(height * scale), QImage.Format.Format_ARGB32_Premultiplied
    )
    canvas.setDevicePixelRatio(scale)
    canvas.fill(0xFF808890)
    p = QPainter(canvas)
    for row, dark in enumerate((True, False)):
        x = pad
        for s in sizes:
            pm = render_app_icon(s, scale, dark=dark)
            p.drawPixmap(QPointF(x, pad + row * (128 + pad) + (128 - s) / 2), pm)
            x += s + pad
    p.end()
    return canvas

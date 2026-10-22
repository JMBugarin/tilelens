from __future__ import annotations

from dataclasses import dataclass, field

Color = tuple[int, int, int]


@dataclass(frozen=True)
class Theme:
    """Colors are BGR. Every theme here is invented for testing."""

    name: str
    window: Color
    header: Color
    header_text: Color
    board: Color
    grid: Color
    square: Color
    premium: dict[str, Color]
    premium_text: Color
    tile: Color
    tile_edge: Color
    letter: Color
    value: Color
    blank_letter: Color
    tentative_tile: Color
    tentative_edge: Color
    recent_tile: Color
    rack_panel: Color
    font: tuple[str, int] = ("sans", 500)
    letter_thickness: float = 0.09
    letter_scale: float = 0.62
    gap: float = 0.08
    radius: float = 0.14
    show_values: bool = True
    value_top: bool = False
    tile_takes_square: bool = False
    blank_mark: Color = (240, 108, 142)
    in_rotation: bool = True
    premium_labels: bool = True
    rack_letter: Color | None = None
    held_out: bool = False
    extra: dict = field(default_factory=dict)


def _premium(dl, tl, dw, tw, other):
    return {
        "double_letter": dl,
        "triple_letter": tl,
        "double_word": dw,
        "triple_word": tw,
        "other": other,
    }


THEMES: dict[str, Theme] = {
    t.name: t
    for t in [
        Theme(
            name="meadow",
            window=(222, 236, 228),
            header=(96, 128, 72),
            header_text=(240, 250, 245),
            board=(170, 196, 160),
            grid=(150, 178, 140),
            square=(214, 232, 220),
            premium=_premium(
                (226, 196, 140), (196, 140, 70), (150, 170, 238), (80, 96, 214), (200, 140, 200)
            ),
            premium_text=(255, 255, 255),
            tile=(150, 214, 246),
            tile_edge=(90, 150, 196),
            letter=(40, 44, 52),
            value=(60, 66, 76),
            blank_letter=(40, 110, 210),
            tentative_tile=(188, 236, 250),
            tentative_edge=(60, 170, 60),
            recent_tile=(120, 232, 200),
            rack_panel=(120, 150, 100),
        ),
        Theme(
            name="slate",
            window=(40, 36, 34),
            header=(70, 60, 54),
            header_text=(220, 220, 220),
            board=(62, 56, 52),
            grid=(48, 44, 40),
            square=(88, 80, 74),
            premium=_premium(
                (150, 110, 60), (190, 120, 40), (90, 80, 160), (60, 50, 190), (150, 80, 150)
            ),
            premium_text=(230, 230, 230),
            tile=(222, 226, 232),
            tile_edge=(160, 166, 172),
            letter=(30, 30, 34),
            value=(80, 80, 86),
            blank_letter=(30, 120, 220),
            tentative_tile=(200, 236, 210),
            tentative_edge=(80, 200, 120),
            recent_tile=(140, 220, 250),
            rack_panel=(58, 52, 48),
            font=("sans", 750),
            gap=0.1,
        ),
        Theme(
            name="candy",
            window=(236, 226, 250),
            header=(170, 110, 220),
            header_text=(255, 255, 255),
            board=(214, 196, 236),
            grid=(200, 180, 226),
            square=(246, 238, 252),
            premium=_premium(
                (240, 210, 170), (230, 170, 110), (190, 190, 250), (140, 140, 250), (190, 240, 190)
            ),
            premium_text=(90, 60, 120),
            tile=(255, 252, 250),
            tile_edge=(200, 170, 220),
            letter=(120, 50, 140),
            value=(150, 100, 170),
            blank_letter=(60, 150, 60),
            tentative_tile=(250, 230, 200),
            tentative_edge=(220, 140, 60),
            recent_tile=(200, 250, 255),
            rack_panel=(190, 150, 220),
            font=("uni", 400),
            radius=0.25,
        ),
        Theme(
            name="paper",
            window=(250, 250, 250),
            header=(230, 230, 230),
            header_text=(40, 40, 40),
            board=(255, 255, 255),
            grid=(200, 200, 200),
            square=(255, 255, 255),
            premium=_premium(
                (245, 230, 215), (235, 205, 175), (215, 215, 245), (190, 190, 240), (215, 240, 215)
            ),
            premium_text=(120, 120, 120),
            tile=(70, 70, 70),
            tile_edge=(40, 40, 40),
            letter=(250, 250, 250),
            value=(210, 210, 210),
            blank_letter=(120, 200, 250),
            tentative_tile=(120, 120, 120),
            tentative_edge=(60, 180, 240),
            recent_tile=(90, 80, 40),
            rack_panel=(235, 235, 235),
            font=("sans", 300),
            letter_scale=0.7,
            gap=0.06,
            radius=0.05,
            show_values=False,
            premium_labels=False,
        ),
        Theme(
            name="harbor",
            window=(214, 226, 236),
            header=(120, 96, 40),
            header_text=(250, 245, 240),
            board=(222, 236, 246),
            grid=(255, 255, 255),
            square=(222, 236, 246),
            premium=_premium(
                (240, 214, 170), (130, 200, 240), (180, 236, 190), (110, 150, 240), (200, 150, 220)
            ),
            premium_text=(255, 255, 255),
            tile=(150, 140, 30),
            tile_edge=(110, 100, 20),
            letter=(255, 255, 255),
            value=(255, 255, 255),
            blank_letter=(60, 230, 250),
            tentative_tile=(190, 180, 80),
            tentative_edge=(40, 40, 220),
            recent_tile=(170, 120, 40),
            rack_panel=(90, 110, 120),
            font=("sans", 800),
            gap=0.05,
            radius=0.18,
            value_top=True,
        ),
        Theme(
            name="mosaic",
            window=(40, 40, 40),
            header=(200, 90, 120),
            header_text=(255, 255, 255),
            board=(205, 228, 248),
            grid=(255, 255, 255),
            square=(200, 228, 250),
            premium=_premium(
                (250, 226, 180), (110, 208, 246), (186, 244, 188), (126, 160, 240), (210, 170, 240)
            ),
            premium_text=(255, 255, 255),
            tile=(102, 142, 239),
            tile_edge=(230, 230, 230),
            letter=(30, 30, 30),
            value=(40, 40, 40),
            blank_letter=(30, 30, 30),
            tentative_tile=(102, 142, 239),
            tentative_edge=(102, 142, 239),
            recent_tile=(250, 250, 250),
            rack_panel=(70, 80, 90),
            font=("sans", 800),
            gap=0.07,
            radius=0.16,
            value_top=True,
            tile_takes_square=True,
            in_rotation=False,
        ),
        Theme(
            # cream board that fills the window, colored tiles on premium squares, orange rack
            # tiles with white letters, values at the top right
            name="apricot",
            window=(202, 230, 251),
            header=(240, 110, 150),
            header_text=(255, 255, 255),
            board=(202, 230, 251),
            grid=(255, 255, 255),
            square=(202, 230, 251),
            premium=_premium(
                (252, 228, 183), (114, 210, 248), (178, 244, 189), (127, 162, 241), (210, 170, 240)
            ),
            premium_text=(255, 255, 255),
            tile=(102, 142, 239),
            tile_edge=(200, 200, 200),
            letter=(40, 34, 30),
            value=(60, 56, 52),
            blank_letter=(40, 34, 30),
            tentative_tile=(102, 142, 239),
            tentative_edge=(102, 142, 239),
            recent_tile=(250, 250, 250),
            rack_panel=(90, 98, 110),
            font=("sans", 900),
            letter_scale=0.66,
            gap=0.06,
            radius=0.18,
            value_top=True,
            tile_takes_square=True,
            in_rotation=False,
            rack_letter=(255, 255, 255),
        ),
        Theme(
            name="ember",
            window=(30, 40, 60),
            header=(20, 60, 120),
            header_text=(200, 230, 250),
            board=(40, 60, 90),
            grid=(30, 46, 70),
            square=(56, 80, 112),
            premium=_premium(
                (110, 140, 60), (60, 160, 90), (60, 90, 190), (40, 50, 200), (120, 60, 140)
            ),
            premium_text=(210, 220, 240),
            tile=(160, 210, 240),
            tile_edge=(90, 140, 190),
            letter=(20, 30, 70),
            value=(40, 60, 110),
            blank_letter=(160, 60, 30),
            tentative_tile=(200, 240, 250),
            tentative_edge=(50, 220, 250),
            recent_tile=(110, 200, 250),
            rack_panel=(25, 50, 95),
            font=("italic", 600),
            letter_scale=0.58,
            gap=0.12,
            radius=0.2,
            held_out=True,
        ),
    ]
}

DEV_THEMES = [n for n, t in THEMES.items() if not t.held_out and t.in_rotation]
HELD_OUT_THEMES = [n for n, t in THEMES.items() if t.held_out]

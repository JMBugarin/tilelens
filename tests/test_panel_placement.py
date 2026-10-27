from types import SimpleNamespace

import pytest

from tilelens.platforms.base import Region
from tilelens.ui.panel_placement import (
    PanelPlacer,
    Placement,
    choose_placement,
    choose_position,
    clamp_position,
    overlap_area,
    panel_scale,
    rect_of,
    union,
)

SCREEN = (0, 0, 1920, 1080)
PANEL = (356, 380)


def place(board, rack=None, region=(0, 0, 1920, 1080), **kw) -> Placement:
    return choose_placement(region, board, rack, PANEL, SCREEN, **kw)


def rect_of_panel(p: Placement) -> tuple:
    return p.x, p.y, PANEL[0], PANEL[1]


def test_rect_of_takes_tuples_qt_rects_regions_and_objects():
    from PySide6.QtCore import QRect, QRectF

    assert rect_of((1, 2, 3, 4)) == (1.0, 2.0, 3.0, 4.0)
    assert rect_of(QRect(1, 2, 3, 4)) == (1.0, 2.0, 3.0, 4.0)
    assert rect_of(QRectF(1.5, 2, 3, 4)) == (1.5, 2.0, 3.0, 4.0)
    assert rect_of(Region(1, 2, 3, 4)) == (1.0, 2.0, 3.0, 4.0)
    assert rect_of(SimpleNamespace(x=1, y=2, width=3, height=4)) == (1.0, 2.0, 3.0, 4.0)
    assert rect_of(None) is None


def test_union_ignores_missing_and_empty_rects():
    assert union(None, None) is None
    assert union((0, 0, 0, 10), None) is None
    assert union((0, 0, 10, 10), None, (20, 20, 10, 10)) == (0, 0, 30, 30)


def test_board_on_the_left_puts_the_panel_on_the_right():
    p = place((100, 100, 700, 600), (150, 720, 600, 90))
    assert p.side == "right"
    assert p.x == 100 + 700 + 16
    assert p.y == 100
    assert p.overlap == 0


def test_board_on_the_right_puts_the_panel_on_the_left():
    p = place((1100, 100, 700, 600), (1150, 720, 600, 90))
    assert p.side == "left"
    assert p.x == 1100 - 16 - PANEL[0]
    assert p.overlap == 0


def test_centered_board_with_equal_room_prefers_the_right():
    p = place((560, 80, 800, 700), (600, 800, 700, 90))
    assert p.side == "right"
    assert p.overlap == 0


def test_a_wide_board_gets_the_panel_above_or_below_when_the_sides_are_full():
    screen = (0, 0, 1000, 1400)
    board = (20, 500, 960, 500)
    p = choose_placement(None, board, None, (356, 300), screen)
    assert p.side in ("below", "above")
    assert p.overlap == 0
    assert 0 <= p.x <= 1000 - 356


def test_the_panel_stays_inside_the_screen():
    p = place((100, 100, 700, 600))
    x, y, w, h = rect_of_panel(p)
    assert x >= 0 and y >= 0 and x + w <= 1920 and y + h <= 1080


def test_the_panel_never_covers_the_rack_when_there_is_room():
    rack = (400, 700, 500, 100)
    p = place((100, 100, 700, 560), rack)
    assert overlap_area(rect_of_panel(p), rack) == 0


def test_a_tall_panel_is_pulled_up_to_fit_the_screen_bottom():
    p = place((100, 900, 700, 150))
    assert p.y + PANEL[1] <= 1080


def test_a_board_that_fills_the_whole_screen_still_gets_a_deterministic_inside_spot():
    first = place(SCREEN, (400, 900, 800, 100))
    again = place(SCREEN, (400, 900, 800, 100))
    assert first == again
    x, y, w, h = rect_of_panel(first)
    assert x >= 0 and y >= 0 and x + w <= 1920 and y + h <= 1080
    assert first.overlap > 0
    assert first.side in ("right", "left", "below", "above")


def test_a_board_wider_than_the_screen_does_not_throw_or_escape():
    p = place((-200, -50, 2400, 1300))
    x, y, w, h = rect_of_panel(p)
    assert x >= 0 and y >= 0 and x + w <= 1920 and y + h <= 1080


def test_a_panel_larger_than_the_screen_keeps_its_top_left_visible():
    p = choose_placement(None, (0, 0, 100, 100), None, (900, 900), (0, 0, 800, 600))
    assert (p.x, p.y) == (8, 8)


def test_a_dragged_position_wins_and_is_clamped():
    p = place((100, 100, 700, 600), user_pos=(300, 200))
    assert (p.x, p.y, p.side) == (300, 200, "user")
    far = place((100, 100, 700, 600), user_pos=(5000, -300))
    assert far.x == 1920 - PANEL[0] - 8
    assert far.y == 8
    assert far.side == "user"


def test_a_dragged_position_is_a_qpoint_too():
    from PySide6.QtCore import QPoint

    assert choose_position(None, None, None, PANEL, SCREEN, user_pos=QPoint(40, 50)) == (40, 50)


def test_no_board_in_sight_means_the_upper_right_of_the_region():
    p = choose_placement((200, 100, 1000, 800), None, None, PANEL, SCREEN)
    assert p.side == "inside"
    assert p.x == 200 + 1000 - PANEL[0] - 24
    assert p.y == 100 + 72


def test_choose_position_is_the_point_of_the_placement():
    board = (100, 100, 700, 600)
    assert choose_position(None, board, None, PANEL, SCREEN) == place(board).point


def test_a_small_drift_of_the_board_does_not_move_the_panel_to_another_side():
    placer = PanelPlacer()
    first = placer.place(None, (560, 80, 800, 700), None, PANEL, SCREEN)
    assert first.side == "right"
    for dx in (-30, -12, 8, 25, 40, -40):
        moved = placer.place(None, (560 + dx, 80, 800, 700), None, PANEL, SCREEN)
        assert moved.side == "right"


def test_without_memory_the_same_drift_flips_the_side():
    sides = {
        choose_placement(None, (560 + dx, 80, 800, 700), None, PANEL, SCREEN).side
        for dx in (-30, 30)
    }
    assert sides == {"left", "right"}


def test_prefer_keeps_the_last_side_while_it_is_nearly_as_good():
    a = choose_placement(None, (560, 80, 800, 700), None, PANEL, SCREEN, prefer="left")
    assert a.side == "left"


def test_prefer_is_dropped_when_that_side_no_longer_fits():
    board = (500, 100, 1300, 700)
    p = choose_placement(None, board, None, PANEL, SCREEN, prefer="right")
    assert p.side == "left"
    assert p.overlap == 0


def test_placer_forgets_on_reset_and_ignores_dragged_results():
    placer = PanelPlacer()
    placer.place(None, (1100, 100, 700, 600), None, PANEL, SCREEN)
    assert placer.side == "left"
    placer.place(None, (1100, 100, 700, 600), None, PANEL, SCREEN, user_pos=(10, 10))
    assert placer.side == "left"
    placer.reset()
    assert placer.side is None


def test_clamp_position_uses_the_margin():
    assert clamp_position(-50, -50, (100, 100), (0, 0, 500, 400), 10) == (10, 10)
    assert clamp_position(900, 900, (100, 100), (0, 0, 500, 400), 10) == (390, 290)


def test_second_screen_with_an_offset_origin():
    screen = (1920, 0, 1920, 1080)
    p = choose_placement(None, (2000, 100, 700, 600), None, PANEL, screen)
    assert p.x >= 1920 and p.x + PANEL[0] <= 3840


def test_panel_scale_follows_the_window_in_steps():
    assert panel_scale(900) == 1.0
    assert panel_scale(400) == 0.85
    assert panel_scale(3000) == 1.35
    assert 0.85 <= panel_scale(1080) <= 1.35
    assert panel_scale(1080) == round(panel_scale(1080) * 20) / 20


def test_panel_scale_does_not_flicker_near_a_step():
    s = panel_scale(900)
    assert panel_scale(912, current=s) == s
    assert panel_scale(1200, current=s) != s


@pytest.mark.parametrize("dpr_height", [560, 700, 1000, 1600])
def test_panel_scale_stays_in_range(dpr_height):
    assert 0.85 <= panel_scale(dpr_height) <= 1.35

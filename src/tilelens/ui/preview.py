from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter

from tilelens.platforms.base import Region
from tilelens.rules.board import WALL_CELL, Board
from tilelens.solver.movegen import Move
from tilelens.ui.design.tokens import type_font
from tilelens.ui.glass import paint_ghosts
from tilelens.vision.grid import Grid

# the game's own colors, for the small board in the settings window
FIELD = QColor(251, 230, 202)
FIELD_LINE = QColor(255, 246, 232)
PREMIUM_COLORS = {
    ("word", 2): QColor(190, 244, 178),
    ("word", 3): QColor(242, 163, 127),
    ("letter", 2): QColor(190, 226, 250),
    ("letter", 3): QColor(248, 210, 114),
}
TILE = QColor(150, 222, 133)
TILE_LIP = QColor(104, 176, 98)
TILE_INK = QColor(28, 32, 44)
BLANK_INK = QColor(86, 54, 190)


@dataclass(frozen=True)
class CellMapper:
    """Maps board squares to rectangles in a target coordinate space."""

    x0: float
    y0: float
    pitch_x: float
    pitch_y: float

    def rect(self, row: int, col: int, inset: float = 0.06) -> QRectF:
        return QRectF(
            self.x0 + (col + inset) * self.pitch_x,
            self.y0 + (row + inset) * self.pitch_y,
            self.pitch_x * (1 - 2 * inset),
            self.pitch_y * (1 - 2 * inset),
        )

    @classmethod
    def for_overlay(cls, grid: Grid, frame_size: tuple[int, int], region: Region) -> CellMapper:
        """Captured-frame pixels to overlay points (the overlay covers the region exactly)."""
        fw, fh = frame_size
        sx = fw / region.width if region.width else 1.0
        sy = fh / region.height if region.height else 1.0
        return cls(grid.x0 / sx, grid.y0 / sy, grid.pitch_x / sx, grid.pitch_y / sy)


def paint_move(
    painter: QPainter,
    move: Move,
    rect_of: Callable[[int, int], QRectF],
    value_of: Callable[[str, bool], int] | None = None,
) -> None:
    """The suggested play as the overlay draws it: smoked ghost tiles, soft bars on its letters."""
    paint_ghosts(painter, move, rect_of, value_of)


def _premium_color(prem) -> QColor | None:
    if prem is None:
        return None
    if prem.word > 1:
        return PREMIUM_COLORS.get(("word", prem.word))
    if prem.letter > 1:
        return PREMIUM_COLORS.get(("letter", prem.letter))
    return None


def paint_board(painter: QPainter, board: Board, mapper: CellMapper, profile=None) -> None:
    """A plain board for the settings preview, in the game's own colors."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    for r in range(board.height):
        for c in range(board.width):
            cell = board.get(r, c)
            if cell == WALL_CELL:
                continue
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(FIELD_LINE)
            painter.drawRect(mapper.rect(r, c, inset=0))
            rect = mapper.rect(r, c, inset=0.03)
            fill = _premium_color(profile.premium_at(r, c)) if profile is not None else None
            painter.setBrush(fill or FIELD)
            painter.drawRect(rect)
            if cell:
                _paint_tile(painter, rect, cell[0], cell[1])
    painter.restore()


def _paint_tile(painter: QPainter, rect: QRectF, letter: str, blank: bool) -> None:
    side = min(rect.width(), rect.height())
    radius = side * 0.2
    lip = side * 0.07
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(TILE_LIP)
    painter.drawRoundedRect(rect, radius, radius)
    face = rect.adjusted(0, 0, 0, -lip)
    painter.setBrush(TILE)
    painter.drawRoundedRect(face, radius, radius)
    font = type_font("word", max(7.0, side * 0.58) / 19)
    painter.setFont(font)
    painter.setPen(BLANK_INK if blank else TILE_INK)
    painter.drawText(face, Qt.AlignmentFlag.AlignCenter, letter)

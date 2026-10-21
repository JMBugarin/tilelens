from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen

from tilelens.platforms.base import Region
from tilelens.rules.board import Board
from tilelens.solver.movegen import Move
from tilelens.vision.grid import Grid

NEW_FILL = QColor(255, 196, 0, 150)
NEW_EDGE = QColor(200, 120, 0, 230)
OLD_EDGE = QColor(30, 144, 255, 220)
LETTER = QColor(20, 20, 20, 240)
BLANK_LETTER = QColor(0, 90, 200, 240)
BLANK_MARK = QColor(0, 90, 200, 230)


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


def paint_move(painter: QPainter, move: Move, rect_of: Callable[[int, int], QRectF]) -> None:
    """Ghost tiles for one placement: new tiles filled, existing word letters outlined."""
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    new = {(t.row, t.col): t for t in move.tiles}
    existing = set()
    for word in move.breakdown.words:
        for cell in word.cells:
            if not cell.new:
                existing.add((cell.row, cell.col))
    for r, c in sorted(existing):
        rect = rect_of(r, c)
        pen = QPen(OLD_EDGE, max(1.5, rect.width() * 0.06))
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, rect.width() * 0.15, rect.width() * 0.15)
    for (r, c), t in new.items():
        rect = rect_of(r, c)
        radius = rect.width() * 0.15
        painter.setPen(QPen(NEW_EDGE, max(1.5, rect.width() * 0.07)))
        painter.setBrush(NEW_FILL)
        painter.drawRoundedRect(rect, radius, radius)
        font = QFont()
        font.setBold(True)
        font.setPixelSize(max(6, int(rect.height() * 0.55)))
        painter.setFont(font)
        painter.setPen(BLANK_LETTER if t.blank else LETTER)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, t.letter)
        if t.blank:
            d = rect.width() * 0.22
            mark = QRectF(rect.right() - d * 1.2, rect.top() + d * 0.2, d, d)
            painter.setPen(QPen(BLANK_MARK, 1.2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(mark)
            small = QFont(font)
            small.setPixelSize(max(5, int(d * 0.8)))
            painter.setFont(small)
            painter.drawText(mark, Qt.AlignmentFlag.AlignCenter, "?")


def paint_board(painter: QPainter, board: Board, mapper: CellMapper, profile=None) -> None:
    """A plain board for the panel preview."""
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    for r in range(board.height):
        for c in range(board.width):
            rect = mapper.rect(r, c, inset=0.04)
            prem = profile.premium_at(r, c) if profile else None
            if prem is None:
                fill = QColor(236, 236, 230)
            elif prem.word > 1:
                fill = QColor(240, 170, 160) if prem.word == 2 else QColor(220, 110, 100)
            else:
                fill = QColor(170, 205, 235) if prem.letter == 2 else QColor(110, 160, 220)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRect(rect)
            cell = board.get(r, c)
            if cell:
                painter.setBrush(QColor(245, 222, 170))
                painter.setPen(QPen(QColor(180, 150, 90), 1))
                painter.drawRoundedRect(rect, 2, 2)
                font = QFont()
                font.setPixelSize(max(6, int(rect.height() * 0.6)))
                painter.setFont(font)
                painter.setPen(BLANK_LETTER if cell[1] else LETTER)
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, cell[0])

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout

from tilelens.solver.movegen import Move


def describe(move: Move) -> dict:
    main = next(
        (
            w
            for w in move.breakdown.words
            if (w.row, w.col, w.direction) == (move.row, move.col, move.direction)
        ),
        move.breakdown.words[0],
    )
    cross = [w for w in move.breakdown.words if w is not main]
    parts = []
    for w in move.breakdown.words:
        mult = f" x{w.word_multiplier}" if w.word_multiplier != 1 else ""
        parts.append(f"{w.text} {w.letter_sum}{mult} = {w.total}")
    if move.breakdown.bonus:
        parts.append(f"full rack +{move.breakdown.bonus}")
    blanks = [f"{t.letter} (blank)" for t in move.tiles if t.blank]
    return {
        "word": move.word,
        "total": move.score,
        "main_total": main.total,
        "where": f"Row {move.row + 1}, col {move.col + 1}",
        "direction": "across" if move.direction == "H" else "down",
        "tiles": len(move.tiles),
        "cross": ", ".join(w.text for w in cross) or "none",
        "breakdown": "; ".join(parts),
        "blanks": ", ".join(blanks),
    }


class ResultCard(QFrame):
    clicked = Signal(int)

    def __init__(self, index: int):
        super().__init__()
        self.index = index
        self.setObjectName("card")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(2)
        self.title = QLabel()
        self.title.setObjectName("cardTitle")
        self.where = QLabel()
        self.cross = QLabel()
        self.breakdown = QLabel()
        self.breakdown.setWordWrap(True)
        self.breakdown.setObjectName("muted")
        for w in (self.title, self.where, self.cross, self.breakdown):
            lay.addWidget(w)
        self.set_move(None)
        self.set_selected(False)

    def set_move(self, move: Move | None) -> None:
        self.move_shown = move
        if move is None:
            self.title.setText(f"#{self.index + 1}  —")
            self.where.setText("")
            self.cross.setText("")
            self.breakdown.setText("")
            self.setEnabled(False)
            return
        d = describe(move)
        self.setEnabled(True)
        self.title.setText(f"#{self.index + 1}  {d['word']}   {d['total']} pts")
        blank = f" · blank: {d['blanks']}" if d["blanks"] else ""
        self.where.setText(f"{d['where']} · {d['direction']} · {d['tiles']} tiles{blank}")
        self.cross.setText(f"Cross words: {d['cross']}")
        self.breakdown.setText(d["breakdown"])

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event) -> None:
        if self.move_shown is not None:
            self.clicked.emit(self.index)
        super().mousePressEvent(event)

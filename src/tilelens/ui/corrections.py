from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from tilelens.rules.profile import BLANK
from tilelens.vision.recognizer import CellFix, Reading

HELP = (
    "Fix highlighted squares. Type a letter for a played tile, a lowercase letter for a blank, "
    "leave empty for an empty square, and start with ~ for a tile that is placed but not yet "
    "played (for example ~A). The rack is read left to right; use ? for a blank."
)


def cell_text(cell) -> str:
    if not cell.letter:
        return ""
    text = cell.letter.lower() if cell.blank else cell.letter
    if cell.state in ("tentative", "unknown") and not cell.uncertain:
        text = "~" + text
    return text


def parse_cell(text: str) -> CellFix | str | None:
    """CellFix, None for empty, or an error message."""
    t = text.strip()
    if t in ("", "."):
        return None
    played = True
    if t.startswith("~"):
        played = False
        t = t[1:]
    if len(t) != 1 or not t.isalpha():
        return f"'{text}' is not a single letter"
    return CellFix(t.upper(), t.islower(), played)


class CorrectionDialog(QDialog):
    def __init__(self, reading: Reading, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Check tiles")
        self.reading = reading
        lay = QVBoxLayout(self)
        info = QLabel(HELP)
        info.setWordWrap(True)
        lay.addWidget(info)
        grid = QGridLayout()
        grid.setSpacing(2)
        self.edits: dict[tuple[int, int], QLineEdit] = {}
        for r, row in enumerate(reading.cells):
            for c, cell in enumerate(row):
                e = QLineEdit(cell_text(cell))
                e.setMaxLength(2)
                e.setFixedWidth(30)
                e.setAlignment(Qt.AlignmentFlag.AlignCenter)
                if cell.uncertain:
                    e.setStyleSheet("background:#ffe58a;font-weight:bold")
                    tip = cell.reason
                    if cell.candidates:
                        tip += " (maybe " + ", ".join(cell.candidates) + ")"
                    e.setToolTip(tip)
                grid.addWidget(e, r, c)
                self.edits[(r, c)] = e
        lay.addLayout(grid)
        lay.addWidget(QLabel("Rack, left to right:"))
        rack_text = "".join(BLANK if t.blank else (t.letter or "_") for t in reading.rack)
        self.rack_edit = QLineEdit(rack_text)
        if reading.rack_uncertain:
            self.rack_edit.setStyleSheet("background:#ffe58a")
        lay.addWidget(self.rack_edit)
        self.error = QLabel()
        self.error.setStyleSheet("color:#b00020")
        lay.addWidget(self.error)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.fixes: dict[tuple[int, int], CellFix | None] = {}
        self.rack: str = ""

    def collect(self) -> str | None:
        fixes = {}
        for (r, c), e in self.edits.items():
            cell = self.reading.cells[r][c]
            parsed = parse_cell(e.text())
            if isinstance(parsed, str):
                return f"row {r + 1}, col {c + 1}: {parsed}"
            if cell.uncertain or e.text() != cell_text(cell):
                fixes[(r, c)] = parsed
        rack = self.rack_edit.text().replace(" ", "")
        if any(not (ch == BLANK or ch.isalpha()) for ch in rack):
            return "the rack may only contain letters and ?"
        self.fixes = fixes
        self.rack = rack
        return None

    def _accept(self) -> None:
        problem = self.collect()
        if problem:
            self.error.setText(problem)
            return
        self.accept()

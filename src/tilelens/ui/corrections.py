from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from tilelens.rules.profile import BLANK
from tilelens.ui.design import Label, Radius, SectionLabel, Space
from tilelens.ui.glass_dialog import (
    CandidateKey,
    GlassDialog,
    LetterField,
    Notice,
    Pane,
    RackField,
)
from tilelens.vision.recognizer import CellFix, Reading

STEPS = ("Fix the highlighted squares", "Check the rack", "Apply")
LEGEND = (
    "A small letter is a blank tile. Start with ~ for a tile that is placed but not played yet."
)
ROW_HEIGHT = 68


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


class SquareRow(Pane):
    """One unclear square: its tile-shaped field with the guess in it, and the other likely
    letters as keycaps. A square with no guess has to be answered; one with a guess counts as
    answered, so a reading that is mostly right takes one press."""

    changed = Signal()
    advance = Signal(int)

    def __init__(self, key: tuple[int, int], cell, parent: QWidget | None = None):
        super().__init__(parent, radius=Radius.CARD)
        self.key = key
        self.cell = cell
        self._touched = False
        r, c = key
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 10, 14, 10)
        lay.setSpacing(Space.M)
        self.field = LetterField(cell_text(cell), label=f"Row {r + 1}, column {c + 1}")
        self.field.set_guess(bool(cell.letter))
        reason = cell.reason or "needs a look"
        tip = reason
        if cell.candidates:
            tip += " (maybe " + ", ".join(cell.candidates) + ")"
        self.field.setToolTip(tip)
        lay.addWidget(self.field)
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addStretch(1)
        text.addWidget(Label(f"Row {r + 1}, column {c + 1}", "label"))
        text.addWidget(Label(reason[:1].upper() + reason[1:], "footnote", "secondary", elide=True))
        text.addStretch(1)
        lay.addLayout(text, 1)
        self.keys: list[CandidateKey] = []
        letters: list[str] = []
        for ch in ([cell.letter] if cell.letter else []) + list(cell.candidates):
            ch = (ch or "").upper()
            if len(ch) == 1 and ch.isalpha() and ch not in letters:
                letters.append(ch)
        for ch in letters:
            self._add_key(lay, ch, f"{ch} for row {r + 1}, column {c + 1}")
        self._add_key(lay, "Empty", f"Empty square, row {r + 1}, column {c + 1}")
        self.field.text_changed.connect(self._on_text)
        self.field.entered.connect(lambda: self.advance.emit(1))
        self.field.stepped.connect(self.advance)
        self._refresh()

    def _add_key(self, lay: QHBoxLayout, text: str, label: str) -> None:
        key = CandidateKey(text, label=label)
        key.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        key.pressed_key.connect(self._pressed)
        lay.addWidget(key)
        self.keys.append(key)

    def _pressed(self, text: str) -> None:
        self._touched = True
        if text == "Empty":
            self.field.setText("", user=True)
            self._refresh()
            self.changed.emit()
            self.advance.emit(1)
            return
        self.field.type_letter(text)

    def _on_text(self, *_) -> None:
        self._touched = True
        self._refresh()
        self.changed.emit()

    @property
    def valid(self) -> bool:
        return self.field.parts()[3]

    @property
    def answered(self) -> bool:
        return self.valid and (self._touched or self.field.guess)

    def _refresh(self) -> None:
        letter, _, _, valid = self.field.parts()
        answered = self.answered
        for key in self.keys:
            same = key.text() == letter or (key.text() == "Empty" and not letter)
            key.set_active(answered and same)
        self.field.set_flag("warning" if valid and not answered else "")


class _Edits(dict):
    """edits[(row, col)] for any square: the unclear ones are on screen, the others are made on
    first use, so a square that was read right can still be fixed from code."""

    def __init__(self, dialog: CorrectionDialog):
        super().__init__()
        self._dialog = dialog

    def __missing__(self, key):
        dlg = self._dialog
        r, c = key
        if not (0 <= r < len(dlg.reading.cells) and 0 <= c < len(dlg.reading.cells[0])):
            raise KeyError(key)
        field = LetterField(cell_text(dlg.reading.cells[r][c]), dlg._spare)
        self[key] = field
        return field


class CorrectionDialog(GlassDialog):
    def __init__(self, reading: Reading, parent: QWidget | None = None):
        super().__init__("Check tiles", "", parent)
        self.reading = reading
        self.fixes: dict[tuple[int, int], CellFix | None] = {}
        self.rack: str = ""
        self._spare = QWidget(self)
        self._spare.hide()
        self.edits = _Edits(self)
        self.rows: list[SquareRow] = []
        self.set_steps(STEPS)

        self.scroll = QScrollArea()
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.viewport().setAutoFillBackground(False)
        self.scroll.setAccessibleName("Unclear squares")
        host = QWidget()
        host.setAutoFillBackground(False)
        self._rows_layout = QVBoxLayout(host)
        self._rows_layout.setContentsMargins(0, 0, Space.S, 0)
        self._rows_layout.setSpacing(Space.S)
        for key in reading.uncertain_cells:
            r, c = key
            row = SquareRow(key, reading.cells[r][c])
            row.setMinimumHeight(ROW_HEIGHT)
            row.changed.connect(self._update)
            row.advance.connect(lambda step, row=row: self._advance(row, step))
            self.edits[key] = row.field
            self.rows.append(row)
            self._rows_layout.addWidget(row)
        self._rows_layout.addStretch(1)
        self.scroll.setWidget(host)
        host.setAutoFillBackground(False)
        self.none_label = Label("No square needs a look.", "body", "secondary")
        self.none_label.setVisible(not self.rows)
        self.body.addWidget(self.none_label)
        self.body.addWidget(self.scroll, 1)
        self.scroll.setVisible(bool(self.rows))
        self.body.addWidget(Label(LEGEND, "footnote", "secondary", wrap=True))

        self.body.addSpacing(Space.XS)
        self.body.addWidget(SectionLabel("Rack"))
        self.rack_hint = Label("", "footnote", "secondary", wrap=True)
        self.body.addWidget(self.rack_hint)
        self.required = len(reading.rack)
        self.rack_edit = RackField(max(1, self.required), label="Rack, left to right")
        slots = [BLANK if t.blank else (t.letter or "") for t in reading.rack]
        self._rack_guess = [i for i, t in enumerate(reading.rack) if t.uncertain and slots[i]]
        self.rack_edit.set_slots(slots, guess=self._rack_guess)
        self.body.addWidget(self.rack_edit)
        self.rack_hint.set_text(
            "Highlighted letters are guesses. Type over any that are wrong."
            if self._rack_guess
            else "Left to right. A question mark is a blank."
        )
        self.status = Notice()
        self.body.addWidget(self.status)

        self.apply_btn, cancel = self.add_footer("Apply", "Cancel")
        self.apply_btn.clicked.connect(self._accept)
        self.focus_chain(*[row.field for row in self.rows], self.rack_edit, cancel, self.apply_btn)
        self.rack_edit.text_changed.connect(self._update)
        self._update()
        self.set_glass_size(580, max(540, min(740, 404 + 76 * len(self.rows))))
        self.set_glass_minimum(520, 480)
        if self.rows:
            self.rows[0].field.setFocus()
        else:
            self.rack_edit.setFocus()

    def _advance(self, row: SquareRow, step: int) -> None:
        i = self.rows.index(row) + step
        if 0 <= i < len(self.rows):
            target = self.rows[i]
            target.field.setFocus(Qt.FocusReason.TabFocusReason)
            self.scroll.ensureWidgetVisible(target, 0, Space.S)
        elif i >= len(self.rows):
            self.rack_edit.focus_slot(0)

    def _rack_missing(self) -> list[int]:
        slots = self.rack_edit.slots()
        return [i for i in range(self.required) if not (slots[i] if i < len(slots) else "")]

    def _first_problem(self) -> str | None:
        for row in self.rows:
            if not row.valid:
                r, c = row.key
                text = row.field.text()
                return f"Row {r + 1}, column {c + 1}: '{text}' is not a single letter"
        return None

    def _update(self, *_) -> None:
        open_rows = [row for row in self.rows if not row.answered]
        missing = self._rack_missing()
        self.rack_edit.set_flags({i: "warning" for i in missing})
        problem = self._first_problem()
        if problem:
            self.status.message(problem, "error")
        else:
            self.status.clear()
        n = len(open_rows)
        if n:
            self.set_subtitle(
                f"{n} squares still need a letter." if n != 1 else "1 square still needs a letter."
            )
        elif missing:
            self.set_subtitle("Fill in every tile of the rack.")
        elif self.rows:
            k = len(self.rows)
            self.set_subtitle(
                f"{k} square{'s' if k != 1 else ''} to check. "
                "The highlighted guess stays unless you pick another letter."
            )
        else:
            self.set_subtitle("Every square looks right. Check the rack, then apply.")
        done = [not open_rows, not missing]
        active = next((i for i, d in enumerate(done) if not d), len(done))
        self.steps.set_states(
            [
                "done" if i < len(done) and done[i] else "active" if i == active else "todo"
                for i in range(len(STEPS))
            ]
        )
        self.set_primary_enabled(not open_rows and not missing and problem is None)

    def collect(self) -> str | None:
        fixes = {}
        for (r, c), e in self.edits.items():
            cell = self.reading.cells[r][c]
            parsed = parse_cell(e.text())
            if isinstance(parsed, str):
                return f"row {r + 1}, col {c + 1}: {parsed}"
            if cell.uncertain or e.text() != cell_text(cell):
                fixes[(r, c)] = parsed
        if self._rack_missing():
            return "fill in every tile of the rack"
        self.fixes = fixes
        self.rack = self.rack_edit.text()
        return None

    def _accept(self) -> None:
        problem = self.collect()
        if problem:
            self.status.message(problem[:1].upper() + problem[1:], "error")
            return
        self.accept()

    def scroll_to(self, key: tuple[int, int]) -> None:
        for row in self.rows:
            if row.key == key:
                self.scroll.ensureWidgetVisible(row, 0, Space.S)
                row.field.setFocus()
                return


__all__ = ["CorrectionDialog", "SquareRow", "cell_text", "parse_cell"]

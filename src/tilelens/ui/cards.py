from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFontMetricsF, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from tilelens.solver.movegen import Move
from tilelens.state.names import square_name
from tilelens.ui.design import (
    FloatAnimator,
    GlassCard,
    Glyph,
    KeyCap,
    Label,
    Motion,
    Radius,
    RankBadge,
    ScoreBadge,
    Space,
    material_of,
    mix,
    paint_wash,
    rgba,
    type_font,
    with_alpha,
)
from tilelens.ui.design.paint import paint_control_rim


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


def _main_word(move: Move):
    words = move.breakdown.words
    return next(
        (w for w in words if (w.row, w.col, w.direction) == (move.row, move.col, move.direction)),
        words[0] if words else None,
    )


def score_parts(move: Move) -> tuple[int, int, int]:
    """Points of the played word, of the words it crosses and the full-rack bonus."""
    main = _main_word(move)
    bonus = move.breakdown.bonus
    main_total = main.total if main is not None else move.score - bonus
    return main_total, move.score - main_total - bonus, bonus


def where_text(move: Move) -> str:
    n = len(move.tiles)
    direction = "across" if move.direction == "H" else "down"
    return f"{square_name(move.row, move.col)} · {direction} · {n} {'tile' if n == 1 else 'tiles'}"


def split_text(move: Move) -> str:
    main, crosses, bonus = score_parts(move)
    parts = [f"Word {main}"]
    if crosses:
        parts.append(f"crosses {crosses}")
    if bonus:
        parts.append(f"bonus {bonus}")
    return " + ".join(parts)


def crossing_text(move: Move) -> str:
    main = _main_word(move)
    cross = [w for w in move.breakdown.words if w is not main]
    parts = [f"Also forms {', '.join(f'{w.text} {w.total}' for w in cross)}" if cross else ""]
    parts[0] = parts[0] or "No crossing words"
    blanks = [t.letter for t in move.tiles if t.blank]
    if blanks:
        parts.append(f"blank as {', '.join(blanks)}")
    return " · ".join(parts)


def spoken(move: Move, rank: int) -> str:
    return f"Play {rank}: {move.word}, {move.score} points, {where_text(move)}"


def restyle(label: Label, role: str, scale: float, tabular: bool = False) -> None:
    label.setFont(type_font(role, scale, tabular))
    label.updateGeometry()
    label.update()


def _line(role: str, scale: float = 1.0) -> int:
    return math.ceil(QFontMetricsF(type_font(role, scale)).height())


class ScoreText(ScoreBadge):
    """ScoreBadge that can be scaled and read back as text."""

    def text(self) -> str:
        return "" if self.score is None else f"{self.score} pts"

    def set_scale(self, scale: float) -> None:
        self.setFont(type_font("score", scale, tabular=True))
        self._unit_font = type_font("caption", scale)
        self.updateGeometry()
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        m = material_of(self)
        r = QRectF(self.rect())
        fm = QFontMetricsF(self.font())
        baseline = r.top() + fm.ascent() + (r.height() - fm.height()) / 2
        _, unit_w = self._widths()
        x_unit = r.right() - unit_w
        p.setFont(self._unit_font)
        p.setPen(m.text2)
        p.drawText(QPointF(x_unit, baseline), self._unit)
        p.setFont(self.font())
        p.setPen(mix(m.text, m.accent, 0.3 if m.dark else 0.55) if self._active else m.text)
        num = self._number()
        p.drawText(QPointF(x_unit - 3 - fm.horizontalAdvance(num), baseline), num)
        p.end()


class PlayCard(GlassCard):
    """One of the best plays: rank, word, position and score; the chosen one explains its score."""

    chosen = Signal(int)

    PAD_Y = 10
    GAP = 2
    DETAIL_ROWS = 2

    def __init__(self, index: int):
        super().__init__()
        self.index = index
        self.move_shown: Move | None = None
        self._scale = 1.0
        self._expanded = False
        self.rank = RankBadge(index + 1)
        self._lead = QWidget()
        lead = QVBoxLayout(self._lead)
        lead.setContentsMargins(0, 0, 0, 0)
        lead.addWidget(self.rank)
        self._lead_lay = lead
        self.word = Label("", "word", elide=True)
        self.lock = Glyph("lock", 14, "accent")
        self.lock.hide()
        self.where = Label("", "footnote", "secondary", elide=True)
        self.split = Label("", "footnote", "primary", elide=True)
        self.note = Label("", "footnote", "secondary", elide=True)
        self.points = ScoreText()
        body = QWidget()
        col = QVBoxLayout(body)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(self.GAP)
        self._top = QHBoxLayout()
        self._top.setContentsMargins(0, 0, 0, 0)
        self._top.setSpacing(Space.S)
        self._top.addWidget(self.word, 1)
        self._top.addWidget(self.lock, 0, Qt.AlignmentFlag.AlignVCenter)
        col.addLayout(self._top)
        col.addWidget(self.where)
        self.detail = QWidget()
        rows = QVBoxLayout(self.detail)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(self.GAP)
        rows.addWidget(self.split)
        rows.addWidget(self.note)
        col.addWidget(self.detail)
        self.detail.hide()
        self.set_leading(self._lead)
        self.set_body(body)
        self.set_trailing(self.points)
        self.clicked.connect(self._emit)
        self.set_scale(1.0)
        self.set_move(None)
        self.setProperty("selected", False)

    @classmethod
    def metrics(cls, scale: float = 1.0) -> tuple[int, int]:
        """Card height collapsed and expanded, for placeholders that stand in for a card."""
        pad = round(cls.PAD_Y * scale)
        gap = max(1, round(cls.GAP * scale))
        base = 2 * pad + _line("word", scale) + gap + _line("footnote", scale)
        return base, base + cls.DETAIL_ROWS * (gap + _line("footnote", scale))

    def _emit(self) -> None:
        if self.move_shown is not None:
            self.chosen.emit(self.index)

    def set_scale(self, scale: float) -> None:
        self._scale = scale
        s = scale
        self.row.setContentsMargins(
            round(Space.M * s), round(self.PAD_Y * s), round(Space.M * s), round(self.PAD_Y * s)
        )
        self.row.setSpacing(round(Space.M * s))
        gap = max(1, round(self.GAP * s))
        self.detail.layout().setSpacing(gap)
        self.layout().invalidate()
        body = self.word.parentWidget()
        body.layout().setSpacing(gap)
        side = round(24 * s)
        self.rank.setFixedSize(side, side)
        self.rank.setFont(type_font("label", 0.92 * s, tabular=True))
        self.lock.setFixedSize(round(14 * s), round(14 * s))
        restyle(self.word, "word", s)
        for label in (self.where, self.split, self.note):
            restyle(label, "footnote", s)
        self.points.set_scale(s)
        word_asc = QFontMetricsF(type_font("word", s)).ascent()
        score_asc = QFontMetricsF(type_font("score", s, True)).ascent()
        down = max(0, round(score_asc - word_asc))
        self._top.setContentsMargins(0, down, 0, 0)
        first = down + _line("word", s) / 2
        self._lead_lay.setContentsMargins(0, max(0, round(first - side / 2)), 0, 0)
        self.updateGeometry()

    def set_move(self, move: Move | None) -> None:
        self.move_shown = move
        self.setVisible(move is not None)
        if move is None:
            self.setAccessibleName("")
            return
        self.word.setText(move.word)
        self.points.set_score(move.score)
        self.where.setText(where_text(move))
        self.split.setText(split_text(move))
        self.note.setText(crossing_text(move))
        self.setAccessibleName(spoken(move, self.index + 1))
        self.setToolTip("")
        self._sync_detail()

    def set_selected(self, selected: bool) -> None:
        super().set_selected(selected)
        self.setProperty("selected", bool(selected))
        self.rank.set_active(selected)
        self.points.set_active(selected)
        tone = "primary" if selected else "secondary"
        self.where.set_tone(tone)
        self.note.set_tone(tone)
        self._sync_detail()

    def set_held(self, held: bool) -> None:
        self.lock.setVisible(held)

    def _sync_detail(self) -> None:
        show = self.selected and self.move_shown is not None
        if show != self.detail.isVisible() or show != self._expanded:
            self._expanded = show
            self.detail.setVisible(show)
            self.updateGeometry()

    @property
    def expanded(self) -> bool:
        return self._expanded


class SkeletonCard(QWidget):
    """A card-shaped placeholder: still, never shimmering."""

    def __init__(self, tall: bool = False):
        super().__init__()
        self._tall = tall
        self._scale = 1.0
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.set_scale(1.0)

    def set_scale(self, scale: float) -> None:
        self._scale = scale
        collapsed, expanded = PlayCard.metrics(scale)
        self.setFixedHeight(expanded if self._tall else collapsed)
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        m = material_of(self)
        s = self._scale
        r = QRectF(self.rect())
        radius = min(float(Radius.CARD) * s, r.height() / 2)
        paint_wash(p, r, radius, m.fill)
        paint_control_rim(p, r, radius, m, 0.7)
        bar = mix(m.fill_hover, m.fill_press, 0.6)
        pad_x, pad_y = Space.M * s, PlayCard.PAD_Y * s
        side = 24 * s
        x_text = pad_x + side + Space.M * s
        word_h = _line("word", s)
        line_h = _line("footnote", s)
        gap = max(1.0, PlayCard.GAP * s)
        first = pad_y + word_h / 2
        paint_wash(p, QRectF(pad_x, first - side / 2, side, side), side / 2, bar)
        score_w = 54 * s
        paint_wash(
            p,
            QRectF(r.right() - pad_x - score_w, first - 9 * s, score_w, 18 * s),
            9 * s,
            bar,
        )
        word = QRectF(x_text, first - 7 * s, 112 * s, 14 * s)
        paint_wash(p, word, 7 * s, bar)
        y = pad_y + word_h + gap
        rows = 3 if self._tall else 1
        for i in range(rows):
            w = (156, 188, 120)[i] * s
            top = y + i * (line_h + gap) + (line_h - 8 * s) / 2
            paint_wash(
                p, QRectF(x_text, top, min(w, r.right() - pad_x - x_text), 8 * s), 4 * s, bar
            )
        p.end()


class Surface(QWidget):
    """A flat pane inside the glass: the empty and first-run states sit on it."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._scale = 1.0
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

    def set_scale(self, scale: float) -> None:
        self._scale = scale
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        m = material_of(self)
        r = QRectF(self.rect())
        radius = float(Radius.CARD) * self._scale
        paint_wash(p, r, radius, m.fill)
        paint_control_rim(p, r, radius, m, 0.8)
        p.end()


class LetterKey(KeyCap):
    """A keycap that answers: the letter the game shows on a tile that was hard to read."""

    clicked = Signal()

    HEIGHT = 30

    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(text, parent)
        self._scale = 1.0
        self._hover = FloatAnimator(self, 0.0, duration=Motion.FAST)
        self._press = FloatAnimator(self, 0.0, duration=Motion.FAST)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(f"Letter {text}")
        self.setAccessibleDescription("Use this letter for the tile")
        self.set_scale(1.0)

    def set_scale(self, scale: float) -> None:
        self._scale = scale
        self.setFont(type_font("label", scale))
        self.setFixedHeight(round(self.HEIGHT * scale))
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:
        w = QFontMetricsF(self.font()).horizontalAdvance(self.text())
        return QSize(
            max(round(34 * self._scale), math.ceil(w) + round(18 * self._scale)), self.height()
        )

    def enterEvent(self, event) -> None:
        self._hover.to(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover.to(0.0)
        self._press.to(0.0)
        super().leaveEvent(event)

    def hideEvent(self, event) -> None:
        self._hover.jump(0.0)
        self._press.jump(0.0)
        super().hideEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press.to(1.0)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        was = self._press.target > 0.5
        self._press.to(0.0)
        if (
            was
            and event.button() == Qt.MouseButton.LeftButton
            and self.rect().contains(event.position().toPoint())
        ):
            self.clicked.emit()
        event.accept()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        m = material_of(self)
        full = QRectF(self.rect())
        lip = 2.0 * self._scale
        cap = QRectF(full.left(), full.top(), full.width(), full.height() - lip)
        radius = min(10.0 * self._scale, cap.height() / 2)
        hover, press = self._hover.value, self._press.value
        face = cap.translated(0, lip * 0.6 * press)
        paint_wash(
            p,
            QRectF(cap).translated(0, lip),
            radius,
            rgba(0, 0, 0, 0.30) if m.dark else rgba(20, 30, 60, 0.20),
        )
        paint_wash(
            p, face, radius, rgba(255, 255, 255, 0.13) if m.dark else rgba(255, 255, 255, 0.85)
        )
        if hover or press:
            paint_wash(p, face, radius, with_alpha(m.accent, 0.20 * hover + 0.14 * press))
        paint_control_rim(p, face, radius, m, 0.9)
        p.setFont(self.font())
        p.setPen(m.text)
        p.drawText(face, int(Qt.AlignmentFlag.AlignCenter.value), self.text())
        p.end()

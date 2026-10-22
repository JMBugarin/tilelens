"""Translucent results panel that floats over the game next to the board."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from tilelens.solver.movegen import Move
from tilelens.state.controller import Snapshot, Status
from tilelens.state.memory import Coverage
from tilelens.ui.cards import describe
from tilelens.ui.glass import AMBER, CYAN, GREEN, square_name

STYLE = """
QWidget { color: #eef4ff; font-size: 13px; background: transparent; }
#title { font-size: 20px; font-weight: 700; color: #7fe7f7; }
#subtitle { font-size: 15px; color: #c9d6f0; }
#live { font-size: 12px; font-weight: 700; }
#card { border: 1px solid rgba(255,255,255,40); border-radius: 12px;
        background: rgba(255,255,255,14); }
#card[selected="true"] { border: 2px solid #22d3ee; background: rgba(34,211,238,30); }
#rank { border: 1px solid rgba(255,255,255,90); border-radius: 13px; font-weight: 700;
        min-width: 26px; max-width: 26px; min-height: 26px; max-height: 26px; }
#card[selected="true"] #rank { background: #22d3ee; color: #08202a; border: none; }
#word { font-size: 18px; font-weight: 700; letter-spacing: 1px; }
#points { font-size: 18px; font-weight: 700; }
#card[selected="true"] #points { color: #7fe7f7; }
#where { color: #7fe7f7; }
#muted { color: #9fb0cc; font-size: 12px; }
QPushButton { border: 1px solid rgba(255,255,255,60); border-radius: 8px; padding: 5px 12px;
              background: rgba(255,255,255,16); }
QPushButton:hover { background: rgba(255,255,255,34); }
#check { border-color: #fbbf24; color: #fbbf24; }
"""


def coverage_subtitle(snap: Snapshot | None) -> str:
    if snap is None or snap.status != Status.READY or snap.result is None:
        return "Highest-scoring plays"
    if snap.coverage == Coverage.PARTIAL:
        return "Best plays among squares in view"
    return "Highest-scoring plays" if snap.result.complete else "Best plays found so far"


class GlassCard(QFrame):
    clicked = Signal(int)

    def __init__(self, index: int):
        super().__init__()
        self.index = index
        self.move_shown: Move | None = None
        self.setObjectName("card")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(12)
        self.rank = QLabel(str(index + 1))
        self.rank.setObjectName("rank")
        self.rank.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.rank, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(2)
        top = QHBoxLayout()
        self.word = QLabel()
        self.word.setObjectName("word")
        self.points = QLabel()
        self.points.setObjectName("points")
        top.addWidget(self.word, 1)
        top.addWidget(self.points)
        mid = QHBoxLayout()
        self.where = QLabel()
        self.where.setObjectName("where")
        self.split = QLabel()
        self.split.setObjectName("where")
        mid.addWidget(self.where, 1)
        mid.addWidget(self.split)
        self.note = QLabel()
        self.note.setObjectName("muted")
        text.addLayout(top)
        text.addLayout(mid)
        text.addWidget(self.note)
        lay.addLayout(text, 1)
        self.set_move(None)
        self.set_selected(False)

    def set_move(self, move: Move | None) -> None:
        self.move_shown = move
        self.setVisible(move is not None)
        if move is None:
            return
        d = describe(move)
        main = d["main_total"]
        crosses = move.score - main - move.breakdown.bonus
        self.word.setText(move.word)
        self.points.setText(f"{move.score} pts")
        self.where.setText(f"{square_name(move.row, move.col)} · {d['direction'].capitalize()}")
        parts = [f"Word {main}"]
        if crosses:
            parts.append(f"crosses {crosses}")
        if move.breakdown.bonus:
            parts.append(f"bonus {move.breakdown.bonus}")
        self.split.setText(" + ".join(parts))
        notes = []
        if d["cross"] != "none":
            notes.append("Includes crossing words")
        if d["blanks"]:
            notes.append(f"Blank as {', '.join(t.letter for t in move.tiles if t.blank)}")
        self.note.setText(" · ".join(notes) or f"{d['tiles']} tiles")

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        for w in (self, self.rank, self.points):
            w.style().unpolish(w)
            w.style().polish(w)

    def mousePressEvent(self, event) -> None:
        if self.move_shown is not None:
            self.clicked.emit(self.index)
        super().mousePressEvent(event)


class Legend(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(26)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        y = self.height() / 2
        x = 2.0
        for kind, text in (("dot", "Rack read"), ("ghost", "Suggested"), ("tile", "Existing")):
            if kind == "dot":
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(GREEN)
                p.drawEllipse(QRectF(x, y - 5, 10, 10))
                x += 16
            elif kind == "ghost":
                pen = QPen(CYAN, 1.5)
                pen.setStyle(Qt.PenStyle.DashLine)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(QRectF(x, y - 9, 18, 18), 4, 4)
                x += 24
            else:
                p.setPen(QPen(QColor(200, 170, 120), 1))
                p.setBrush(QColor(246, 230, 200))
                p.drawRoundedRect(QRectF(x, y - 9, 18, 18), 4, 4)
                x += 24
            p.setPen(CYAN if kind == "ghost" else QColor(238, 244, 255))
            p.drawText(QRectF(x, 0, 90, self.height()), Qt.AlignmentFlag.AlignVCenter, text)
            x += p.fontMetrics().horizontalAdvance(text) + 18
        p.end()


class GlassPanel(QWidget):
    """Shows the three best plays. Clicking a card previews that play on the board."""

    card_clicked = Signal(int)
    pause_clicked = Signal()
    rescan_clicked = Signal()
    check_clicked = Signal()

    def __init__(self):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setStyleSheet(STYLE)
        self.setFixedWidth(340)
        self._drag: QPoint | None = None
        self.user_moved = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(8)
        head = QHBoxLayout()
        self.title = QLabel("TileLens")
        self.title.setObjectName("title")
        self.live = QLabel()
        self.live.setObjectName("live")
        head.addWidget(self.title, 1)
        head.addWidget(self.live)
        lay.addLayout(head)
        self.subtitle = QLabel("Highest-scoring plays")
        self.subtitle.setObjectName("subtitle")
        lay.addWidget(self.subtitle)
        self.cards = [GlassCard(i) for i in range(3)]
        for card in self.cards:
            card.clicked.connect(self.card_clicked)
            lay.addWidget(card)
        self.message = QLabel()
        self.message.setObjectName("muted")
        self.message.setWordWrap(True)
        lay.addWidget(self.message)
        row = QHBoxLayout()
        self.pause_btn = QPushButton("Pause")
        self.rescan_btn = QPushButton("Rescan")
        self.check_btn = QPushButton("Check tiles")
        self.check_btn.setObjectName("check")
        self.updated = QLabel()
        self.updated.setObjectName("muted")
        self.pause_btn.clicked.connect(self.pause_clicked)
        self.rescan_btn.clicked.connect(self.rescan_clicked)
        self.check_btn.clicked.connect(self.check_clicked)
        row.addWidget(self.pause_btn)
        row.addWidget(self.rescan_btn)
        row.addWidget(self.check_btn)
        row.addStretch(1)
        row.addWidget(self.updated)
        lay.addLayout(row)
        lay.addWidget(Legend())
        self.show_snapshot(None, 0)

    def show_snapshot(self, snap: Snapshot | None, selected: int, age: float | None = None) -> None:
        ready = snap is not None and snap.status == Status.READY and snap.result is not None
        moves = snap.result.moves if ready else []
        for i, card in enumerate(self.cards):
            card.set_move(moves[i] if i < len(moves) else None)
            card.set_selected(i == selected and card.move_shown is not None)
        if snap is None:
            live, color = "SETUP", QColor(150, 160, 180)
        elif snap.status == Status.READY:
            live, color = "LIVE", GREEN
        elif snap.status in (Status.CHECK, Status.NOT_VISIBLE):
            live, color = snap.status.value.upper(), AMBER
        else:
            live, color = snap.status.value.upper(), CYAN
        self.live.setText(f"● {live}")
        self.live.setStyleSheet(f"color: {color.name()}")
        self.subtitle.setText(coverage_subtitle(snap))
        if snap is None:
            msg = "Calibrate from the main window to start."
        elif ready and not moves:
            msg = snap.detail or "No legal placement."
        elif not ready:
            msg = snap.detail if snap.detail != snap.status.value else ""
        elif snap.coverage == Coverage.PARTIAL:
            msg = "Zoom out briefly so every square can be checked."
        else:
            msg = ""
        self.message.setText(msg)
        self.message.setVisible(bool(msg))
        self.check_btn.setVisible(snap is not None and snap.status == Status.CHECK)
        self.pause_btn.setText(
            "Resume" if snap is not None and snap.status == Status.PAUSED else "Pause"
        )
        self.set_age(age)
        self.adjustSize()

    def set_age(self, age: float | None) -> None:
        if age is None:
            self.updated.setText("")
        elif age < 2:
            self.updated.setText("Updated just now")
        else:
            self.updated.setText(f"Updated {age:.0f} s ago")

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(34, 211, 238, 90), 1.2))
        p.setBrush(QColor(13, 18, 36, 222))
        p.drawRoundedRect(rect, 18, 18)
        p.end()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event) -> None:
        if self._drag is not None:
            self.move(event.globalPosition().toPoint() - self._drag)
            self.user_moved = True

    def mouseReleaseEvent(self, event) -> None:
        self._drag = None


__all__ = ["GlassPanel", "coverage_subtitle"]

"""The floating results panel: three plays beside the board, in glass."""

from __future__ import annotations

import contextlib
import math
from dataclasses import dataclass

from PySide6.QtCore import QMetaMethod, QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFontMetricsF, QGuiApplication, QKeySequence, QPainter
from PySide6.QtWidgets import (
    QAbstractButton,
    QGridLayout,
    QHBoxLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from tilelens.solver.movegen import Move
from tilelens.state.controller import WAITING, Snapshot, Status
from tilelens.state.memory import Coverage
from tilelens.state.phase import phase_of
from tilelens.ui.cards import (
    LetterKey,
    PlayCard,
    ScoreText,
    SkeletonCard,
    Surface,
    restyle,
    where_text,
)
from tilelens.ui.cards import describe as describe
from tilelens.ui.design import (
    FLOATING,
    SHADOW_BLEED,
    Appearance,
    Banner,
    Divider,
    FloatAnimator,
    GlassButton,
    GlassWindow,
    IconButton,
    KeyCombo,
    Label,
    LoopAnimator,
    Material,
    Motion,
    Radius,
    Space,
    default_material,
    material_of,
    mix,
    native_backdrop,
    paint_icon,
    paint_wash,
    type_font,
    with_alpha,
)
from tilelens.ui.design.components import TONES
from tilelens.ui.design.paint import dim, flags, paint_control_rim
from tilelens.ui.panel_placement import PanelPlacer, Placement, panel_scale

CONTENT_WIDTH = 340
PAD = Space.S
BAR_HEIGHT = 44
HEADER_HEIGHT = 32
STALE_AFTER = 10.0
MAX_CHIPS = 5

ERROR, ANSWER, INFO = "error", "answer", "info"
_RANK = {ERROR: 0, ANSWER: 1, INFO: 2}
_KIND = {ERROR: "error", ANSWER: "warning", INFO: "info"}


def coverage_subtitle(snap: Snapshot | None) -> str:
    if snap is None or snap.status != Status.READY or snap.result is None:
        return "Highest-scoring plays"
    if snap.coverage == Coverage.PARTIAL:
        return "Best plays among squares in view"
    if snap.coverage == Coverage.TRACKING and snap.result.complete:
        return "Highest-scoring plays · view partly remembered"
    return "Highest-scoring plays" if snap.result.complete else "Best plays found so far"


@dataclass(frozen=True)
class Notice:
    rank: str
    text: str
    action: str = ""
    do: str = ""
    dismissible: bool = False

    @property
    def kind(self) -> str:
        return _KIND[self.rank]


@dataclass(frozen=True)
class PanelView:
    """Everything the panel shows for a snapshot, decided apart from any widget."""

    label: str
    tone: str
    working: bool = False
    caption: str = ""
    moves: tuple[Move, ...] = ()
    skeleton: bool = False
    empty: tuple[str, str] | None = None
    setup: bool = False
    notice: Notice | None = None
    chips: tuple[str, object, tuple[str, ...]] | None = None
    held: int | None = None
    more: int = 0


def _empty_reason(snap: Snapshot) -> tuple[str, str]:
    detail = (snap.detail or "").strip()
    if detail.lower().startswith("rack is empty"):
        return "Rack is empty", "Plays return when your next tiles arrive."
    if snap.coverage == Coverage.PARTIAL:
        return (
            "No legal placement",
            "Some squares are out of view. Zoom out briefly so every square can be checked.",
        )
    return "No legal placement", "No word fits this rack on the board with the word list."


def _letters(entry) -> tuple[str, ...]:
    _kind, _where, guess, cands = entry
    found: list[str] = []
    for ch in ([guess] if guess else []) + list(cands):
        if ch and ch not in found:
            found.append(ch)
    return tuple(found[:MAX_CHIPS])


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def view_of(snap: Snapshot | None, framed: bool = False, note: str = "") -> PanelView:
    """Map a tracker snapshot, and the one thing the session wants noticed, to the panel."""
    notes: list[Notice] = []
    if note:
        notes.append(Notice(ANSWER, note, "Calibrate", "calibrate", dismissible=True))
    if snap is None:
        return PanelView("Setup", "idle", setup=True, notice=notes[0] if notes else None)
    phase = phase_of(snap)
    st, detail = snap.status, snap.detail or ""
    label, tone = phase.label, phase.tone
    held = snap.held
    ready = st == Status.READY and snap.result is not None
    moves = tuple(snap.result.moves[:3]) if ready else ()
    unsure = snap.unsure if held is None else ()
    chips = None
    if unsure:
        chips = (unsure[0][0], unsure[0][1], _letters(unsure[0]))
    caption = coverage_subtitle(snap)
    working = False
    skeleton = False
    empty = None
    if ready:
        if not moves:
            empty = _empty_reason(snap)
            caption = ""
        elif detail == WAITING:
            caption = "This suggestion stays until you submit"
        elif detail == "Updating":
            caption = "Updating for newly seen squares"
        if held is not None:
            notes.insert(
                0,
                Notice(
                    INFO,
                    "Holding this play. Tracking resumes when your rack refills.",
                    "Resume tracking",
                    "release",
                ),
            )
        elif unsure:
            text = snap.notice or "A tile is hard to read. Tap the letter it shows."
            notes.insert(0, Notice(ANSWER, text))
        elif snap.notice:
            notes.append(Notice(INFO, snap.notice, dismissible=True))
        if moves and snap.coverage == Coverage.PARTIAL and held is None:
            text = (
                "A few squares sit behind the game's panels, so plays avoid them."
                if framed
                else "Zoom out briefly so every square can be checked."
            )
            notes.append(Notice(INFO, text, dismissible=True))
    elif st == Status.READING:
        skeleton = True
        if detail == WAITING:
            caption = "Suggestions return once you submit"
        elif detail == "Aligning":
            working, caption = True, "Finding the board"
        else:
            working, caption = True, "Reading the board and your rack"
    elif st == Status.SOLVING:
        skeleton, working, caption = True, True, "Searching for the best plays"
    elif st == Status.CHECK:
        low = detail.lower()
        caption = "Plays return once these are confirmed"
        if low.startswith(("solver stopped", "search failed")):
            label, tone = "Search problem", "error"
            caption = "Plays return after a rescan"
            notes.insert(0, Notice(ERROR, _capital(detail) + ".", "Rescan", "rescan"))
        elif "recalibrate" in low:
            label = "Recalibrate"
            notes.insert(
                0,
                Notice(
                    ANSWER,
                    "The board changed size. Calibrate again so it can be read.",
                    "Calibrate",
                    "calibrate",
                ),
            )
        else:
            label = "Check tiles"
            notes.insert(0, Notice(ANSWER, _capital(detail), "Check tiles", "check"))
    elif st == Status.PAUSED:
        caption = "Tracking is paused"
        notes.insert(
            0, Notice(INFO, "Paused. Resume when you want suggestions again.", "Resume", "pause")
        )
    elif st == Status.NOT_VISIBLE:
        caption = "Waiting for the game"
        if "permission" in detail.lower():
            label, tone = "No capture", "error"
            notes.insert(
                0,
                Notice(
                    ERROR,
                    "Screen Recording is off for TileLens. Allow it, then reopen the app.",
                    "Open Settings",
                    "permission",
                ),
            )
        else:
            notes.insert(0, Notice(ANSWER, phase.hint or detail, "Rescan", "rescan"))
    notice = min(notes, key=lambda n: _RANK[n.rank]) if notes else None
    return PanelView(
        label=label,
        tone=tone,
        working=working,
        caption=caption,
        moves=moves,
        skeleton=skeleton,
        empty=empty,
        notice=notice,
        chips=chips,
        held=held,
        more=max(0, len(unsure) - 1),
    )


def age_text(age: float | None) -> str:
    if age is None:
        return ""
    if age < 2:
        return "Updated just now"
    if age < 90:
        return f"Updated {age:.0f} s ago"
    return f"Updated {age / 60:.0f} min ago"


def keycaps(sequence: str) -> str:
    """A shortcut in the platform's own notation, as "+"-joined keys for KeyCombo."""
    if not sequence:
        return ""
    text = QKeySequence(sequence).toString(QKeySequence.SequenceFormat.NativeText) or sequence
    if "+" in text or len(text) <= 1:
        return text
    return "+".join(text)


class AppMark(QWidget):
    """The TileLens mark: a lens on a rounded tile in the accent."""

    def __init__(self, size: int = 24):
        super().__init__()
        self._side = size
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAccessibleName("TileLens")

    def set_scale(self, scale: float) -> None:
        side = round(self._side * scale)
        self.setFixedSize(side, side)

    def paintEvent(self, event) -> None:
        from PySide6.QtGui import QLinearGradient

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        m = material_of(self)
        r = QRectF(self.rect())
        radius = r.width() * 0.3
        grad = QLinearGradient(r.topLeft(), r.bottomRight())
        grad.setColorAt(0, mix(m.accent, m.text, 0.18 if m.dark else 0.10))
        grad.setColorAt(1, mix(m.accent, m.on_accent, 0.14))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawRoundedRect(r, radius, radius)
        c = r.center()
        ring = r.width() * 0.27
        pen_w = max(1.4, r.width() * 0.085)
        from PySide6.QtGui import QPen

        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(m.on_accent, pen_w))
        p.drawEllipse(c, ring, ring)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(with_alpha(m.on_accent, 0.9))
        p.drawEllipse(c, r.width() * 0.07, r.width() * 0.07)
        p.end()


class StatePill(QWidget):
    """Dot and label in a tinted capsule; a small spinner takes the dot's place while the
    tracker is working and nothing is on show. It never pulses."""

    def __init__(self, text: str = "", tone: str = "idle", dot_only: bool = False):
        super().__init__()
        self._text = text
        self._tone = "idle"
        self._working = False
        self._dot_only = dot_only
        self._scale = 1.0
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._loop = LoopAnimator(self, 900)
        self.set_scale(1.0)
        self.set_status(text, tone)

    def text(self) -> str:
        return self._text

    @property
    def tone(self) -> str:
        return self._tone

    @property
    def spinning(self) -> bool:
        return self._loop.running

    @property
    def working(self) -> bool:
        return self._working

    def set_scale(self, scale: float) -> None:
        self._scale = scale
        self.setFont(type_font("caption", scale))
        self.setFixedHeight(round(22 * scale))
        self.updateGeometry()
        self.update()

    def set_status(self, text: str, tone: str, working: bool = False) -> None:
        if tone not in TONES:
            raise ValueError(f"tone must be one of {TONES}")
        self._text, self._tone, self._working = text, tone, bool(working)
        self.setAccessibleName(text)
        self.setAccessibleDescription(f"Status: {tone}")
        if working:
            self._loop.start()
        else:
            self._loop.stop()
        self.updateGeometry()
        self.update()

    def _metrics(self) -> tuple[float, float, float]:
        s = self._scale
        return 10 * s, 7 * s, 6 * s

    def sizeHint(self) -> QSize:
        pad, dot, gap = self._metrics()
        h = round(22 * self._scale)
        if self._dot_only:
            return QSize(round(dot + 2 * 4 * self._scale + 5), h)
        w = QFontMetricsF(self.font()).horizontalAdvance(self._text)
        return QSize(math.ceil(w + pad + dot + gap + pad), h)

    def minimumSizeHint(self) -> QSize:
        if self._dot_only:
            return self.sizeHint()
        pad, dot, gap = self._metrics()
        return QSize(math.ceil(2 * pad + dot + gap + 36 * self._scale), self.sizeHint().height())

    def _color(self, m: Material):
        return {
            "ok": m.success,
            "busy": m.accent,
            "warn": m.warning,
            "idle": m.text3,
            "error": m.danger,
        }[self._tone]

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        m = material_of(self)
        c = self._color(m)
        r = QRectF(self.rect())
        pad, dot, gap = self._metrics()
        idle = self._tone == "idle"
        if not self._dot_only:
            fill = (0.10 if idle else 0.16) if m.dark else (0.09 if idle else 0.12)
            paint_wash(
                p, r, r.height() / 2, with_alpha(c, fill), with_alpha(c, 0.0 if idle else 0.30)
            )
            if idle:
                paint_control_rim(p, r, r.height() / 2, m, 0.8)
        cx = (r.center().x()) if self._dot_only else pad + dot / 2
        if self._working:
            side = dot * 1.7
            p.save()
            p.translate(cx, r.center().y())
            p.rotate(self._loop.phase * 360.0)
            paint_icon(p, "spinner", QRectF(-side / 2, -side / 2, side, side), c, 2.6)
            p.restore()
        else:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawEllipse(QRectF(cx - dot / 2, r.center().y() - dot / 2, dot, dot))
        if not self._dot_only:
            p.setFont(self.font())
            p.setPen(m.text if not idle else m.text2)
            x = pad + dot + gap
            text = QFontMetricsF(self.font()).elidedText(
                self._text, Qt.TextElideMode.ElideRight, r.width() - x - pad
            )
            p.drawText(
                QRectF(x, 0, r.width() - x - pad, r.height()),
                flags(Qt.AlignmentFlag.AlignLeft, Qt.AlignmentFlag.AlignVCenter),
                text,
            )
        p.end()


class RoundButton(QAbstractButton):
    """Round glass button with an icon that can face either way."""

    def __init__(self, icon: str, tooltip: str, size: int = 28, flip: bool = False):
        super().__init__()
        self._icon = icon
        self._flip = flip
        self._size = size
        self._hover = FloatAnimator(self, 0.0, duration=Motion.FAST)
        self._press = FloatAnimator(self, 0.0, duration=Motion.FAST)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(size, size)
        self.pressed.connect(lambda: self._press.to(1.0))
        self.released.connect(lambda: self._press.to(0.0))
        self.set_tooltip(tooltip)

    def set_tooltip(self, text: str) -> None:
        self.setToolTip(text)
        self.setAccessibleName(text)

    def set_icon_name(self, name: str) -> None:
        self._icon = name
        self.update()

    def set_scale(self, scale: float) -> None:
        side = round(self._size * scale)
        self.setFixedSize(side, side)

    def enterEvent(self, event) -> None:
        if self.isEnabled():
            self._hover.to(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover.to(0.0)
        super().leaveEvent(event)

    def hideEvent(self, event) -> None:
        self._hover.jump(0.0)
        self._press.jump(0.0)
        super().hideEvent(event)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        m = material_of(self)
        en = self.isEnabled()
        hover = self._hover.value if en else 0.0
        press = self._press.value if en else 0.0
        fill = mix(mix(m.fill, m.fill_hover, hover), m.fill_press, press)
        r = QRectF(self.rect())
        paint_wash(p, r, r.height() / 2, dim(fill, en))
        paint_control_rim(p, r, r.height() / 2, m, 1.0 if en else 0.5)
        side = r.height() * 0.5
        icon = QRectF(r.center().x() - side / 2, r.center().y() - side / 2, side, side)
        p.save()
        if self._flip:
            p.translate(r.center().x(), 0)
            p.scale(-1, 1)
            p.translate(-r.center().x(), 0)
        paint_icon(p, self._icon, icon, dim(m.text if hover else m.text2, en), 2.0)
        p.restore()
        p.end()


class NoticeBanner(Banner):
    """Banner with its action on a row of its own, so a short action never squeezes the text."""

    def __init__(self):
        super().__init__("", "info", action_text="", dismissible=False)
        old = self.layout()
        for w in (self.glyph, self.label, self.action, self.close_button):
            old.removeWidget(w)
        QWidget().setLayout(old)
        grid = QGridLayout(self)
        grid.setContentsMargins(12, 10, 8, 10)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        grid.addWidget(self.glyph, 0, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self.label, 0, 1)
        grid.addWidget(self.close_button, 0, 2, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self.action, 1, 1, Qt.AlignmentFlag.AlignLeft)
        grid.setColumnStretch(1, 1)
        self._grid = grid
        self.glyph.setContentsMargins(0, 0, 0, 0)

    def set_scale(self, scale: float) -> None:
        g = self._grid
        g.setContentsMargins(
            round(12 * scale), round(10 * scale), round(8 * scale), round(10 * scale)
        )
        g.setHorizontalSpacing(round(10 * scale))
        g.setVerticalSpacing(round(8 * scale))
        side = round(16 * scale)
        self.glyph.setFixedSize(side, side)
        restyle(self.label, "footnote", scale)
        self.action.setFont(type_font("label", 0.92 * scale))
        self.action.setFixedHeight(round(26 * scale))
        self.close_button.setFixedSize(round(24 * scale), round(24 * scale))

    def show_notice(self, notice: Notice) -> None:
        self.set_kind(notice.kind)
        self.set_text(notice.text)
        self.set_action(notice.action)
        self.action.set_variant("tinted" if notice.rank == INFO else "prominent")
        self.set_dismissible(notice.dismissible)
        self.show()


class CheckRow(QWidget):
    """One line of the first-run list: a state mark and what it is about."""

    def __init__(self, text: str, problem: str = ""):
        super().__init__()
        self._state: bool | None = None
        self._scale = 1.0
        self._texts = (text, problem or text)
        self.text = Label(text, "body", "primary", elide=True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(Space.M)
        self._lay = lay
        self._mark = QWidget()
        self._mark.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._mark.paintEvent = self._paint_mark
        self._mark.setFixedSize(20, 20)
        lay.addWidget(self._mark)
        lay.addWidget(self.text, 1)
        self.set_state(None)

    @property
    def state(self) -> bool | None:
        return self._state

    def set_state(self, state: bool | None) -> None:
        self._state = state
        self.text.set_text(self._texts[1] if state is False else self._texts[0])
        self.text.set_tone("secondary" if state else "primary")
        self.setAccessibleName(
            f"{self.text.text()}: {'done' if state else 'not done' if state is None else 'problem'}"
        )
        self._mark.update()

    def set_scale(self, scale: float) -> None:
        self._scale = scale
        side = round(20 * scale)
        self._mark.setFixedSize(side, side)
        self._lay.setSpacing(round(Space.M * scale))
        restyle(self.text, "body", scale)

    def _paint_mark(self, event) -> None:
        p = QPainter(self._mark)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        m = material_of(self)
        r = QRectF(self._mark.rect()).adjusted(1, 1, -1, -1)
        if self._state is True:
            paint_wash(p, r, r.width() / 2, m.success)
            paint_icon(p, "check", r.adjusted(3.6, 3.6, -3.6, -3.6), m.on_accent, 3.0)
        elif self._state is False:
            paint_wash(p, r, r.width() / 2, m.warning)
            paint_icon(p, "close", r.adjusted(4.6, 4.6, -4.6, -4.6), m.on_accent, 3.2)
        else:
            paint_wash(p, r, r.width() / 2, with_alpha(m.text, 0.06 if m.dark else 0.04))
            paint_wash(p, r, r.width() / 2, with_alpha(m.text, 0.0), with_alpha(m.text2, 0.7))
        p.end()


class SetupCard(Surface):
    """First run: what TileLens needs, what is done, and the one next action."""

    action_clicked = Signal(str)

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(Space.L, Space.L, Space.L, Space.L)
        lay.setSpacing(Space.M)
        self._lay = lay
        self.title = Label("Set up TileLens", "headline", elide=True)
        self.sub = Label(
            "Plays appear here once the game is in view.", "footnote", "secondary", elide=True
        )
        head = QVBoxLayout()
        head.setSpacing(2)
        head.addWidget(self.title)
        head.addWidget(self.sub)
        lay.addLayout(head)
        self.rows = {
            "capture": CheckRow("Screen capture allowed", "Screen Recording is off"),
            "words": CheckRow("Word list loaded", "No word list found"),
            "game": CheckRow("Game open, board and rack in view"),
        }
        rows = QVBoxLayout()
        rows.setSpacing(Space.S)
        for row in self.rows.values():
            rows.addWidget(row)
        self._rows_lay = rows
        lay.addLayout(rows)
        self.button = GlassButton("Calibrate", variant="prominent")
        self.button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._do = "calibrate"
        self.button.clicked.connect(lambda: self.action_clicked.emit(self._do))
        lay.addWidget(self.button)
        self.set_state(None, None, None)

    def set_state(self, capture: bool | None, words: bool | None, game: bool | None) -> None:
        for key, value in zip(("capture", "words", "game"), (capture, words, game), strict=True):
            self.rows[key].set_state(value)
        if capture is False:
            self.button.setText("Open Settings")
            self._do = "permission"
        elif words is False:
            self.button.setText("Choose a word list")
            self._do = "settings"
        else:
            self.button.setText("Calibrate")
            self._do = "calibrate"

    def set_scale(self, scale: float) -> None:
        super().set_scale(scale)
        s = scale
        self._lay.setContentsMargins(*(round(Space.L * s),) * 4)
        self._lay.setSpacing(round(Space.M * s))
        self._rows_lay.setSpacing(round(Space.S * s))
        restyle(self.title, "headline", s)
        restyle(self.sub, "footnote", s)
        for row in self.rows.values():
            row.set_scale(s)
        self.button.setFont(type_font("label", s))
        self.button.setFixedHeight(round(32 * s))


class EmptyCard(Surface):
    """No legal placement, and why."""

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(Space.L, Space.M + 2, Space.L, Space.M + 2)
        lay.setSpacing(4)
        self._lay = lay
        self.title = Label("No legal placement", "headline", elide=True)
        self.reason = Label("", "footnote", "secondary", wrap=True)
        lay.addWidget(self.title)
        lay.addWidget(self.reason)

    def set_text(self, title: str, reason: str) -> None:
        self.title.set_text(title)
        self.reason.set_text(reason)
        self.setAccessibleName(f"{title}. {reason}")

    def set_scale(self, scale: float) -> None:
        super().set_scale(scale)
        self._lay.setContentsMargins(
            round(Space.L * scale),
            round((Space.M + 2) * scale),
            round(Space.L * scale),
            round((Space.M + 2) * scale),
        )
        self._lay.setSpacing(round(4 * scale))
        restyle(self.title, "headline", scale)
        restyle(self.reason, "footnote", scale)


class MenuRow(QAbstractButton):
    """A line of the overflow menu: icon, label and the shortcut that does the same."""

    HEIGHT = 32

    def __init__(self, key: str, icon: str, text: str, keys: str = ""):
        super().__init__()
        self.key = key
        self._icon = icon
        self._hover = FloatAnimator(self, 0.0, duration=Motion.FAST)
        self._scale = 1.0
        self.setText(text)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(self.HEIGHT)
        self.setFont(type_font("body"))
        self.setAccessibleName(text)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, Space.S, 0)
        lay.addStretch(1)
        self._combo = KeyCombo(keys)
        lay.addWidget(self._combo)
        self._combo.setVisible(bool(keys))
        self._lay = lay

    def set_icon_name(self, name: str) -> None:
        self._icon = name
        self.update()

    def set_label(self, text: str) -> None:
        self.setText(text)
        self.setAccessibleName(text)
        self.update()

    def set_keys(self, keys: str) -> None:
        self._combo.set_sequence(keys)
        self._combo.setVisible(bool(keys))

    def set_scale(self, scale: float) -> None:
        self._scale = scale
        self.setFixedHeight(round(self.HEIGHT * scale))
        self.setFont(type_font("body", scale))
        self._lay.setContentsMargins(0, 0, round(Space.S * scale), 0)

    def sizeHint(self) -> QSize:
        fm = QFontMetricsF(self.font())
        s = self._scale
        keys = self._combo.sizeHint().width() + Space.M * s if self._combo.isVisibleTo(self) else 0
        return QSize(
            math.ceil(40 * s + fm.horizontalAdvance(self.text()) + keys + 10 * s), self.height()
        )

    def enterEvent(self, event) -> None:
        if self.isEnabled():
            self._hover.to(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover.to(0.0)
        super().leaveEvent(event)

    def hideEvent(self, event) -> None:
        self._hover.jump(0.0)
        super().hideEvent(event)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        m = material_of(self)
        en = self.isEnabled()
        s = self._scale
        r = QRectF(self.rect())
        hover = self._hover.value if en else 0.0
        if hover:
            paint_wash(
                p,
                r,
                Radius.CONTROL * s,
                with_alpha(m.fill_hover, m.fill_hover.alphaF() * hover * 1.4),
            )
        paint_icon(
            p,
            self._icon,
            QRectF(12 * s, r.center().y() - 8 * s, 16 * s, 16 * s),
            dim(m.text2, en),
            1.9,
        )
        p.setFont(self.font())
        p.setPen(dim(m.text, en))
        left = 40 * s
        right = r.width() - (
            self._combo.width() + Space.M * s if self._combo.isVisibleTo(self) else 10 * s
        )
        text = QFontMetricsF(self.font()).elidedText(
            self.text(), Qt.TextElideMode.ElideRight, right - left
        )
        p.drawText(
            QRectF(left, 0, right - left, r.height()),
            flags(Qt.AlignmentFlag.AlignLeft, Qt.AlignmentFlag.AlignVCenter),
            text,
        )
        p.end()


class OverflowMenu(GlassWindow):
    """Glass popover with the less used actions; closes on a click anywhere else."""

    triggered = Signal(str)

    WIDTH = 252
    ORDER = ("pause", "rescan", "check", "calibrate", "settings", "reset")

    def __init__(self, parent: QWidget | None = None, material: Material | None = None):
        super().__init__(
            parent,
            material=material or default_material(),
            elevation=FLOATING,
            radius=Radius.CARD,
            take_focus=False,
            draggable=False,
        )
        mask = Qt.WindowType.WindowType_Mask
        self.setWindowFlags((self.windowFlags() & ~mask) | Qt.WindowType.Popup)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self._scale = 1.0
        self.rows: dict[str, MenuRow] = {
            "pause": MenuRow("pause", "pause", "Pause tracking"),
            "rescan": MenuRow("rescan", "refresh", "Rescan the board"),
            "check": MenuRow("check", "check", "Check tiles"),
            "calibrate": MenuRow("calibrate", "target", "Calibrate"),
            "settings": MenuRow("settings", "gear", "Settings"),
            "reset": MenuRow("reset", "chevron-up", "Move beside the board"),
        }
        lay = QVBoxLayout(self)
        lay.setSpacing(2)
        self._lay = lay
        self._rule = Divider(inset=Space.S)
        order = ("pause", "rescan", "check", "calibrate")
        for key in order:
            lay.addWidget(self.rows[key])
        lay.addWidget(self._rule)
        lay.addWidget(self.rows["settings"])
        lay.addWidget(self.rows["reset"])
        for row in self.rows.values():
            row.clicked.connect(lambda _=False, k=row.key: self._pick(k))
        self.rows["reset"].hide()
        self.set_scale(1.0)

    def set_scale(self, scale: float) -> None:
        self._scale = scale
        self._radius = Radius.CARD * scale
        self._lay.setContentsMargins(self.content_margins(round(6 * scale)))
        self._lay.setSpacing(round(2 * scale))
        self.set_glass_width(round(self.WIDTH * scale))
        for row in self.rows.values():
            row.set_scale(scale)
        self._invalidate()

    def _pick(self, key: str) -> None:
        self.hide()
        self.triggered.emit(key)

    def configure(
        self,
        *,
        paused: bool,
        has_state: bool,
        check: bool,
        moved: bool,
        shortcuts: dict[str, str],
    ) -> None:
        pause = self.rows["pause"]
        pause.set_label("Resume tracking" if paused else "Pause tracking")
        pause.set_icon_name("play" if paused else "pause")
        pause.set_keys(keycaps(shortcuts.get("pause", "")))
        pause.setEnabled(has_state)
        rescan = self.rows["rescan"]
        rescan.set_keys(keycaps(shortcuts.get("rescan", "")))
        rescan.setEnabled(has_state)
        chk = self.rows["check"]
        chk.set_keys(keycaps(shortcuts.get("check_tiles", "")))
        chk.setEnabled(check)
        cal = self.rows["calibrate"]
        cal.set_keys(keycaps(shortcuts.get("recalibrate", "")))
        self.rows["reset"].setVisible(moved)
        self._lay.activate()
        self.setFixedHeight(
            self._lay.totalHeightForWidth(self.width())
            if self._lay.hasHeightForWidth()
            else self._lay.sizeHint().height()
        )

    def popup_below(self, anchor: QWidget) -> None:
        """Open under a widget with the menu's right edge on the widget's right edge."""
        glass = anchor.mapToGlobal(QPoint(anchor.width(), anchor.height()))
        gx = glass.x() - (self.width() - 2 * SHADOW_BLEED)
        gy = glass.y() + round(6 * self._scale)
        screen = QGuiApplication.screenAt(glass) or QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            gw, gh = self.width() - 2 * SHADOW_BLEED, self.height() - 2 * SHADOW_BLEED
            gx = max(area.left() + 8, min(gx, area.right() - gw - 8))
            if gy + gh > area.bottom() - 8:
                gy = glass.y() - anchor.height() - gh - round(6 * self._scale)
            gy = max(area.top() + 8, gy)
        self.move_glass_to(gx, gy)
        self.show()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)


class CompactBar(QWidget):
    """The one-line form of the panel: status dot, the shown play, its score and arrows."""

    prev_clicked = Signal()
    next_clicked = Signal()
    expand_clicked = Signal()

    def __init__(self):
        super().__init__()
        self._scale = 1.0
        self.dot = StatePill(dot_only=True)
        self.prev = RoundButton("chevron-right", "Previous play", 28, flip=True)
        self.next = RoundButton("chevron-right", "Next play", 28)
        self.expand = RoundButton("chevron-down", "Show all plays", 28)
        self.word = Label("", "headline", elide=True)
        self.where = Label("", "caption", "secondary", elide=True)
        self.points = ScoreText()
        self.status = Label("", "headline", elide=True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(Space.M, Space.XS + 2, Space.XS + 2, Space.XS + 2)
        lay.setSpacing(Space.XS)
        self._lay = lay
        self.center = QVBoxLayout()
        self.center.setContentsMargins(0, 0, 0, 0)
        self.center.setSpacing(0)
        self.center.addStretch(1)
        self.center.addWidget(self.word)
        self.center.addWidget(self.where)
        self.center.addWidget(self.status)
        self.center.addStretch(1)
        lay.addWidget(self.dot)
        lay.addSpacing(Space.XS)
        lay.addWidget(self.prev)
        lay.addLayout(self.center, 1)
        lay.addWidget(self.points)
        lay.addSpacing(Space.XS)
        lay.addWidget(self.next)
        lay.addWidget(self.expand)
        self.prev.clicked.connect(self.prev_clicked)
        self.next.clicked.connect(self.next_clicked)
        self.expand.clicked.connect(self.expand_clicked)
        self.setFixedHeight(BAR_HEIGHT)
        self.set_scale(1.0)
        self.show_status("", "idle")

    def set_scale(self, scale: float) -> None:
        self._scale = scale
        self.setFixedHeight(round(BAR_HEIGHT * scale))
        self._lay.setContentsMargins(
            round(Space.M * scale), round(6 * scale), round(6 * scale), round(6 * scale)
        )
        self._lay.setSpacing(round(Space.XS * scale))
        self.dot.set_scale(scale)
        for b in (self.prev, self.next, self.expand):
            b.set_scale(scale)
        restyle(self.word, "headline", scale)
        restyle(self.where, "caption", scale)
        restyle(self.status, "headline", scale)
        self.points.set_scale(scale)

    def show_play(self, move: Move, position: int, total: int, held: bool) -> None:
        self.word.set_text(move.word)
        place = where_text(move).rsplit(" · ", 1)[0]
        self.where.set_text(
            f"{position} of {total} · holding" if held else f"{position} of {total} · {place}"
        )
        self.points.set_score(move.score)
        for w in (self.word, self.where, self.points, self.prev, self.next):
            w.show()
        self.status.hide()
        many = total > 1
        self.prev.setEnabled(many)
        self.next.setEnabled(many)
        self.setAccessibleName(f"Play {position} of {total}: {move.word}, {move.score} points")

    def show_status(self, text: str, tone: str, working: bool = False) -> None:
        self.status.set_text(text)
        self.status.set_tone({"error": "danger", "warn": "warning"}.get(tone, "primary"))
        for w in (self.word, self.where, self.points, self.prev, self.next):
            w.hide()
        self.status.show()
        self.dot.set_status(text, tone, working)
        self.setAccessibleName(text)


class GlassPanel(GlassWindow):
    """Shows the three best plays. Clicking a card previews that play on the board."""

    card_clicked = Signal(int)
    pause_clicked = Signal()
    rescan_clicked = Signal()
    check_clicked = Signal()
    calibrate_clicked = Signal()
    release_clicked = Signal()
    settings_clicked = Signal()
    permission_clicked = Signal()
    position_reset = Signal()
    chip_clicked = Signal(str, object, str)
    compact_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(
            parent,
            material=default_material(),
            elevation=FLOATING,
            radius=Radius.PANEL,
            take_focus=False,
            draggable=True,
        )
        if type(self).backdrop is None:
            self.backdrop = native_backdrop()
        self.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._scale = 1.0
        self._compact = False
        self._snap: Snapshot | None = None
        self._selected = 0
        self._age: float | None = None
        self._framed = False
        self._note = ""
        self._view = view_of(None)
        self._dismissed = ""
        self._shown_text = ""
        self._setup = (None, None, None)
        self._user_pos: QPoint | None = None
        self._placer = PanelPlacer()
        self._shortcuts = self._default_shortcuts()
        self._chip_target = None
        self._labels: list[tuple[Label, str, bool]] = []
        self.on_popover_shown = None

        outer = QVBoxLayout(self)
        outer.setSpacing(0)
        self._outer = outer
        self._full = QWidget()
        full = QVBoxLayout(self._full)
        full.setContentsMargins(0, 0, 0, 0)
        full.setSpacing(Space.S)
        self._stack = full
        self._bar = CompactBar()
        outer.addWidget(self._full)
        outer.addWidget(self._bar)
        self._bar.hide()

        # header
        self.mark = AppMark()
        self.title = Label("TileLens", "headline")
        self.pill = StatePill("Setup", "idle")
        self.pause_btn = IconButton("pause", "Pause tracking", size="s", variant="bare")
        self.collapse_btn = IconButton("chevron-up", "Compact view", size="s", variant="bare")
        self.more_btn = IconButton("ellipsis", "More", size="s", variant="bare")
        for b in (self.pause_btn, self.collapse_btn, self.more_btn):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        head = QHBoxLayout()
        head.setContentsMargins(Space.M, 0, Space.S, 0)
        head.setSpacing(Space.S)
        self._head = head
        head.addWidget(self.mark)
        head.addWidget(self.title)
        head.addSpacing(Space.XS)
        head.addWidget(self.pill)
        head.addStretch(1)
        tools = QHBoxLayout()
        tools.setContentsMargins(0, 0, 0, 0)
        tools.setSpacing(Space.XS)
        self._tools = tools
        for b in (self.pause_btn, self.collapse_btn, self.more_btn):
            tools.addWidget(b)
        head.addLayout(tools)
        self._header = QWidget()
        self._header.setLayout(head)
        self._header.setFixedHeight(HEADER_HEIGHT)
        self.subtitle = self._label("Highest-scoring plays", "footnote", "secondary", elide=True)
        cap = QHBoxLayout()
        cap.setContentsMargins(Space.M, 0, Space.M, 0)
        cap.addWidget(self.subtitle)
        self._cap = cap
        self._cap_row = QWidget()
        self._cap_row.setLayout(cap)
        top = QVBoxLayout()
        top.setSpacing(2)
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(self._header)
        top.addWidget(self._cap_row)
        self._top = top
        full.addLayout(top)

        # plays and the states that stand in for them
        self.cards = [PlayCard(i) for i in range(3)]
        self.skeletons = [SkeletonCard(tall=i == 0) for i in range(3)]
        self.empty = EmptyCard()
        self.setup = SetupCard()
        for w in (*self.cards, *self.skeletons, self.empty, self.setup):
            full.addWidget(w)
            w.hide()

        # notice, letter keys, footer
        self.banner = NoticeBanner()
        self.banner.hide()
        full.addWidget(self.banner)
        self.chip_row = QWidget()
        chips = QHBoxLayout(self.chip_row)
        chips.setContentsMargins(Space.M, 0, Space.M, 0)
        chips.setSpacing(Space.S)
        self._chips_lay = chips
        self.chip_label = self._label("It is", "label", "secondary")
        chips.addWidget(self.chip_label)
        self.chip_btns = [LetterKey("") for _ in range(MAX_CHIPS)]
        for key in self.chip_btns:
            key.clicked.connect(lambda k=key: self._chip(k))
            chips.addWidget(key)
            key.hide()
        chips.addStretch(1)
        self.chip_row.hide()
        full.addWidget(self.chip_row)
        foot = QHBoxLayout()
        foot.setContentsMargins(Space.M, 0, Space.M, Space.XS)
        foot.setSpacing(Space.S)
        self._foot = foot
        self.updated = self._label("", "footnote", "secondary")
        self.hint = self._label("Next play", "footnote", "secondary")
        self.hint_keys = KeyCombo("")
        foot.addWidget(self.updated)
        foot.addStretch(1)
        foot.addWidget(self.hint)
        foot.addWidget(self.hint_keys)
        self._footer = QWidget()
        self._footer.setLayout(foot)
        self._footer.hide()
        full.addWidget(self._footer)

        self.menu = OverflowMenu(self)
        self.menu.on_shown = self._menu_shown
        if type(self.menu).backdrop is None:
            self.menu.backdrop = native_backdrop()

        self.pause_btn.clicked.connect(self.pause_clicked)
        self.collapse_btn.clicked.connect(lambda: self.set_compact(True))
        self.more_btn.clicked.connect(self._open_menu)
        self.menu.triggered.connect(self._menu_action)
        self.banner.action_clicked.connect(self._banner_action)
        self.banner.dismissed.connect(self._banner_dismissed)
        self.setup.action_clicked.connect(self._do)
        for i, card in enumerate(self.cards):
            card.clicked.connect(lambda _=False, n=i: self._card(n))
        self._bar.expand_clicked.connect(lambda: self.set_compact(False))
        self._bar.prev_clicked.connect(lambda: self._step(-1))
        self._bar.next_clicked.connect(lambda: self._step(1))
        self.moved_by_user.connect(self._remember_position)
        self.material_changed.connect(self.menu.set_material)

        self._apply_scale()
        self._render()

    # shortcuts

    @staticmethod
    def _default_shortcuts() -> dict[str, str]:
        from tilelens.ui.settings import DEFAULT_SHORTCUTS

        return dict(DEFAULT_SHORTCUTS)

    def set_shortcuts(self, shortcuts: dict[str, str]) -> None:
        """The keyboard shortcuts to show (the Settings.shortcuts mapping)."""
        self._shortcuts = {**self._default_shortcuts(), **shortcuts}
        self.hint_keys.set_sequence(keycaps(self._shortcuts.get("next_card", "")))
        self._fit()

    # building blocks

    def _label(self, text: str, role: str, tone: str = "primary", **kw) -> Label:
        label = Label(text, role, tone, **kw)
        self._labels.append((label, role, kw.get("tabular", False)))
        return label

    # scale

    @property
    def scale(self) -> float:
        return self._scale

    def set_scale(self, factor: float) -> None:
        """Size of the whole panel as a factor of its design size (0.85 to 1.5)."""
        factor = max(0.85, min(1.5, float(factor)))
        if abs(factor - self._scale) < 1e-3:
            return
        self._scale = factor
        self._apply_scale()
        self._fit()

    def _apply_scale(self) -> None:
        s = self._scale
        self.set_glass_width(round((CONTENT_WIDTH + 2 * PAD) * s))
        self._stack.setSpacing(round(Space.S * s))
        self._top.setSpacing(round(2 * s))
        self._head.setContentsMargins(round(Space.M * s), 0, round(Space.S * s), 0)
        self._head.setSpacing(round(Space.S * s))
        self._tools.setSpacing(round(Space.XS * s))
        self._header.setFixedHeight(round(HEADER_HEIGHT * s))
        self._cap.setContentsMargins(round(Space.M * s), 0, round(Space.M * s), 0)
        self._chips_lay.setContentsMargins(round(Space.M * s), 0, round(Space.M * s), 0)
        self._chips_lay.setSpacing(round(Space.S * s))
        self._foot.setContentsMargins(
            round(Space.M * s), 0, round(Space.M * s), round(Space.XS * s)
        )
        self._foot.setSpacing(round(Space.S * s))
        self.mark.set_scale(s)
        self.pill.set_scale(s)
        for b in (self.pause_btn, self.collapse_btn, self.more_btn):
            side = round(24 * s)
            b.setFixedSize(side, side)
        restyle(self.title, "headline", s)
        for label, role, tabular in self._labels:
            restyle(label, role, s, tabular)
        for card in self.cards:
            card.set_scale(s)
        for sk in self.skeletons:
            sk.set_scale(s)
        self.empty.set_scale(s)
        self.setup.set_scale(s)
        self.banner.set_scale(s)
        for key in self.chip_btns:
            key.set_scale(s)
        self.hint_keys.setFixedHeight(round(21 * s))
        self._bar.set_scale(s)
        self.menu.set_scale(s)
        self._set_shape(compact=self._compact)

    def _set_shape(self, compact: bool) -> None:
        s = self._scale
        pad = 0 if compact else round(PAD * s)
        self._outer.setContentsMargins(self.content_margins(pad))
        radius = (BAR_HEIGHT / 2 if compact else Radius.PANEL) * s
        if abs(radius - self._radius) > 1e-3:
            self._radius = radius
            self._invalidate()
            if self.isVisible():
                self._ask_backdrop()
            self.update()

    # compact mode

    def is_compact(self) -> bool:
        return self._compact

    def set_compact(self, compact: bool) -> None:
        compact = bool(compact)
        if compact == self._compact:
            return
        self._compact = compact
        self._full.setVisible(not compact)
        self._bar.setVisible(compact)
        self._set_shape(compact)
        self._render()
        self.compact_changed.emit(compact)

    # rendering

    def show_snapshot(
        self,
        snap: Snapshot | None,
        selected: int,
        age: float | None = None,
        framed: bool = False,
    ) -> None:
        self._snap, self._selected, self._age, self._framed = snap, selected, age, framed
        self._render()

    def set_note(self, text: str) -> None:
        """Something to act on that is not part of the tracking state (calibration trouble)."""
        if text == self._note:
            return
        self._note = text
        self._render()

    def set_setup(
        self, capture: bool | None = None, words: bool | None = None, game: bool | None = None
    ) -> None:
        """The first-run checklist: True is done, False is a problem, None is still to do."""
        self._setup = (capture, words, game)
        self.setup.set_state(capture, words, game)
        if self._view.setup:
            self._fit()

    @property
    def view(self) -> PanelView:
        return self._view

    @property
    def calm(self) -> bool:
        """True while a result is on show: nothing here moves then."""
        return bool(self._view.moves)

    def _render(self) -> None:
        view = view_of(self._snap, self._framed, self._note)
        self._view = view
        self.setUpdatesEnabled(False)
        try:
            self._render_view(view)
        finally:
            self.setUpdatesEnabled(True)
        self._fit()
        self.update()

    def _render_view(self, view: PanelView) -> None:
        moves = view.moves
        n = len(moves)
        spinning = view.working and not view.moves
        self.pill.set_status(view.label, view.tone, spinning)
        paused = self._snap is not None and self._snap.status == Status.PAUSED
        self.pause_btn.set_icon_name("play" if paused else "pause")
        self.pause_btn.set_tooltip("Resume tracking" if paused else "Pause tracking")
        self.pause_btn.setEnabled(self._snap is not None)
        self.pause_btn.setVisible(not view.setup)
        self.collapse_btn.setVisible(not view.setup)
        self.subtitle.set_text(view.caption)
        self._cap_row.setVisible(bool(view.caption))
        selected = self._selected if 0 <= self._selected < n else -1
        for i, card in enumerate(self.cards):
            card.set_move(moves[i] if i < n else None)
            card.set_selected(i == selected)
            card.set_held(view.held == i and i < n)
        for sk in self.skeletons:
            sk.setVisible(view.skeleton and not self._compact)
        self.empty.setVisible(view.empty is not None)
        if view.empty is not None:
            self.empty.set_text(*view.empty)
        self.setup.setVisible(view.setup)
        self._show_notice(view)
        self._show_chips(view)
        self._show_footer(view)
        self._render_bar(view, selected)

    def _show_notice(self, view: PanelView) -> None:
        notice = view.notice
        if notice is not None and notice.dismissible and notice.text == self._dismissed:
            notice = None
        if notice is None:
            if view.notice is None or view.notice.text != self._dismissed:
                self._dismissed = ""
            self._shown_text = ""
            self.banner.hide()
            return
        self._shown_text = notice.text
        self._notice = notice
        self.banner.show_notice(notice)

    def _show_chips(self, view: PanelView) -> None:
        self._chip_target = None
        if view.chips is None:
            self.chip_row.hide()
            return
        kind, where, letters = view.chips
        self._chip_target = (kind, where)
        self.chip_label.set_text("It is" if not view.more else f"It is ({view.more + 1} to answer)")
        for i, key in enumerate(self.chip_btns):
            key.setVisible(i < len(letters))
            if i < len(letters):
                key._text = letters[i]
                key.setAccessibleName(f"Letter {letters[i]}")
                key.updateGeometry()
                key.update()
        self.chip_row.show()

    def _show_footer(self, view: PanelView) -> None:
        shown = bool(view.moves) and self._age is not None
        self.updated.set_text(age_text(self._age) if shown else "")
        stale = self._age is not None and self._age >= STALE_AFTER
        self.updated.set_tone("warning" if stale else "secondary")
        many = len(view.moves) > 1
        self.hint.setVisible(many)
        self.hint_keys.setVisible(many and bool(self._shortcuts.get("next_card")))
        self.hint_keys.set_sequence(keycaps(self._shortcuts.get("next_card", "")))
        self._footer.setVisible(bool(view.moves) and (shown or many) and not self._compact)

    def _render_bar(self, view: PanelView, selected: int) -> None:
        if view.moves:
            at = selected if selected >= 0 else 0
            self._bar.show_play(view.moves[at], at + 1, len(view.moves), view.held == at)
            self._bar.dot.set_status(view.label, view.tone, False)
        elif view.empty is not None:
            self._bar.show_status(view.empty[0], view.tone)
        else:
            self._bar.show_status(view.label, view.tone, view.working)

    def _settle_layouts(self) -> None:
        for root in (self._full, self._bar):
            for w in (*reversed(root.findChildren(QWidget)), root):
                lay = w.layout()
                if lay is not None:
                    lay.invalidate()
                    lay.activate()
        self.layout().invalidate()
        self.layout().activate()

    def _fit(self) -> None:
        lay = self.layout()
        self._settle_layouts()
        if self._compact:
            h = round(BAR_HEIGHT * self._scale) + 2 * SHADOW_BLEED
        elif lay.hasHeightForWidth():
            h = lay.totalHeightForWidth(self.width())
        else:
            h = lay.sizeHint().height()
        if h != self.height():
            self.setFixedHeight(h)

    def set_age(self, age: float | None) -> None:
        self._age = age
        if not self._view.moves:
            return
        text = age_text(age)
        if text != self.updated.text():
            self.updated.set_text(text)
        self.updated.set_tone("warning" if age is not None and age >= STALE_AFTER else "secondary")
        if self._footer.isHidden() and not self._compact and age is not None:
            self._footer.show()
            self._fit()

    # actions

    def _card(self, index: int) -> None:
        card = self.cards[index]
        if card.move_shown is not None:
            self.card_clicked.emit(index)

    def _step(self, delta: int) -> None:
        n = len(self._view.moves)
        if n < 2:
            return
        at = self._selected if 0 <= self._selected < n else 0
        self.card_clicked.emit((at + delta) % n)

    def _chip(self, key: LetterKey) -> None:
        if self._chip_target is not None:
            self.chip_clicked.emit(self._chip_target[0], self._chip_target[1], key.text())

    def _banner_action(self) -> None:
        notice = getattr(self, "_notice", None)
        if notice is not None:
            self._do(notice.do)

    def _banner_dismissed(self) -> None:
        self._dismissed = self._shown_text
        self._fit()

    def _do(self, what: str) -> None:
        if what == "check":
            self.check_clicked.emit()
        elif what == "calibrate":
            self.calibrate_clicked.emit()
        elif what == "release":
            self.release_clicked.emit()
        elif what == "rescan":
            self.rescan_clicked.emit()
        elif what == "pause":
            self.pause_clicked.emit()
        elif what == "settings":
            self.settings_clicked.emit()
        elif what == "permission":
            if self.isSignalConnected(QMetaMethod.fromSignal(self.permission_clicked)):
                self.permission_clicked.emit()
            else:
                self.settings_clicked.emit()

    def _open_menu(self) -> None:
        if self.menu.isVisible():
            self.menu.hide()
            return
        snap = self._snap
        self.menu.set_material(self.material)
        self.menu.set_strength(self.strength)
        self.menu.configure(
            paused=snap is not None and snap.status == Status.PAUSED,
            has_state=snap is not None,
            check=snap is not None and snap.status == Status.CHECK,
            moved=self.user_moved,
            shortcuts=self._shortcuts,
        )
        self.menu.popup_below(self.more_btn)

    def _menu_action(self, key: str) -> None:
        if key == "reset":
            self.reset_user_position()
        else:
            self._do({"pause": "pause", "check": "check"}.get(key, key))

    def _menu_shown(self, win_id: int) -> None:
        if self.on_shown is not None:
            with contextlib.suppress(Exception):
                self.on_shown(win_id)
        if self.on_popover_shown is not None:
            with contextlib.suppress(Exception):
                self.on_popover_shown(win_id)

    # appearance

    def bind_appearance(self, appearance: Appearance) -> None:
        """Follow light and dark and the fill strength of the backdrop-aware appearance."""
        self.bind(appearance)

    def set_material(self, m: Material) -> None:
        super().set_material(m)
        if self.calm:
            self._fade.settle()

    def set_strength(self, strength: float) -> None:
        super().set_strength(strength)
        self.menu.set_strength(strength)
        if self.calm:
            self._strength_fade.settle()

    # placement

    def _remember_position(self, glass_top_left: QPoint) -> None:
        self._user_pos = QPoint(glass_top_left)

    @property
    def user_position(self) -> QPoint | None:
        return self._user_pos if self.user_moved else None

    def set_user_position(self, glass_top_left: QPoint) -> None:
        """Treat this glass top-left as where the player put the panel (a saved position)."""
        self._user_pos = QPoint(glass_top_left)
        self.user_moved = True

    def reset_user_position(self) -> None:
        self.user_moved = False
        self._user_pos = None
        self._placer.reset()
        self.position_reset.emit()

    def glass_size(self) -> tuple[int, int]:
        return self.width() - 2 * SHADOW_BLEED, self.height() - 2 * SHADOW_BLEED

    def auto_place(
        self, region, board_rect=None, rack_rect=None, screen_rect=None, margin: float = 16
    ) -> Placement:
        """Put the panel beside the board (or where the player dragged it) and fit its size to
        the game window. Rectangles are in screen points; call it after every refresh."""
        if screen_rect is None:
            x, y, w, h = _rect(region)
            screen = QGuiApplication.screenAt(QPoint(round(x + w / 2), round(y + h / 2)))
            screen = screen or QGuiApplication.primaryScreen()
            area = screen.availableGeometry()
            screen_rect = (area.x(), area.y(), area.width(), area.height())
        self.set_scale(panel_scale(_rect(region)[3], self._scale))
        found = self._placer.place(
            region,
            board_rect,
            rack_rect,
            self.glass_size(),
            screen_rect,
            margin,
            self.user_position,
        )
        now = self.pos() + QPoint(SHADOW_BLEED, SHADOW_BLEED)
        if abs(now.x() - found.x) > 1 or abs(now.y() - found.y) > 1:
            self.move_glass_to(found.x, found.y)
        return found

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._fit()

    def hideEvent(self, event) -> None:
        self.menu.hide()
        super().hideEvent(event)


def _rect(obj) -> tuple[float, float, float, float]:
    from tilelens.ui.panel_placement import rect_of

    return rect_of(obj)


__all__ = [
    "CompactBar",
    "GlassPanel",
    "Notice",
    "OverflowMenu",
    "PanelView",
    "ScoreText",
    "coverage_subtitle",
    "describe",
    "view_of",
]

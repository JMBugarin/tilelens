"""The settings sheet: a glass window with the status, the plays, and every option.

Nothing here is on screen while a round is played; the panel beside the board is the interface
then. The sheet leads with a checklist until the game can be read, and with the settings after.
"""

from __future__ import annotations

import contextlib
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFontMetricsF, QKeyEvent, QKeySequence, QPainter, QPainterPath, QShortcut
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QScrollArea,
    QScrollBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from tilelens.state.controller import Snapshot, Status
from tilelens.state.memory import COVERAGE_NOTES, Coverage
from tilelens.state.names import square_name
from tilelens.state.phase import phase_of
from tilelens.ui import notify
from tilelens.ui.app_icon import paint_app_icon
from tilelens.ui.cards import describe
from tilelens.ui.corrections import CorrectionDialog
from tilelens.ui.design import (
    SHADOW_BLEED,
    Banner,
    Divider,
    GlassButton,
    GlassCard,
    GlassWindow,
    IconButton,
    KeyCombo,
    Label,
    Radius,
    RankBadge,
    ScoreBadge,
    SectionLabel,
    Segmented,
    Space,
    StatusPill,
    Switch,
    material_of,
    native_backdrop,
    paint_wash,
    set_reduce_motion,
    with_alpha,
)
from tilelens.ui.design.components import split_keys
from tilelens.ui.design.paint import paint_control_rim
from tilelens.ui.preview import CellMapper, paint_board, paint_move
from tilelens.ui.settings import APPEARANCE_MODES, GUIDE_MODES, OVERLAY_MODES

WIDTH = 480
MAX_HEIGHT = 780
SCREEN_MARGIN = 24
ROW_HEIGHT = 44
FULL_BOARD_SIDE = 15
WINDOW_COLS = 17
WINDOW_ROWS = 9
PLAY_SLOTS = 3

MODES = (
    (
        "exclude",
        "Hidden from capture",
        "TileLens draws over the game and keeps itself out of screen capture.",
    ),
    (
        "suppress",
        "Hide while capturing",
        "The overlay switches off for each capture. Use it where windows cannot be kept out of "
        "capture.",
    ),
    (
        "panel_only",
        "Panel only",
        "Nothing is drawn over the game. Plays show in the panel and in the preview here.",
    ),
)
GUIDES = (("auto", "Auto"), ("always", "Always"), ("off", "Off"))
THEMES = (("auto", "Auto"), ("light", "Light"), ("dark", "Dark"))
SHORTCUT_LABELS = {
    "pause": "Pause or resume",
    "rescan": "Rescan the board",
    "next_card": "Next play",
    "select_1": "Select play 1",
    "select_2": "Select play 2",
    "select_3": "Select play 3",
    "hide_preview": "Show or hide suggestions",
    "check_tiles": "Check tiles",
    "recalibrate": "Recalibrate",
}
MAC_KEYS = {
    "ctrl": "⌘",
    "control": "⌘",
    "shift": "⇧",
    "alt": "⌥",
    "option": "⌥",
    "meta": "⌃",
    "return": "↩",
    "enter": "↩",
    "esc": "⎋",
    "escape": "⎋",
    "backspace": "⌫",
    "del": "⌦",
    "delete": "⌦",
    "tab": "⇥",
}
MAC_ORDER = {"⌃": 0, "⌥": 1, "⇧": 2, "⌘": 3}
MISSING = "–"
RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
DETAIL_HEIGHT = 60
STEP_BUTTON_WIDTH = 124


def shortcut_text(sequence: str, mac: bool | None = None) -> str:
    """The keys of a shortcut as they are named on this keyboard: Qt's Ctrl is Command on a
    Mac, so it is shown as one."""
    mac = sys.platform == "darwin" if mac is None else mac
    keys = split_keys(sequence)
    if not mac:
        return "+".join(keys)
    mapped = [MAC_KEYS.get(k.lower(), k) for k in keys]
    mods = sorted((k for k in mapped if k in MAC_ORDER), key=MAC_ORDER.__getitem__)
    return "+".join([*mods, *(k for k in mapped if k not in MAC_ORDER)])


@contextlib.contextmanager
def quiet(*widgets: QObject) -> Iterator[None]:
    """Change controls to match the model without them reporting a user edit."""
    before = [w.blockSignals(True) for w in widgets]
    try:
        yield
    finally:
        for w, was in zip(widgets, before, strict=True):
            w.blockSignals(was)


class KeyedSegmented(Segmented):
    """A Segmented whose options carry a settings key next to the label that is shown."""

    key_changed = Signal(str)

    def __init__(self, items: Sequence[tuple[str, str]], current: str, parent=None):
        self._keys = [key for key, *_ in items]
        super().__init__(
            [label for _, label, *_ in items],
            self._keys.index(current) if current in self._keys else 0,
            parent,
        )
        self.current_changed.connect(lambda i: self.key_changed.emit(self._keys[i]))

    @property
    def keys(self) -> list[str]:
        return list(self._keys)

    def current_key(self) -> str:
        return self._keys[self.current_index()]

    def set_current_key(self, key: str) -> None:
        if key in self._keys:
            self.set_current_index(self._keys.index(key))

    currentText = current_key
    setCurrentText = set_current_key

    def sizeHint(self) -> QSize:
        fm = QFontMetricsF(self.font())
        widest = max(fm.horizontalAdvance(o) for o in self.options)
        return QSize(int((widest + 16) * len(self._keys) + 2 * self.PAD) + 1, self.HEIGHT)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()


class GroupCard(QWidget):
    """A rounded pane that holds rows, with a hairline between the visible ones."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[QWidget] = []
        self.rows = QVBoxLayout(self)
        self.rows.setContentsMargins(Space.L, Space.XS, Space.L, Space.XS)
        self.rows.setSpacing(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

    def add_row(self, row: QWidget) -> QWidget:
        self._rows.append(row)
        self.rows.addWidget(row)
        row.installEventFilter(self)
        return row

    def eventFilter(self, obj, event) -> bool:
        if event.type() in (QEvent.Type.Show, QEvent.Type.Hide):
            self.update()
        return False

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        m = material_of(self)
        r = QRectF(self.rect())
        paint_wash(p, r, Radius.CARD, m.fill)
        paint_control_rim(p, r, Radius.CARD, m)
        shown = [w for w in self._rows if not w.isHidden()]
        line = 1.0 / max(1.0, p.device().devicePixelRatioF())
        for above, below in pairwise(shown):
            y = (above.geometry().bottom() + below.geometry().top() + 1) / 2
            p.fillRect(QRectF(Space.L, y - line / 2, r.width() - 2 * Space.L, line), m.divider)
        p.end()


class Row(QWidget):
    """A title with an optional second line on the left and a control on the right."""

    def __init__(
        self,
        title: str,
        caption: str = "",
        control: QWidget | None = None,
        *,
        elide: bool = False,
        pad: int = 10,
        parent=None,
    ):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, pad, 0, pad)
        lay.setSpacing(Space.M)
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)
        self.title = Label(title, "body", elide=True)
        self.caption = Label(caption, "footnote", "secondary", wrap=not elide, elide=elide)
        self.caption.setVisible(bool(caption) or elide)
        text.addWidget(self.title)
        text.addWidget(self.caption)
        lay.addLayout(text, 1)
        self.control = control
        if control is not None:
            lay.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
        self.setMinimumHeight(ROW_HEIGHT if pad >= 10 else ROW_HEIGHT - 8)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

    def set_caption(self, text: str, tip: str = "") -> None:
        self.caption.set_text(text)
        self.caption.setVisible(bool(text))
        self.caption.setToolTip(tip or text)


class Section(QWidget):
    """A caps label over a body; a collapsible one folds its body away with one click."""

    toggled = Signal(bool)

    def __init__(
        self, title: str, *, collapsible: bool = False, expanded: bool = True, parent=None
    ):
        super().__init__(parent)
        self._collapsible = collapsible
        self._expanded = True
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(Space.S)
        self.head = QWidget()
        head = QHBoxLayout(self.head)
        head.setContentsMargins(Space.XS, 0, 0, 0)
        head.setSpacing(Space.S)
        self.title = SectionLabel(title)
        self.title.set_tone("secondary")
        head.addWidget(self.title, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)
        self.chevron = IconButton("chevron-down", "Collapse", size="s", variant="bare")
        self.chevron.setVisible(collapsible)
        self.chevron.clicked.connect(self.toggle)
        head.addWidget(self.chevron)
        self.head.setMinimumHeight(24)
        if collapsible:
            self.head.setCursor(Qt.CursorShape.PointingHandCursor)
            self.head.mousePressEvent = lambda e: self.toggle()
        self.body = QWidget()
        self.box = QVBoxLayout(self.body)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(Space.S)
        lay.addWidget(self.head)
        lay.addWidget(self.body)
        if not expanded:
            self.set_expanded(False)

    @property
    def expanded(self) -> bool:
        return self._expanded

    def add_head(self, widget: QWidget) -> QWidget:
        """A widget at the right end of the caps label's line, before the chevron."""
        self.head.layout().insertWidget(self.head.layout().count() - 1, widget, 1)
        return widget

    def add(self, widget: QWidget, stretch: int = 0) -> QWidget:
        self.box.addWidget(widget, stretch)
        return widget

    def toggle(self) -> None:
        self.set_expanded(not self._expanded)

    def set_expanded(self, on: bool) -> None:
        if on == self._expanded:
            return
        self._expanded = on
        self.body.setVisible(on)
        self.chevron.set_icon_name("chevron-down" if on else "chevron-right")
        self.chevron.set_tooltip("Collapse" if on else "Expand")
        self.toggled.emit(on)


class PlayRow(GlassCard):
    """One of the best plays as a compact row: rank, word, where, score."""

    chosen = Signal(int)

    def __init__(self, index: int):
        super().__init__()
        self.index = index
        self.move_shown = None
        self.row.setContentsMargins(Space.M, Space.S, Space.M, Space.S)
        self.rank = RankBadge(index + 1)
        self.set_leading(self.rank)
        self.row.setAlignment(self.rank, Qt.AlignmentFlag.AlignVCenter)
        body = QWidget()
        text = QVBoxLayout(body)
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(1)
        self.word = Label("", "headline", elide=True)
        self.where = Label("", "footnote", "secondary", elide=True)
        text.addWidget(self.word)
        text.addWidget(self.where)
        self.set_body(body)
        self.score = ScoreBadge(None)
        self.set_trailing(self.score)
        self.row.setAlignment(self.score, Qt.AlignmentFlag.AlignVCenter)
        self.clicked.connect(lambda: self.chosen.emit(self.index))
        self.setFixedHeight(54)
        self.set_move(None)

    def set_move(self, move) -> None:
        self.move_shown = move
        self.setEnabled(move is not None)
        self.score.setVisible(move is not None)
        if move is None:
            self.word.set_text(MISSING)
            self.where.set_text("")
            self.score.set_score(None)
            self.setToolTip("")
            return
        d = describe(move)
        tiles = d["tiles"]
        direction = "Across" if move.direction == "H" else "Down"
        self.word.set_text(move.word)
        plural = "s" if tiles != 1 else ""
        self.where.set_text(
            f"{square_name(move.row, move.col)} · {direction} · {tiles} tile{plural}"
        )
        self.score.set_score(move.score)
        self.setToolTip(f"{move.word}: {d['breakdown']}")


def view_box(board, move) -> tuple[int, int, int, int]:
    """(row, col, rows, cols) of the part of the board the preview shows: all of a normal board,
    a window around the play on a large one."""
    w, h = board.width, board.height
    if w <= FULL_BOARD_SIDE and h <= FULL_BOARD_SIDE:
        return 0, 0, h, w
    cells: list[tuple[int, int]] = []
    if move is not None:
        cells = [(t.row, t.col) for t in move.tiles]
        for word in move.breakdown.words:
            cells.extend((c.row, c.col) for c in word.cells)
    if not cells:
        cells = [(r, c) for r in range(h) for c in range(w) if board.get(r, c)]
    if not cells:
        cells = [(h // 2, w // 2)]
    r0, r1 = min(r for r, _ in cells) - 1, max(r for r, _ in cells) + 1
    c0, c1 = min(c for _, c in cells) - 1, max(c for _, c in cells) + 1
    rows = min(h, max(r1 - r0 + 1, WINDOW_ROWS))
    cols = min(w, max(c1 - c0 + 1, WINDOW_COLS))
    top = max(0, min(h - rows, (r0 + r1 + 1 - rows) // 2))
    left = max(0, min(w - cols, (c0 + c1 + 1 - cols) // 2))
    return top, left, rows, cols


class BoardPreview(QWidget):
    """The board with the selected play on it. Also what shows the plays when nothing is drawn
    over the game."""

    HEIGHT = 216
    PAD = 8

    def __init__(self):
        super().__init__()
        self.board = None
        self.move = None
        self.profile = None
        self.setFixedHeight(self.HEIGHT)
        self.setMinimumWidth(160)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_state(self, board, move, profile) -> None:
        self.board, self.move, self.profile = board, move, profile
        self.setAccessibleName(
            "Board preview" if board is None else f"Board preview, {board.width} by {board.height}"
        )
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        m = material_of(self)
        r = QRectF(self.rect())
        paint_wash(p, r, Radius.CARD, m.fill)
        paint_control_rim(p, r, Radius.CARD, m)
        if self.board is None:
            p.setPen(m.text2)
            p.setFont(Label("", "footnote").font())
            p.drawText(
                r, int(Qt.AlignmentFlag.AlignCenter.value), "The board appears here once it is read"
            )
            p.end()
            return
        row0, col0, rows, cols = view_box(self.board, self.move)
        inner = r.adjusted(self.PAD, self.PAD, -self.PAD, -self.PAD)
        side = min(inner.width() / cols, inner.height() / rows)
        w, h = side * cols, side * rows
        x0, y0 = inner.center().x() - w / 2, inner.center().y() - h / 2
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(x0, y0, w, h), Radius.CARD - self.PAD, Radius.CARD - self.PAD)
        p.setClipPath(clip)
        mapper = CellMapper(x0 - col0 * side, y0 - row0 * side, side, side)
        paint_board(p, self.board, mapper, self.profile)
        if self.move is not None:
            paint_move(p, self.move, lambda rr, cc: mapper.rect(rr, cc, 0.04))
        p.end()


class StepBadge(QWidget):
    """The number of a setup step; a check mark once it is done, the accent while it is next."""

    SIZE = 28

    def __init__(self, number: int):
        super().__init__()
        self._number = number
        self.state = "todo"
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setFont(Label("", "label").font())
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_state(self, state: str, number: int | None = None) -> None:
        self.state = state
        if number is not None:
            self._number = number
        self.setAccessibleName(f"Step {self._number}, {'done' if state == 'done' else 'to do'}")
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        m = material_of(self)
        r = QRectF(self.rect())
        if self.state == "done":
            paint_wash(p, r, r.height() / 2, with_alpha(m.success, 0.20 if m.dark else 0.16))
            from tilelens.ui.design import paint_icon

            paint_icon(p, "check", r.adjusted(7, 7, -7, -7), m.success, 2.6)
        elif self.state == "current":
            paint_wash(p, r, r.height() / 2, m.accent)
            p.setFont(self.font())
            p.setPen(m.on_accent)
            p.drawText(r, int(Qt.AlignmentFlag.AlignCenter.value), str(self._number))
        else:
            paint_wash(p, r, r.height() / 2, m.fill_hover)
            paint_control_rim(p, r, r.height() / 2, m)
            p.setFont(self.font())
            p.setPen(m.text2)
            p.drawText(r, int(Qt.AlignmentFlag.AlignCenter.value), str(self._number))
        p.end()


class StepRow(QWidget):
    """One line of the checklist: badge, what and why, and the one button that does it."""

    def __init__(self, key: str, number: int):
        super().__init__()
        self.key = key
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, Space.M, 0, Space.M)
        lay.setSpacing(Space.M)
        self.badge = StepBadge(number)
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)
        self.title = Label("", "label", wrap=True)
        self.caption = Label("", "footnote", "secondary", wrap=True)
        text.addWidget(self.title)
        text.addWidget(self.caption)
        self.button = GlassButton("", size="m")
        self.button.setMinimumWidth(STEP_BUTTON_WIDTH)
        lay.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(text, 1)
        lay.addWidget(self.button, 0, Qt.AlignmentFlag.AlignTop)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

    def show_state(self, *, done: bool, current: bool, enabled: bool) -> None:
        self.badge.set_state("done" if done else "current" if current else "todo")
        self.button.setVisible(not done)
        self.button.set_variant("prominent" if current else "plain")
        self.button.setEnabled(enabled)
        self.title.set_tone("secondary" if done else "primary")


class GlassScroll(QScrollArea):
    """A scroll area with no frame and a thin bar laid over its right edge."""

    BAR = 14

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setAutoFillBackground(False)
        self.viewport().setAutoFillBackground(False)
        self.bar = QScrollBar(Qt.Orientation.Vertical, self)
        self.bar.hide()
        inner = self.verticalScrollBar()
        inner.rangeChanged.connect(self._range)
        inner.valueChanged.connect(self.bar.setValue)
        self.bar.valueChanged.connect(inner.setValue)

    def setWidget(self, widget: QWidget) -> None:
        super().setWidget(widget)
        widget.setAutoFillBackground(False)

    def _range(self, lo: int, hi: int) -> None:
        self.bar.setRange(lo, hi)
        self.bar.setPageStep(self.verticalScrollBar().pageStep())
        self.bar.setVisible(hi > lo)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.bar.setGeometry(self.width() - self.BAR, 0, self.BAR, self.height())
        self.bar.setPageStep(self.verticalScrollBar().pageStep())
        self.bar.raise_()


class AppMark(QWidget):
    def __init__(self, size: int = 28):
        super().__init__()
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        paint_app_icon(p, QRectF(self.rect()))
        p.end()


@dataclass(frozen=True)
class SetupState:
    key: str
    done: bool


class MainWindow(GlassWindow):
    settings_changed = Signal(str)

    def __init__(
        self,
        session,
        clock: Callable[[], float] = time.monotonic,
        *,
        toaster: Callable[..., object] | None = None,
    ):
        super().__init__(None, radius=Radius.SHEET, take_focus=True)
        if type(self).backdrop is None:
            self.backdrop = native_backdrop()
        self.fit_timer = QTimer(self)
        self.fit_timer.setSingleShot(True)
        self.fit_timer.timeout.connect(self.fit)
        self.session = session
        self.clock = clock
        self.toaster = toaster
        self._setup_skipped = False
        self._saw_no_permission = False
        self._steps: dict[str, StepRow] = {}
        self.setWindowTitle("TileLens")
        self.setAccessibleName("TileLens settings")
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.set_glass_width(WIDTH)

        root = QVBoxLayout(self)
        root.setContentsMargins(self.content_margins(0))
        root.setSpacing(0)
        root.addWidget(self._build_header())
        root.addWidget(Divider())
        self.banner_box = QWidget()
        banner_lay = QVBoxLayout(self.banner_box)
        banner_lay.setContentsMargins(Space.L, Space.M, Space.L, 0)
        self.banner = Banner("", "info", dismissible=True)
        self.banner.dismissed.connect(lambda: self.banner_box.hide())
        banner_lay.addWidget(self.banner)
        self.banner_box.hide()
        root.addWidget(self.banner_box)

        self.scroll = GlassScroll()
        self.content = QWidget()
        body = QVBoxLayout(self.content)
        body.setContentsMargins(Space.XL, Space.L, Space.XL, Space.XL)
        body.setSpacing(0)
        self.setup_view = self._build_setup()
        self.settings_view = self._build_settings()
        body.addWidget(self.setup_view)
        body.addWidget(self.settings_view)
        body.addStretch(1)
        self.scroll.setWidget(self.content)
        self.content.installEventFilter(self)
        root.addWidget(self.scroll, 1)

        self._connect()
        self._install_shortcuts()
        self.age_timer = QTimer(self)
        self.age_timer.timeout.connect(self._tick)
        self.refresh_settings()
        self.show_snapshot(session.snapshot if session.controller is not None else None)

    def _build_header(self) -> QWidget:
        head = QWidget()
        lay = QHBoxLayout(head)
        lay.setContentsMargins(Space.XL, Space.M, Space.M, Space.M)
        lay.setSpacing(Space.M)
        lay.addWidget(AppMark(28))
        lay.addWidget(Label("TileLens", "headline"))
        self.status = StatusPill("Setup", "idle")
        lay.addWidget(self.status, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addStretch(1)
        self.close_btn = IconButton("close", "Close", variant="bare")
        self.close_btn.clicked.connect(self.hide)
        lay.addWidget(self.close_btn)
        return head

    def _build_setup(self) -> QWidget:
        view = QWidget()
        lay = QVBoxLayout(view)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(Space.S)
        lay.addWidget(Label("Set up TileLens", "title"))
        self.setup_intro = Label(
            "TileLens only watches your screen. It never clicks or types.",
            "body",
            "secondary",
            wrap=True,
        )
        lay.addWidget(self.setup_intro)
        lay.addSpacing(Space.M)
        self.steps_card = GroupCard()
        lay.addWidget(self.steps_card)
        lay.addSpacing(Space.S)
        self.skip_btn = GlassButton("Show all settings", size="s")
        self.skip_btn.clicked.connect(self._skip_setup)
        lay.addWidget(self.skip_btn, 0, Qt.AlignmentFlag.AlignLeft)
        return view

    def _build_settings(self) -> QWidget:
        view = QWidget()
        lay = QVBoxLayout(view)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(Space.XXL)
        for section in (
            self._build_status(),
            self._build_preview(),
            self._build_overlay(),
            self._build_appearance(),
            self._build_game(),
            self._build_shortcuts(),
            self._build_calibration(),
            self._build_diagnostics(),
        ):
            lay.addWidget(section)
        return view

    def _build_status(self) -> Section:
        sec = Section("Status")
        self.timing = Label("", "footnote", "secondary", elide=True, align=RIGHT)
        sec.add_head(self.timing)
        card = GroupCard()
        row = QWidget()
        col = QVBoxLayout(row)
        col.setContentsMargins(0, Space.M, 0, Space.M)
        self.detail = Label("", "body", wrap=True)
        col.addWidget(self.detail, 0, Qt.AlignmentFlag.AlignVCenter)
        row.setMinimumHeight(DETAIL_HEIGHT)
        card.add_row(row)
        sec.add(card)
        buttons = QHBoxLayout()
        buttons.setSpacing(Space.S)
        self.pause_btn = GlassButton("Pause", icon="pause")
        self.rescan_btn = GlassButton("Rescan", icon="refresh")
        self.check_btn = GlassButton("Check tiles", variant="tinted", icon="check")
        for b in (self.pause_btn, self.rescan_btn, self.check_btn):
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            buttons.addWidget(b)
        wrap = QWidget()
        wrap.setLayout(buttons)
        buttons.setContentsMargins(0, 0, 0, 0)
        sec.add(wrap)
        return sec

    def _build_preview(self) -> Section:
        sec = Section("Preview", collapsible=True)
        self.preview_section = sec
        self.label = Label("", "footnote", "secondary", elide=True)
        sec.add(self.label)
        self.cards = [PlayRow(i) for i in range(PLAY_SLOTS)]
        for card in self.cards:
            card.chosen.connect(self.select_card)
            sec.add(card)
        self.preview = BoardPreview()
        sec.add(self.preview)
        return sec

    def _build_overlay(self) -> Section:
        sec = Section("Overlay")
        self.mode = KeyedSegmented(MODES, self.session.settings.overlay_mode)
        self.mode.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.mode.setToolTip("\n".join(f"{label}: {text}" for _, label, text in MODES))
        self.mode_caption = Label("", "footnote", "secondary", wrap=True)
        sec.add(self.mode)
        sec.add(self.mode_caption)
        card = GroupCard()
        self.guides = KeyedSegmented(GUIDES, self.session.settings.guides)
        self.guides.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.guides.setToolTip(
            "Auto shows the outlines briefly and whenever something needs a look."
        )
        card.add_row(
            Row("Alignment guides", "Outlines of the board and rack that were found.", self.guides)
        )
        self.hide_btn = Switch(True, "Show suggestions on the board")
        card.add_row(Row("Show suggestions on the board", "", self.hide_btn))
        self.compact_switch = Switch(False, "Compact panel")
        card.add_row(
            Row(
                "Compact panel",
                "One play at a time, with arrows to see the others.",
                self.compact_switch,
            )
        )
        self.reset_panel_btn = GlassButton("Reset", size="s")
        self.panel_row = card.add_row(
            Row(
                "Panel position",
                "You moved the panel. It normally sits beside the board.",
                self.reset_panel_btn,
            )
        )
        sec.add(card)
        return sec

    def _build_appearance(self) -> Section:
        sec = Section("Appearance")
        card = GroupCard()
        self.theme = KeyedSegmented(THEMES, self.session.settings.appearance)
        self.theme.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        card.add_row(Row("Theme", "Auto follows what is behind the window.", self.theme))
        self.motion_switch = Switch(False, "Reduce motion")
        card.add_row(
            Row("Reduce motion", "Changes happen at once instead of fading.", self.motion_switch)
        )
        sec.add(card)
        return sec

    def _build_game(self) -> Section:
        sec = Section("Game")
        card = GroupCard()
        self.profile_btn = GlassButton("Profile…", size="s")
        self.profile_row = card.add_row(Row("Profile", "", self.profile_btn, elide=True))
        self.profile_name = self.profile_row.caption
        self.words_btn = GlassButton("Word list…", size="s")
        self.words_row = card.add_row(Row("Word list", "", self.words_btn, elide=True))
        self.words_name = self.words_row.caption
        self.region_btn = GlassButton("Select region", size="s")
        self.region_row = card.add_row(Row("Game area", "", self.region_btn, elide=True))
        sec.add(card)
        return sec

    def _build_shortcuts(self) -> Section:
        sec = Section("Shortcuts", collapsible=True, expanded=False)
        self.shortcuts_section = sec
        card = GroupCard()
        self.shortcut_rows: dict[str, Row] = {}
        for name, label in SHORTCUT_LABELS.items():
            row = Row(label, "", None, pad=6)
            row.keys = None
            self.shortcut_rows[name] = row
            card.add_row(row)
        sec.add(card)
        note = Label(
            "Shortcuts work while a TileLens window is in front.",
            "footnote",
            "secondary",
            wrap=True,
        )
        sec.add(note)
        return sec

    def _build_calibration(self) -> Section:
        sec = Section("Calibration")
        card = GroupCard()
        self.cal_btn = GlassButton("Calibrate", size="s")
        self.cal_row = card.add_row(Row("Calibration", "", self.cal_btn, elide=True))
        sec.add(card)
        return sec

    def _build_diagnostics(self) -> Section:
        sec = Section("Diagnostics", collapsible=True, expanded=False)
        self.diagnostics_section = sec
        card = GroupCard()
        self.diag_read = Label(MISSING, "body", "secondary", tabular=True)
        self.diag_solve = Label(MISSING, "body", "secondary", tabular=True)
        self.diag_age = Label(MISSING, "body", "secondary", tabular=True)
        for title, value in (
            ("Last read", self.diag_read),
            ("Last search", self.diag_solve),
            ("Result age", self.diag_age),
        ):
            card.add_row(Row(title, "", value, pad=6))
        self.dump_btn = GlassButton("Save debug dump", size="s")
        self.dump_btn.setToolTip("Save the current capture and what was read from it to disk")
        card.add_row(
            Row(
                "Debug dump",
                "Saves the current capture and what was read from it.",
                self.dump_btn,
            )
        )
        sec.add(card)
        return sec

    def _connect(self) -> None:
        s = self.session
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.rescan_btn.clicked.connect(s.rescan)
        self.hide_btn.toggled.connect(lambda shown: self.toggle_hide(not shown))
        self.check_btn.clicked.connect(self.open_corrections)
        self.words_btn.clicked.connect(self.choose_words)
        self.profile_btn.clicked.connect(self.choose_profile)
        self.dump_btn.clicked.connect(self.save_dump)
        self.mode.key_changed.connect(self.set_mode)
        self.guides.key_changed.connect(self.set_guides)
        self.theme.key_changed.connect(self.set_appearance)
        self.motion_switch.toggled.connect(self.set_motion_reduced)
        self.compact_switch.toggled.connect(self.set_panel_compact)
        self.reset_panel_btn.clicked.connect(self.reset_panel_position)
        self.preview_section.toggled.connect(lambda _on: self.queue_fit())
        self.shortcuts_section.toggled.connect(lambda _on: self.queue_fit())
        self.diagnostics_section.toggled.connect(lambda _on: self.queue_fit())
        s.updated.connect(self.show_snapshot)
        s.message.connect(self.show_message)
        s.selection_changed.connect(lambda _i: self.refresh_selection())
        s.panel.check_clicked.connect(self.open_corrections)
        changed = getattr(s, "settings_changed", None)
        if changed is not None:
            changed.connect(lambda _key: self.refresh_settings())

    def _install_shortcuts(self) -> None:
        self.shortcuts: dict[str, QShortcut] = {}
        actions = {
            "pause": self.toggle_pause,
            "rescan": self.session.rescan,
            "hide_preview": self.hide_btn.toggle,
            "next_card": self.next_card,
            "check_tiles": self.open_corrections,
            "recalibrate": self.cal_btn.click,
            "select_1": lambda: self.select_card(0),
            "select_2": lambda: self.select_card(1),
            "select_3": lambda: self.select_card(2),
        }
        for name, fn in actions.items():
            seq = self.session.settings.shortcuts.get(name)
            if seq:
                sc = QShortcut(QKeySequence(seq), self)
                sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
                sc.activated.connect(fn)
                self.shortcuts[name] = sc

    # settings model

    def refresh_settings(self) -> None:
        """Bring every control in line with session.settings and the loaded profile."""
        st = self.session.settings
        with quiet(
            self.mode,
            self.guides,
            self.theme,
            self.motion_switch,
            self.compact_switch,
            self.hide_btn,
        ):
            self.mode.set_current_key(st.overlay_mode)
            self.guides.set_current_key(st.guides)
            self.theme.set_current_key(st.appearance)
            self.motion_switch.setChecked(st.reduce_motion)
            self.compact_switch.setChecked(st.panel_compact)
            self.hide_btn.setChecked(not self.session.preview_hidden)
        self._show_mode_caption()
        self._show_game()
        self._show_shortcuts()
        self._show_calibration()
        self.panel_row.setVisible(st.panel_offset is not None)

    def _show_mode_caption(self) -> None:
        text = next(t for key, _, t in MODES if key == self.session.settings.overlay_mode)
        self.mode_caption.set_text(text)

    def _show_game(self) -> None:
        s = self.session
        self.profile_name.set_text(s.profile.name)
        self.profile_name.setToolTip(str(s.profile.source or s.profile.name))
        words = s.settings.words or getattr(s.profile, "word_list", None)
        if s.lexicon is None:
            self.words_name.set_text("Not chosen yet")
            self.words_name.setToolTip("")
        else:
            self.words_name.set_text(Path(str(words)).name if words else "Built into the profile")
            self.words_name.setToolTip(str(words or ""))
        needs_region = s.profile.appearance is None
        self.region_row.setVisible(needs_region)
        region = s.settings.region_obj if needs_region else None
        self.region_row.set_caption(
            f"{region.width} × {region.height} at {region.x}, {region.y}" if region else "Not set"
        )
        self.region_btn.setToolTip(
            "" if needs_region else "Not needed: the board is found anywhere on the screen"
        )

    def _show_shortcuts(self) -> None:
        for name, row in self.shortcut_rows.items():
            seq = self.session.settings.shortcuts.get(name, "")
            old = getattr(row, "keys", None)
            if old is not None:
                row.layout().removeWidget(old)
                old.setParent(None)
                old.deleteLater()
            if seq:
                keys = KeyCombo(shortcut_text(seq))
                keys.setToolTip(seq)
            else:
                keys = Label("Not set", "footnote", "secondary")
            row.keys = keys
            row.layout().addWidget(keys, 0, Qt.AlignmentFlag.AlignVCenter)

    def _show_calibration(self) -> None:
        s = self.session
        done = s.recognizer is not None
        cal = getattr(s.recognizer, "cal", None)
        if done:
            how = (
                "Learned from the game" if getattr(cal, "auto", False) else "Learned from your rack"
            )
            self.cal_row.title.set_text("Calibrated")
            self.cal_row.set_caption(how)
        else:
            needs_region = s.profile.appearance is None and s.settings.region is None
            self.cal_row.title.set_text("Not calibrated yet")
            self.cal_row.set_caption(
                "Select the game area, then calibrate"
                if needs_region
                else "Open the game, then calibrate"
            )
        self.cal_btn.setText("Recalibrate" if done else "Calibrate")
        self.cal_btn.setToolTip("Learn from the screen as it is now")

    def _change(self, key: str) -> None:
        self.settings_changed.emit(key)

    def set_mode(self, mode: str) -> None:
        if mode not in OVERLAY_MODES:
            return
        self.session.settings.overlay_mode = mode
        with quiet(self.mode):
            self.mode.set_current_key(mode)
        self._show_mode_caption()
        if self.session.controller is not None:
            self.session.controller.source = self.session._make_source()
        self.session.refresh_overlay()
        self._change("overlay_mode")

    def set_guides(self, guides: str) -> None:
        if guides not in GUIDE_MODES:
            return
        self.session.settings.guides = guides
        with quiet(self.guides):
            self.guides.set_current_key(guides)
        self.session.refresh_overlay()
        self._change("guides")

    def set_appearance(self, mode: str) -> None:
        if mode not in APPEARANCE_MODES:
            return
        self.session.settings.appearance = mode
        with quiet(self.theme):
            self.theme.set_current_key(mode)
        self._change("appearance")

    def set_motion_reduced(self, on: bool) -> None:
        self.session.settings.reduce_motion = bool(on)
        with quiet(self.motion_switch):
            self.motion_switch.setChecked(bool(on))
        set_reduce_motion(bool(on))
        self._change("reduce_motion")

    def set_panel_compact(self, on: bool) -> None:
        self.session.settings.panel_compact = bool(on)
        with quiet(self.compact_switch):
            self.compact_switch.setChecked(bool(on))
        self.session.refresh_panel()
        self._change("panel_compact")

    def reset_panel_position(self) -> None:
        self.session.settings.panel_offset = None
        panel = self.session.panel
        if hasattr(panel, "user_moved"):
            panel.user_moved = False
        self.panel_row.setVisible(False)
        self.session.refresh_panel()
        self._change("panel_offset")
        self.toast("Panel moved back beside the board")

    # messages

    def toast(self, text: str, kind: str = "success", ms: int = 2500) -> None:
        show = self.toaster or notify.show
        show(text, kind, ms, self.toast_anchor())

    def toast_anchor(self) -> QWidget | None:
        if self.isVisible():
            return self
        panel = getattr(self.session, "panel", None)
        return panel if panel is not None and panel.isVisible() else None

    def show_message(self, text: str, kind: str = "info") -> None:
        if text:
            self.banner.set_kind(kind)
            self.banner.set_text(text)
        self.banner_box.setVisible(bool(text))
        self.queue_fit()

    # session state

    def show_snapshot(self, snap: Snapshot | None) -> None:
        label, tone = self._pill_for(snap)
        self.status.set_status(label, tone)
        self.detail.set_text(self._detail_for(snap))
        moves = snap.result.moves if snap and snap.result and snap.status == Status.READY else []
        for i, card in enumerate(self.cards):
            card.set_move(moves[i] if i < len(moves) else None)
        self.label.set_text(self._preview_label(snap, moves))
        self.check_btn.setEnabled(
            snap is not None and snap.status == Status.CHECK and snap.reading is not None
        )
        paused = snap is not None and snap.status == Status.PAUSED
        self.pause_btn.setText("Resume" if paused else "Pause")
        self.pause_btn.set_icon_name("play" if paused else "pause")
        self.pause_btn.setEnabled(snap is not None)
        self.rescan_btn.setEnabled(snap is not None)
        self.refresh_selection()
        self.update_timing()
        self._show_game()
        self._show_calibration()
        self._sync_setup()

    @staticmethod
    def _pill_for(snap: Snapshot | None) -> tuple[str, str]:
        phase = phase_of(snap)
        tone = phase.tone
        if tone == "busy" and snap is not None and snap.result is not None:
            tone = "ok"
        return phase.label, tone

    def _detail_for(self, snap: Snapshot | None) -> str:
        if snap is None:
            if self.session.lexicon is None:
                return "Choose a word list"
            if self.session.recognizer is None:
                return (
                    "Calibrate: type your rack"
                    if self.session.profile.appearance is not None
                    else "Select the region, then calibrate"
                )
            return "Starting"
        if snap.detail and snap.detail != snap.status.value:
            return snap.detail
        hint = phase_of(snap).hint
        if hint:
            return hint
        return {
            Status.READY: "Watching the board and your rack",
            Status.READING: "Reading the screen",
            Status.SOLVING: "Looking for plays",
        }.get(snap.status, "")

    @staticmethod
    def _preview_label(snap: Snapshot | None, moves: list) -> str:
        if snap is None or snap.status != Status.READY or snap.result is None:
            return ""
        if not moves:
            return snap.detail or "No legal placement"
        label = snap.result.label.capitalize()
        if snap.coverage == Coverage.PARTIAL:
            label = f"{label} among the squares in view"
        note = COVERAGE_NOTES.get(snap.coverage)
        return f"{label} · {note}" if note else label

    def refresh_selection(self) -> None:
        sel = self.session.selected
        for i, card in enumerate(self.cards):
            card.set_selected(i == sel and card.move_shown is not None)
        snap = self.session.snapshot
        board = snap.board if snap is not None and snap.board is not None else None
        self.preview.set_state(board, self.session.selected_move(), self.session.profile)
        with quiet(self.hide_btn):
            self.hide_btn.setChecked(not self.session.preview_hidden)

    def update_timing(self) -> None:
        snap = self.session.snapshot
        ctrl = self.session.controller
        read = getattr(ctrl, "last_read_ms", None) if ctrl is not None else None
        ready = snap is not None and snap.published_at is not None and snap.status == Status.READY
        solve = snap.solve_ms if ready else None
        parts = []
        if read is not None:
            parts.append(f"read {read:.0f} ms")
        if solve is not None:
            parts.append("from cache" if solve == 0 else f"solved in {solve:.0f} ms")
        self.timing.set_text(" · ".join(parts))
        self.diag_read.set_text(MISSING if read is None else f"{read:.0f} ms")
        self.diag_solve.set_text(
            MISSING if solve is None else "from cache" if solve == 0 else f"{solve:.0f} ms"
        )
        age = self.clock() - snap.published_at if ready else None
        self.diag_age.set_text(MISSING if age is None else f"{age:.1f} s")

    def _tick(self) -> None:
        self.update_timing()
        self._sync_setup()
        if self.session.settings.panel_offset is not None and self.panel_row.isHidden():
            self.panel_row.show()

    # setup checklist

    def permission(self) -> bool | None:
        adapter = self.session.adapter
        if adapter is None:
            return None
        try:
            return adapter.screen_permission()
        except Exception:
            return None

    def setup_states(self) -> list[SetupState]:
        s = self.session
        permission = self.permission()
        self._saw_no_permission = self._saw_no_permission or permission is False
        out = []
        if self._saw_no_permission:
            out.append(SetupState("permission", permission is not False))
        out.append(SetupState("words", s.lexicon is not None))
        if s.profile.appearance is None:
            out.append(SetupState("region", s.settings.region is not None))
        out.append(SetupState("learn", s.recognizer is not None))
        return out

    @property
    def needs_setup(self) -> bool:
        return any(not st.done for st in self.setup_states())

    def _step_texts(self, key: str, done: bool) -> tuple[str, str, str]:
        s = self.session
        auto = s.profile.appearance is not None
        if key == "permission":
            return (
                "Allow screen recording",
                "Screen recording is allowed."
                if done
                else "TileLens needs permission to see the game. Turn it on, then reopen TileLens.",
                "Allow access",
            )
        if key == "words":
            name = Path(s.settings.words).name if s.settings.words else "the profile's word list"
            return (
                "Choose a word list",
                f"Using {name}."
                if done
                else "The dictionary the game accepts: a text file with one word per line.",
                "Choose…",
            )
        if key == "region":
            return (
                "Select the game area",
                "The area is saved." if done else "Drag a box around the board and your rack.",
                "Select area",
            )
        return (
            "Open the game and let TileLens learn it",
            "TileLens knows how this game looks."
            if done
            else (
                "Open the game with its board and rack in view. TileLens learns how it looks "
                "by itself."
                if auto
                else "Open the game, then type your rack letters when asked."
            ),
            "Learn the game",
        )

    def _step_action(self, key: str) -> Callable[[], None]:
        return {
            "permission": self._ask_permission,
            "words": self.choose_words,
            "region": self.region_btn.click,
            "learn": self.cal_btn.click,
        }[key]

    def _ask_permission(self) -> None:
        adapter = self.session.adapter
        if adapter is None:
            return
        adapter.request_permission()
        self.show_message(adapter.permission_help(), "warning")

    def _sync_setup(self) -> None:
        states = self.setup_states()
        keys = [st.key for st in states]
        if keys != list(self._steps):
            self._rebuild_steps(keys)
        first_open = next((st.key for st in states if not st.done), None)
        earlier_done = True
        for number, st in enumerate(states, 1):
            row = self._steps[st.key]
            title, caption, button = self._step_texts(st.key, st.done)
            row.badge.set_state(
                "done" if st.done else "current" if st.key == first_open else "todo", number
            )
            row.title.set_text(title)
            row.caption.set_text(caption)
            row.button.setText(button)
            row.show_state(
                done=st.done,
                current=st.key == first_open,
                enabled=earlier_done or st.key != "learn",
            )
            if st.key != "learn":
                earlier_done = earlier_done and st.done
        show = first_open is not None and not self._setup_skipped
        if show != self.setup_view.isVisibleTo(self) or show == self.settings_view.isVisibleTo(
            self
        ):
            self.setup_view.setVisible(show)
            self.settings_view.setVisible(not show)
            self.queue_fit()

    def _rebuild_steps(self, keys: list[str]) -> None:
        for row in self._steps.values():
            self.steps_card.rows.removeWidget(row)
            row.setParent(None)
            row.deleteLater()
        self.steps_card._rows.clear()
        self._steps = {}
        for number, key in enumerate(keys, 1):
            row = StepRow(key, number)
            row.button.clicked.connect(lambda _=False, k=key: self._step_action(k)())
            self._steps[key] = row
            self.steps_card.add_row(row)

    def _skip_setup(self) -> None:
        self._setup_skipped = True
        self._sync_setup()

    # actions

    def select_card(self, index: int) -> None:
        self.session.select(index)
        self.refresh_selection()

    def next_card(self) -> None:
        self.session.next_card()
        self.refresh_selection()

    def toggle_pause(self) -> None:
        self.session.toggle_pause()

    def toggle_hide(self, hidden: bool) -> None:
        self.session.set_preview_hidden(hidden)
        with quiet(self.hide_btn):
            self.hide_btn.setChecked(not hidden)

    def open_corrections(self) -> CorrectionDialog | None:
        snap = self.session.snapshot
        if snap is None or snap.status != Status.CHECK or snap.reading is None:
            return None
        dlg = CorrectionDialog(snap.reading)
        if self.session.adapter is not None:
            self.session.adapter.show_on_all_spaces(int(dlg.winId()))
        if dlg.exec():
            ok = self.session.controller.apply_corrections(dlg.fixes, rack=dlg.rack)
            if not ok:
                self.show_message(
                    "Some squares are still unclear; fill in every highlighted square.", "warning"
                )
        return dlg

    def save_dump(self) -> None:
        path = self.session.save_debug_dump()
        if path:
            self.toast(f"Debug dump saved: {Path(path).name}")
        else:
            self.toast("Nothing captured yet", "warning")

    def choose_words(self) -> None:
        current = self.session.settings.words
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Word list",
            str(Path(current).parent) if current else "",
            "Text files (*.txt);;All files (*)",
        )
        if path:
            self.session.settings.words = path
            self.session.reload()
            self.show_message("")
            self._show_game()
            self._sync_setup()
            if self.session.lexicon is not None:
                self.toast(f"Word list loaded: {Path(path).name}")

    def choose_profile(self) -> None:
        current = self.session.settings.profile
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Profile",
            str(Path(current).parent) if current else "",
            "Profiles (*.toml)",
        )
        if path:
            self.session.settings.profile = path
            self.session.settings.calibration = None
            self.session.reload()
            self._show_game()
            self._show_calibration()
            self._sync_setup()
            self.toast(f"Profile loaded: {self.session.profile.name}")

    # window

    def _skin_height(self) -> int:
        """The glass height the content wants, before it is limited to the screen."""
        head = self.layout().itemAt(0).widget().sizeHint().height()
        banner = 0
        if not self.banner_box.isHidden():
            banner = self.banner_box.layout().totalHeightForWidth(WIDTH)
        lay = self.content.layout()
        body = (
            lay.totalHeightForWidth(WIDTH) if lay.hasHeightForWidth() else lay.sizeHint().height()
        )
        return head + 1 + banner + body

    def _screen_cap(self) -> int:
        screen = self.screen()
        room = screen.availableGeometry().height() if screen is not None else MAX_HEIGHT
        return max(320, min(MAX_HEIGHT, room - 2 * SCREEN_MARGIN))

    def fit(self) -> None:
        """Size the window to its content, up to what the screen allows; the rest scrolls."""
        glass = min(self._skin_height(), self._screen_cap())
        total = glass + 2 * SHADOW_BLEED
        if total == self.height() and self.minimumHeight() == self.maximumHeight():
            return
        self.setFixedHeight(total)
        if self.isVisible():
            self._keep_on_screen()

    def queue_fit(self) -> None:
        if not self.fit_timer.isActive():
            self.fit_timer.start(0)

    def _keep_on_screen(self) -> None:
        screen = self.screen()
        if screen is None:
            return
        area = screen.availableGeometry()
        glass = self.glass_geometry()
        overshoot = glass.bottom() - (area.bottom() - SCREEN_MARGIN // 3)
        if overshoot > 0:
            self.move(self.x(), self.y() - overshoot)

    def eventFilter(self, obj, event) -> bool:
        if obj is self.content and event.type() == QEvent.Type.LayoutRequest:
            self.queue_fit()
        return super().eventFilter(obj, event)

    def showEvent(self, event) -> None:
        self.ensurePolished()
        self.fit()
        super().showEvent(event)
        self.refresh_settings()
        self.update_timing()
        self.age_timer.start(500)

    def hideEvent(self, event) -> None:
        self.age_timer.stop()
        super().hideEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            event.accept()
            return
        super().keyPressEvent(event)

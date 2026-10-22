from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from tilelens.state.controller import Snapshot, Status
from tilelens.state.memory import COVERAGE_NOTES, Coverage
from tilelens.ui.cards import ResultCard
from tilelens.ui.corrections import CorrectionDialog
from tilelens.ui.preview import CellMapper, paint_board, paint_move
from tilelens.ui.settings import OVERLAY_MODES

STATUS_COLORS = {
    Status.READING: "#5b7fa6",
    Status.SOLVING: "#8a63b8",
    Status.READY: "#2e8b57",
    Status.CHECK: "#c98a00",
    Status.PAUSED: "#777777",
    Status.NOT_VISIBLE: "#b00020",
}

STYLE = """
QWidget { font-size: 13px; }
#status { color: white; border-radius: 10px; padding: 3px 10px; font-weight: bold; }
#card { border: 1px solid #c8c8c8; border-radius: 8px; background: palette(base); }
#card[selected="true"] { border: 2px solid #e0a000; }
#cardTitle { font-size: 16px; font-weight: bold; }
#muted { color: #666666; font-size: 12px; }
"""


class BoardPreview(QWidget):
    """Duplicate of the overlay inside the panel; also the fallback when no overlay is used."""

    def __init__(self):
        super().__init__()
        self.board = None
        self.move = None
        self.profile = None
        self.setMinimumSize(220, 220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_state(self, board, move, profile) -> None:
        self.board, self.move, self.profile = board, move, profile
        self.update()

    def paintEvent(self, event) -> None:
        if self.board is None:
            return
        side = min(self.width() / self.board.width, self.height() / self.board.height)
        x0 = (self.width() - side * self.board.width) / 2
        y0 = (self.height() - side * self.board.height) / 2
        mapper = CellMapper(x0, y0, side, side)
        p = QPainter(self)
        paint_board(p, self.board, mapper, self.profile)
        if self.move is not None:
            paint_move(p, self.move, lambda r, c: mapper.rect(r, c, 0.04))
        p.end()


class MainWindow(QMainWindow):
    def __init__(self, session, clock=time.monotonic):
        super().__init__()
        self.session = session
        self.clock = clock
        self.setWindowTitle("TileLens")
        self.setStyleSheet(STYLE)
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        top = QHBoxLayout()
        self.status = QLabel("Starting")
        self.status.setObjectName("status")
        self.detail = QLabel()
        self.timing = QLabel()
        self.timing.setObjectName("muted")
        top.addWidget(self.status)
        top.addWidget(self.detail, 1)
        top.addWidget(self.timing)
        root.addLayout(top)

        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet("background:#fff3cd;padding:6px;border-radius:6px")
        self.banner.hide()
        root.addWidget(self.banner)

        body = QHBoxLayout()
        cards_col = QVBoxLayout()
        self.label = QLabel()
        self.label.setObjectName("muted")
        cards_col.addWidget(self.label)
        self.cards = [ResultCard(i) for i in range(3)]
        for card in self.cards:
            card.clicked.connect(self.select_card)
            cards_col.addWidget(card)
        cards_col.addStretch(1)
        body.addLayout(cards_col, 3)
        self.preview = BoardPreview()
        body.addWidget(self.preview, 2)
        root.addLayout(body, 1)

        buttons = QHBoxLayout()
        self.region_btn = QPushButton("Select region")
        self.cal_btn = QPushButton("Calibrate")
        self.pause_btn = QPushButton("Pause")
        self.rescan_btn = QPushButton("Rescan")
        self.hide_btn = QPushButton("Hide preview")
        self.hide_btn.setCheckable(True)
        self.check_btn = QPushButton("Check tiles")
        for b in (
            self.region_btn,
            self.cal_btn,
            self.pause_btn,
            self.rescan_btn,
            self.hide_btn,
            self.check_btn,
        ):
            buttons.addWidget(b)
        root.addLayout(buttons)

        opts = QHBoxLayout()
        self.words_btn = QPushButton("Word list…")
        self.profile_btn = QPushButton("Profile…")
        self.mode = QComboBox()
        self.mode.addItems(OVERLAY_MODES)
        self.mode.setCurrentText(session.settings.overlay_mode)
        self.mode.setToolTip(
            "exclude: overlay hidden from capture; suppress: hidden during each capture; "
            "panel_only: no overlay, preview in this window"
        )
        self.dump_btn = QPushButton("Save debug dump")
        self.dump_btn.setToolTip("Save the current capture and what was read from it to disk")
        opts.addWidget(self.profile_btn)
        opts.addWidget(self.words_btn)
        opts.addWidget(QLabel("Overlay:"))
        opts.addWidget(self.mode)
        opts.addWidget(self.dump_btn)
        opts.addStretch(1)
        self.profile_name = QLabel(session.profile.name)
        self.profile_name.setObjectName("muted")
        opts.addWidget(self.profile_name)
        root.addLayout(opts)

        self.pause_btn.clicked.connect(self.toggle_pause)
        self.rescan_btn.clicked.connect(session.rescan)
        self.hide_btn.toggled.connect(self.toggle_hide)
        self.check_btn.clicked.connect(self.open_corrections)
        self.words_btn.clicked.connect(self.choose_words)
        self.profile_btn.clicked.connect(self.choose_profile)
        self.mode.currentTextChanged.connect(self.set_mode)
        self.dump_btn.clicked.connect(self.save_dump)
        session.updated.connect(self.show_snapshot)
        session.message.connect(self.show_message)
        session.selection_changed.connect(lambda _i: self.refresh_selection())
        session.panel.check_clicked.connect(self.open_corrections)

        self.shortcuts = {}
        actions = {
            "pause": self.toggle_pause,
            "rescan": session.rescan,
            "hide_preview": self.hide_btn.toggle,
            "next_card": self.next_card,
            "check_tiles": self.open_corrections,
            "recalibrate": self.cal_btn.click,
        }
        for name, fn in actions.items():
            seq = session.settings.shortcuts.get(name)
            if seq:
                sc = QShortcut(QKeySequence(seq), self)
                sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
                sc.activated.connect(fn)
                self.shortcuts[name] = sc

        self.age_timer = QTimer(self)
        self.age_timer.timeout.connect(self.update_timing)
        self.age_timer.start(500)
        self.resize(820, 560)
        if not session.ready_to_run:
            self.show_snapshot(None)

    def show_message(self, text: str) -> None:
        self.banner.setText(text)
        self.banner.setVisible(bool(text))

    def show_snapshot(self, snap: Snapshot | None) -> None:
        if snap is None:
            if self.session.lexicon is None:
                text = "Choose a word list"
            elif self.session.recognizer is None:
                text = (
                    "Calibrate: type your rack"
                    if self.session.profile.appearance is not None
                    else "Select the region, then calibrate"
                )
            else:
                text = "Starting"
            self.status.setText("Setup")
            self.status.setStyleSheet("background:#777777")
            self.detail.setText(text)
            return
        self.status.setText(snap.status.value)
        self.status.setStyleSheet(f"background:{STATUS_COLORS[snap.status]}")
        self.detail.setText(snap.detail if snap.detail != snap.status.value else "")
        moves = snap.result.moves if snap.result and snap.status == Status.READY else []
        for i, card in enumerate(self.cards):
            card.set_move(moves[i] if i < len(moves) else None)
        if snap.status == Status.READY and snap.result is not None:
            if moves:
                label = snap.result.label.capitalize()
                if snap.coverage == Coverage.PARTIAL:
                    label = f"{snap.result.label.capitalize()} among the squares in view"
                note = COVERAGE_NOTES.get(snap.coverage)
                self.label.setText(f"{label} · {note}" if note else label)
            else:
                self.label.setText(snap.detail or "No legal placement")
        else:
            self.label.setText("")
        self.check_btn.setEnabled(snap.status == Status.CHECK and snap.reading is not None)
        self.pause_btn.setText("Resume" if snap.status == Status.PAUSED else "Pause")
        self.refresh_selection()
        self.update_timing()

    def refresh_selection(self) -> None:
        sel = self.session.selected
        for i, card in enumerate(self.cards):
            card.set_selected(i == sel and card.move_shown is not None)
        snap = self.session.snapshot
        board = snap.board if snap is not None and snap.board is not None else None
        self.preview.set_state(board, self.session.selected_move(), self.session.profile)

    def update_timing(self) -> None:
        snap = self.session.snapshot
        ctrl = self.session.controller
        parts = []
        if snap is not None and snap.published_at is not None and snap.status == Status.READY:
            parts.append(f"result {self.clock() - snap.published_at:.1f} s old")
            if snap.solve_ms is not None:
                parts.append(
                    "from cache" if snap.solve_ms == 0 else f"solved in {snap.solve_ms:.0f} ms"
                )
        if ctrl is not None and ctrl.last_read_ms is not None:
            parts.append(f"read {ctrl.last_read_ms:.0f} ms")
        self.timing.setText(" · ".join(parts))

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
        self.hide_btn.setText("Show preview" if hidden else "Hide preview")

    def set_mode(self, mode: str) -> None:
        self.session.settings.overlay_mode = mode
        if self.session.controller is not None:
            self.session.controller.source = self.session._make_source()
        self.session.refresh_overlay()

    def open_corrections(self) -> CorrectionDialog | None:
        snap = self.session.snapshot
        if snap is None or snap.status != Status.CHECK or snap.reading is None:
            return None
        dlg = CorrectionDialog(snap.reading)
        dlg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        if self.session.adapter is not None:
            self.session.adapter.show_on_all_spaces(int(dlg.winId()))
        if dlg.exec():
            ok = self.session.controller.apply_corrections(dlg.fixes, rack=dlg.rack)
            if not ok:
                self.show_message(
                    "Some squares are still unclear; fill in every highlighted square."
                )
        return dlg

    def save_dump(self) -> None:
        path = self.session.save_debug_dump()
        self.show_message(f"Saved {path}" if path else "Nothing captured yet.")

    def choose_words(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Word list", "", "Text files (*.txt);;All files (*)"
        )
        if path:
            self.session.settings.words = path
            self.session.reload()
            self.show_message("")

    def choose_profile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Profile", "", "Profiles (*.toml)")
        if path:
            self.session.settings.profile = path
            self.session.settings.calibration = None
            self.session.reload()
            self.profile_name.setText(self.session.profile.name)

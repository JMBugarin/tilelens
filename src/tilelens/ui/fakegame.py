from __future__ import annotations

import itertools
import random
import time

import numpy as np
from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from tilelens.platforms.base import Region
from tilelens.synth.game import FakeGame
from tilelens.ui.calibrate_dialog import to_qimage


class FakeGameWindow(QWidget):
    """A window that plays a synthetic crossword-style tile game by itself.

    Used for the demo, the end-to-end tests and the screenshots in the docs.
    """

    stepped = Signal(str)

    def __init__(
        self,
        seed: int = 3,
        theme: str = "meadow",
        cell: int = 36,
        step_ms: int = 100,
        script: bool = True,
    ):
        super().__init__()
        self.setWindowTitle("Fake tile game")
        self.game = FakeGame(seed=seed, theme=theme, cell=cell)
        self.rng = random.Random(seed)
        self.view = QLabel()
        self.view.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.view)
        self.frame_cache: np.ndarray | None = None
        self.events: list[tuple[float, str]] = []
        self._action = None
        self._ticks = 0
        self._idle = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.step)
        self.redraw()
        if script:
            self.timer.start(step_ms)

    def redraw(self) -> None:
        self.frame_cache = self.game.frame()
        pix = QPixmap.fromImage(to_qimage(self.frame_cache))
        self.view.setPixmap(pix)
        self.view.setFixedSize(pix.size())

    def current_frame(self) -> np.ndarray | None:
        return None if self.frame_cache is None else self.frame_cache.copy()

    def capture_region(self) -> Region:
        top_left = self.view.mapToGlobal(QPoint(0, 0))
        return Region(top_left.x(), top_left.y(), self.view.width(), self.view.height())

    def _next_action(self):
        roll = self.rng.random()
        if roll < 0.3:
            return "opponent", self.game.opponent_move(frames=4)
        if roll < 0.45:
            return "shuffle", self.game.shuffle(frames=3)
        if roll < 0.75:
            return "place", itertools.chain(
                self.game.place_best(), self._hold(12), self.game.submit()
            )
        return "wait", self._hold(self.rng.randint(15, 30))

    def _hold(self, n):
        for _ in range(n):
            yield

    def step(self) -> None:
        self._ticks += 1
        if self._ticks % 10 == 0:
            self.game.tick_timer()
        if self._action is None:
            if self._idle > 0:
                self._idle -= 1
                self.redraw()
                return
            name, gen = self._next_action()
            self._action = (name, gen)
            self.events.append((time.monotonic(), f"start {name}"))
        name, gen = self._action
        try:
            next(gen)
        except StopIteration:
            self._action = None
            self._idle = 20
            self.events.append((time.monotonic(), f"stable after {name}"))
            self.stepped.emit(name)
        self.redraw()

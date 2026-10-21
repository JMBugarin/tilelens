"""A scripted fake game built on the synthetic renderer.

It changes state the way a real game would (opponent moves that fade in, drags, tentative
placement, submission, rack shuffles, a ticking timer and chat) and always knows the true
position, so replays can check what the live loop shows.
"""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Iterator

import numpy as np

from tilelens.rules.board import Board, Tile
from tilelens.rules.profile import BLANK
from tilelens.solver.selfplay import full_bag
from tilelens.synth.render import COMMITTED, RECENT, TENTATIVE, Scene, layout, render
from tilelens.synth.scenes import demo_engine


class FakeGame:
    def __init__(
        self,
        seed: int = 0,
        theme: str = "meadow",
        cell: int = 36,
        profile_name: str = "demo_garden",
        opening_turns: int = 6,
    ):
        self.rng = random.Random(seed)
        self.profile, self.lexicon, self.gen = demo_engine(profile_name)
        self.theme = theme
        self.cell = cell
        self.geometry = layout(self.profile, cell)
        self.bag = full_bag(self.profile)
        self.rng.shuffle(self.bag)
        self.board = Board(self.profile.width, self.profile.height)
        self.recent: tuple[Tile, ...] = ()
        self.rack: list[str | None] = []
        self.tentative: dict[tuple[int, int], tuple[str, bool]] = {}
        self.fade: dict[tuple[int, int], float] = {}
        self.drag: tuple[float, float, str] | None = None
        self.seconds = 120
        self.chat: list[str] = []
        self.scores = [0, 0]
        self._refill()
        for _ in range(opening_turns):
            self._play_instant(self._opponent_rack())

    # truth

    def position(self) -> tuple[Board, Counter]:
        rack = Counter(t for t in self.rack if t is not None)
        for letter, blank in self.tentative.values():
            rack[BLANK if blank else letter] += 1
        return self.board, rack

    # rendering

    def scene(self) -> Scene:
        tiles = {}
        recent = {(t.row, t.col) for t in self.recent}
        for t in self.board.tiles():
            state = RECENT if (t.row, t.col) in recent else COMMITTED
            tiles[(t.row, t.col)] = (t.letter, t.blank, state)
        for (r, c), (letter, blank) in self.tentative.items():
            tiles[(r, c)] = (letter, blank, TENTATIVE)
        return Scene(
            profile=self.profile,
            tiles=tiles,
            rack=list(self.rack),
            theme=self.theme,
            cell=self.cell,
            header_text=f"Ana {self.scores[0]}   Bo {self.scores[1]}",
            timer=f"{self.seconds // 60}:{self.seconds % 60:02d}",
            chat=self.chat[-8:],
            drag=self.drag,
            fade=dict(self.fade),
        )

    def frame(self) -> np.ndarray:
        return render(self.scene())[0]

    # helpers

    def _refill(self) -> None:
        self.rack = [t for t in self.rack if t is not None]
        while len(self.rack) < self.profile.rack_size and self.bag:
            self.rack.append(self.bag.pop())
        self.rack += [None] * (self.profile.rack_size - len(self.rack))

    def _opponent_rack(self) -> Counter:
        n = min(self.profile.rack_size, len(self.bag))
        letters = [self.bag.pop() for _ in range(n)]
        return Counter(letters)

    def _best(self, rack: Counter, pick: int = 3):
        moves = self.gen.solve(self.board, rack, top_k=pick).moves
        return self.rng.choice(moves) if moves else None

    def _play_instant(self, rack: Counter) -> None:
        move = self._best(rack)
        if move is None:
            return
        self.board = self.board.with_tiles(move.tiles)
        self.recent = move.tiles
        used = Counter(BLANK if t.blank else t.letter for t in move.tiles)
        leftover = list((rack - used).elements())
        self.bag.extend(leftover)
        self.rng.shuffle(self.bag)

    def tick_timer(self) -> None:
        self.seconds = max(0, self.seconds - 1)

    def say(self, text: str) -> None:
        self.chat.append(text)

    # scripted actions, each yields once per animation frame

    def opponent_move(self, frames: int = 4) -> Iterator[None]:
        rack = self._opponent_rack()
        move = self._best(rack)
        if move is None:
            return
        used = Counter(BLANK if t.blank else t.letter for t in move.tiles)
        self.bag.extend((rack - used).elements())
        self.rng.shuffle(self.bag)
        self.recent = ()
        self.board = self.board.with_tiles(move.tiles)
        self.recent = move.tiles
        for i in range(frames):
            self.fade = {(t.row, t.col): (i + 1) / (frames + 1) for t in move.tiles}
            yield
        self.fade = {}
        self.scores[1] += move.score
        yield

    def drag_to(self, slot: int, square: tuple[int, int], frames: int = 5) -> Iterator[None]:
        letter = self.rack[slot]
        if letter is None:
            return
        sx, sy, sw, sh = self.geometry.rack_slots[slot]
        x0, y0 = sx + sw / 2, sy + sh / 2
        cx, cy, cw, ch = self.geometry.cell_rect(*square)
        x1, y1 = cx + cw / 2, cy + ch / 2
        self.rack[slot] = None
        shown = "?" if letter == BLANK else letter
        for i in range(1, frames + 1):
            t = i / (frames + 1)
            self.drag = (x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, shown if shown != "?" else "")
            yield
        self.drag = None
        designated = letter if letter != BLANK else self.rng.choice(sorted(self.profile.alphabet))
        self.tentative[square] = (designated, letter == BLANK)
        yield

    def place_best(self, frames_per_tile: int = 3) -> Iterator[None]:
        rack = Counter(t for t in self.rack if t is not None)
        move = self._best(rack, pick=1)
        if move is None:
            return
        for t in move.tiles:
            want = BLANK if t.blank else t.letter
            slot = self.rack.index(want)
            yield from self.drag_to(slot, (t.row, t.col), frames_per_tile)
            self.tentative[(t.row, t.col)] = (t.letter, t.blank)
        yield

    def submit(self) -> Iterator[None]:
        if not self.tentative:
            return
        tiles = tuple(
            sorted(Tile(r, c, letter, blank) for (r, c), (letter, blank) in self.tentative.items())
        )
        self.board = self.board.with_tiles(tiles)
        self.recent = tiles
        self.tentative = {}
        yield
        self._refill()
        yield

    def recall(self) -> Iterator[None]:
        for (_r, _c), (letter, blank) in list(self.tentative.items()):
            slot = self.rack.index(None)
            self.rack[slot] = BLANK if blank else letter
        self.tentative = {}
        yield

    def shuffle(self, frames: int = 3) -> Iterator[None]:
        letters = [t for t in self.rack if t is not None]
        if not letters:
            return
        rx, ry, rw, rh = self.geometry.rack
        for i in range(frames):
            slot = self.rng.choice([k for k, t in enumerate(self.rack) if t is not None])
            moving = self.rack[slot]
            self.rack[slot] = None
            x = rx + rw * (i + 1) / (frames + 1)
            self.drag = (x, ry - self.cell * 0.6, "" if moving == BLANK else moving)
            yield
            self.drag = None
            self.rack[slot] = moving
            self.rng.shuffle(self.rack)
        self.rng.shuffle(letters)
        self.rack = letters + [None] * (self.profile.rack_size - len(letters))
        yield


def calibration_samples(
    seeds, theme: str = "meadow", cell: int = 36, profile_name: str = "demo_garden"
):
    """Screens and typed truth from fake games, as a user would provide during calibration."""
    samples = []
    game = None
    for seed in seeds:
        game = FakeGame(
            seed=seed, theme=theme, cell=cell, profile_name=profile_name, opening_turns=10
        )
        rack = "".join(t for t in game.rack if t is not None)
        samples.append((game.frame(), game.board.to_rows(), rack))
    geo = game.geometry
    hint = (geo.origin[0], geo.origin[1], geo.cols * geo.pitch, geo.rows * geo.pitch)
    return samples, hint

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass

from tilelens.rules.board import Board, Tile
from tilelens.rules.profile import BLANK, Profile
from tilelens.solver.movegen import MoveGenerator


@dataclass(frozen=True)
class Turn:
    board: Board
    rack: Counter
    played: tuple[Tile, ...]
    bag_left: int


def full_bag(profile: Profile) -> list[str]:
    bag = [L for L, n in profile.distribution.items() for _ in range(n)]
    return bag + [BLANK] * profile.blanks


def play_game(
    profile: Profile,
    gen: MoveGenerator,
    rng: random.Random,
    max_turns: int = 40,
    choose_from: int = 5,
) -> list[Turn]:
    """Self-play a game, picking randomly among the top few moves each turn."""
    bag = full_bag(profile)
    rng.shuffle(bag)
    board = Board(profile.width, profile.height)
    rack: Counter = Counter()
    turns: list[Turn] = []
    passes = 0
    for _ in range(max_turns):
        while sum(rack.values()) < profile.rack_size and bag:
            rack[bag.pop()] += 1
        if not rack:
            break
        result = gen.solve(board, rack, top_k=choose_from)
        if not result.moves:
            passes += 1
            if passes >= 2:
                break
            if bag:
                back = list(rack.elements())
                bag.extend(back)
                rng.shuffle(bag)
                rack = Counter()
            continue
        passes = 0
        move = rng.choice(result.moves)
        turns.append(Turn(board, Counter(rack), move.tiles, len(bag)))
        for t in move.tiles:
            rack[BLANK if t.blank else t.letter] -= 1
        rack = +rack
        board = board.with_tiles(move.tiles)
    return turns

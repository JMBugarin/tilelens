from __future__ import annotations

import argparse
import json
import random
from functools import lru_cache
from pathlib import Path

import cv2

from tilelens.rules.board import Board
from tilelens.rules.lexicon import Lexicon
from tilelens.rules.profile import BLANK, demo_profile
from tilelens.solver.movegen import MoveGenerator
from tilelens.solver.selfplay import play_game
from tilelens.synth.render import COMMITTED, RECENT, TENTATIVE, Scene, render
from tilelens.synth.themes import DEV_THEMES

NAMES = ["Ana", "Bo", "Cyd", "Dee", "Eli", "Fay", "Gus", "Hal"]


@lru_cache(maxsize=4)
def demo_engine(profile_name: str = "demo_garden"):
    profile = demo_profile(profile_name)
    lexicon = Lexicon.load(profile.word_list, profile.alphabet)
    return profile, lexicon, MoveGenerator(profile, lexicon)


@lru_cache(maxsize=64)
def demo_game(profile_name: str, seed: int):
    profile, _, gen = demo_engine(profile_name)
    return play_game(profile, gen, random.Random(seed), max_turns=40)


def scene_from_board(
    profile, board: Board, rack, recent=(), theme="meadow", cell=36, scale=1.0, rng=None
) -> Scene:
    rng = rng or random.Random(0)
    recent_squares = {(t.row, t.col) for t in recent}
    tiles = {
        (t.row, t.col): (
            t.letter,
            t.blank,
            RECENT if (t.row, t.col) in recent_squares else COMMITTED,
        )
        for t in board.tiles()
    }
    letters = list(rack.elements()) if hasattr(rack, "elements") else list(rack)
    rng.shuffle(letters)
    slots: list[str | None] = letters + [None] * (profile.rack_size - len(letters))
    a, b = rng.sample(NAMES, 2)
    return Scene(
        profile=profile,
        tiles=tiles,
        rack=slots,
        theme=theme,
        cell=cell,
        scale=scale,
        header_text=f"{a} {rng.randint(0, 400)}   {b} {rng.randint(0, 400)}",
        timer=f"{rng.randint(0, 2)}:{rng.randint(0, 59):02d}",
        chat=[
            f"{rng.choice(NAMES)}: {rng.choice(['gg', 'hmm', 'nice', 'ok', 'brb'])}"
            for _ in range(rng.randint(0, 6))
        ],
    )


def random_scene(
    seed: int,
    profile_name: str = "demo_garden",
    theme: str | None = None,
    cell: int | None = None,
    scale: float = 1.0,
    tentative: bool | None = None,
    min_turn: int = 1,
) -> Scene:
    rng = random.Random(seed)
    profile, _, gen = demo_engine(profile_name)
    turns = demo_game(profile_name, seed % 16)
    i = rng.randint(min(min_turn, len(turns) - 2), len(turns) - 2)
    board = turns[i + 1].board
    rack = turns[i + 1].rack
    picked_theme = rng.choice(DEV_THEMES)
    picked_cell = rng.choice([28, 32, 36, 40, 44, 48])
    scene = scene_from_board(
        profile,
        board,
        rack,
        recent=turns[i].played,
        theme=theme or picked_theme,
        cell=cell or picked_cell,
        scale=scale,
        rng=rng,
    )
    if tentative is None:
        tentative = rng.random() < 0.3
    if tentative:
        moves = gen.solve(board, rack, top_k=5).moves
        if moves:
            move = rng.choice(moves)
            k = rng.randint(1, len(move.tiles))
            for t in move.tiles[:k]:
                scene.tiles[(t.row, t.col)] = (t.letter, t.blank, TENTATIVE)
                want = BLANK if t.blank else t.letter
                slot = scene.rack.index(want)
                scene.rack[slot] = None
    return scene


def write_fixtures(out: Path, seeds, **kwargs) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for seed in seeds:
        img, labels = render(random_scene(seed, **kwargs))
        stem = out / f"scene-{labels['theme']}-{seed:04d}"
        cv2.imwrite(str(stem.with_suffix(".png")), img)
        stem.with_suffix(".json").write_text(json.dumps(labels, indent=1) + "\n")
        paths.append(stem.with_suffix(".png"))
    return paths


def main() -> int:
    ap = argparse.ArgumentParser(description="Render synthetic board fixtures")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--count", type=int, default=8)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--theme")
    ap.add_argument("--profile", default="demo_garden")
    args = ap.parse_args()
    paths = write_fixtures(
        args.out,
        range(args.start, args.start + args.count),
        profile_name=args.profile,
        theme=args.theme,
    )
    print(f"wrote {len(paths)} scenes to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

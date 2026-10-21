import random
import string
from collections import Counter

from tilelens.rules.board import Board
from tilelens.rules.lexicon import Lexicon
from tilelens.rules.profile import BUILTIN_PREMIUMS, parse_profile
from tilelens.solver.exhaustive import exhaustive_moves

LEGEND = {
    "d": "double_letter",
    "t": "triple_letter",
    "D": "double_word",
    "T": "triple_word",
    "+": {"name": "both", "letter": 2, "word": 2},
}
assert set(BUILTIN_PREMIUMS) >= {v for v in LEGEND.values() if isinstance(v, str)}


def random_profile(rng: random.Random):
    width = rng.randint(4, 7)
    height = rng.randint(4, 7)
    chars = "....." + "".join(LEGEND)
    layout = ["".join(rng.choice(chars) for _ in range(width)) for _ in range(height)]
    letters = rng.sample(string.ascii_uppercase, rng.randint(5, 7))
    values = {L: rng.choice([0, 1, 1, 1, 2, 3, 4, 5, 8, 10]) for L in letters}
    n_starts = rng.choice([0, 1, 1, 2])
    starts = [[rng.randrange(height), rng.randrange(width)] for _ in range(n_starts)]
    rack_size = rng.randint(3, 7)
    raw = {
        "version": 1,
        "name": "Random",
        "board": {"width": width, "height": height, "layout": layout, "legend": LEGEND},
        "rules": {
            "start_squares": starts,
            "min_word_length": rng.choice([2, 2, 2, 3]),
            "word_premium_stacking": rng.choice(["multiply", "add", "max"]),
        },
        "rack": {"size": rack_size, "blanks": 2},
        "scoring": {
            "full_rack_bonus": rng.choice([0, 20, 50]),
            "full_rack_tiles": rng.randint(1, rack_size),
        },
        "tiles": {"values": values, "distribution": {L: 9 for L in letters}},
    }
    return parse_profile(raw)


def random_lexicon(rng: random.Random, letters):
    words = set()
    for _ in range(rng.randint(15, 70)):
        n = rng.choice([2, 2, 3, 3, 3, 4, 4, 5])
        words.add("".join(rng.choice(letters) for _ in range(n)))
    return Lexicon.from_words(words)


def random_rack(rng: random.Random, letters, size):
    n = rng.randint(1, size)
    blanks = min(n, rng.choice([0, 0, 0, 1, 1, 2]))
    rack = Counter(rng.choice(letters) for _ in range(n - blanks))
    if blanks:
        rack["?"] = blanks
    return rack


def random_position(seed: int):
    rng = random.Random(seed)
    profile = random_profile(rng)
    letters = sorted(profile.alphabet)
    lexicon = random_lexicon(rng, letters)
    board = Board(profile.width, profile.height)
    for _ in range(rng.randint(0, 8)):
        moves = exhaustive_moves(
            board, random_rack(rng, letters, profile.rack_size), profile, lexicon
        )
        if not moves:
            continue
        key = rng.choice(sorted(moves))
        board = board.with_tiles(key)
    rack = random_rack(rng, letters, profile.rack_size)
    return profile, lexicon, board, rack

import copy
import os

import pytest

from tilelens.rules.profile import parse_profile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

TINY_VALUES = dict(
    A=1, B=3, C=3, D=2, E=1, F=4, G=2, H=4, I=1, J=8, K=5, L=1, M=3,
    N=1, O=1, P=3, Q=10, R=1, S=1, T=1, U=1, V=4, W=4, X=8, Y=4, Z=10,
)  # fmt: skip

TINY_RAW = {
    "version": 1,
    "name": "Tiny Test",
    "board": {
        "width": 7,
        "height": 7,
        "layout": [
            "T..d..T",
            ".D...D.",
            "..t.t..",
            "d..*..d",
            "..t.t..",
            ".D...D.",
            "T..d..T",
        ],
        "legend": {
            "d": "double_letter",
            "t": "triple_letter",
            "D": "double_word",
            "T": "triple_word",
            "*": {"name": "start", "word": 2},
        },
    },
    "rules": {"start_squares": [[3, 3]], "word_premium_stacking": "multiply"},
    "rack": {"size": 7, "blanks": 2},
    "scoring": {"full_rack_bonus": 50},
    "tiles": {"values": TINY_VALUES, "distribution": {k: 4 for k in TINY_VALUES}},
}


def make_profile(**overrides):
    raw = copy.deepcopy(TINY_RAW)
    for dotted, value in overrides.items():
        *path, key = dotted.split("__")
        node = raw
        for part in path:
            node = node.setdefault(part, {})
        node[key] = value
    return parse_profile(raw)


@pytest.fixture
def tiny():
    return make_profile()

from __future__ import annotations

import hashlib
from collections import Counter

from tilelens.rules.board import Board, rack_to_str
from tilelens.rules.profile import Profile


def position_id(board: Board, rack: Counter, profile: Profile, lexicon_version: str) -> str:
    """Identify everything a search result depends on."""
    parts = [
        "\n".join(board.to_rows()),
        rack_to_str(rack),
        profile.fingerprint(),
        repr(sorted(profile.letter_values.items())),
        lexicon_version,
    ]
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:20]

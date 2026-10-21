from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from tilelens.paths import words_dir
from tilelens.rules.lexicon import Lexicon
from tilelens.rules.profile import Profile
from tilelens.solver.movegen import graph_for, playable_words


@dataclass
class ImportReport:
    path: Path
    words: int
    skipped: int
    version: str


def import_word_list(
    source: str | Path, profile: Profile | None = None, name: str | None = None
) -> ImportReport:
    """Normalize a user word list into the app data folder and pre-build its search graph."""
    source = Path(source).expanduser()
    alphabet = profile.alphabet if profile else None
    lexicon = Lexicon.load(source, alphabet)
    stem = name or source.stem
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "-", stem).strip("-") or "words"
    dest_dir = words_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{stem}.txt"
    dest.write_text("\n".join(sorted(lexicon.words)) + "\n", encoding="utf-8")
    if profile is not None:
        graph_for(playable_words(profile, lexicon), use_cache=True)
    return ImportReport(dest, len(lexicon), lexicon.skipped, lexicon.version)

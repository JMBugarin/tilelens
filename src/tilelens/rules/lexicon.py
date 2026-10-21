from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path


class WordListError(ValueError):
    pass


@dataclass(frozen=True)
class Lexicon:
    words: frozenset[str]
    version: str
    skipped: int = 0
    source: Path | None = field(default=None, compare=False)

    def __contains__(self, word: str) -> bool:
        return word in self.words

    def __len__(self) -> int:
        return len(self.words)

    @classmethod
    def from_words(
        cls,
        words: Iterable[str],
        alphabet: Iterable[str] | None = None,
        min_length: int = 2,
        source: Path | None = None,
    ) -> Lexicon:
        allowed = frozenset(alphabet) if alphabet is not None else None
        kept: set[str] = set()
        skipped = 0
        for raw in words:
            word = raw.strip()
            if not word or word.startswith("#"):
                continue
            word = word.split()[0].upper()
            if len(word) < min_length or not word.isalpha():
                skipped += 1
                continue
            if allowed is not None and not set(word) <= allowed:
                skipped += 1
                continue
            kept.add(word)
        digest = hashlib.sha256("\n".join(sorted(kept)).encode()).hexdigest()[:16]
        return cls(frozenset(kept), digest, skipped, source)

    @classmethod
    def load(cls, path: str | Path, alphabet: Iterable[str] | None = None) -> Lexicon:
        path = Path(path).expanduser()
        try:
            text = path.read_text(encoding="utf-8-sig")
        except FileNotFoundError:
            raise WordListError(f"word list not found: {path}") from None
        except UnicodeDecodeError:
            raise WordListError(f"word list is not UTF-8 text: {path}") from None
        lex = cls.from_words(text.splitlines(), alphabet, source=path)
        if not lex.words:
            raise WordListError(f"word list has no usable words: {path}")
        return lex

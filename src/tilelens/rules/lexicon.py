from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path


class WordListError(ValueError):
    pass


@dataclass(frozen=True)
class Lexicon:
    words: frozenset[str]
    version: str
    skipped: int = 0
    source: Path | None = field(default=None, compare=False)
    baseline: str = ""  # hash of the baseline list alone
    overrides: str = ""  # hash of the allow and block lists alone
    allowed: int = field(default=0, compare=False)
    blocked: int = field(default=0, compare=False)

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


def with_overrides(lex: Lexicon, overrides, alphabet: Iterable[str] | None = None) -> Lexicon:
    """The baseline plus allowed words minus blocked ones. Overrides always win.

    The version names both parts, so a change to either one changes every position id and the
    cached search graph.
    """
    if not overrides.allow and not overrides.block:
        return replace(lex, baseline=lex.version)
    allowed = set(overrides.allow)
    if alphabet is not None:
        allowed = {w for w in allowed if set(w) <= set(alphabet)}
    words = (lex.words | allowed) - set(overrides.block)
    return replace(
        lex,
        words=frozenset(words),
        version=f"{lex.version}+{overrides.version}",
        baseline=lex.version,
        overrides=overrides.version,
        allowed=len(allowed),
        blocked=len(set(overrides.block) & lex.words),
    )


def lexicon_for(
    profile, words_path: str | Path | None = None, overrides_path: str | Path | None = None
) -> Lexicon:
    """The profile's word list with its word overrides applied."""
    from tilelens.rules.overrides import Overrides, default_path

    path = words_path or profile.word_list
    if path is None:
        raise WordListError("no word list: set words.path in the profile or choose one")
    base = Lexicon.load(path, profile.alphabet)
    where = overrides_path or default_path(getattr(profile, "source", None))
    try:
        overrides = Overrides.load(where)
    except ValueError as exc:
        raise WordListError(str(exc)) from None
    return with_overrides(base, overrides, profile.alphabet)

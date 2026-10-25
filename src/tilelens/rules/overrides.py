"""Word overrides kept apart from the baseline word list.

A game's own dictionary is rarely published, so a baseline list is only an assumption about it.
Two small lists correct that assumption word by word and take precedence over the baseline:

- ``allow``: words the game was seen to accept that the baseline lacks;
- ``block``: words the game was seen to reject that the baseline has.

Every entry records its evidence and the date it was confirmed. The file lives beside the
profile (``<profile>.overrides.json``), never inside the baseline list, so replacing or
updating the baseline keeps every override.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

FORMAT = "tilelens-word-overrides"
WORD = re.compile(r"^[A-Z]{2,40}$")


class OverrideError(ValueError):
    pass


@dataclass
class Overrides:
    path: Path | None = None
    game: str = ""
    allow: dict[str, dict] = field(default_factory=dict)
    block: dict[str, dict] = field(default_factory=dict)

    @property
    def version(self) -> str:
        """Identifies the overrides' effect (the words, not the evidence), for cache keys."""
        if not self.allow and not self.block:
            return ""
        blob = json.dumps([sorted(self.allow), sorted(self.block)], separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:12]

    @classmethod
    def load(cls, path: str | Path | None) -> Overrides:
        if path is None:
            return cls()
        path = Path(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls(path)
        except (OSError, ValueError) as exc:
            raise OverrideError(f"cannot read word overrides {path}: {exc}") from None
        if data.get("format") != FORMAT:
            raise OverrideError(f"{path}: not a {FORMAT} file")
        out = cls(path, str(data.get("game", "")))
        for kind in ("allow", "block"):
            for word, entry in (data.get(kind) or {}).items():
                key = word.upper()
                if not WORD.match(key):
                    raise OverrideError(f"{path}: '{word}' is not a word")
                getattr(out, kind)[key] = dict(entry or {})
        both = set(out.allow) & set(out.block)
        if both:
            raise OverrideError(f"{path}: {', '.join(sorted(both))} is both allowed and blocked")
        return out

    def save(self, path: str | Path | None = None) -> None:
        target = Path(path or self.path or "")
        if not str(target):
            raise OverrideError("no file to save the overrides to")
        data = {
            "format": FORMAT,
            "version": 1,
            "game": self.game,
            "allow": dict(sorted(self.allow.items())),
            "block": dict(sorted(self.block.items())),
        }
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        tmp.replace(target)
        self.path = target

    def record(self, kind: str, word: str, evidence: str, when: str | None = None) -> str:
        """Confirm a word as accepted ('allow') or rejected ('block'), with its evidence."""
        key = word.strip().upper()
        if not WORD.match(key):
            raise OverrideError(f"'{word}' is not a word of letters")
        if kind not in ("allow", "block"):
            raise OverrideError("kind must be 'allow' or 'block'")
        if not evidence.strip():
            raise OverrideError(
                "say what you saw (for example 'game rejected UMU played alone, row 8 across, "
                "no other word formed')"
            )
        other = "block" if kind == "allow" else "allow"
        getattr(self, other).pop(key, None)
        getattr(self, kind)[key] = {
            "evidence": evidence.strip(),
            "date": when or date.today().isoformat(),
        }
        return key

    def remove(self, word: str) -> bool:
        key = word.strip().upper()
        found = self.allow.pop(key, None) is not None
        return self.block.pop(key, None) is not None or found

    def status(self, word: str) -> str | None:
        key = word.strip().upper()
        return "allow" if key in self.allow else "block" if key in self.block else None


def default_path(profile_source: Path | None) -> Path | None:
    if profile_source is None:
        return None
    return profile_source.with_name(profile_source.stem + ".overrides.json")

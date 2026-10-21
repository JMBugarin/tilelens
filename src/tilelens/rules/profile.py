from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FORMAT_NAME = "tilelens-profile"
FORMAT_VERSION = 1
STACKING_RULES = ("multiply", "add", "max")
PLAIN_CHARS = frozenset(". ")
BLANK = "?"

BUILTIN_PREMIUMS: dict[str, tuple[int, int]] = {
    "double_letter": (2, 1),
    "triple_letter": (3, 1),
    "double_word": (1, 2),
    "triple_word": (1, 3),
}

_TOP_KEYS = {"format", "version", "name", "board", "rules", "rack", "scoring", "tiles", "words"}
_SECTION_KEYS = {
    "board": {"width", "height", "layout", "legend"},
    "rules": {"start_squares", "min_word_length", "word_premium_stacking"},
    "rack": {"size", "blanks"},
    "scoring": {"full_rack_bonus", "full_rack_tiles"},
    "tiles": {"values", "distribution"},
    "words": {"path"},
}


class ProfileError(ValueError):
    def __init__(self, errors: list[str], source: Path | None = None):
        self.errors = errors
        where = f"{source}: " if source else ""
        super().__init__(where + "; ".join(errors))


@dataclass(frozen=True)
class Premium:
    name: str
    letter: int = 1
    word: int = 1


@dataclass(frozen=True)
class Profile:
    name: str
    width: int
    height: int
    layout: tuple[tuple[Premium | None, ...], ...]
    letter_values: dict[str, int]
    distribution: dict[str, int]
    blanks: int
    rack_size: int
    full_rack_bonus: int
    full_rack_tiles: int
    word_stacking: str
    start_squares: tuple[tuple[int, int], ...]
    min_word_length: int = 2
    word_list: Path | None = None
    source: Path | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def alphabet(self) -> frozenset[str]:
        return frozenset(self.letter_values)

    def premium_at(self, row: int, col: int) -> Premium | None:
        return self.layout[row][col]

    def letter_multiplier(self, row: int, col: int) -> int:
        p = self.layout[row][col]
        return p.letter if p else 1

    def word_multiplier(self, row: int, col: int) -> int:
        p = self.layout[row][col]
        return p.word if p else 1

    def tile_value(self, letter: str, blank: bool = False) -> int:
        return 0 if blank else self.letter_values[letter]

    def combine_word_multipliers(self, multipliers: list[int]) -> int:
        active = [m for m in multipliers if m != 1]
        if not active:
            return 1
        if self.word_stacking == "multiply":
            out = 1
            for m in active:
                out *= m
            return out
        if self.word_stacking == "add":
            return 1 + sum(m - 1 for m in active)
        return max(active)

    def in_bounds(self, row: int, col: int) -> bool:
        return 0 <= row < self.height and 0 <= col < self.width

    def fingerprint(self) -> str:
        canon = {k: v for k, v in self.raw.items() if k != "words"}
        blob = json.dumps(canon, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(blob).hexdigest()[:16]


def load_profile(path: str | Path) -> Profile:
    path = Path(path).expanduser()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ProfileError(["file not found"], path) from None
    except tomllib.TOMLDecodeError as exc:
        raise ProfileError([f"invalid TOML: {exc}"], path) from None
    return parse_profile(data, source=path)


def parse_profile(data: dict[str, Any], source: Path | None = None) -> Profile:
    errors: list[str] = []
    v = _Validator(errors)

    for key in sorted(set(data) - _TOP_KEYS):
        errors.append(f"unknown key '{key}'")
    for section, allowed in _SECTION_KEYS.items():
        sec = data.get(section, {})
        if not isinstance(sec, dict):
            errors.append(f"'{section}' must be a table")
            continue
        for key in sorted(set(sec) - allowed):
            errors.append(f"unknown key '{section}.{key}'")

    if data.get("format", FORMAT_NAME) != FORMAT_NAME:
        errors.append(f"format must be '{FORMAT_NAME}'")
    version = data.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        errors.append("version: required integer")
    elif version > FORMAT_VERSION or version < 1:
        errors.append(f"version: unsupported profile version {version} (supported: 1)")
    if errors and any(e.startswith("version") for e in errors):
        raise ProfileError(errors, source)

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append("name: required non-empty string")
        name = "?"

    board = _table(data, "board")
    width = v.int_range(board, "board.width", "width", 2, 40)
    height = v.int_range(board, "board.height", "height", 2, 40)

    legend = _parse_legend(board.get("legend", {}), errors)
    layout = _parse_layout(board.get("layout"), width, height, legend, errors)

    rules = _table(data, "rules")
    stacking = rules.get("word_premium_stacking", "multiply")
    if stacking not in STACKING_RULES:
        errors.append(f"rules.word_premium_stacking: must be one of {', '.join(STACKING_RULES)}")
    min_len = v.int_range(rules, "rules.min_word_length", "min_word_length", 2, 40, default=2)
    starts = _parse_starts(rules.get("start_squares"), width, height, errors)

    rack = _table(data, "rack")
    rack_size = v.int_range(rack, "rack.size", "size", 1, 20)
    blanks = v.int_range(rack, "rack.blanks", "blanks", 0, 20, default=0)

    tiles = _table(data, "tiles")
    values = _parse_letter_map(tiles.get("values"), "tiles.values", errors, required=True)
    dist = _parse_letter_map(tiles.get("distribution"), "tiles.distribution", errors, required=True)
    for letter in sorted(set(dist) - set(values)):
        errors.append(f"tiles.distribution: letter '{letter}' has no value in tiles.values")
    if rack_size and values and dist is not None and sum(dist.values()) + (blanks or 0) < rack_size:
        errors.append("tiles.distribution: fewer tiles in the bag than one rack holds")

    scoring = _table(data, "scoring")
    bonus = v.int_range(scoring, "scoring.full_rack_bonus", "full_rack_bonus", 0, 1000, default=0)
    full_tiles = v.int_range(
        scoring, "scoring.full_rack_tiles", "full_rack_tiles", 1, 20, default=rack_size or 1
    )
    if rack_size and full_tiles and full_tiles > rack_size:
        errors.append("scoring.full_rack_tiles: cannot exceed rack.size")

    words = _table(data, "words")
    word_list = None
    if "path" in words:
        if not isinstance(words["path"], str) or not words["path"].strip():
            errors.append("words.path: must be a non-empty string")
        else:
            word_list = Path(words["path"]).expanduser()
            if not word_list.is_absolute() and source is not None:
                word_list = (source.parent / word_list).resolve()

    if errors:
        raise ProfileError(errors, source)

    return Profile(
        name=name.strip(),
        width=width,
        height=height,
        layout=layout,
        letter_values=values,
        distribution=dist,
        blanks=blanks,
        rack_size=rack_size,
        full_rack_bonus=bonus,
        full_rack_tiles=full_tiles,
        word_stacking=stacking,
        start_squares=starts,
        min_word_length=min_len,
        word_list=word_list,
        source=source,
        raw=data,
    )


class _Validator:
    def __init__(self, errors: list[str]):
        self.errors = errors

    def int_range(self, table, label, key, lo, hi, default=None):
        if key not in table:
            if default is None:
                self.errors.append(f"{label}: required")
                return 0
            return default
        val = table[key]
        if not isinstance(val, int) or isinstance(val, bool):
            self.errors.append(f"{label}: must be an integer")
            return 0
        if not lo <= val <= hi:
            self.errors.append(f"{label}: must be between {lo} and {hi}")
            return 0
        return val


def _table(data: dict, key: str) -> dict:
    sec = data.get(key, {})
    return sec if isinstance(sec, dict) else {}


def _parse_legend(raw: Any, errors: list[str]) -> dict[str, Premium]:
    legend: dict[str, Premium] = {}
    if not isinstance(raw, dict):
        errors.append("board.legend: must be a table")
        return legend
    for char, spec in raw.items():
        label = f"board.legend.{char!r}"
        if len(char) != 1 or char in PLAIN_CHARS:
            errors.append(f"{label}: legend keys must be one character other than '.' or space")
            continue
        if isinstance(spec, str):
            if spec not in BUILTIN_PREMIUMS:
                errors.append(f"{label}: unknown premium '{spec}'")
                continue
            lm, wm = BUILTIN_PREMIUMS[spec]
            legend[char] = Premium(spec, lm, wm)
        elif isinstance(spec, dict):
            unknown = set(spec) - {"name", "letter", "word"}
            if unknown:
                errors.append(f"{label}: unknown keys {sorted(unknown)}")
                continue
            lm, wm = spec.get("letter", 1), spec.get("word", 1)
            if not all(isinstance(m, int) and not isinstance(m, bool) and m >= 1 for m in (lm, wm)):
                errors.append(f"{label}: letter and word multipliers must be integers >= 1")
                continue
            pname = spec.get("name", f"custom_{char}")
            if not isinstance(pname, str):
                errors.append(f"{label}: name must be a string")
                continue
            legend[char] = Premium(pname, lm, wm)
        else:
            errors.append(f"{label}: must be a premium name or a table")
    return legend


def _parse_layout(raw, width, height, legend, errors):
    if not isinstance(raw, list) or not all(isinstance(r, str) for r in raw):
        errors.append("board.layout: required list of strings")
        return ()
    if height and len(raw) != height:
        errors.append(f"board.layout: expected {height} rows, got {len(raw)}")
    rows = []
    for i, line in enumerate(raw):
        if width and len(line) != width:
            errors.append(f"board.layout[{i}]: expected {width} columns, got {len(line)}")
        row = []
        for j, ch in enumerate(line):
            if ch in PLAIN_CHARS:
                row.append(None)
            elif ch in legend:
                row.append(legend[ch])
            else:
                errors.append(f"board.layout[{i}][{j}]: '{ch}' is not in board.legend")
                row.append(None)
        rows.append(tuple(row))
    return tuple(rows)


def _parse_starts(raw, width, height, errors):
    if raw is None:
        errors.append("rules.start_squares: required (use [] to allow any first placement)")
        return ()
    if not isinstance(raw, list):
        errors.append("rules.start_squares: must be a list of [row, col] pairs")
        return ()
    out = []
    for i, sq in enumerate(raw):
        ok = (
            isinstance(sq, list)
            and len(sq) == 2
            and all(isinstance(x, int) and not isinstance(x, bool) for x in sq)
        )
        if not ok:
            errors.append(f"rules.start_squares[{i}]: must be [row, col]")
            continue
        r, c = sq
        if width and height and not (0 <= r < height and 0 <= c < width):
            errors.append(f"rules.start_squares[{i}]: ({r}, {c}) is outside the board")
            continue
        out.append((r, c))
    return tuple(out)


def _parse_letter_map(raw, label, errors, required):
    if raw is None:
        if required:
            errors.append(f"{label}: required")
        return {}
    if not isinstance(raw, dict) or not raw:
        errors.append(f"{label}: must be a non-empty table of letter = integer")
        return {}
    out = {}
    for letter, val in raw.items():
        if len(letter) != 1 or not letter.isalpha() or letter != letter.upper():
            errors.append(f"{label}: '{letter}' must be a single uppercase letter")
            continue
        if not isinstance(val, int) or isinstance(val, bool) or val < 0:
            errors.append(f"{label}.{letter}: must be an integer >= 0")
            continue
        out[letter] = val
    return out


def demo_profile_dir() -> Path:
    import sys

    here = Path(__file__).resolve()
    candidates = (
        here.parents[3] / "profiles",
        here.parents[2] / "profiles",
        here.parents[1] / "profiles",
        Path(sys.argv[0]).resolve().parent / "profiles",
    )
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError("demo profiles directory not found")


def demo_profile(name: str = "demo_garden") -> Profile:
    return load_profile(demo_profile_dir() / f"{name}.toml")

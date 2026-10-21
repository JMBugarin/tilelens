from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

import numpy as np


class _BuildNode:
    __slots__ = ("edges", "final", "uid")

    def __init__(self):
        self.edges: dict[str, _BuildNode] = {}
        self.final = False
        self.uid = -1


class Dawg:
    """Minimized directed acyclic word graph. Node 0 is the root."""

    __slots__ = ("edges", "terminal", "version")

    def __init__(self, edges: list[dict[str, int]], terminal: list[bool], version: str = ""):
        self.edges = edges
        self.terminal = terminal
        self.version = version

    def __len__(self) -> int:
        return len(self.edges)

    def __contains__(self, word: str) -> bool:
        node = 0
        for ch in word:
            node = self.edges[node].get(ch)
            if node is None:
                return False
        return self.terminal[node]

    def words(self) -> list[str]:
        out = []
        stack = [(0, "")]
        while stack:
            node, prefix = stack.pop()
            if self.terminal[node]:
                out.append(prefix)
            for ch, child in self.edges[node].items():
                stack.append((child, prefix + ch))
        return sorted(out)

    @classmethod
    def build(cls, words: Iterable[str], version: str = "") -> Dawg:
        root = _BuildNode()
        registry: dict[tuple, _BuildNode] = {}
        unchecked: list[tuple[_BuildNode, str, _BuildNode]] = []
        counter = [0]

        def minimize(down_to: int) -> None:
            while len(unchecked) > down_to:
                parent, letter, child = unchecked.pop()
                key = (child.final, tuple(sorted((ch, n.uid) for ch, n in child.edges.items())))
                existing = registry.get(key)
                if existing is not None:
                    parent.edges[letter] = existing
                else:
                    child.uid = counter[0]
                    counter[0] += 1
                    registry[key] = child

        prev = ""
        for word in sorted(set(words)):
            common = 0
            for a, b in zip(word, prev, strict=False):
                if a != b:
                    break
                common += 1
            minimize(common)
            node = unchecked[-1][2] if unchecked else root
            for letter in word[common:]:
                child = _BuildNode()
                node.edges[letter] = child
                unchecked.append((node, letter, child))
                node = child
            node.final = True
            prev = word
        minimize(0)

        index: dict[int, int] = {id(root): 0}
        order = [root]
        i = 0
        while i < len(order):
            for _, child in sorted(order[i].edges.items()):
                if id(child) not in index:
                    index[id(child)] = len(order)
                    order.append(child)
            i += 1
        edges = [{ch: index[id(c)] for ch, c in sorted(n.edges.items())} for n in order]
        terminal = [n.final for n in order]
        return cls(edges, terminal, version)

    def save(self, path: str | Path) -> None:
        path = Path(path)
        offsets = np.zeros(len(self.edges) + 1, dtype=np.int64)
        letters: list[int] = []
        targets: list[int] = []
        for i, e in enumerate(self.edges):
            for ch, t in e.items():
                letters.append(ord(ch))
                targets.append(t)
            offsets[i + 1] = len(letters)
        tmp = path.with_name(path.name + f".{os.getpid()}.tmp.npz")
        np.savez_compressed(
            tmp,
            offsets=offsets,
            letters=np.array(letters, dtype=np.int32),
            targets=np.array(targets, dtype=np.int32),
            terminal=np.array(self.terminal, dtype=np.bool_),
            version=np.array(self.version),
        )
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: str | Path) -> Dawg:
        with np.load(Path(path), allow_pickle=False) as data:
            offsets = data["offsets"].tolist()
            letters = [chr(x) for x in data["letters"].tolist()]
            targets = data["targets"].tolist()
            terminal = data["terminal"].tolist()
            version = str(data["version"])
        edges = [
            dict(
                zip(
                    letters[offsets[i] : offsets[i + 1]],
                    targets[offsets[i] : offsets[i + 1]],
                    strict=True,
                )
            )
            for i in range(len(terminal))
        ]
        return cls(edges, terminal, version)


def cache_dir() -> Path:
    base = os.environ.get("TILELENS_CACHE")
    if base:
        return Path(base)
    if os.name == "posix" and Path.home().joinpath("Library").is_dir():
        return Path.home() / "Library" / "Caches" / "tilelens"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "tilelens"


def load_or_build(words: Iterable[str], version: str, use_cache: bool = True) -> Dawg:
    if not use_cache:
        return Dawg.build(words, version)
    path = cache_dir() / f"dawg-{version}.npz"
    if path.is_file():
        try:
            dawg = Dawg.load(path)
            if dawg.version == version:
                return dawg
        except (OSError, ValueError, KeyError):
            pass
    dawg = Dawg.build(words, version)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        dawg.save(path)
    except OSError:
        pass
    return dawg

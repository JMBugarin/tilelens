from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

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


@dataclass
class BaselineReport(ImportReport):
    manifest: dict


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


ENABLE_URL = "https://raw.githubusercontent.com/dolph/dictionary/master/enable1.txt"
ENABLE_SHA256 = "3f16130220645692ed49c7134e24a18504c2ca55b3c012f7290e3e77c63b1a89"
ENABLE_NOTE = (
    "ENABLE (Enhanced North American Benchmark Lexicon), 172,823 words. Distributed as a free "
    "word list intended for Scrabble-style games and widely described as public domain; this "
    "project could not verify the licence at its primary source. It is an older list than the "
    "U.S. Merriam-Webster Official Scrabble Players Dictionary and is NOT that dictionary."
)


def manifest_path(words_file: Path) -> Path:
    return words_file.with_suffix(".manifest.json")


def read_manifest(words_file: Path) -> dict:
    import json

    try:
        manifest = json.loads(manifest_path(words_file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return manifest if isinstance(manifest, dict) else {}


def install_baseline(file: str | Path | None = None, name: str = "enable1") -> BaselineReport:
    """Install the baseline word list and a manifest saying where it came from.

    Without a file this downloads ENABLE from a pinned address and refuses it unless its hash
    matches. With a file (for example a licensed Scrabble list you own) that file is used as it
    is, and the manifest records its hash and that its provenance is yours to state.
    """
    import hashlib
    import json
    import urllib.request
    from datetime import date

    dest_dir = words_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    if file is None:
        with urllib.request.urlopen(ENABLE_URL, timeout=60) as resp:
            raw = resp.read()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != ENABLE_SHA256:
            raise ValueError(f"downloaded list has hash {digest}, expected {ENABLE_SHA256}")
        source, note, edition = ENABLE_URL, ENABLE_NOTE, "ENABLE1"
    else:
        raw = Path(file).expanduser().read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        source, note, edition = str(file), "Provided by the user; licence not checked.", name
    dest = dest_dir / f"{name}.txt"
    lexicon = Lexicon.from_bytes(raw, Path(file).expanduser() if file is not None else dest)
    manifest = {
        "name": name,
        "edition": edition,
        "source": source,
        "sha256": digest,
        "installed": date.today().isoformat(),
        "words": len(lexicon),
        "note": note,
    }
    # Stage both files on the destination volume before publishing either one.
    with TemporaryDirectory(dir=dest.parent) as staging:
        stage = Path(staging)
        candidate = stage / "words"
        candidate.write_bytes(raw)
        metadata = stage / "manifest"
        metadata.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        _replace_baseline(dest, candidate, metadata)
    return BaselineReport(dest, len(lexicon), lexicon.skipped, lexicon.version, manifest)


def _replace_baseline(dest: Path, candidate: Path, metadata: Path) -> None:
    # Serialize installers so the provenance and list belong to the same invocation.
    from PySide6.QtCore import QLockFile

    lock = QLockFile(str(dest) + ".lock")
    if not lock.tryLock(0):
        raise OSError(f"could not lock baseline for installation: {dest}")
    try:
        manifest = manifest_path(dest)
        # Hide old provenance across the two renames. Even if the process stops here,
        # status can at worst see no manifest for a complete word list.
        previous_manifest = metadata.with_name("previous-manifest")
        previous_words = candidate.with_name("previous-words")
        if dest.is_file() or dest.is_symlink():
            shutil.copy2(dest, previous_words, follow_symlinks=False)
        if manifest.is_file() or manifest.is_symlink():
            manifest.replace(previous_manifest)
        try:
            candidate.replace(dest)
            try:
                metadata.replace(manifest)
            except OSError:
                if previous_words.exists() or previous_words.is_symlink():
                    previous_words.replace(dest)
                else:
                    dest.unlink()
                raise
        except OSError:
            if previous_manifest.exists() or previous_manifest.is_symlink():
                previous_manifest.replace(manifest)
            raise
    finally:
        lock.unlock()

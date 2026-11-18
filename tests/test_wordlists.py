import hashlib

import pytest

from tilelens.app import main
from tilelens.rules.lexicon import Lexicon
from tilelens.solver.wordlists import manifest_path, read_manifest


@pytest.mark.parametrize(
    ("raw", "message"),
    [(b"\xff\xfe", "not UTF-8"), (b"# comment\n1\n!\n", "no usable words")],
)
def test_invalid_baseline_preserves_existing_install(tmp_path, monkeypatch, capsys, raw, message):
    monkeypatch.setenv("TILELENS_HOME", str(tmp_path))
    source = tmp_path / "source.txt"
    source.write_bytes(b"cat\ndog\n")
    install = ["words", "install-baseline", "--file", str(source)]
    assert main(install) == 0
    words = tmp_path / "words" / "enable1.txt"
    manifest = manifest_path(words).read_bytes()

    source.write_bytes(raw)
    assert main(install) == 1
    error = capsys.readouterr().err
    assert message in error and str(source) in error
    assert words.read_bytes() == b"cat\ndog\n"
    assert manifest_path(words).read_bytes() == manifest
    assert set(words.parent.iterdir()) == {words, manifest_path(words)}

    raw = "bird\r\nfish\r\n".encode("utf-8-sig")
    source.write_bytes(raw)
    assert main(install) == 0
    assert words.read_bytes() == raw
    assert Lexicon.load(words).words == {"BIRD", "FISH"}
    assert read_manifest(words)["sha256"] == hashlib.sha256(raw).hexdigest()


def test_baseline_rolls_back_manifest_if_word_file_cannot_be_replaced(tmp_path, monkeypatch):
    monkeypatch.setenv("TILELENS_HOME", str(tmp_path))
    source = tmp_path / "source.txt"
    source.write_bytes(b"cat\n")
    words = tmp_path / "words" / "enable1.txt"
    words.mkdir(parents=True)
    manifest_path(words).write_bytes(b"previous contents")
    assert main(["words", "install-baseline", "--file", str(source)]) == 1
    assert words.is_dir()
    assert manifest_path(words).read_bytes() == b"previous contents"


def test_baseline_rolls_back_words_if_manifest_cannot_be_replaced(tmp_path, monkeypatch):
    monkeypatch.setenv("TILELENS_HOME", str(tmp_path))
    source = tmp_path / "source.txt"
    source.write_bytes(b"new\n")
    words = tmp_path / "words" / "enable1.txt"
    words.parent.mkdir()
    words.write_bytes(b"original\n")
    manifest_path(words).mkdir()
    assert main(["words", "install-baseline", "--file", str(source)]) == 1
    assert words.read_bytes() == b"original\n"
    assert manifest_path(words).is_dir()


def test_concurrent_baseline_install_preserves_current_files(tmp_path, monkeypatch):
    from PySide6.QtCore import QLockFile

    monkeypatch.setenv("TILELENS_HOME", str(tmp_path))
    source = tmp_path / "source.txt"
    source.write_bytes(b"first\n")
    install = ["words", "install-baseline", "--file", str(source)]
    assert main(install) == 0
    words = tmp_path / "words" / "enable1.txt"
    manifest = manifest_path(words).read_bytes()
    lock = QLockFile(str(words) + ".lock")
    assert lock.tryLock(0)
    try:
        source.write_bytes(b"next\n")
        assert main(install) == 1
        assert words.read_bytes() == b"first\n"
        assert manifest_path(words).read_bytes() == manifest
    finally:
        lock.unlock()
    assert main(install) == 0
    assert words.read_bytes() == b"next\n"

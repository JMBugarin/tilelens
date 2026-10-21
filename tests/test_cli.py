import random

import pytest

from tilelens.app import main
from tilelens.rules.board import Board
from tilelens.rules.legality import check_placement
from tilelens.rules.lexicon import Lexicon, WordListError, lexicon_for
from tilelens.rules.profile import demo_profile, demo_profile_dir
from tilelens.solver.movegen import MoveGenerator
from tilelens.solver.selfplay import full_bag, play_game


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("TILELENS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TILELENS_CACHE", str(tmp_path / "cache"))


def test_solve_cli(tmp_path, capsys):
    board = tmp_path / "board.txt"
    board.write_text("\n".join(["........."] * 9))
    profile = demo_profile_dir() / "demo_pocket.toml"
    assert main(["solve", "--profile", str(profile), "--board", str(board), "--rack", "TONES"]) == 0
    out = capsys.readouterr().out
    assert "1. " in out and "highest scoring" in out and "searched in" in out


def test_solve_cli_errors(tmp_path, capsys):
    board = tmp_path / "board.txt"
    board.write_text("...\n...\n")
    profile = demo_profile_dir() / "demo_pocket.toml"
    assert main(["solve", "--profile", str(profile), "--board", str(board), "--rack", "A"]) == 1
    assert "does not match" in capsys.readouterr().err
    args = ["solve", "--profile", str(profile), "--board", str(board), "--rack", "A"]
    assert main([*args, "--words", str(tmp_path / "missing.txt")]) == 1


def test_import_words(tmp_path, capsys):
    src = tmp_path / "My List!.txt"
    src.write_text("cat\nCATS\n# note\nñu\nx\n")
    profile = demo_profile_dir() / "demo_pocket.toml"
    assert main(["import-words", str(src), "--profile", str(profile)]) == 0
    out = capsys.readouterr().out
    assert "imported 2 words (2 skipped)" in out
    dest = tmp_path / "home" / "words" / "My-List.txt"
    assert dest.read_text() == "CAT\nCATS\n"
    assert list((tmp_path / "cache").glob("dawg-*.npz"))
    assert main(["import-words", str(tmp_path / "nope.txt")]) == 1


def test_lexicon_for_requires_a_list():
    p = demo_profile()
    assert len(lexicon_for(p)) > 1000
    from dataclasses import replace

    with pytest.raises(WordListError, match="no word list"):
        lexicon_for(replace(p, word_list=None))


def test_self_play_produces_legal_turns():
    p = demo_profile("demo_pocket")
    lexicon = Lexicon.load(p.word_list, p.alphabet)
    gen = MoveGenerator(p, lexicon)
    turns = play_game(p, gen, random.Random(1), max_turns=8)
    assert turns
    board = Board(p.width, p.height)
    for t in turns:
        assert t.board == board
        check_placement(board, t.rack, t.played, p, lexicon)
        board = board.with_tiles(t.played)
    assert len(full_bag(p)) == sum(p.distribution.values()) + p.blanks

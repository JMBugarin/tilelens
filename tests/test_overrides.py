"""Word overrides: a baseline list plus confirmed allow and block lists that always win."""

import json
from collections import Counter

import pytest
from conftest import make_profile

from tilelens.app import main
from tilelens.rules.board import Board
from tilelens.rules.legality import formed_words
from tilelens.rules.lexicon import Lexicon, WordListError, lexicon_for, with_overrides
from tilelens.rules.overrides import OverrideError, Overrides
from tilelens.solver.movegen import MoveGenerator, playable_words
from tilelens.state.position import position_id

WORDS = ["UMU", "MU", "UM", "UN", "NU", "MUN", "NUN", "MUM", "UMM"]


def lex(words=WORDS):
    return Lexicon.from_words(words)


def overrides(**kinds) -> Overrides:
    o = Overrides()
    for kind, words in kinds.items():
        for w in words:
            o.record(kind, w, "seen in the game", "2026-10-25")
    return o


def all_words_formed(board, gen, rack):
    seen = set()
    for key in gen.all_moves(board, rack):
        for w in formed_words(board, key):
            seen.add(w.text)
    return seen


def test_baseline_alone_forms_umu():
    board = Board.from_rows(
        [".......", ".......", "...M...", ".......", ".......", ".......", "......."]
    )
    gen = MoveGenerator(make_profile(), lex(), use_cache=False)
    assert "UMU" in all_words_formed(board, gen, Counter("UU"))


def test_umu_never_appears_as_a_main_word_or_a_crossing_word_once_blocked():
    profile = make_profile()
    blocked = with_overrides(lex(), overrides(block=["UMU"]))
    gen = MoveGenerator(profile, blocked, use_cache=False)
    boards = [
        ["......." for _ in range(7)],
        # M with a U above it: a U below would make UMU down, and across for the same square
        [".......", "...U...", "...M...", ".......", ".......", ".......", "......."],
        [".......", ".......", "..UM...", ".......", ".......", ".......", "......."],
        [".......", "...U...", "...M...", "..N....", ".......", ".......", "......."],
    ]
    played = set()
    for rows in boards:
        for rack in ("UU", "UUM", "MUN", "NUMU"):
            formed = all_words_formed(Board.from_rows(rows), gen, Counter(rack))
            assert "UMU" not in formed, (rows, rack)
            played |= formed
    assert {"MU", "UM", "NU"} <= played, "other words are still played"
    for move in gen.solve(Board.from_rows(boards[1]), Counter("UU")).moves:
        assert all(w.text != "UMU" for w in move.breakdown.words)


def test_allow_adds_a_word_the_baseline_lacks_and_block_removes_one_it_has():
    base = lex(["UN", "NU"])
    both = with_overrides(base, overrides(allow=["MU"], block=["UN"]))
    assert "MU" in both and "UN" not in both and "NU" in both
    assert "MU" not in base  # the baseline is untouched


def test_overrides_take_precedence_over_the_baseline():
    o = Overrides()
    o.record("block", "UN", "game rejected UN played alone", "2026-10-01")
    o.record("allow", "UN", "game accepted UN on a later turn", "2026-10-02")
    assert o.status("UN") == "allow" and "UN" not in o.block
    assert "UN" in with_overrides(lex(), o)


def test_effective_list_gives_the_same_answer_to_every_check():
    # the solver, the legality check and the letter fixing all read the same effective words
    blocked = with_overrides(lex(), overrides(block=["UMU"]))
    assert "UMU" not in blocked.words
    assert "UMU" not in playable_words(make_profile(), blocked)


def test_versions_name_the_baseline_and_the_overrides_and_change_with_either():
    base = lex()
    plain = with_overrides(base, Overrides())
    blocked = with_overrides(base, overrides(block=["UMU"]))
    other_base = with_overrides(lex([*WORDS, "MUUMUU"]), overrides(block=["UMU"]))
    allowed = with_overrides(base, overrides(block=["UMU"], allow=["NUMM"]))
    assert plain.version == base.version
    versions = {plain.version, blocked.version, other_base.version, allowed.version}
    assert len(versions) == 4
    assert blocked.baseline == base.version and blocked.overrides
    assert blocked.version.startswith(base.version + "+")
    profile, board, rack = (
        make_profile(),
        Board.from_rows(["......." for _ in range(7)]),
        Counter("U"),
    )
    ids = {position_id(board, rack, profile, v) for v in versions}
    assert len(ids) == 4  # cached results from one list are never reused under another


def test_the_search_graph_is_keyed_by_the_effective_words():
    from tilelens.solver.movegen import graph_for

    profile = make_profile()
    plain = graph_for(playable_words(profile, with_overrides(lex(), Overrides())))
    blocked = graph_for(playable_words(profile, with_overrides(lex(), overrides(block=["UMU"]))))
    assert "UMU" in plain and "UMU" not in blocked
    assert plain.version != blocked.version  # a cached graph is never reused after a change


def test_recording_needs_evidence_and_a_real_word(tmp_path):
    o = Overrides()
    with pytest.raises(OverrideError):
        o.record("block", "UMU", "   ")
    with pytest.raises(OverrideError):
        o.record("block", "U1U", "seen")
    with pytest.raises(OverrideError):
        o.record("maybe", "UMU", "seen")
    o.record("block", "umu", "game said no", "2026-10-25")
    path = tmp_path / "x.overrides.json"
    o.save(path)
    back = Overrides.load(path)
    assert back.block["UMU"] == {"evidence": "game said no", "date": "2026-10-25"}
    assert back.version == o.version


def test_overrides_live_beside_the_profile_and_survive_a_new_baseline(tmp_path):
    import copy

    from conftest import TINY_RAW

    from tilelens.rules.profile import parse_profile

    raw = copy.deepcopy(TINY_RAW)
    source = tmp_path / "game.toml"
    source.write_text("")
    profile = parse_profile(raw, source=source)
    o = Overrides()
    o.record("block", "UMU", "rejected", "2026-10-25")
    o.save(tmp_path / "game.overrides.json")
    first = tmp_path / "words1.txt"
    first.write_text("\n".join(WORDS))
    second = tmp_path / "words2.txt"
    second.write_text("\n".join([*WORDS, "MUUMUU", "NUMMUM"]))
    one, two = lexicon_for(profile, first), lexicon_for(profile, second)
    assert "UMU" not in one and "UMU" not in two  # the block outlives the baseline change
    assert one.overrides == two.overrides and one.baseline != two.baseline
    assert "UMU" in lexicon_for(profile, first, tmp_path / "none.json")


def test_a_damaged_overrides_file_is_reported_not_ignored(tmp_path):
    import copy

    from conftest import TINY_RAW

    from tilelens.rules.profile import parse_profile

    source = tmp_path / "game.toml"
    source.write_text("")
    profile = parse_profile(copy.deepcopy(TINY_RAW), source=source)
    (tmp_path / "game.overrides.json").write_text("{not json")
    words = tmp_path / "w.txt"
    words.write_text("\n".join(WORDS))
    with pytest.raises(WordListError):
        lexicon_for(profile, words)
    (tmp_path / "game.overrides.json").write_text(
        json.dumps({"format": "tilelens-word-overrides", "allow": {"UN": {}}, "block": {"UN": {}}})
    )
    with pytest.raises(WordListError):
        lexicon_for(profile, words)


def test_command_line_records_checks_and_forgets(tmp_path, monkeypatch, capsys):
    import shutil

    from tilelens.rules.profile import demo_profile_dir

    home = tmp_path / "home"
    (home / "profiles").mkdir(parents=True)
    monkeypatch.setenv("TILELENS_HOME", str(home))
    profile = home / "profiles" / "demo.toml"
    shutil.copy(demo_profile_dir() / "demo_pocket.toml", profile)
    shutil.copy(demo_profile_dir() / "demo_words.txt", home / "profiles" / "demo_words.txt")
    base = ["words", "--profile", str(profile)]
    assert main([*base, "block", "tone", "--evidence", "game said not a word"]) == 0
    assert main([*base, "block", "tone", "--evidence", " "]) == 1
    assert main([*base, "check", "tone"]) == 0
    assert "TONE: rejected (override)" in capsys.readouterr().out
    saved = json.loads((home / "profiles" / "demo.overrides.json").read_text())
    assert saved["block"]["TONE"]["evidence"] == "game said not a word"
    assert saved["block"]["TONE"]["date"]
    assert main([*base, "allow", "tone", "--evidence", "accepted on turn 9"]) == 0
    assert main([*base, "check", "tone"]) == 0
    assert "accepted (override)" in capsys.readouterr().out
    assert main([*base, "forget", "tone"]) == 0
    assert main([*base, "status"]) == 0


def test_the_shipped_letter_league_starter_blocks_umu():
    from pathlib import Path

    path = Path(__file__).parents[1] / "profiles" / "letter-league.overrides.json"
    o = Overrides.load(path)
    assert o.game == "Letter League" and "UMU" in o.block
    assert o.block["UMU"]["date"] and o.block["UMU"]["evidence"]

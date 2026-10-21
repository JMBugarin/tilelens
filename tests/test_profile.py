import copy
import tomllib

import pytest

from tilelens.rules.profile import (
    ProfileError,
    demo_profile,
    demo_profile_dir,
    load_profile,
    parse_profile,
)


@pytest.fixture
def raw():
    return tomllib.loads((demo_profile_dir() / "demo_garden.toml").read_text())


def errors_for(data):
    with pytest.raises(ProfileError) as exc:
        parse_profile(data)
    return exc.value.errors


def test_demo_profiles_load():
    garden = demo_profile("demo_garden")
    assert (garden.width, garden.height, garden.rack_size, garden.blanks) == (13, 13, 7, 2)
    assert garden.start_squares == ((6, 6),)
    assert garden.word_multiplier(6, 6) == 2
    assert garden.letter_multiplier(0, 4) == 2
    assert garden.word_multiplier(0, 0) == 3
    assert garden.premium_at(0, 1) is None
    assert garden.word_list is not None and garden.word_list.name == "demo_words.txt"
    assert garden.word_list.is_file()

    pocket = demo_profile("demo_pocket")
    assert pocket.full_rack_tiles == pocket.rack_size == 5
    assert pocket.premium_at(4, 2).name == "quad_letter"
    assert pocket.premium_at(2, 2).letter == 2 and pocket.premium_at(2, 2).word == 2
    assert pocket.start_squares == ((2, 2), (6, 6))


def test_layout_is_symmetric_in_demo():
    garden = demo_profile()
    for r in range(garden.height):
        for c in range(garden.width):
            assert garden.premium_at(r, c) == garden.premium_at(garden.height - 1 - r, c)


@pytest.mark.parametrize(
    ("rule", "mults", "expected"),
    [
        ("multiply", [2, 3], 6),
        ("multiply", [2, 2, 1], 4),
        ("add", [2, 2], 3),
        ("add", [3, 2], 4),
        ("max", [2, 3], 3),
        ("multiply", [], 1),
        ("add", [1, 1], 1),
    ],
)
def test_word_stacking(raw, rule, mults, expected):
    raw["rules"]["word_premium_stacking"] = rule
    assert parse_profile(raw).combine_word_multipliers(mults) == expected


def test_fingerprint_changes_with_rules(raw):
    a = parse_profile(raw)
    b_raw = copy.deepcopy(raw)
    b_raw["tiles"]["values"]["A"] = 2
    assert a.fingerprint() == parse_profile(copy.deepcopy(raw)).fingerprint()
    assert a.fingerprint() != parse_profile(b_raw).fingerprint()


def test_relative_word_list_resolves_against_profile(tmp_path, raw):
    path = tmp_path / "p.toml"
    path.write_text((demo_profile_dir() / "demo_garden.toml").read_text())
    assert load_profile(path).word_list == (tmp_path / "demo_words.txt").resolve()


def test_missing_file(tmp_path):
    with pytest.raises(ProfileError, match="not found"):
        load_profile(tmp_path / "nope.toml")


def test_bad_toml(tmp_path):
    p = tmp_path / "bad.toml"
    p.write_text("name = ")
    with pytest.raises(ProfileError, match="invalid TOML"):
        load_profile(p)


def test_unsupported_version(raw):
    raw["version"] = 2
    assert any("unsupported profile version 2" in e for e in errors_for(raw))


def test_missing_version(raw):
    del raw["version"]
    assert errors_for(raw) == ["version: required integer"]


def test_wrong_format_name(raw):
    raw["format"] = "something-else"
    assert any("format must be" in e for e in errors_for(raw))


def test_unknown_keys(raw):
    raw["colour"] = 1
    raw["board"]["depth"] = 3
    errs = errors_for(raw)
    assert "unknown key 'colour'" in errs
    assert "unknown key 'board.depth'" in errs


def test_layout_shape_errors(raw):
    raw["board"]["layout"] = raw["board"]["layout"][:-1]
    raw["board"]["layout"][0] = raw["board"]["layout"][0][:-1]
    errs = errors_for(raw)
    assert "board.layout: expected 13 rows, got 12" in errs
    assert "board.layout[0]: expected 13 columns, got 12" in errs


def test_unknown_layout_char(raw):
    raw["board"]["layout"][1] = "Z" + raw["board"]["layout"][1][1:]
    assert "board.layout[1][0]: 'Z' is not in board.legend" in errors_for(raw)


def test_bad_legend_entries(raw):
    raw["board"]["legend"]["x"] = "quintuple_word"
    raw["board"]["legend"]["y"] = {"letter": 0}
    raw["board"]["legend"]["."] = "double_word"
    errs = errors_for(raw)
    assert any("unknown premium 'quintuple_word'" in e for e in errs)
    assert any("multipliers must be integers >= 1" in e for e in errs)
    assert any("legend keys must be one character" in e for e in errs)


def test_custom_premium(raw):
    raw["board"]["legend"]["Q"] = {"name": "quad_word", "word": 4}
    raw["board"]["layout"][0] = "Q" + raw["board"]["layout"][0][1:]
    p = parse_profile(raw)
    assert p.premium_at(0, 0).name == "quad_word"
    assert p.word_multiplier(0, 0) == 4


def test_start_square_out_of_bounds(raw):
    raw["rules"]["start_squares"] = [[13, 0], [1]]
    errs = errors_for(raw)
    assert "rules.start_squares[0]: (13, 0) is outside the board" in errs
    assert "rules.start_squares[1]: must be [row, col]" in errs


def test_start_squares_required_but_may_be_empty(raw):
    del raw["rules"]["start_squares"]
    assert any("start_squares: required" in e for e in errors_for(raw))
    raw["rules"]["start_squares"] = []
    assert parse_profile(raw).start_squares == ()


def test_bad_stacking(raw):
    raw["rules"]["word_premium_stacking"] = "power"
    assert any("word_premium_stacking" in e for e in errors_for(raw))


def test_bad_numbers(raw):
    raw["board"]["width"] = 1
    raw["rack"]["size"] = "seven"
    raw["rack"]["blanks"] = -1
    raw["rules"]["min_word_length"] = 1
    errs = errors_for(raw)
    assert "board.width: must be between 2 and 40" in errs
    assert "rack.size: must be an integer" in errs
    assert "rack.blanks: must be between 0 and 20" in errs
    assert "rules.min_word_length: must be between 2 and 40" in errs


def test_booleans_are_not_integers(raw):
    raw["rack"]["size"] = True
    assert "rack.size: must be an integer" in errors_for(raw)


def test_letter_map_errors(raw):
    raw["tiles"]["values"]["a"] = 1
    raw["tiles"]["values"]["AB"] = 1
    raw["tiles"]["values"]["B"] = -3
    raw["tiles"]["distribution"]["Ñ"] = 1
    errs = errors_for(raw)
    assert "tiles.values: 'a' must be a single uppercase letter" in errs
    assert "tiles.values: 'AB' must be a single uppercase letter" in errs
    assert "tiles.values.B: must be an integer >= 0" in errs
    assert "tiles.distribution: letter 'Ñ' has no value in tiles.values" in errs


def test_non_latin_alphabet_allowed(raw):
    raw["tiles"]["values"]["Ñ"] = 8
    raw["tiles"]["distribution"]["Ñ"] = 1
    assert "Ñ" in parse_profile(raw).alphabet


def test_bag_smaller_than_rack(raw):
    raw["tiles"]["distribution"] = {"A": 2}
    raw["rack"]["blanks"] = 0
    assert any("fewer tiles in the bag" in e for e in errors_for(raw))


def test_full_rack_tiles_cannot_exceed_rack(raw):
    raw["scoring"]["full_rack_tiles"] = 8
    assert "scoring.full_rack_tiles: cannot exceed rack.size" in errors_for(raw)


def test_missing_required_sections():
    errs = errors_for({"version": 1})
    for needed in (
        "name: required non-empty string",
        "board.width: required",
        "board.layout: required list of strings",
        "rack.size: required",
        "tiles.values: required",
        "tiles.distribution: required",
    ):
        assert needed in errs


def test_word_path_type(raw):
    raw["words"]["path"] = 5
    assert "words.path: must be a non-empty string" in errors_for(raw)


def test_error_message_includes_source(tmp_path, raw):
    p = tmp_path / "x.toml"
    p.write_text('version = 1\nname = "x"\n')
    with pytest.raises(ProfileError) as exc:
        load_profile(p)
    assert str(p) in str(exc.value)


def test_check_profile_cli(capsys, tmp_path):
    from tilelens.app import main

    assert main(["check-profile", str(demo_profile_dir() / "demo_pocket.toml")]) == 0
    assert "Demo Pocket" in capsys.readouterr().out
    bad = tmp_path / "bad.toml"
    bad.write_text("version = 1\n")
    assert main(["check-profile", str(bad)]) == 1
    assert "name: required" in capsys.readouterr().err

import pytest

from tilelens.ui.settings import DEFAULT_SHORTCUTS, Settings


@pytest.mark.parametrize("text", ["[]", "null", '"text"', "3", "{not json"])
def test_settings_file_that_is_not_an_object_gives_defaults(tmp_path, text):
    path = tmp_path / "s.json"
    path.write_text(text)
    s = Settings.load(path)
    assert s.hz == 10 and s.overlay_mode == "exclude" and s.profile is None
    assert s.shortcuts == DEFAULT_SHORTCUTS


@pytest.mark.parametrize("hz", ['"fast"', "null", "[10]", "{}"])
def test_unreadable_rate_falls_back_to_default(tmp_path, hz):
    path = tmp_path / "s.json"
    path.write_text(f'{{"hz": {hz}, "profile": "p.toml"}}')
    s = Settings.load(path)
    assert s.hz == 10 and s.profile == "p.toml"


def test_rate_given_as_text_number_is_kept(tmp_path):
    path = tmp_path / "s.json"
    path.write_text('{"hz": "11"}')
    assert Settings.load(path).hz == 11


@pytest.mark.parametrize("shortcuts", ["[1]", '"Ctrl+P"', "null"])
def test_shortcuts_that_are_not_an_object_give_defaults(tmp_path, shortcuts):
    path = tmp_path / "s.json"
    path.write_text(f'{{"shortcuts": {shortcuts}}}')
    assert Settings.load(path).shortcuts == DEFAULT_SHORTCUTS


def test_shortcut_that_is_not_text_keeps_its_default(tmp_path):
    path = tmp_path / "s.json"
    path.write_text('{"shortcuts": {"pause": 5, "rescan": "Ctrl+R"}}')
    shortcuts = Settings.load(path).shortcuts
    assert shortcuts["pause"] == DEFAULT_SHORTCUTS["pause"]
    assert shortcuts["rescan"] == "Ctrl+R"

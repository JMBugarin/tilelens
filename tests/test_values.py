from collections import Counter

from tilelens.rules.board import Board
from tilelens.rules.profile import (
    demo_profile_dir,
    learned_values,
    load_profile,
    save_learned_values,
)
from tilelens.state.position import position_id
from tilelens.vision.values import ValueVotes


def test_values_are_voted_and_need_agreement():
    v = ValueVotes()
    for _ in range(2):
        v.add("E", 2)
    assert v.trusted() == {}
    v.add("E", 2)
    assert v.trusted() == {"E": 2}
    for _ in range(3):
        v.add("E", 1)
    assert v.trusted() == {}  # 3 of 6 is not agreement


def test_learned_values_ride_beside_the_profile(tmp_path):
    src = demo_profile_dir() / "demo_garden.toml"
    path = tmp_path / "game.toml"
    path.write_text(src.read_text())
    (tmp_path / "demo_words.txt").write_text((demo_profile_dir() / "demo_words.txt").read_text())
    base = load_profile(path)
    save_learned_values(path, {"E": 7})
    assert learned_values(path) == {"E": 7}
    amended = load_profile(path)
    assert amended.letter_values["E"] == 7 and base.letter_values["E"] != 7
    assert amended.fingerprint() == base.fingerprint()  # calibration stays valid
    board = Board.empty_like(base)
    assert position_id(board, Counter("E"), base, "v") != position_id(
        board, Counter("E"), amended, "v"
    )

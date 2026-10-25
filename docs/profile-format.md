# Profile format

A profile describes one crossword-style tile game: board size, premium squares, tile values,
rack rules and scoring rules. Profiles are TOML files validated on load; every problem is
reported at once with the key that caused it.

No real game's profile ships with TileLens. The two files in `profiles/` are fictional and
exist for tests and documentation. To use TileLens with a game, copy one and edit it to match.

## Example

```toml
format = "tilelens-profile"
version = 1
name = "Demo Pocket"

[board]
width = 9
height = 9
layout = [
  "T..d.d..T",
  ".D.....D.",
  "..+...+..",
  "d...t...d",
  "..q...q..",
  "d...t...d",
  "..+...+..",
  ".D.....D.",
  "T..d.d..T",
]

[board.legend]
d = "double_letter"
t = "triple_letter"
D = "double_word"
T = "triple_word"
q = { name = "quad_letter", letter = 4 }
"+" = { name = "double_both", letter = 2, word = 2 }

[rules]
start_squares = [[2, 2], [6, 6]]
min_word_length = 2
word_premium_stacking = "add"

[rack]
size = 5
blanks = 1

[scoring]
full_rack_bonus = 25

[tiles.values]
A = 1
B = 3
# ...

[tiles.distribution]
A = 5
B = 1
# ...

[words]
path = "demo_words.txt"
```

## Keys

| Key | Required | Meaning |
| --- | --- | --- |
| `format` | no | Must be `"tilelens-profile"` if present. |
| `version` | yes | Profile format version. Only `1` exists. |
| `name` | yes | Display name. |
| `board.width`, `board.height` | yes | Grid size, 2 to 40. |
| `board.layout` | yes | One string per row. `.` or space is a plain square; any other character must be in `board.legend`. |
| `board.expand` | no | Squares of room to add on every side for a board that grows during play. The premium pattern repeats into the room, so it needs `board.period`. TileLens finds the board's real edges on screen and treats everything outside them as wall. Default 0. |
| `board.period` | with `expand` | `[rows, columns]` after which the layout repeats. The layout must repeat exactly (a start marker such as `*` may break the pattern once); TileLens names the first square that does not. |
| `board.legend` | no | Character to premium. Either a built-in name (`double_letter`, `triple_letter`, `double_word`, `triple_word`) or a table `{ name, letter, word }` with integer multipliers of at least 1. |
| `rules.start_squares` | yes | List of `[row, col]` (zero-based). The first placement on an empty board must cover at least one. `[]` allows any first placement. |
| `rules.min_word_length` | no | Default 2. |
| `rules.word_premium_stacking` | no | How several word premiums under one word combine: `multiply` (default; 2 and 3 give 6), `add` (1 plus the sum of each multiplier minus 1; 2 and 2 give 3), or `max`. |
| `rack.size` | yes | Tiles held per turn. |
| `rack.blanks` | no | Number of blank tiles in the set. Blanks score 0. |
| `scoring.full_rack_bonus` | no | Points added when a placement uses `scoring.full_rack_tiles` tiles. Default 0. |
| `scoring.full_rack_tiles` | no | Defaults to `rack.size`. |
| `tiles.values` | yes | Letter to points. Letters are single uppercase characters; non-Latin letters are fine. |
| `tiles.distribution` | yes | Letter to count in the full set. Used to sanity-check what the recognizer reads. |
| `words.path` | no | Word list, one word per line. Relative paths resolve against the profile file. |
| `appearance.squares` | no | Hex color (`"#RRGGBB"`) of each empty square type, keyed by layout character (`.` for plain). Lets TileLens find the board on screen by itself. |
| `appearance.rack_tile` | no | Hex color of a tile in the rack. Needed for automatic calibration. |
| `appearance.rack_zone` | no | `[left, top, right, bottom]` fractions of the frame where the rack sits. Defaults to the lower middle. |

## Scoring rules applied

- Letters already on the board count at face value; premiums apply only to newly covered squares.
- Every distinct word formed by the placement (the main word and each cross word) is scored once.
- A blank scores 0 but still triggers the word premium of the square it covers.
- The full-rack bonus is added once, on top of the word totals.

Check a profile with:

```bash
uv run tilelens check-profile path/to/profile.toml
```

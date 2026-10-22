# Synthetic boards

TileLens never uses screenshots of a real game. All of its test images come from its own
renderer in `src/tilelens/synth/`, which draws boards and racks from a profile with exact
ground-truth labels.

## What it draws

- A window with a header (scores and a ticking timer), a chat panel, the board and the rack.
  The timer and chat panel exist so the live loop can be tested with masked regions.
- Premium squares colored by type, with optional short labels such as `2L` or `3W`, and a
  marker on each start square.
- Tiles in three states: committed, tentative (placed but not submitted, drawn with a
  colored outline) and recently played (a highlight fill).
- Blanks: the substituted letter is drawn in a different color, with no point value.
- Point-value marks, which can be turned off per theme. The rack may contain blanks and
  empty slots.
- A floating tile being dragged, partially faded tiles for animations, and any cell size
  or output scale.

## Themes

| Theme | Typeface | Notes |
| --- | --- | --- |
| meadow | sans, medium | light, labeled premiums |
| slate | sans, bold | dark |
| candy | uni | rounded tiles |
| paper | sans, light | dark tiles on white, no premium labels, no point values |
| ember | italic | **held out**: never used to tune the recognizer |
| mosaic | sans, heavy | tiles take their square's color, labels and values in the top corners |
| apricot | sans, black | cream board filling the window, white tiles that take premium colors, orange rack tiles with white letters, unsubmitted tiles in the rack color; used by the zoomed-view tests |

`render_tile_chart` draws a panel with one rack-style tile per letter in alphabetical order and
the blank last, the kind of tile-set screen some games have.

Text is drawn with the fonts built into OpenCV (`cv2.FontFace`), so no font files are
added to the project.

## Labels

`render(scene)` returns the image and a JSON-serializable dict with each board cell
(letter, blank flag, state or empty), each rack slot, the theme, whether it is held out,
and the geometry: board rectangle, grid origin, cell pitch, rack slots, and the timer and
chat rectangles.

## Fixtures

```bash
uv run python -m tilelens.synth.scenes --out tests/fixtures/synthetic --count 4 --start 100
uv run python -m tilelens.synth.scenes --out tests/fixtures/synthetic --count 2 --start 900 --theme ember
```

Scenes come from seeded self-play on the demo profiles, so the boards hold real words from
the demo list. The committed fixtures are checked against a fresh render in
`tests/test_synth.py`.

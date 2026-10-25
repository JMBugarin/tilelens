# Reading the board

Nothing game-specific ships with TileLens. The recognizer learns what your game looks like
from your own screen during calibration.

## Calibration

1. Select the screen region that contains the board and the rack.
2. Drag a rough rectangle around the board grid. Within about a third of a cell is enough.
3. Type the letters on the board (`.` for empty, lowercase for a blank) and the rack from left
   to right (`?` for a blank). One screen works; two or three cover more letters.

From that, TileLens learns:

- **Grid.** The rectangle is refined by finding the cell pitch and origin whose inner cell
  boundaries carry the most edge energy (horizontal and vertical Sobel projections, smoothed in
  proportion to the cell size).
- **Square and tile colors.** It records the median color of an inner ring and of the outer
  band of each cell. Empty squares are grouped by premium type, and played tiles are clustered
  into committed and highlighted states.
- **Glyph templates.** The letter is separated from the tile background. The corner
  point-value mark and fragments at the tile edge are dropped, and the result is normalized to
  a 24x24 binary-shape template. Rack tiles are resampled to the board's cell resolution first,
  so rack and board glyphs pass through the same pipeline.
- **Blank style.** It learns the ink color of blanks and whether normal tiles carry a value mark.
- **Empty-square labels.** It learns the labels printed on premium squares, so a label is not
  mistaken for a tile.

Calibration re-reads its own screens and refuses to save if more than 3% of squares disagree
with what you typed. That usually means the rectangle or the typed board is wrong.

The same thing is available from the command line, for screenshots you saved yourself:

```bash
uv run tilelens calibrate --profile my.toml --image a.png --board a.txt --rack "AEINRST" \
  --rect 120,80,600,600 --out my-calibration.json
uv run tilelens read --profile my.toml --calibration my-calibration.json --image b.png
```

## Automatic calibration

When the profile has an `[appearance]` section, the board does not need to be centered, whole
or uncovered, and no rectangle is drawn. Nothing is typed either: the rack is read from
stock-font letter shapes (below), and you are only asked about tiles that read unclearly.

- **Letters never seen yet.** `src/tilelens/vision/priors.npz` holds every letter and digit
  drawn in eight common bold fonts (regenerate with `tools/make_priors.py`; only shape vectors
  are stored). A glyph that no learned template explains is matched against them, and a letter
  with no learned template is named only if its best stock shape scores at least 0.85 and
  leads the next letter by 0.07. A learned shape that a stock shape agrees with is trusted
  at a lower score than one it contradicts. Every confirmed tile then becomes a learned
  template, saved with the calibration, so the stock shapes matter less each game.
- **Point values.** The digits printed on rack tiles are read with the digit shapes and
  voted per letter; a value seen three times with 80% agreement replaces the profile's value
  (kept in `<profile>.learned.json`, never in the profile itself). Board tiles are not used for
  this: they print the points the tile scored, with premiums applied.
- **Stamped values on the board.** Letter League counts the number printed on a played tile
  whenever the tile is used again (an N from a triple-word play counts 3, not 1). TileLens
  reads that number off every played tile at full resolution, accepts it only if it is the
  letter's value times 2s and 3s (which settles 3 against 5 on a thick font), and scores
  crossing words and extensions with it. A tile whose number cannot be read counts at the
  letter's own value. Checked against a real game: this model reproduces the game's score
  (90 after three turns, and 11 for a play the plain values scored 8).
- **A board that grows.** Letter League adds columns and rows as play nears an edge. With
  `board.expand` and `board.period` in the profile, the layout repeats into room around the
  board, the lattice is registered against it (a repeating pattern fits at offsets one period
  apart, so the offset is chosen from the star on the start square, from where tiles were last
  seen, or from a tile on the start square), and the board's real extent is measured from the
  squares that show an empty square's own color. A side that visibly ends is a wall beyond it;
  a side that runs off the screen is unknown, as before. A board of 27 columns that grew to 30
  is scanned over all 30.
- **The starting round.** Before the first word the start square shows a spinning purple star.
  A star on a square that still has its own color is read as an empty square (never as an
  unreadable tile, which used to leave the start square unknown and block every suggestion
  until the animation stopped), and it means no word has been played: tile-like shapes
  elsewhere are treated as tiles being placed, not as played ones. Once a tile covers the
  square the star is gone and the board is read as usual.
- **Squares only inside the game's canvas.** Squares must lie inside the area where board
  colors are common, so the sidebar, the letterbox bars and video tiles that surround an
  activity are never read as tiles.

- **Finding the board.** Pixels are classified against the square colors. The cell pitch is
  the period whose grid lines (light lines between squares, or edges when there are none) give
  the strongest on-line versus between-line contrast, tried over a fine log-spaced range and at
  twice each peak. For each candidate lattice, the visible premium squares are registered
  against the profile layout. The best registration must agree on at least 60% of the visible
  premium squares and beat the runner-up offset clearly. Colors that cannot be told apart (a
  start square drawn like a double word) count as one class. Blocks of one premium color wider
  than a square are treated as interface panels.
- **Tracking.** While a sample of visible squares still shows the expected colors, the last
  position is reused. A zoom, pan or resize triggers a new search.
- **What is read.** Only squares fully on screen are read. A square that is covered by a
  panel, a popup or the floating rack, or whose glyph runs off the square, is marked unseen
  rather than empty. The search treats unseen squares as unavailable, so suggestions are the
  best among the visible squares.
- **Corrections stick.** What you enter in the correction grid is kept with the glyph it was
  entered for, per square and per rack tile, so the next frames read it the same way even when
  the match is not strong enough on its own. A square whose tile is remembered from an earlier
  confirmed reading keeps that letter (played tiles never change). An unclear square's guess
  is not part of the position's signature, so a guess that changes from frame to frame does
  not restart the reading.
- **Square labels.** Tiles carry point values and labels do not. A glyph in the label ink
  color without a value mark on a premium square is a label unless it is clearly a letter; then
  it is asked about (a blank tile also has no value) instead of being read as a tile.
- **Learning.** Letter shapes come from the typed rack. Premium-square labels are learned by
  clustering the glyphs that repeat on squares of one type (glyphs with a point value are
  tiles, not labels). The blank's value mark color comes from a `?` in the rack.

## Learning every letter from a tile chart

Some games have a screen that lists the whole tile set: one tile per letter in alphabetical
order and the blank last. After the first calibration, open that screen and press Capture
again, then Learn. TileLens finds the rack-colored tiles, orders them by rows, checks there
is one per letter (plus an optional blank), refuses a chart whose tiles repeat a shape, and
learns every letter in the game's own font. The alphabet order is the profile's letters
sorted.

### Measured accuracy on the look-alike theme

Synthetic zoomed, panned and covered views (`apricot` and `mosaic` themes, 80 views each), after
one typed rack and the tile chart, with the player answering Check tiles as they come up:

| Views | Board tiles read | Flagged | Wrong |
| --- | --- | --- | --- |
| first 20 | 89–90% | 10% | 0 |
| next 20 | 95–96% | 4–5% | 0 |
| last 40 | 96–98% | 2–4% | 0 |

Before the chart, with only the typed rack, about 21% of board tiles were read and the rest
flagged. What raised accuracy without wrong reads:

- Matching board glyphs against rack or chart shapes needs a score of 0.92 and a lead of 0.04;
  board shapes kept from confirmed positions are then matched like for like.
- A blank is told apart by its value mark: a normal value is drawn in the letter's color, so
  its pixels lie between the letter and tile colors; a blank's mark does not.
- Tile background colors are learned. Played tiles can be white, take their square's color,
  or be highlighted (the last word played). A background shared in one frame by three or more
  squares that each show a point value and a clearly matching letter is learned on the spot;
  a tile color on fewer squares is asked about once. A glyph on any other background is
  covered by something and is not read.
- A square is partly covered, and not read, when a corner differs from the rest of it, two
  corners differ from each other, or a band along one side has another color (a panel edge
  cutting a letter in half).
- A blank is also recognized by a letter drawn in a color of its own instead of dark ink.
- Labels are only learned from squares showing their own color.

## Reading a frame

For each square, color and glyph evidence are combined:

- An empty-looking color with no glyph, or with the square's known label, is read as empty.
- A glyph that is not that label is a tile. The tile's background color decides whether it is
  committed, highlighted or unknown, and the glyph is matched against every template by
  correlation.
- A letter is accepted only when its best score is at least 0.8 and clearly ahead of the
  runner-up. If some letters have no template yet, the bar rises to 0.95 so an unseen letter
  is not read as a lookalike.
- The blank decision combines the missing value mark with the ink color. If those cues
  disagree, or a blank style was never seen, the square is flagged rather than treated as a
  normal letter.

Anything uncertain goes to the correction grid: a tile in an unknown color, an unclear letter,
a possible blank, a square that matches nothing, or more copies of a letter than the set
contains. Corrections are learned immediately as new templates, colors or label shapes. If the
board's size changes by more than 3% since calibration, every result is held until you
recalibrate.

## Measured accuracy

`uv run python benchmarks/bench_vision.py --scenes 60` on synthetic boards (Apple M5 Pro).
Each theme is calibrated on three of its own screens and read on 60 others at the same size.
`ember` is the held-out theme: no threshold or rule was tuned while looking at its results.

| Theme | Squares correct | Squares flagged | Silent errors | Rack exact | Rack flagged | Positions fully read |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| meadow | 100% | 0% | 0 | 96.2% | 3.8% | 75.0% |
| slate | 99.87% | 0.13% | 0 | 97.6% | 2.4% | 63.3% |
| candy | 100% | 0% | 0 | 98.1% | 1.9% | 86.7% |
| paper | 100% | 0% | 0 | 88.2% | 11.8% | 41.7% |
| **ember (held out)** | **100%** | **0%** | **0** | **95.7%** | **4.3%** | **75.0%** |

With eight calibration screens instead of three, rack flags fall to 0% for meadow, slate and
candy, 4.1% for paper and 2.4% for ember, still with no silent errors.

Rack tiles are matched first against templates taken from the rack itself. A letter only seen
on the board can still be accepted on the rack, but only with a score of at least 0.90 and a
lead of 0.08 over the next letter. On 175 rack tiles per theme, that rule accepted 81 to 100%
of them and none wrongly.

- Each theme has 10,140 squares, and 60 positions with 380 to 420 rack tiles.
- "Positions fully read" means the board and rack were read with nothing flagged. Every other
  position had at least one flagged rack tile and no silent error.
- A second run per theme with tentative tiles on the board found no tentative tile read as
  played.
- Reading a frame takes 22 to 25 ms (median), and calibrating takes under a second.

Because there were no silent errors, no learned classifier was added.

### What these numbers do not show

- The boards come from TileLens's own renderer. No real game was measured, and real games
  add animations, shadows, textures and fonts this renderer does not draw.
- Calibration and test screens share a theme and cell size. When the board is resized
  afterwards, roughly 3 to 20% of squares get flagged, plus a few silent O/G swaps at 0.85x.
  For that reason a size change now holds results until you recalibrate.
- Fully automatic grid detection, with no rectangle from you, misplaced the grid by one or two
  cells in 11 of 180 synthetic screens. That is why calibration asks for a rough rectangle;
  with one, all 180 screens were placed within 0.05 of a cell.

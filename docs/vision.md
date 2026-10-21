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
| meadow | 100% | 0% | 0 | 93.5% | 6.5% | 66.7% |
| slate | 100% | 0% | 0 | 96.4% | 3.6% | 80.0% |
| candy | 100% | 0% | 0 | 100% | 0% | 100% |
| paper | 100% | 0% | 0 | 100% | 0% | 100% |
| **ember (held out)** | **100%** | **0%** | **0** | **97.6%** | **2.4%** | **85.0%** |

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

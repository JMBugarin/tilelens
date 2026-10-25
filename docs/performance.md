# Performance

All numbers in this file were measured on one machine, and other hardware will give
different results. The raw JSON output lives in `benchmarks/results/`.

Hardware: Apple M5 Pro, 24 GB, macOS 26.6, Python 3.13.16.

## Solver

`uv run python benchmarks/bench_solver.py --games 12 --racks 8`

- Profile: Demo Garden (13x13, 7-tile rack, 2 blanks).
- Word list: `/usr/share/dict/words`, restricted to lowercase entries of 2 to 15 letters,
  which leaves 204,217 words. The list is read at benchmark time and never shipped.
- Positions: boards from seeded self-play games. "Mid-game" boards have 30 to 55 tiles and
  "dense" boards have at least 70 tiles on 169 squares. Racks are drawn from the tiles not
  yet on the board.
- Every search completed (none were cancelled), so every result is exact for that list.

| Scenario | Searches | Median | p95 | Worst |
| --- | ---: | ---: | ---: | ---: |
| Opening, 7 tiles | 40 | 1.2 ms | 3.0 ms | 6.2 ms |
| Mid-game, 7 tiles | 80 | 2.2 ms | 6.2 ms | 7.1 ms |
| Dense, 5 tiles + 2 blanks | 80 | 17.0 ms | 94.7 ms | 117.7 ms |
| Dense, 6 tiles + 1 blank | 80 | 5.9 ms | 24.0 ms | 30.3 ms |
| Late game, 2 to 4 tiles | 80 | 0.5 ms | 0.7 ms | 1.0 ms |

| One-time cost | Measured |
| --- | ---: |
| Word graph build (204k words, 103,315 nodes) | 0.58 s |
| Cached graph load | 0.05 s |
| Peak memory of the benchmark process | 184 MB |

No solver optimization was done beyond the standard anchor, cross-check and left-part
pruning, because no profiled bottleneck needed it: the slowest case measured is well
under the time one capture frame takes.

## Correctness checks

- `tests/test_search.py` compares the complete set of legal placements and their scores
  against an independent brute-force solver. The brute-force solver enumerates every line
  segment, word and blank assignment and asks the rules module about each one. The check
  runs on 400 seeded random positions in the normal test run, and on 5,000 more with
  `pytest -m slow`. A one-off run of 20,000 slow cases also passed.
- One dense self-play board with a two-blank rack, checked against the full 204k list, also
  matched exactly: 12,372 placements, found in 80 ms by the search and in 7.3 s by brute
  force.

## End to end

`uv run python benchmarks/bench_e2e.py --mode inproc --events 40`

This measures the time from a stable change in the fake game window (the last animation frame
drawn) to the matching result being published to the panel. The full app runs in one process:
the live controller at 10 Hz, the recognizer, the solver worker process, the overlay and the
panel. In this mode in-process mode frames come straight from the fake game window; in screen mode they come
from real screen capture.

| Mode | Position changes | Median | p95 | Worst | Stale results shown |
| --- | ---: | ---: | ---: | ---: | ---: |
| in-process frames, 40 game events | 20 | 490 ms | 517 ms | 520 ms | 0 |
| real screen capture, 40 game events | 20 | 572 ms | 619 ms | 680 ms | 0 |

- Most of the time is spent waiting on purpose. A position must look the same on three
  consecutive frames at 10 Hz before it is trusted, which is about 300 ms plus up to one frame
  of alignment.
- Reading takes about 25 ms per frame and the search takes a few ms on the demo list.
- The real-screen row (`--mode screen`) is the full app, including real screen capture of the
  fake game window and the overlay drawing ghost tiles on screen. It sampled the ghost-tile
  squares in 99 captures, and the largest mean difference from the game's own frame was 4.7,
  which is animation noise; a captured ghost tile differs by tens. The overlay was never
  captured.
- Hardware and versions: Apple M5 Pro, 24 GB, macOS 26.6, Python 3.13.16, PySide6 6.12.0,
  OpenCV 5.0.0.93, NumPy 2.5.4, mss 10.2.0. The raw JSON is in
  `benchmarks/results/e2e-inproc.json` and `benchmarks/results/e2e-screen.json`.

## Live Letter League captures

`uv run python benchmarks/bench_discord.py` reads two rack strips cropped from a real game
(`tests/fixtures/discord`) with no calibration, at 100%, 75% and 50% of the captured size.
Letters and printed point values were 14 of 14 correct at every size
(`benchmarks/results/discord-rack.json`). A rack strip takes about 40 ms to find and read at
full size.

`uv run tilelens check-screen` times the same steps on the live screen. On a 3456x2234 capture:
the first read, which finds the board, takes about 0.5 s; later reads of an unchanged board take
22 to 40 ms (squares that look as before are reused, and the rack is searched near where it
was); and a settled position reaches the top three plays after about 0.25 s of compute. The
three matching frames the loop waits for add 0.3 s at 10 Hz.

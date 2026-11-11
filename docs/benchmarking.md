# Benchmarking

Three kinds of evidence exist in this repository and they are never mixed.

| Kind | Where it comes from | What it can show |
| --- | --- | --- |
| **Live** | The running app on a real Discord Letter League window, traced while it is played | End-to-end latency, CPU, memory, overlay behavior |
| **Replay** | Frames saved from a real game, with labels written by eye, replayed through the reader and solver | Recognition accuracy, move legality, per-stage timing |
| **Simulator** | The built-in fake game and synthetic scenes | Regression coverage only; never used as proof of real-world accuracy |

## Real data

`tools/capture_real.py` records frames of a game window at the display's native resolution with
no TileLens window in them, plus a `session.json` with the window rectangle, display scale and
frame times:

```bash
uv run python tools/capture_real.py --out benchmarks/real/raw/session-1 --on-change --count 60
```

Raw captures stay out of git (`benchmarks/real/raw/`), because a Discord window can show other
conversations. Frames are cropped to the game canvas and checked by eye before they are copied
next to `benchmarks/real/cases.json`.

Labels in `cases.json` are written by reading the frame, never copied from TileLens output:
each board square is `(row, col)` counted from the top-left square of the visible board, with
its letter, the number printed on the tile and whether it is a blank. Racks list their letters
and printed values. When a label is uncertain the case is left out rather than guessed.

## Replay benchmark

```bash
uv run python benchmarks/bench_real.py
```

For every case: cold read (the board is found), warm read (steady state), and the letters,
blanks and values compared with the labels at the native size and at resampled copies (a
smaller window or a zoomed view, flagged `derived_from_real_frame`). Every mistake is listed:
a wrong letter that was flagged as unsure and one that was not (silent) are counted apart.
On the board read, seeded racks are searched and each of the top three plays is checked with
the rules module (`check_placement`, `score_words`) and against a full enumeration of the
search. The racks are synthetic; the board is real. Scoring against the game's own numbers
needs frames of a play with the score before and after, which is recorded separately.

## Live benchmark

```bash
uv run python benchmarks/bench_live.py --seconds 900 --label letter-league-game
```

Starts the app with `TILELENS_TRACE`, samples CPU and resident memory of the process tree every
second and analyzes the trace. An episode starts at the first captured frame that shows a
change after a quiet period and ends when the overlay or panel was painted with the result
published for it. The game changes slightly before the first frame that shows it, so these
latencies are lower bounds by up to one capture interval (100 ms at the default 10 Hz). Episodes
without a result are listed.

With Discord open and no game on screen the loop is idle, and the report says so; that run
measures idle cost, not gameplay.

## Hardware and environment

Every report records the machine, OS, Python and library versions. Results from different
machines are not compared with each other. Before-and-after pairs are measured on the same
machine, interleaved where timing is noisy, from the code before this work (a separate
worktree) and the current tree.

## Simulator benchmarks

`bench_solver.py`, `bench_vision.py` and `bench_e2e.py` use generated boards and the fake game.
They guard against regressions and are reported as such.

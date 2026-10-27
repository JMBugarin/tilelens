# Engineering log

Running record of the Liquid Glass redesign, optimization and benchmarking work. Measurements
are only listed here after they were run; each entry names the command and the environment.

## Environment

- Apple M5 Pro, 24 GB, macOS 26.6, display 3456x2234 Retina.
- Python 3.13.16, PySide6 6.12.0, OpenCV 5.0.0.93, NumPy 2.5.4, mss 10.2.0.
- Branch `feat/liquid-glass`, cut from `origin/main` (c1f9b4d) with the foreign-window
  recognition fix (1614078) carried over from `feat/highlight-unsure-tiles`.

## Decisions

- The magenta tile outline (and the yellow "tile to confirm" outline before it) live only on
  `feat/highlight-unsure-tiles` (open PR 23). The new branch does not carry them over; the
  recognition fix from that branch is kept.
- The Liquid Glass reference image mentioned in the brief did not come through with the request,
  so the design follows Apple's published Liquid Glass description (translucent lensing
  material, specular rim, concentric radii, adaptive light and dark).
- Nothing is merged or released without approval; the work is a pull request left open.

## Baseline (before any change from this branch)

Measured on the code of `feat/highlight-unsure-tiles` at b25330e, in a separate worktree.

- Test suite: 348 passed, 1 skipped (a Space-switching test that needs an opt-in) in 262 s.
- Solver (`benchmarks/bench_solver.py --games 12 --racks 8`, 204k words, demo 13x13, simulator
  boards): opening 1.4 ms median / 6.8 worst; mid-game 2.6 / 7.7; dense 5 tiles + 2 blanks
  19.0 ms median, 105 p95, 134 worst; dense 1 blank 7.2 / 27 / 39; late game 0.6 / 1.2.
- Vision (`bench_vision.py`, simulator scenes): 24.6 to 31 ms median read per frame.
- Real Discord rack strips (`bench_discord.py`, 2 real strips, 14 tiles): letters 14/14 and
  values 14/14 at 100%, 75% and 50% size; 39 ms to read a strip at full size.
- End to end, fake game, in-process frames (`bench_e2e.py --mode inproc --events 40`): median
  484 ms, p95 520 ms, worst 1575 ms (the worst sample overlapped another job on the machine and
  is re-measured at the end), 0 stale results shown.
- Real board frame replay (`bench_real.py`, one real Discord frame labelled by eye, 18 tiles):
  18/18 letters and blanks correct at 0.65x to 1.5x of the captured size; 14/18 at 0.5x, where four
  letters are read as blanks and after the word-list settle step they are no longer marked
  unsure, so those four are silent errors. Cold read (board found) 540 to 600 ms, of which
  locating the grid is about 555 ms; warm read 15 ms at 0.5x to 54 ms at 1.5x (30 ms at 1x).
  The numbers printed on played tiles are not read at the native size (0 of 18; 6 of 7 read at
  1.25x and 1.5x), so scores that reuse those tiles count the base value where the game counts
  the printed one. 120 top-3 plays on that board for 40 seeded racks: all legal, scores agree
  with the rules module, top three equal a full enumeration; solve median 12 ms, p95 162 ms,
  worst 172 ms (racks are synthetic, the board is real).
- Screen capture of the whole 3456x2234 display takes 41 ms median; a Discord-sized window
  29 ms; an 800x600 region 21 ms.

## Log

- Design system, native blur (macOS verified: detail behind the glass drops about 3000x,
  corners stay sharp, window still excluded from capture; Windows unit-tested with mocks only),
  screenshot gallery (75 states) and the measuring tools are committed.
- Discord was launched. It is signed in but no Letter League activity is running, so no live game
  frames exist yet; everything labelled "real" above comes from frames saved earlier from a real game.

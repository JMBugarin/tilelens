# TileLens

[![CI](https://github.com/JMBugarin/tilelens/actions/workflows/ci.yml/badge.svg)](https://github.com/JMBugarin/tilelens/actions/workflows/ci.yml)
![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-blue) ![macOS](https://img.shields.io/badge/macOS-Apple%20silicon-lightgrey) ![License](https://img.shields.io/badge/license-MIT-green)

**A real-time computer-vision and search system: it reads a tile board off your screen, finds every legal word placement, and previews the top three as ghost tiles, all locally, in milliseconds.**

![TileLens over Discord's Letter League: confirmed rack, ghost tiles for the selected play and the top three placements](docs/images/discord-tracking.png)

*Rack outlined green once every tile is read, selected play previewed as dashed ghost tiles, top three plays ranked by points. The player's name is blurred.*

> A side project for experimenting with computer vision and word search, not for competitive or ranked play (assistance tools can get accounts banned). It never clicks or places anything, and is not affiliated with any game or company.

## Highlights

- **Fast exact search.** DAWG-based move generator finds every legal placement: **2.2 ms median** on mid-game 13x13 boards with a 204k-word list. Checked against an independent brute-force solver on 400 seeded positions per test run (5,000+ more in the slow suite).
- **Vision that knows what it doesn't know.** 99.9-100% of squares read correctly on synthetic boards across five themes (one held out from all tuning), **zero silent errors**; uncertain tiles are flagged, never guessed. On real Discord captures: 14/14 letters and values at 100%, 75% and 50% scale, with no calibration (small sample, see [limits](docs/limits.md)).
- **Real-time pipeline.** 10 Hz capture, change detection so unchanged frames cost 22-40 ms, a separate search process, and stale results never shown.
- **Engineered like a product.** Strict lint and format checks, 235 test functions including GUI and randomized suites, CI on macOS and Ubuntu, reproducible `uv` lockfile, benchmarks with raw JSON committed.
- **Private.** No network requests, telemetry, accounts or cloud models. Frames stay in memory.

**Stack:** Python · OpenCV · NumPy · PySide6 · PyObjC/Quartz · DAWG · multiprocessing · uv · pytest · ruff · GitHub Actions

## How it works

1. **Capture.** The whole display that shows the game is captured 8 to 12 times a second (10 by
   default), and only read when something changed. Frames stay in memory. TileLens's own windows
   are never part of a capture.
2. **Align.** The board is found from its square colors on every frame where it moved, so
   zooming, panning, resizing and fullscreen need no setup. When the board moves the preview is
   cleared at once and returns once the board is found again.
3. **Read.** Each square is classified as empty or holding a tile, and the rack is read tile by
   tile. Letters are matched against shapes learned from your own screen and, for letters not
   seen yet, against stock-font shapes, so a fresh install reads the rack without typing it.
   The point values printed on rack and board tiles are read too: Letter League counts the
   number printed on a played tile whenever it is used again, so scores follow the game's own.
4. **Settle.** A position must read the same on three frames in a row. Squares and rack tiles
   that read unclear are settled in this order: remembered tiles, then the word list (the
   next-best letter that makes every word through the square valid), then a lone-tile check.
   What is still unclear never stops the suggestions: an unclear square is treated as a wall
   and the panel names the tile and offers its best letters as buttons.
5. **Search.** A word graph (DAWG) search runs in a separate process. It finds every legal
   placement and keeps the top three by total score, premiums and crossing words included. A
   result is shown only while the board on screen is still the position it was computed for.
6. **Preview.** A click-through overlay draws the selected placement as ghost tiles. Clicking
   a card in the panel holds that play still while you place your tiles, and tracking resumes
   by itself when your rack refills. The overlay and the panel are hidden from screen capture.

"Highest scoring" means the search finished; "best found so far" means it was cut short. A
result is only as exact as the board, rack, profile and word list it was given.

More detail: [vision](docs/vision.md), [live loop](docs/live-loop.md),
[panel and overlay](docs/overlay.md).

## Install

You need macOS on Apple silicon, [uv](https://docs.astral.sh/uv/) and Git.

```bash
git clone git@github.com:JMBugarin/tilelens.git
cd tilelens
uv run tilelens
```

`uv run` creates the environment from the lockfile on first use. To try it without a game:

```bash
uv run tilelens demo
```

That opens a self-playing fake game window next to the panel and needs no permissions.

### Screen Recording permission

macOS only lets apps read the screen with the Screen Recording permission.

```bash
uv run tilelens doctor
```

If it reports `denied`, open **System Settings > Privacy & Security > Screen & System Audio
Recording**, enable the terminal you run TileLens from, and reopen that terminal. Until then the
panel shows "Game not visible: Screen Recording permission is missing". See
[docs/manual-verification.md](docs/manual-verification.md).

**Next:** [usage with Letter League](docs/usage.md), [profile format](docs/profile-format.md), [word lists](docs/word-lists.md).

## Measured performance

All numbers are from an Apple M5 Pro with 24 GB, macOS 26.6 and Python 3.13.16. The details
and the raw JSON are in [docs/performance.md](docs/performance.md).

### Search

On a 13x13 board with a 204k-word list:

| Case | Median | p95 | Worst |
| --- | ---: | ---: | ---: |
| Mid-game, 7 tiles | 2.2 ms | 6.2 ms | 7.1 ms |
| Dense board, 2 blanks | 17 ms | 95 ms | 118 ms |

### Recognition

On synthetic boards across five themes, including one held out from all tuning:

- 99.9–100% of squares are read correctly, and there were zero silent errors.
- 88–98% of rack tiles are read without help. The rest are flagged, never guessed.

On real Letter League captures in Discord (two racks, 14 tiles, `benchmarks/bench_discord.py`),
with no calibration at all, from stock-font shapes and the printed values:

| Capture size | Letters named correctly | Point values correct |
| --- | ---: | ---: |
| 100% (Retina) | 14 / 14 | 14 / 14 |
| 75% | 14 / 14 | 14 / 14 |
| 50% | 14 / 14 | 14 / 14 |

That is a small sample. Before this change, the first of those racks read 3 of 7 tiles
correctly and flagged or misread the other four, on a calibration that had met 12 of 26 letters.

Settled-position timings and methodology: [docs/performance.md](docs/performance.md).

## Limits

TileLens maximizes this turn's score only (no strategy or opponent modeling). Its word list is an assumption about the game's dictionary, and live testing covered one machine (macOS 26.6, Apple silicon). Details: [docs/limits.md](docs/limits.md).

## Privacy

Everything runs locally. TileLens makes no network requests at run time and has no telemetry,
accounts, API keys or cloud models. Captured frames stay in memory and are never written to
disk unless you press **Save debug dump**, which writes the current capture and what was read from it to `~/Library/Application Support/tilelens/debug_dumps`. Your settings, calibration, imported word lists and search-graph cache are stored in
`~/Library/Application Support/tilelens` and `~/Library/Caches/tilelens`.

## Platform support

| Platform | Status |
| --- | --- |
| macOS 26, Apple silicon | Tested (capture, overlay exclusion, permission handling) |
| Other macOS versions or Intel Macs | Untested |
| Windows | Unsupported. The platform adapter raises a clear "not supported" error. |
| Linux | Untested generic capture; no permission or overlay-exclusion handling |

Packaging into an unsigned `TileLens.app` is described in [packaging/README.md](packaging/README.md).

## Development

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pytest -m "not gui and not slow"   # what CI runs on macOS and Ubuntu
uv run pytest                              # everything, including GUI and long randomized tests
```

Benchmarks live in `benchmarks/` and synthetic fixtures come from `src/tilelens/synth/`.

## License

MIT. See [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

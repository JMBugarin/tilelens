# Live loop

The live loop turns screen captures into at most one current result. A result made for an
older position is never shown.

## States

| State | Meaning |
| --- | --- |
| Reading | The board changed and is being re-read, or it has not stopped changing yet. "Waiting for board" means tiles are placed but not submitted, or a tile is being dragged. |
| Solving | The position is stable and confirmed; the search runs in the worker process. |
| Ready | The cards show the top placements for exactly the position on screen. |
| Check tiles | Something is uncertain: a square, a rack tile, a blank, a tile color never seen, more copies of a letter than the set holds, or a board size change. The correction grid shows what. |
| Paused | Capturing is stopped and the search is cancelled. |
| Game not visible | Capture failed (often a missing permission) or the board is not in the region. |

## Per tick

1. **Capture.** The selected region is captured 8 to 12 times a second (10 by default).
   After two quiet seconds in Ready, Check tiles or Game not visible, it drops to twice a
   second. Frames are never written to disk.
2. **Change check.** A cheap pixel diff runs over the board and rack only. The timer, chat and
   anything else outside them never matter, and masked rectangles inside them are ignored
   too. If nothing changed and the state has settled, nothing else happens.
3. **Read.** The recognizer reads the board and rack. If what it reads differs from the
   position currently shown, the preview is cleared on that same frame.
4. **Stability.** The same reading must appear on three consecutive frames before anything is
   decided, so animations, fades, drags and shuffles never start a search.
5. **Decision.**
   - Tentative tiles (a learned tentative color, or an unknown tile color while the rack has
     shrunk) show "Waiting for board".
   - Anything uncertain goes to Check tiles.
   - Otherwise the position is confirmed.
6. **Identify.** The position ID is a hash of the board letters and blank flags, the rack
   multiset, the profile fingerprint and the word-list version. Complete results are cached by
   that ID, so recalling tiles or shuffling the rack shows the cached result again without a
   new search.
7. **Search.**
   - The worker process (`ProcessSolver`) runs one search at a time and holds at most one
     waiting position.
   - A newer position cancels the running search through a shared generation counter.
   - Each result carries its position ID. A result is shown only while the loop is still
     solving that same ID, and every other result is dropped.

## Guarantees and how they are tested

`tests/test_live.py` replays the scripted fake game (`tilelens.synth.game`) through the real
renderer, recognizer and controller. It checks the true position on every state change and
after every tick, and fails if a shown result's ID differs from the ID of what is on screen.

- **Opponent move with a 4-frame fade:** the preview clears on the first changed frame, and
  exactly one search runs, for the final position.
- **Drag and tentative placement:** the loop waits with no search, then shows the new result
  after submit.
- **Recall and rack shuffle:** the cached result returns, with no new search.
- **Two positions while a slow search runs:** the older result is counted as stale and
  dropped.
- **Six randomized replays:** a random mix of opponent moves, shuffles, placements, submits,
  recalls and timer ticks, with solver delays of 1 to 7 ticks.
- **Other states:** pause, resume, lost board, failed capture, corrections, a dead solver,
  rescan and capture-rate limits.
- **Worker process:** submitting three positions back to back delivers only the newest.

## Known limits

- A game that draws tentative tiles exactly like played ones gives no color difference to
  detect. Such tiles are treated as played until the rack shrinks without a color change.
- After the game window is resized, results are held with "Board size changed; recalibrate"
  until you recalibrate. Recalibration starts from the last confirmed position.
- The cursor is not part of macOS screen captures. If it ever is, hovering over a square for
  more than three frames could send that square to Check tiles.

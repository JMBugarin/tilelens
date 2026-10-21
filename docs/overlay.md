# Control panel and overlay

## Control panel

- **Status:** Reading, Solving, Ready, Check tiles, Paused or Game not visible, with a detail
  line such as "Waiting for board". It also shows how old the result is, how long the search
  took ("from cache" for a cached result) and how long the last frame took to read.
- **Up to three cards:** the word, total, starting row and column, direction, tiles placed,
  any blank assignments, cross words and the per-word score breakdown. The header says
  "Highest scoring" only when the search finished, and "Best found so far" otherwise. Clicking
  a card previews that placement.
- **Buttons:** Select region, Calibrate, Pause/Resume, Rescan, Hide preview, Check tiles
  (opens the correction grid), Profile and Word list (both reload live), and the overlay mode.
- **Board preview:** a copy of the overlay inside the panel. It is the only preview in
  `panel_only` mode.

Shortcuts work while TileLens is focused and are set under `shortcuts` in the settings file
(`~/Library/Application Support/tilelens/settings.json` on macOS):

| Action | Default |
| --- | --- |
| Pause or resume | Ctrl+Shift+P |
| Rescan | Ctrl+Shift+R |
| Hide preview | Ctrl+Shift+H |
| Next card | Ctrl+Shift+N |
| Check tiles | Ctrl+Shift+C |
| Recalibrate | Ctrl+Shift+K |

System-wide hotkeys are not implemented; on macOS they need the Accessibility permission.

## Overlay

The overlay is a frameless, transparent, always-on-top window covering the capture region.
It ignores all input (`WindowTransparentForInput` and transparent mouse events) and never
sends any. Previewing a placement never clicks or places anything in the game.

It draws one placement at a time:

- New tiles are amber with the letter.
- Letters already on the board that are part of the word have a dashed blue outline.
- A blank shows its assigned letter in blue with a small circled `?`.

Positions come from the grid found in the latest frame, scaled from captured pixels to
screen points. Retina capture is twice the point size, and the transform is recomputed when
displays are added or removed, change geometry or change scale.

### Keeping the overlay out of what is read

| Mode | How | Cost |
| --- | --- | --- |
| `exclude` (default) | Sets the macOS window sharing type to none through pyobjc so screen captures skip the overlay. | None. If setting it fails, TileLens switches to `suppress` and says so. |
| `suppress` | Sets the overlay's opacity to zero for the moment of each capture. | Per capture, the overlay was hidden for under 50 ms in tests. Visible flicker on a real screen has not been measured. |
| `panel_only` | No overlay; the preview is only in the panel. | None |

Tested:

- The overlay window's sharing type is set to none in a real Cocoa window.
- In a 40-event run with real screen capture on macOS 26.6, the overlay drew ghost tiles over
  the fake game window and never showed up in 99 sampled captures.
- An overlay that leaks into a capture is never read as played tiles. Its squares come back
  uncertain or in an unknown tile color, so a leak can only cause Check tiles, never a wrong
  position.
- In `suppress` mode the overlay is hidden for every capture and restored afterwards.

Not verified:

- other macOS versions, where capture exclusion may behave differently;
- how much on-screen flicker `suppress` mode causes, since only how long the overlay stays
  hidden was measured.

See the manual checklist in `docs/manual-verification.md`.

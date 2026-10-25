# Control panel and overlay

## Control panel

- **Status:** Tracking, Aligning, Reading rack, Calculating, Holding, Check one tile, Board
  obscured, Paused or Setup, with one line saying what to do when something needs you. The
  panel shows how old the result is; the settings window shows how long the search and the
  last read took.
- **Up to three cards:** the word, total, starting row and column, direction, tiles placed,
  any blank assignments, cross words and the per-word score breakdown. The header says
  "Highest scoring" only when the search finished, and "Best found so far" otherwise. Clicking
  a card previews that placement and **holds** it: nothing is recomputed while you place the
  tiles, and tracking resumes by itself when your rack refills. Click the card again, or
  press Resume tracking, to let go early.
- **Unsure tile:** when a tile cannot be read with confidence the cards stay live and the panel
  names the tile with its best candidates as letter buttons. One tap teaches TileLens that
  tile.
- **Buttons:** Select region, Calibrate, Pause/Resume, Rescan, Hide preview, Check tiles
  (opens the correction grid), Profile and Word list (both reload live), and the overlay mode.
- **Board preview:** a copy of the suggestion inside this window. It is the only preview in
  `panel_only` mode.
- **Hide preview** hides the ghost tiles; the board frame and rack outline stay.

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
sends any. Like the glass panel, it stays visible when another app (the game) is clicked:
macOS normally hides an app's tool windows when the app is not frontmost, and TileLens turns
that off for both. Previewing a placement never clicks or places anything in the game.

It draws in a glass style:

- **Board frame.** Cyan corner brackets around the squares in view, with a "Board aligned"
  label when the whole board is in view. In a partial view the frame is a dashed amber outline
  labelled "Partial view · tracking" (every square known), "Partial view · zoom out to
  check" (squares are out of view, so the result is only the best among the squares in view)
  or "Whole board in view · some squares behind game panels" (the game's own timer or rack
  covers a few squares, and plays avoid them).
- **Ghost tiles** for the selected play only: dashed cyan outlines on dark glass with the
  letter and its point value and a "+N pts" label above. Letters already on
  the board that are part of the word get a cyan ring. A blank's letter is drawn in cyan.
- **Rack outline.** Green with "Rack confirmed · 7/7" when every rack tile was read, amber with
  how many were read otherwise.

## Glass panel

A translucent results panel floats over the game, by default at the right edge of the capture
region; drag it anywhere. It shows the three best plays (rank, word, points, starting square
such as H8 and direction, word points versus crossing-word points, blanks), Pause, Rescan,
Check tiles when something needs checking, how fresh the result is, and a legend. Clicking a
card previews and holds that play on the board. Calibrate re-learns the game from the screen
without typing anything; only when tiles cannot be read does it open a dialog, with the best
guess filled in. The ⚙ button opens the settings window. The panel, the overlay and the dialogs
are all shown over a full-screen game: macOS keeps ordinary Qt tool windows out of other apps'
full-screen Spaces, so TileLens gives its windows the non-activating panel style that lets
them in. The screenshot used to learn never includes TileLens itself. Unlike the overlay it takes clicks, and like the overlay
it is kept out of screen capture in `exclude` mode. If it is captured anyway, it covers squares
the way any game panel does: they are read as unseen, never as tiles. The panel is hidden in
`panel_only` mode.

Positions come from the grid found in the latest frame, scaled from captured pixels to
screen points. Retina capture is twice the point size, and the transform is recomputed when
displays are added or removed, change geometry or change scale.

### Keeping the overlay out of what is read

On macOS every capture is built from the windows of other apps only, so the overlay, the glass
panel, the control window and any open dialog are never in a capture, whatever the window's
sharing setting does on a given macOS version. The modes below remain as a second layer.

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

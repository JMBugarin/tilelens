# Panel, overlay and windows

The look is described in [design-system.md](design-system.md). This page says what each window
shows and does while a game is on.

## Results panel

A small pane of glass floats beside the board. It never takes focus, so the game keeps the
keyboard. It is placed on the side of the board and rack with the most free room (right, left,
below, above), keeps that side unless another is clearly better so it does not hop, and scales
a little with the height of the game window. Drag it anywhere: the position is saved relative
to the capture region, so it follows the game window. The overflow menu has **Move beside the
board** once it was dragged, and **Compact view** (a single play with arrows) is remembered.

- **Header:** the app mark, a status pill (Tracking, Aligning, Reading rack, Calculating,
  Holding, Check tiles, Board obscured, Paused, Setup, No capture), pause, compact view and the
  overflow menu. The menu holds Pause or Resume, Rescan, Check tiles, Calibrate, Settings and
  the shortcuts.
- **Three cards:** rank, word, total score, starting square with direction and tile count. The
  selected card also shows the per-word breakdown, crossing words and blanks. A card click
  previews that play on the board and **holds** it: nothing is recomputed while you place the
  tiles, and tracking resumes by itself when your rack refills. Click the held card again, or
  press Resume tracking, to let go early.
- **One banner at a time** for what needs you: an unsure tile, a partial view, a hold, a
  calibration problem, or no Screen Recording permission. The action is a button on the banner.
- **Unsure tile:** the cards stay live and the banner names the tile; its best candidates are
  letter buttons under the cards. One tap teaches TileLens that tile.
- **Before the game is learned,** a short checklist replaces the cards: allow screen capture,
  choose a word list, open the game. Each row turns into a check mark and has one button.
- **Footer:** how old the result is and the shortcut hint.

Nothing in the panel loops while a result is showing. Changes take at most 180 ms and are
instant with **Reduce motion**. The status spinner only runs while a read or search has no result
yet.

## Overlay

The overlay is a frameless, transparent, always-on-top window exactly over the capture region.
It ignores all input (`WindowTransparentForInput` and transparent mouse events) and never
sends any. Previewing a placement never clicks or places anything in the game. It stays
visible when another app (the game) is clicked: macOS normally hides an app's tool windows
when the app is not frontmost, and TileLens turns that off.

It draws only what the suggested play adds:

- **Ghost tiles** for the selected play: smoked glass with a light rim, so they differ from
  every tile and premium colour of the board, with the letter and its point value. A blank's
  letter is marked. Tiles sit exactly on their squares at any window size, display scale or
  zoom, because the squares come from the grid found in the newest frame.
- **A soft bar** under the letters already on the board that the word runs through.
- **One score tag** beside the play: `+34 pts`, and `2 of 3` when there is a choice. It keeps
  off the game's own tiles when it can.
- **Guides** (board corner brackets, the rack outline and a short label) flash for 180 ms when
  the lock changes (the board was found again, moved or resized, the rack was read) and fade
  out after 1.4 s. They stay only while something needs attention: a partial view, a view being
  tracked from memory, or a rack that is not fully read. The **Alignment guides** setting is
  Automatic, Always or Off.
- **One soft amber ring** around the tile the panel is asking about.

Tiles that were read are never highlighted, in any colour. The overlay does not mark what
was recognized.

Hiding the suggestions (the **Show suggestions on the board** switch, or Ctrl+Shift+H) removes
the ghost tiles and the tag and leaves the guides to the setting above.

## Settings window

A sheet of glass that takes focus, opened from the tray, the panel menu or the first run. The
first run shows the checklist (screen capture, word list, game area, learning the game) and
**Show all settings** skips it. After that it has Status, Preview, Overlay (capture mode,
guides, suggestions, compact panel, panel position), Appearance (Auto, Light or Dark, Reduce
motion), Game (profile, word list, region), Shortcuts, Calibration and Diagnostics. Settings
apply at once and are saved on every change. Confirmations such as "Region saved" or "Word list
changed" appear as short toasts under the panel; anything that has to stay until it is dealt
with is a banner.

Shortcuts work while a TileLens window is in front. The panel does not take focus, so while the
game is in front the cards are chosen by clicking. System-wide hotkeys are not implemented; on
macOS they need the Accessibility permission.

| Action | Default |
| --- | --- |
| Pause or resume | Ctrl+Shift+P |
| Rescan | Ctrl+Shift+R |
| Hide suggestions | Ctrl+Shift+H |
| Next play | Ctrl+Shift+N |
| Choose play 1, 2 or 3 | Ctrl+Shift+1, 2, 3 |
| Check tiles | Ctrl+Shift+C |
| Recalibrate | Ctrl+Shift+K |

## Dialogs

Calibration, automatic calibration, check tiles and the region selector use the same sheet,
header, buttons and inputs. The check dialog lists only the squares that are unclear, with the
guess highlighted and the other candidates as keys, and the rack. Apply is enabled once every
square without a guess has an answer. The calibration pictures outline the board once and
number the rack tiles; they mark no individual square.

## Windows that float over the game

Every window TileLens owns (panel and its menu, overlay, settings sheet, dialogs, toasts) is
given a native blur behind the glass where the platform has one (`NSVisualEffectView` on
macOS, acrylic on Windows) and a more opaque body where it does not. On each show the session
puts the window over other apps' full-screen Spaces on every Space and, in `exclude` mode,
out of screen capture, before the blur is requested; the three are independent and are checked
together in `tests/test_session_wiring.py` on real Cocoa windows. Light or dark and the fill
strength follow the brightness and detail of the game behind the panel, sampled from the newest
frame at most once a second; when the panel sits outside the captured region the system
appearance is kept. macOS keeps ordinary Qt tool windows out of other apps' full-screen Spaces,
so the windows get the non-activating panel style that lets them in.

Positions come from the grid found in the latest frame, scaled from captured pixels to
screen points. Retina capture is twice the point size, and the transform is recomputed when
displays are added or removed, change geometry or change scale.

### Keeping the overlay out of what is read

On macOS every capture is built from the windows of other apps only, so the overlay, the glass
panel, the control window and any open dialog are never in a capture, whatever the window's
sharing setting does on a given macOS version. The modes below remain as a second layer.

| Mode (setting) | How | Cost |
| --- | --- | --- |
| `exclude` (default, "Hidden from capture") | Sets the macOS window sharing type to none through pyobjc so screen captures skip the overlay. | None. If setting it fails, TileLens switches to `suppress` and says so. |
| `suppress` ("Hide while capturing") | Sets the overlay's opacity to zero for the moment of each capture. | Per capture, the overlay was hidden for under 50 ms in tests. Visible flicker on a real screen has not been measured. |
| `panel_only` ("Panel only") | No overlay; the panel is the only interface and shows no board preview. | None |

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

## Windows over the board

Other windows (the Discord frame, the game's own panels, TileLens's panel when it is not excluded
from capture) are never read as tiles. Dark colors are not tile colors, a square that keeps
reading as something other than a tile is remembered as an obstruction and skipped, and only
letters that form valid words are learned into the calibration. A calibration poisoned by an
earlier version is cleaned on load.

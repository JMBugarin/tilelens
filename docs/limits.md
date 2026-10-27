# Limits

- **Dictionary mismatch.** The baseline is an assumption about the game's dictionary. If it
  differs, suggestions can be words the game rejects (block them) and some valid plays will be
  missing (allow them). The word list is also used to settle
  unclear letters, so a play the list lacks can make TileLens doubt a correctly read tile; the
  panel says when it changed a letter.
- **The board must be on screen.** A menu, popup or another window over the board reads as
  "Board obscured". Squares behind the game's own panels (timer, rack) are never read, and
  suggestions avoid them.
- **Tile values.** Rack tiles show a letter's own value, which TileLens learns. Played tiles show
  the value they scored (premiums applied) and Letter League counts that number when the tile is
  used again, so TileLens reads it per tile. A number it cannot read falls back to the letter's
  own value, which scores such a play too low. A letter that has not been in your rack yet uses
  the profile's value until it is.
- **Letter League was tested live on one machine** (macOS 26.6, Apple silicon, Discord's
  windowed and maximized view). Discord's own fullscreen could not be driven from the test
  setup; the panel, overlay and dialogs were verified over a native fullscreen window of
  another app, which is the same macOS mechanism. Blank tiles were not seen in the live game.
- **Tentative tiles.** A game that draws placed-but-unplayed tiles exactly like played ones
  cannot be told apart by color. Clicking a card holds the suggestion so your own placing
  never counts as a change.
- **One turn only.** TileLens maximizes this turn's score, with no strategy, rack-leave
  evaluation or opponent modeling.

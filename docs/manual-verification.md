# Permissions and manual verification

## Screen Recording permission (macOS)

TileLens reads the screen through macOS screen capture, which needs the Screen Recording
permission for the app that launches it. That is your terminal when you use `uv run tilelens`,
or TileLens.app for the packaged build.

1. Run `uv run tilelens doctor`. It prints `screen capture granted`, `denied` or `unknown`,
   along with versions and data folders.
2. If it is denied, open **System Settings > Privacy & Security > Screen & System Audio
   Recording** and turn on your terminal (Terminal, iTerm, or the editor you launch from) or
   TileLens.app.
3. Quit and reopen that app. macOS only applies the change to new processes.

While the permission is missing, TileLens shows **Game not visible: Screen Recording permission
is missing**, asks macOS to show its prompt once, and captures nothing. It checks again every
two seconds, so granting it while TileLens is open is picked up after a relaunch of the
terminal.

## What was verified during development

On an Apple M5 Pro with macOS 26.6 and Python 3.13.16:

- **Real screen capture.** With Screen Recording granted to the launching terminal, a capture
  of the fake game window matched the rendered frame to a mean pixel difference of 1.2.
- **Overlay exclusion.** In a 12-event on-screen run with the overlay drawing ghost tiles,
  sampled captures of the ghost-tile squares matched the game's own frame to within a mean
  difference of 2.2 over 34 checks. The overlay was not in the capture.
- **On-screen latency.** That run measured 509 ms median and 573 ms worst over five position
  changes.
  - The same run also found a rack letter misread, now fixed. Its latency numbers are kept
    because the fix does not touch timing.
  - Later on-screen runs could not happen: the display went to sleep, and TileLens does not
    wake or unlock the screen. The in-process numbers in `docs/performance.md` use the same
    pipeline apart from the capture call.
- **Sharing type.** A real Cocoa overlay window reported sharing type "none" after
  `exclude_from_capture`.

## Please verify by hand

1. **Permission flow.**
   - With Screen Recording off for your terminal, run `uv run tilelens`. You should see the
     permission banner and "Game not visible".
   - Grant the permission, reopen the terminal, and run `uv run tilelens doctor`. It should
     print `granted`.
2. **Your real game.**
   - Select the region around the board and rack.
   - Calibrate from two or three screens that together show many letters, and include a blank
     if you can.
   - Check that the cards and ghost tiles match what you expect. None of this has been tried
     on a real game.
3. **Overlay on your display.** On your own setup (including a second monitor or a scaled
   Retina mode, if you use them), check that:
   - ghost tiles sit on the right squares;
   - they move correctly when the game window moves or the display scale changes;
   - the board is not misread while the overlay is showing (no flips to Check tiles while
     nothing changes).
4. **Suppress mode.** If the overlay ever shows up in captures on your macOS version, switch
   the overlay mode to `suppress`. Check whether the brief hide on each capture causes visible
   flicker. If it does, use `panel_only`.
5. **Tentative tiles.** In your game, drag tiles onto the board without submitting. The panel
   should say "Waiting for board" and not suggest anything until you submit or recall.
6. **Resizing.** Resize the game window. TileLens should hold results with "Board size changed;
   recalibrate" until you recalibrate. The calibration dialog is pre-filled with the last
   confirmed board.
7. **Packaged app.** If you build TileLens.app (see `packaging/README.md`), it needs its own
   Screen Recording permission. Gatekeeper will warn, because the app is unsigned.

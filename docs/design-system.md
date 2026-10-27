# Design system: Liquid Glass

TileLens floats a thin layer of glass over a game that is being played against a clock. The
design serves that: the game stays the content, the glass stays readable and quiet.

## Principles

1. **Content first.** The board and rack are never covered when avoidable. The panel sits beside
   the board; the overlay draws only what the suggested play adds.
2. **Glass, not a window.** Translucent body over a native blur where the platform provides one,
   a more opaque body where it does not. A specular rim on the lit edge, a hairline on the
   other, two stacked shadows for depth. Corners are concentric.
3. **Quiet while playing.** Nothing loops while a result is showing. Changes take at most
   180 ms. A reduce-motion setting makes every transition instant.
4. **One accent.** Cyan blue marks the selected play and primary actions. Green, amber and red
   appear only as status.
5. **Always readable.** Fill strength follows the brightness behind the window (with
   hysteresis so it does not flicker). Body text meets 4.5:1 and large text 3:1 against the
   worst backdrop in the gallery, in both materials.
6. **One system.** Every window and dialog is built from `tilelens.ui.design`. No ad-hoc
   stylesheets, colors or fonts in surface code.

## Tokens (`ui/design/tokens.py`)

| Group | Values |
| --- | --- |
| Space | 4, 8, 12, 16, 20, 24 |
| Radius | control 12, card 18, sheet 22, panel 26 (a child is its parent's radius minus the gap) |
| Motion | fast 120 ms, base 180 ms, slow 280 ms; ease-out; instant with reduce motion |
| Type | caption 11 medium, footnote 12, body 13, label 13 semibold, headline 15 semibold, title 20 bold, word 19 bold, score 21 bold (tabular figures); the platform UI font |
| Materials | dark and light, each with body tint, solid fallback, rim light, hairline, shadow, text levels, control fills, divider, accent and status colors |

## Material (`ui/design/material.py`)

`paint_glass` draws body gradient, upper sheen and rim; `paint_shadow` draws the layered shadow
from nine-slice tiles rendered once per shape; `paint_wash` draws flat tinted panes for controls
inside glass. Repaints are blits and one gradient fill.

Native blur comes from the platform adapter (`apply_backdrop`): `NSVisualEffectView` on macOS,
DWM acrylic on Windows 11 and acrylic blur-behind on Windows 10. When the adapter returns False
the window paints the solid fallback body.

## Surfaces

- **Panel** (always-on-top, never takes focus): header with mark, status pill and quick
  controls; three play cards (rank, word, position and direction, score; the selected card
  shows its score breakdown); one banner at a time for notices; letter buttons when a tile
  needs an answer; footer with the age of the result. A compact mode keeps one play and
  arrows. It sits on the side of the board with the most free room and remembers where it
  was dragged.
- **Overlay** (click-through): smoked-glass ghost tiles for the new letters, a soft underline on
  existing letters the word uses, one score tag. Alignment guides and the rack outline fade in
  when the lock changes and fade out; they stay only while something needs attention. Tiles that
  were read are never highlighted.
- **Settings window**: grouped glass sections (overlay, appearance, game, shortcuts,
  calibration, diagnostics) and a first-run checklist.
- **Dialogs** (calibration, automatic calibration, check tiles, region): the same sheet,
  header, buttons and inputs.
- **Notifications**: toasts for confirmations, banners for things that persist.

# Word lists

TileLens ships no game's official word list. Suggestions can only be as good as the list behind
them: words the game rejects get suggested, and words it accepts get missed.

Validation has two layers, and every word a suggested move forms (the main word, any extension
and every crossing word) is checked against the combination of both:

1. a **baseline list**, a plain text file with a manifest saying where it came from;
2. **overrides**, a small file of words the game was seen to accept (`allow`) or reject
   (`block`), each with the evidence and the date. Overrides always win over the baseline.

## The baseline

The baseline is an assumption about the game's dictionary, not a fact. For Letter League the
working assumption is the U.S. Merriam-Webster *Official Scrabble Players Dictionary*. Nobody
has confirmed that Letter League uses it.

- **What was used before:** the macOS `/usr/share/dict/words`, which is Webster's Second
  International Dictionary (1934), 235,976 entries, reduced to 204,217 lowercase words of 2 to
  15 letters. It accepts many words no Scrabble dictionary has, such as UMU.
- **What is installed now:** ENABLE1, 172,823 words, by `tilelens words install-baseline`. It is
  downloaded from a pinned address and refused unless its SHA-256 matches
  (`3f16130220645692...`). ENABLE is a free list made for Scrabble-style games and is widely
  described as public domain; this project could not confirm the licence at its primary source.
- **How it differs from the requested dictionary:** the exact list is not available under a
  licence that lets a project like this ship it. ENABLE is older than the current Official
  Scrabble Players Dictionary, so it lacks newer entries (QI, ZA and ZEN are not in it) and may
  hold a few the newer dictionary dropped. It is not that dictionary and TileLens does not
  claim it is. No Collins/SOWPODS list and no general English dictionary was substituted.
- **If you have the exact list** (for example a licensed Official Scrabble Players Dictionary
  or NASPA word list file), install it instead with
  `tilelens words install-baseline --file path/to/list.txt --name ospd`, and point the
  profile's `words.path` at the result. Overrides stay as they are.

Check what is in use at any time:

```bash
uv run tilelens words status
uv run tilelens words check umu around qi
```

## Overrides

Overrides live next to the profile, as `<profile name>.overrides.json`, and never inside the
baseline, so replacing or updating the baseline keeps them. `profiles/letter-league.overrides.json`
is a starting file with UMU blocked; copy it next to your profile under the profile's name
(`my-profile.overrides.json`). To record what you see in the game:

```bash
uv run tilelens words block UMU --evidence "game rejected UMU played alone at H8, no other word formed"
uv run tilelens words allow QI --evidence "accepted at J4 across on turn 9; crossing word QIS"
uv run tilelens words forget QI
```

Evidence is required, and the date is today unless you pass `--date`. A running TileLens picks
the change up within seconds and refreshes its suggestions.

Record a word as blocked only when the game rejected the word itself. A placement can also be
refused because of where it sits or because of another word it forms, so check each word the
card lists ("Cross words: ...") before blaming one of them. Rejecting a play of several words
tells you that at least one is wrong, not which.

## Versions and caches

The version of the list in use names both parts: `<baseline hash>+<overrides hash>`. It is part
of every position identifier, and the cached search graph is keyed by the effective words, so
changing either the baseline or the overrides can never reuse an earlier result.

## Bring your own list

Any UTF-8 text file with one word per line works. Lines starting with `#` are ignored, case
does not matter, and anything after the first space on a line is dropped, so lists with
definitions or frequencies are fine.

```bash
uv run tilelens import-words ~/Downloads/my-words.txt --profile profiles/my-game.toml
```

This normalizes the list into the app data folder
(`~/Library/Application Support/tilelens/words/` on macOS), drops words with letters outside
the profile's alphabet, builds the search graph once and caches it
(`~/Library/Caches/tilelens/`). It prints the path to put in the profile:

```toml
[words]
path = "/Users/you/Library/Application Support/tilelens/words/my-words.txt"
```

You can also pick a list from the control panel. The list's content hash is part of every
position identifier, so changing the list never reuses stale results.

## How the search uses it

Words are loaded into a minimized word graph (a DAWG) once per list. The cached graph is
keyed by a hash of the playable words, so editing the list rebuilds it automatically.

# Word lists

TileLens does not ship any game's official word list. A suggestion can only be as good as
the list you give it: if your list contains words the game rejects, or lacks words the game
accepts, the ranking will be wrong for that game.

The only list in the repository is `profiles/demo_words.txt`, about 1,800 everyday words
typed by hand for the demo profiles and tests. It is part of this project and MIT licensed.

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

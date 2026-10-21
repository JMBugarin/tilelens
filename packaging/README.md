# Packaging a macOS app

`uv run tilelens` is the supported way to run TileLens. A standalone `TileLens.app` can also be
built with `pyside6-deploy`, which compiles the app with Nuitka. The result is **unsigned and
not notarized**. Signing and notarization were not done, because they need a paid Apple
Developer account.

## Build

`pyside6-deploy` installs Nuitka with `pip` into the active environment, and uv environments
have no `pip`. So build in a separate, pip-seeded environment:

```bash
uv venv --seed --python 3.13 /tmp/tilelens-build
uv pip install --python /tmp/tilelens-build/bin/python .
cd packaging
/tmp/tilelens-build/bin/pyside6-deploy -c pysidedeploy.spec --force
```

This produces `packaging/TileLens.app`. On an Apple M5 Pro the first build took about 80 seconds (36 seconds once Nuitka was installed), and
the app was about 220 MB. `pysidedeploy.spec` builds a standalone app and bundles the fictional
demo profiles (`--include-data-dir=../profiles=profiles`). The build needs Xcode's command line
tools for the C compiler.

## Checked on the build machine

- `TileLens.app/Contents/MacOS/tilelens_main --version`, `check-profile` and `doctor --solver`.
  The last one starts the solver worker process inside the frozen app.
- `TileLens.app/Contents/MacOS/tilelens_main demo` started the panel and fake game and was still running without errors after 10 seconds (offscreen).

These checks were not done:

- running the app on a different, clean Mac;
- a signed or notarized build;
- installing from a disk image.

## Opening an unsigned app (Gatekeeper)

Because the app is not signed, macOS blocks it the first time:

1. Try to open `TileLens.app` once, then dismiss the warning.
2. Open **System Settings > Privacy & Security**, scroll to the message about TileLens, and
   click **Open Anyway**. Confirm with your password.

Or, for an app you built yourself, remove the quarantine flag:

```bash
xattr -dr com.apple.quarantine packaging/TileLens.app
```

The app needs its own Screen Recording permission, separate from your terminal's. Enable
TileLens under **System Settings > Privacy & Security > Screen & System Audio Recording**, then
reopen the app.

## Licenses

The app bundles Qt and PySide6 under the LGPL-3.0. See `THIRD_PARTY_NOTICES.md` for what that
means if you hand the app to someone else.

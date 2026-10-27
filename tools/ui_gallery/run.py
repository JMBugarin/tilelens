"""Render every registered UI state to PNGs and an index.html contact sheet.

    uv run python tools/ui_gallery/run.py
    uv run python tools/ui_gallery/run.py --only panel/ --backdrop real --scale 2
    uv run python tools/ui_gallery/run.py --out build/after --compare build/before

Windows are rendered offscreen with QWidget.grab() and laid over backdrop images. A native blur
cannot be grabbed, so --blur approximates one under the glass of translucent windows. When the
application uses tilelens.ui.design the run mirrors it: see scheme.py.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import os
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

SCALES = (1.0, 1.25, 1.5, 2.0, 3.0)
MOTION_SETTLE_MS = 320


def parse(argv: list[str] | None) -> argparse.Namespace:
    from ui_gallery import scheme

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(ROOT / "build" / "ui-gallery"), help="output directory")
    ap.add_argument(
        "--only", action="append", help="render states whose name contains this (repeatable)"
    )
    ap.add_argument(
        "--backdrop",
        default="all",
        help="real, discord, gradient, a comma list of them, or all",
    )
    ap.add_argument(
        "--scale",
        type=float,
        choices=SCALES,
        default=2.0,
        help="device pixel ratio; 1.25 and 1.5 are common on Windows",
    )
    ap.add_argument(
        "--scheme",
        default="default",
        choices=(*scheme.SCHEMES, "both"),
        help="force the light or dark appearance; both renders each",
    )
    ap.add_argument(
        "--settle",
        type=int,
        default=None,
        help="ms to let animations finish (default 0 when the design package holds its motion "
        f"still, else {MOTION_SETTLE_MS})",
    )
    ap.add_argument(
        "--animate",
        action="store_true",
        help="leave the design's motion on instead of showing every state at rest",
    )
    ap.add_argument(
        "--blur",
        nargs="?",
        const="on",
        default="auto",
        choices=scheme.BLUR_MODES,
        help="on: windows act as if the platform blurred behind them and the blur is approximated "
        "under their glass; off: no blur, windows paint their opaque fallback. auto is on when "
        "the application uses tilelens.ui.design",
    )
    ap.add_argument(
        "--style",
        default=None,
        help="Qt widget style: fusion, native, or a QStyleFactory key (default: the design's "
        "GlassStyle when it is in use, else fusion). The macOS and Windows styles draw nothing "
        "for stock controls without a real window server",
    )
    ap.add_argument(
        "--app-style",
        default="auto",
        choices=scheme.STYLE_MODES,
        help="design: install the design's palette and stylesheet per scheme as the application "
        "does; plain: Qt only; auto: design when the application installs it",
    )
    ap.add_argument("--compare", help="an earlier output directory to show next to this one")
    ap.add_argument("--list", action="store_true", help="list the states and exit")
    ap.add_argument("--strict", action="store_true", help="exit 1 when any state fails")
    return ap.parse_args(argv)


def load_states() -> list[tuple[str, str]]:
    """Import every states_*.py; returns (module, error) for the ones that failed."""
    from ui_gallery import registry

    failed = []
    for path in sorted(HERE.glob("states_*.py")):
        known = set(registry.REGISTRY)
        try:
            importlib.import_module(f"ui_gallery.{path.stem}")
        except Exception:
            for name in set(registry.REGISTRY) - known:
                del registry.REGISTRY[name]
            failed.append((path.stem, traceback.format_exc(limit=-4)))
    return failed


def pick_backdrops(spec: str) -> list[str]:
    from ui_gallery import backdrops

    if spec == "all":
        return list(backdrops.NAMES)
    names = [n.strip() for n in spec.split(",") if n.strip()]
    bad = [n for n in names if n not in backdrops.NAMES]
    if bad or not names:
        raise SystemExit(f"unknown backdrop {bad or spec!r}; choose from {backdrops.NAMES} or all")
    return names


def materialize(state, obj, settle_ms: int):
    """Turn what a state returned into (stage size in points, layers, translucent)."""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QImage, QPixmap
    from PySide6.QtWidgets import QWidget

    from ui_gallery import compose, ctx
    from ui_gallery.compose import PAD, Layer, Stage

    if (
        isinstance(obj, tuple)
        and len(obj) == 2
        and isinstance(obj[0], QWidget)
        and not isinstance(obj[1], tuple)
    ):
        widget, scene = obj
        widget.show_scene(scene)
        obj = widget

    def ratio(img) -> float:
        return img.devicePixelRatio() or 1.0

    if isinstance(obj, Stage):
        layers = []
        for item in obj.items:
            thing, (x, y), *rest = item
            if isinstance(thing, QWidget):
                compose.present(thing, rest[0] if rest else None, settle_ms)
                click_through = bool(thing.windowFlags() & Qt.WindowType.WindowTransparentForInput)
                layers.append(Layer(compose.to_image(thing), QPointF(x, y), not click_through))
            else:
                layers.append(Layer(compose.to_image(thing), QPointF(x, y), True))
        return obj.size, layers, True

    if isinstance(obj, QWidget):
        if state.kind == "overlay":
            size = state.stage or ctx.scenario().size_pt
            compose.present(obj, size, settle_ms)
            return size, [Layer(compose.to_image(obj), QPointF(0, 0), False)], True
        compose.present(obj, None, settle_ms)
        img = compose.to_image(obj)
        w, h = round(img.width() / ratio(img)), round(img.height() / ratio(img))
        translucent = obj.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        size = state.stage or (w + 2 * PAD, h + 2 * PAD)
        pos = QPointF((size[0] - w) / 2, (size[1] - h) / 2)
        return size, [Layer(img, pos, True)], translucent

    if isinstance(obj, QImage | QPixmap):
        img = compose.to_image(obj)
        w, h = round(img.width() / ratio(img)), round(img.height() / ratio(img))
        size = (w + 2 * PAD, h + 2 * PAD)
        return size, [Layer(img, QPointF(PAD, PAD), True)], compose.has_alpha(img)

    raise TypeError(f"state {state.name} returned {type(obj).__name__}")


def file_for(name: str, backdrop: str, scheme: str) -> str:
    base, _, leaf = name.rpartition("/")
    tail = f"__{backdrop}" + ("" if scheme == "default" else f"__{scheme}")
    return f"{base}/{leaf}{tail}.png"


QT_MESSAGES: list[str] = []
RUN_NOTES: list[str] = []
PREVIOUS_HANDLER: list = []


def take_notes() -> None:
    """Qt warnings raised outside any state (a stylesheet that does not parse, say) go to the
    index instead of being lost between states."""
    RUN_NOTES.extend(m for m in dict.fromkeys(QT_MESSAGES) if m not in RUN_NOTES)
    QT_MESSAGES.clear()


def watch_modals(stuck: list[str]):
    """A state that opens a dialog or menu with exec() would wait for a click forever. Inside
    such a loop the timer still fires, dismisses the pop-up and records it in stuck."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    def release() -> None:
        app = QApplication.instance()
        if app is None or app.thread().loopLevel() == 0:
            return
        popup = QApplication.activeModalWidget() or QApplication.activePopupWidget()
        if popup is not None:
            stuck.append(type(popup).__name__)
            popup.close()

    timer = QTimer()
    timer.setInterval(30)
    timer.timeout.connect(release)
    timer.start()
    return timer


def dispose(app, before: set) -> None:
    """Close and really delete what a state left behind: with no event loop running, deleteLater
    alone never fires, and the hidden windows would keep their timers alive for the next states."""
    from PySide6.QtCore import QCoreApplication, QEvent

    for w in set(app.topLevelWidgets()) - before:
        with contextlib.suppress(RuntimeError):
            w.close()
            w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def render_all(
    args: argparse.Namespace, settle_ms: int, blur: bool
) -> tuple[list[dict], list[dict], list[str]]:
    from PySide6.QtWidgets import QApplication

    from ui_gallery import compose, ctx, registry, scheme

    app = QApplication.instance()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    chosen = pick_backdrops(args.backdrop)
    states = registry.select(args.only)
    schemes = ["light", "dark"] if args.scheme == "both" else [args.scheme]
    scale = app.primaryScreen().devicePixelRatio()
    records: list[dict] = []
    failures: list[dict] = []
    raised: list[str] = []
    stuck: list[str] = []
    watch = watch_modals(stuck)

    def collect(kind, value, tb) -> None:
        raised.append("".join(traceback.format_exception(kind, value, tb, limit=-4)).rstrip())

    previous_hook = sys.excepthook
    sys.excepthook = collect
    try:
        for sch in schemes:
            scheme.apply(sch)
            take_notes()
            for state in states:
                started = time.perf_counter()
                before = set(app.topLevelWidgets())
                raised.clear()
                stuck.clear()
                QT_MESSAGES.clear()
                made: list[dict] = []
                error = None
                try:
                    built = state.build()
                    scheme.force_windows(set(app.topLevelWidgets()) - before, sch)
                    settle = settle_ms if state.settle is None else state.settle
                    size, layers, translucent = materialize(state, built, settle)
                    targets = [None]
                    if translucent:
                        targets = [
                            b for b in chosen if state.backdrops is None or b in state.backdrops
                        ]
                    for target in targets:
                        if target is None:
                            img = layers[0].image
                            name = "none"
                        else:
                            img = compose.composite(size, layers, target, scale, blur)
                            name = target
                        rel = file_for(state.name, name, sch)
                        dest = out / rel
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        if not img.save(str(dest), "PNG"):
                            raise OSError(f"could not write {dest}")
                        ratio = img.devicePixelRatio() or 1.0
                        made.append(
                            {
                                "state": state.name,
                                "kind": state.kind,
                                "doc": state.doc,
                                "backdrop": name,
                                "scheme": sch,
                                "file": rel,
                                "size_px": [img.width(), img.height()],
                                "size_pt": [
                                    round(img.width() / ratio),
                                    round(img.height() / ratio),
                                ],
                            }
                        )
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"
                    raised.insert(0, traceback.format_exc(limit=-4).rstrip())
                finally:
                    ctx.run_deferred()
                    dispose(app, before)
                if stuck:
                    raised.append(
                        f"{', '.join(dict.fromkeys(stuck))} was run with exec() and dismissed "
                        "by the gallery; show it with show() instead"
                    )
                notes = list(dict.fromkeys(QT_MESSAGES))[:6]
                for rec in made:
                    if notes:
                        rec["qt"] = notes
                records.extend(made)
                if raised:
                    text = "\n--\n".join(raised)
                    if error is None:
                        error = f"{len(raised)} exception(s) inside Qt callbacks"
                    failures.append({"state": state.name, "scheme": sch, "error": text})
                    print(f"FAIL  {state.name:<34} {sch:<8} {error}")
                else:
                    extra = f" +{len(notes)} Qt message(s)" if notes else ""
                    print(
                        f"ok    {state.name:<34} {sch:<8} {len(made)} image(s) "
                        f"{time.perf_counter() - started:5.2f}s{extra}"
                    )
    finally:
        watch.stop()
        sys.excepthook = previous_hook
        scheme.apply("default")
    return records, failures, list(RUN_NOTES)


QUIET = (
    "does not support raise()",
    "does not support propagateSizeHints()",
    "does not support grabbing the keyboard",
)


def quiet_qt() -> None:
    from PySide6.QtCore import QtMsgType, qInstallMessageHandler

    loud = (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg)

    def handler(kind, context, message) -> None:
        if any(text in message for text in QUIET):
            return
        print(message, file=sys.stderr)
        if kind in loud:
            QT_MESSAGES.append(message)

    PREVIOUS_HANDLER[:] = [qInstallMessageHandler(handler)]


def main(argv: list[str] | None = None) -> int:
    args = parse(argv)
    from PySide6.QtWidgets import QApplication

    from ui_gallery import scheme

    QT_MESSAGES.clear()
    RUN_NOTES.clear()
    quiet_qt()

    env_before = {}
    if QApplication.instance() is None:
        wanted = [("QT_QPA_PLATFORM", "offscreen"), ("QT_SCALE_FACTOR", str(args.scale))]
        if "QT_LOGGING_RULES" not in os.environ:
            wanted.append(("QT_LOGGING_RULES", "qt.qpa.fonts.warning=false"))
        for key, value in wanted:
            env_before[key] = os.environ.get(key)
            os.environ[key] = value
        QApplication(["ui-gallery"])
        for key, value in env_before.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    elif args.scale != QApplication.primaryScreen().devicePixelRatio():
        print("a Qt application already runs; keeping its scale", file=sys.stderr)

    home = tempfile.mkdtemp(prefix="ui-gallery-home-")
    saved = os.environ.get("TILELENS_HOME")
    os.environ["TILELENS_HOME"] = home
    app = QApplication.instance()
    try:
        broken = load_states()
        from ui_gallery import registry, sheet

        if args.list:
            for module, err in broken:
                print(f"(module {module} failed)\n{err}", file=sys.stderr)
            for state in registry.select(args.only):
                print(f"{state.name:<36} {state.kind:<8} {state.doc}")
            return 0
        if not registry.select(args.only):
            print(f"no state matches {args.only}; try --list", file=sys.stderr)
            return 2
        decided = scheme.begin(
            app,
            style=args.style,
            app_style=args.app_style,
            blur=args.blur,
            animate=args.animate,
        )
        settle_ms = args.settle
        if settle_ms is None:
            settle_ms = 0 if decided["reduce_motion"] else MOTION_SETTLE_MS
        take_notes()
        flag = {True: "on", False: "off"}
        print(
            f"design {flag[decided['design']]}, blur {flag[decided['blur']]}, "
            f"motion {flag[decided['motion']]}, scale {app.primaryScreen().devicePixelRatio():g}, "
            f"settle {settle_ms} ms"
        )
        records, failures, notes = render_all(args, settle_ms, decided["blur"])
        for module, err in broken:
            failures.append({"state": f"(module {module})", "scheme": "-", "error": err})
        out = Path(args.out)
        compare = None
        if args.compare:
            compare = Path(args.compare).resolve()
            if not compare.is_dir():
                print(f"--compare {args.compare}: not a directory; ignored", file=sys.stderr)
                compare = None
            elif compare == out.resolve():
                print("--compare is the output directory, already overwritten; ignored")
                compare = None
        (out / "manifest.json").write_text(
            json.dumps({"images": records, "failures": failures, "notes": notes}, indent=2),
            encoding="utf-8",
        )
        index = sheet.write_index(out, records, failures, compare, notes)
        extra = f", {len(notes)} Qt warning(s) outside the states" if notes else ""
        print(f"\n{len(records)} images, {len(failures)} failed{extra} -> {index}")
        return 1 if failures and args.strict else 0
    finally:
        from PySide6.QtCore import qInstallMessageHandler

        from ui_gallery import ctx

        ctx.shutdown()
        scheme.end()
        qInstallMessageHandler(PREVIOUS_HANDLER[0] if PREVIOUS_HANDLER else None)
        RUN_NOTES.clear()
        if saved is None:
            os.environ.pop("TILELENS_HOME", None)
        else:
            os.environ["TILELENS_HOME"] = saved
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())

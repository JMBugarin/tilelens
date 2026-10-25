import argparse
import sys
from pathlib import Path

from tilelens import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tilelens")
    parser.add_argument("--version", action="version", version=f"tilelens {__version__}")
    sub = parser.add_subparsers(dest="command")

    check = sub.add_parser("check-profile", help="validate a profile file")
    check.add_argument("path")

    solve = sub.add_parser("solve", help="find the top placements for a board written as text")
    solve.add_argument("--profile", required=True)
    solve.add_argument("--words", help="word list (defaults to the profile's words.path)")
    solve.add_argument(
        "--board",
        required=True,
        help="file with one row per line: '.' empty, uppercase tile, lowercase blank",
    )
    solve.add_argument("--rack", required=True, help="rack letters, '?' for a blank")
    solve.add_argument("--top", type=int, default=3)

    cal = sub.add_parser("calibrate", help="learn a game's look from screenshots you label")
    cal.add_argument("--profile", required=True)
    cal.add_argument("--image", action="append", required=True, help="screenshot (repeatable)")
    cal.add_argument(
        "--board", action="append", required=True, help="text file with that screenshot's board"
    )
    cal.add_argument("--rack", action="append", required=True, help="rack letters, left to right")
    cal.add_argument("--rect", required=True, help="rough board grid rectangle x,y,w,h in pixels")
    cal.add_argument("--out", required=True)

    read = sub.add_parser("read", help="read a screenshot with a saved calibration")
    read.add_argument("--profile", required=True)
    read.add_argument("--calibration", required=True)
    read.add_argument("--image", required=True)

    doc = sub.add_parser("doctor", help="check permissions, platform support and versions")
    doc.add_argument("--solver", action="store_true", help="also start the solver worker")
    scr = sub.add_parser(
        "check-screen",
        help="read the game on screen (or an image) and time every step, without the interface",
    )
    scr.add_argument("--image", help="read this screenshot instead of capturing the screen")
    scr.add_argument("--profile", help="profile to use (defaults to the one in your settings)")
    sub.add_parser("demo", help="run against the built-in fake game (no screen capture needed)")

    imp = sub.add_parser("import-words", help="normalize a word list into the app data folder")
    imp.add_argument("path")
    imp.add_argument("--profile", help="filter to this profile's alphabet and pre-build the graph")
    imp.add_argument("--name")
    return parser


def cmd_check_profile(args) -> int:
    from tilelens.rules.profile import ProfileError, load_profile

    try:
        p = load_profile(args.path)
    except ProfileError as exc:
        print("invalid profile:", file=sys.stderr)
        for err in exc.errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    blanks = f"{p.blanks} blank{'s' if p.blanks != 1 else ''}"
    print(f"ok: {p.name} ({p.width}x{p.height}, rack {p.rack_size}, {blanks})")
    return 0


def format_move(i: int, move, label: str) -> str:
    lines = [
        f"{i}. {move.word}  {move.score} pts  row {move.row + 1}, col {move.col + 1}, "
        f"{'across' if move.direction == 'H' else 'down'}  ({label})",
        "   tiles: " + " ".join(str(t) for t in move.tiles),
    ]
    for w in move.breakdown.words:
        mult = f" x{w.word_multiplier}" if w.word_multiplier != 1 else ""
        lines.append(f"   {w.text}: {w.letter_sum}{mult} = {w.total}")
    if move.breakdown.bonus:
        lines.append(f"   full-rack bonus: {move.breakdown.bonus}")
    return "\n".join(lines)


def cmd_solve(args) -> int:
    from tilelens.rules.board import Board, parse_rack
    from tilelens.rules.lexicon import WordListError, lexicon_for
    from tilelens.rules.profile import ProfileError, load_profile
    from tilelens.solver.movegen import MoveGenerator

    try:
        profile = load_profile(args.profile)
        lexicon = lexicon_for(profile, args.words)
        rows = [ln.strip() for ln in Path(args.board).read_text().splitlines() if ln.strip()]
        board = Board.from_rows(rows)
        rack = parse_rack(args.rack)
    except (ProfileError, WordListError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if (board.width, board.height) != (profile.width, profile.height):
        print("error: board size does not match the profile", file=sys.stderr)
        return 1
    gen = MoveGenerator(profile, lexicon, use_cache=True)
    result = gen.solve(board, rack, top_k=args.top)
    if not result.moves:
        print("no legal placements")
    for i, move in enumerate(result.moves, 1):
        print(format_move(i, move, result.label))
    print(f"searched in {result.elapsed * 1000:.1f} ms")
    return 0


def cmd_calibrate(args) -> int:
    import cv2

    from tilelens.rules.profile import ProfileError, load_profile
    from tilelens.vision.grid import GridNotFound
    from tilelens.vision.recognizer import CalibrationError, calibrate

    if not (len(args.image) == len(args.board) == len(args.rack)):
        print("error: give one --board and one --rack per --image", file=sys.stderr)
        return 1
    try:
        profile = load_profile(args.profile)
        rect = tuple(float(v) for v in args.rect.split(","))
        if len(rect) != 4:
            raise ValueError("--rect needs four numbers")
        samples = []
        for image, board, rack in zip(args.image, args.board, args.rack, strict=True):
            img = cv2.imread(image)
            if img is None:
                raise ValueError(f"cannot read image {image}")
            rows = [ln.strip() for ln in Path(board).read_text().splitlines() if ln.strip()]
            samples.append((img, rows, rack))
        cal = calibrate(samples, profile, board_hint=rect)
    except (ProfileError, CalibrationError, GridNotFound, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    cal.save(args.out)
    print(f"saved calibration with {len(cal.templates)} letters to {args.out}")
    return 0


def cmd_read(args) -> int:
    import cv2

    from tilelens.rules.board import rack_to_str
    from tilelens.rules.profile import load_profile
    from tilelens.vision.recognizer import Calibration, Recognizer

    profile = load_profile(args.profile)
    rec = Recognizer(Calibration.load(args.calibration), profile)
    img = cv2.imread(args.image)
    if img is None:
        print(f"error: cannot read image {args.image}", file=sys.stderr)
        return 1
    reading = rec.read(img)
    if not reading.visible:
        print("board not visible")
        return 2
    for row in reading.cells:
        line = ""
        for c in row:
            if c.uncertain:
                line += "!"
            elif c.letter is None:
                line += "."
            else:
                line += c.letter.lower() if c.blank else c.letter
        print(line)
    print(
        "rack:",
        rack_to_str(reading.rack_counter()),
        "(uncertain)" if reading.rack_uncertain else "",
    )
    for r, c in reading.uncertain_cells:
        print(f"check row {r + 1}, col {c + 1}: {reading.cells[r][c].reason}")
    for w in reading.warnings:
        print("warning:", w)
    print(f"read in {reading.elapsed * 1000:.1f} ms")
    return 0 if reading.confident else 3


def cmd_check_screen(args) -> int:
    """What TileLens sees right now, step by step, with timings: the first thing to run when
    something reads wrong."""
    import time

    import cv2

    from tilelens.rules.lexicon import lexicon_for
    from tilelens.rules.profile import demo_profile_dir, load_profile
    from tilelens.state.controller import LiveController, Status
    from tilelens.state.worker import InlineSolver
    from tilelens.ui.settings import Settings
    from tilelens.vision.autoread import NeedsLetters, ViewReader, calibrate_unaided
    from tilelens.vision.recognizer import Calibration, CalibrationError

    settings = Settings.load()
    profile = load_profile(args.profile or settings.profile or demo_profile_dir() / "x.toml")
    if profile.appearance is None:
        print("check-screen needs a profile with an [appearance] section", file=sys.stderr)
        return 1
    if args.image:
        frame = cv2.imread(args.image)
        if frame is None:
            print(f"error: cannot read image {args.image}", file=sys.stderr)
            return 1
        grab = lambda: frame  # noqa: E731
    else:
        from tilelens.platforms import get_adapter
        from tilelens.platforms.base import Region

        adapter = get_adapter()
        import mss

        with mss.mss() as sct:
            mon = sct.monitors[1]
        region = Region(mon["left"], mon["top"], mon["width"], mon["height"])
        grab = lambda: adapter.capture(region)  # noqa: E731
        frame = grab()
    print(f"frame {frame.shape[1]}x{frame.shape[0]}")
    cal = None
    if settings.calibration and Path(settings.calibration).is_file():
        cal = Calibration.load(settings.calibration)
    t0 = time.perf_counter()
    try:
        if cal is None or cal.profile_fingerprint != profile.fingerprint():
            cal = calibrate_unaided(frame, profile)
            print(f"calibrated without typing in {(time.perf_counter() - t0) * 1000:.0f} ms")
    except NeedsLetters as exc:
        print(f"needs a human: tiles {[i + 1 for i in exc.unsure]} (guess {exc.guess})")
        return 3
    except CalibrationError as exc:
        print(f"calibration failed: {exc}")
        return 3
    reader = ViewReader(cal, profile)
    t0 = time.perf_counter()
    reading = reader.read(frame)
    first = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    reader.read(frame)
    again = (time.perf_counter() - t0) * 1000
    if not reading.visible:
        print("board not found:", "; ".join(reading.warnings))
        return 2
    g = reading.grid
    seen = int(reading.view.visible.sum())
    print(f"board: pitch {g.pitch_x:.1f}px, {seen} of {profile.width * profile.height} in view")
    print(f"locate+read {first:.0f} ms, read when aligned {again:.0f} ms")
    rack = [
        ("?" if t.blank else t.letter or "_") + ("!" if t.uncertain else "") for t in reading.rack
    ]
    print("rack:", " ".join(rack), f"({len(reading.rack)} tiles)")
    unsure = [(r, c) for r, c in reading.uncertain_cells]
    print(f"unclear squares: {len(unsure)}")
    lexicon = lexicon_for(profile, settings.words)
    from tilelens.solver.movegen import MoveGenerator

    class Src:
        def grab(self):
            return grab()

    now = [0.0]
    ctrl = LiveController(
        Src(), reader, InlineSolver(MoveGenerator(profile, lexicon, use_cache=True)), profile,
        lexicon.version, clock=lambda: now[0], lexicon=lexicon,
    )  # fmt: skip
    t0 = time.perf_counter()
    for _ in range(60):
        now[0] += 0.1
        ctrl.tick()
        if ctrl.snapshot.status in (Status.READY, Status.CHECK, Status.NOT_VISIBLE):
            break
    total = (time.perf_counter() - t0) * 1000
    snap = ctrl.snapshot
    print(f"settled to {snap.status.value} in {total:.0f} ms of compute")
    if snap.notice:
        print("notice:", snap.notice)
    for i, m in enumerate(snap.result.moves if snap.result else [], 1):
        print(f"{i}. {m.word} {m.score} pts")
    return 0


def versions() -> dict[str, str]:
    import platform
    from importlib.metadata import PackageNotFoundError, version

    out = {"tilelens": __version__, "python": platform.python_version(), "os": platform.platform()}
    for pkg in ("pyside6-essentials", "opencv-python-headless", "numpy", "mss"):
        try:
            out[pkg] = version(pkg)
        except PackageNotFoundError:
            out[pkg] = "missing"
    return out


def cmd_doctor(args) -> int:
    from tilelens.paths import data_dir
    from tilelens.platforms import UnsupportedPlatform, get_adapter
    from tilelens.solver.dawg import cache_dir

    for key, value in versions().items():
        print(f"{key:24s} {value}")
    print(f"{'data folder':24s} {data_dir()}")
    print(f"{'cache folder':24s} {cache_dir()}")
    try:
        adapter = get_adapter()
    except UnsupportedPlatform as exc:
        print(f"platform: {exc}")
        return 2
    if getattr(args, "solver", False):
        import time

        from tilelens.rules.profile import demo_profile_dir
        from tilelens.state.worker import ProcessSolver

        t0 = time.perf_counter()
        solver = ProcessSolver(demo_profile_dir() / "demo_pocket.toml")
        ok = solver.wait_ready(60)
        result = None
        if ok:
            solver.submit("doctor", ["." * 9] * 9, "TONES")
            end = time.monotonic() + 30
            while result is None and time.monotonic() < end:
                outs = solver.poll()
                result = outs[0].result if outs else None
                time.sleep(0.01)
        solver.close()
        elapsed = (time.perf_counter() - t0) * 1000
        status = f"ok ({elapsed:.0f} ms)" if result and result.moves else f"failed {solver.fatal}"
        print(f"{'solver worker':24s} {status}")
    print(f"{'platform adapter':24s} {adapter.name}")
    allowed = adapter.screen_permission()
    status = {True: "granted", False: "denied", None: "unknown"}[allowed]
    print(f"{'screen capture':24s} {status}")
    if allowed is False:
        print()
        print(adapter.permission_help())
        adapter.close()
        return 1
    adapter.close()
    return 0


def cmd_import_words(args) -> int:
    from tilelens.rules.lexicon import WordListError
    from tilelens.rules.profile import ProfileError, load_profile
    from tilelens.solver.wordlists import import_word_list

    try:
        profile = load_profile(args.profile) if args.profile else None
        report = import_word_list(args.path, profile, args.name)
    except (ProfileError, WordListError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"imported {report.words} words ({report.skipped} skipped) to {report.path}")
    print(f'set  words.path = "{report.path}"  in your profile')
    return 0


def main(argv: list[str] | None = None) -> int:
    import multiprocessing

    multiprocessing.freeze_support()
    args = build_parser().parse_args(argv)
    if args.command == "check-profile":
        return cmd_check_profile(args)
    if args.command == "solve":
        return cmd_solve(args)
    if args.command == "import-words":
        return cmd_import_words(args)
    if args.command == "calibrate":
        return cmd_calibrate(args)
    if args.command == "read":
        return cmd_read(args)
    if args.command == "doctor":
        return cmd_doctor(args)
    if args.command == "check-screen":
        return cmd_check_screen(args)
    if args.command == "demo":
        from tilelens.ui.app_main import run

        return run(demo=True)
    from tilelens.ui.app_main import run

    return run()


if __name__ == "__main__":
    sys.exit(main())

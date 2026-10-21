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
    print(f"ok: {p.name} ({p.width}x{p.height}, rack {p.rack_size}, {p.blanks} blanks)")
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
    if args.command == "demo":
        from tilelens.ui.app_main import run

        return run(demo=True)
    from tilelens.ui.app_main import run

    return run()


if __name__ == "__main__":
    sys.exit(main())

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
    args = build_parser().parse_args(argv)
    if args.command == "check-profile":
        return cmd_check_profile(args)
    if args.command == "solve":
        return cmd_solve(args)
    if args.command == "import-words":
        return cmd_import_words(args)
    print("tilelens: the desktop app is not available yet")
    return 0


if __name__ == "__main__":
    sys.exit(main())

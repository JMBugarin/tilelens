import argparse
import sys

from tilelens import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tilelens")
    parser.add_argument("--version", action="version", version=f"tilelens {__version__}")
    sub = parser.add_subparsers(dest="command")
    check = sub.add_parser("check-profile", help="validate a profile file")
    check.add_argument("path")
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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "check-profile":
        return cmd_check_profile(args)
    print("tilelens: nothing to run yet")
    return 0


if __name__ == "__main__":
    sys.exit(main())

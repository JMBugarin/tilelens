import argparse
import sys

from tilelens import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tilelens")
    parser.add_argument("--version", action="version", version=f"tilelens {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    print("tilelens: nothing to run yet")
    return 0


if __name__ == "__main__":
    sys.exit(main())

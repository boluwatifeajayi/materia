"""Command line entrypoint.

`audit` lands in T17 and `trace render` in T24. What is here now is the corpus
build, which `make corpus` and `make corpus-check` call.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from materia import __version__

DEFAULT_CORPUS = Path("corpus")


def _build_corpus(arguments: argparse.Namespace) -> int:
    from materia.corpus.build import build_corpus

    manifest = build_corpus(arguments.directory)
    workbooks = manifest["workbooks"]
    print(f"{len(workbooks)} workbooks written to {arguments.directory}")
    for entry in workbooks:
        breaks = entry["legitimate_breaks"]
        note = f", {len(breaks)} legitimate pattern breaks" if breaks else ""
        print(f"  {entry['id']}  {entry['role']:14} {entry['formula_count']} formulas{note}")
    print(f"manifest and checksums written to {arguments.directory}")
    return 0


def _check_corpus(arguments: argparse.Namespace) -> int:
    from materia.corpus.build import check_corpus

    try:
        result = check_corpus(arguments.directory)
    except FileNotFoundError as missing:
        print(f"no checksums at {missing}. Run make corpus first.", file=sys.stderr)
        return 1

    if result.ok:
        print(f"{len(result.matched)} workbooks match the committed checksums")
        return 0

    for name in result.missing:
        print(f"missing: {name}", file=sys.stderr)
    for name in result.mismatched:
        print(f"differs from the committed checksum: {name}", file=sys.stderr)
    print(
        "\nA mismatch usually means a different openpyxl version. "
        "See docs/REPRODUCTION.md section 10.",
        file=sys.stderr,
    )
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="materia", description=__doc__)
    parser.add_argument("-V", "--version", action="version", version=f"materia {__version__}")
    commands = parser.add_subparsers(dest="command")

    corpus = commands.add_parser("corpus", help="generate or verify the evaluation corpus")
    actions = corpus.add_subparsers(dest="action", required=True)

    build = actions.add_parser("build", help="generate all twelve workbooks")
    build.add_argument("--directory", type=Path, default=DEFAULT_CORPUS)
    build.set_defaults(handler=_build_corpus)

    check = actions.add_parser("check", help="compare workbooks against the checksums")
    check.add_argument("--directory", type=Path, default=DEFAULT_CORPUS)
    check.set_defaults(handler=_check_corpus)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(sys.argv[1:] if argv is None else argv)
    handler = getattr(arguments, "handler", None)
    if handler is None:
        parser.print_help(sys.stderr)
        return 1
    return handler(arguments)


if __name__ == "__main__":
    raise SystemExit(main())

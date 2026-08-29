"""Command line entrypoint.

Stub. The audit pipeline is wired up in T17 and the trace renderer in T24.
"""

import sys

from materia import __version__


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv

    if argv and argv[0] in ("-V", "--version"):
        print(f"materia {__version__}")
        return 0

    print(f"materia {__version__}: no commands are implemented yet.", file=sys.stderr)
    print("The audit command lands in T17. See TASKS.md.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

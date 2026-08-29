"""Tokeniser for the supported formula grammar.

Anything the grammar does not cover is refused here rather than passed on.
See README section 6.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from materia.parse import REFERENCE, STRING_LITERAL


class UnsupportedFormula(ValueError):
    """The formula is outside the grammar Materia can evaluate faithfully.

    Raised rather than degrading, guessing, or returning a partial tree. A
    wrong evaluation is worse than a refusal, because it produces a confident
    number nobody can tell is wrong.

    `function` is set when the cause was a function outside the grammar, so
    preflight can name it in its reason code. It is None for a syntax error,
    which is a different problem and gets a different code.
    """

    def __init__(self, message: str, *, function: str | None = None) -> None:
        super().__init__(message)
        self.function = function


@dataclass(frozen=True)
class Token:
    kind: str
    text: str
    position: int


# Order matters. A reference is tried before a bare identifier because A1 is
# both. The reference pattern already refuses to match a name followed by "(",
# so LOG10( falls through to FUNCTION and is refused there by name.
_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("SPACE", re.compile(r"\s+")),
    ("STRING", STRING_LITERAL),
    ("REFERENCE", REFERENCE),
    ("FUNCTION", re.compile(r"[A-Za-z_][A-Za-z0-9_.]*(?=\s*\()")),
    ("BOOLEAN", re.compile(r"(?:TRUE|FALSE)\b", re.IGNORECASE)),
    ("NAME", re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")),
    ("NUMBER", re.compile(r"(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")),
    ("OPERATOR", re.compile(r"<>|<=|>=|[+\-*/^=<>]")),
    ("PERCENT", re.compile(r"%")),
    ("LPAREN", re.compile(r"\(")),
    ("RPAREN", re.compile(r"\)")),
    ("COMMA", re.compile(r",")),
]


def tokenise(formula: str) -> list[Token]:
    """Split a formula into tokens, or raise UnsupportedFormula."""
    text = formula[1:] if formula.startswith("=") else formula
    if not text.strip():
        raise UnsupportedFormula(f"{formula!r} is empty")

    tokens: list[Token] = []
    position = 0
    while position < len(text):
        for kind, pattern in _RULES:
            match = pattern.match(text, position)
            if match is None:
                continue
            if kind == "SPACE":
                position = match.end()
                break
            if kind == "NAME":
                # An identifier that is not a function call and not a boolean
                # is a defined name or a typo. Either way it is not something
                # we can resolve to a value.
                raise UnsupportedFormula(
                    f"{formula!r} uses {match.group(0)!r}, which is not a cell "
                    "reference, a function or a literal"
                )
            tokens.append(Token(kind, match.group(0), position))
            position = match.end()
            break
        else:
            raise UnsupportedFormula(
                f"{formula!r} contains {text[position]!r} at position "
                f"{position}, which is outside the supported grammar"
            )

    return tokens

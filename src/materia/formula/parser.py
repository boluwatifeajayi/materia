"""Recursive descent parser for the supported formula grammar.

Precedence follows Excel, which is not the same as most languages. Unary minus
binds tighter than exponentiation, so `-2^2` is 4 rather than -4, and `^` is
left associative, so `2^3^2` is 64 rather than 512. Getting either wrong would
put a silently incorrect number into every impact figure downstream.

Loosest to tightest:

    comparison        =  <>  <  <=  >  >=
    additive          +  -
    multiplicative    *  /
    exponent          ^                     left associative
    unary             -  +
    postfix           %
    primary           literal, reference, ( ... ), function call
"""

from __future__ import annotations

from materia.formula.lexer import Token, UnsupportedFormula, tokenise
from materia.formula.nodes import (
    BinaryOp,
    Boolean,
    CellRef,
    FunctionCall,
    Node,
    Number,
    Percent,
    RangeRef,
    Text,
    UnaryOp,
)
from materia.parse import parse_reference

# README section 6. Argument counts are checked here rather than left to the
# recompute engine, so a malformed call is refused instead of being guessed at.
# None as an upper bound means variadic.
FUNCTIONS: dict[str, tuple[int, int | None]] = {
    "SUM": (1, None),
    "AVERAGE": (1, None),
    "MIN": (1, None),
    "MAX": (1, None),
    "IF": (2, 3),
    "ROUND": (2, 2),
    "ABS": (1, 1),
    "SUMIF": (2, 3),
}

SUPPORTED_FUNCTIONS = frozenset(FUNCTIONS)

_COMPARISON = ("=", "<>", "<", "<=", ">", ">=")


def parse_formula(formula: str) -> Node:
    """Parse a formula into an AST, or raise UnsupportedFormula."""
    return _Parser(tokenise(formula), formula).parse()


class _Parser:
    def __init__(self, tokens: list[Token], formula: str) -> None:
        self._tokens = tokens
        self._formula = formula
        self._index = 0

    # --- token handling ---

    def _peek(self) -> Token | None:
        return self._tokens[self._index] if self._index < len(self._tokens) else None

    def _advance(self) -> Token:
        token = self._tokens[self._index]
        self._index += 1
        return token

    def _at_operator(self, *texts: str) -> bool:
        token = self._peek()
        return token is not None and token.kind == "OPERATOR" and token.text in texts

    def _at(self, kind: str) -> bool:
        token = self._peek()
        return token is not None and token.kind == kind

    def _expect(self, kind: str) -> Token:
        token = self._peek()
        if token is None:
            raise UnsupportedFormula(
                f"{self._formula!r} ends unexpectedly, expected {kind.lower()}"
            )
        if token.kind != kind:
            raise UnsupportedFormula(
                f"{self._formula!r} has {token.text!r} at position "
                f"{token.position} where {kind.lower()} was expected"
            )
        return self._advance()

    # --- grammar ---

    def parse(self) -> Node:
        node = self._comparison()
        remaining = self._peek()
        if remaining is not None:
            raise UnsupportedFormula(
                f"{self._formula!r} has trailing {remaining.text!r} at position "
                f"{remaining.position}"
            )
        return node

    def _comparison(self) -> Node:
        left = self._additive()
        while self._at_operator(*_COMPARISON):
            operator = self._advance().text
            left = BinaryOp(operator, left, self._additive())
        return left

    def _additive(self) -> Node:
        left = self._multiplicative()
        while self._at_operator("+", "-"):
            operator = self._advance().text
            left = BinaryOp(operator, left, self._multiplicative())
        return left

    def _multiplicative(self) -> Node:
        left = self._exponent()
        while self._at_operator("*", "/"):
            operator = self._advance().text
            left = BinaryOp(operator, left, self._exponent())
        return left

    def _exponent(self) -> Node:
        left = self._unary()
        while self._at_operator("^"):
            self._advance()
            left = BinaryOp("^", left, self._unary())
        return left

    def _unary(self) -> Node:
        if self._at_operator("+", "-"):
            operator = self._advance().text
            return UnaryOp(operator, self._unary())
        return self._postfix()

    def _postfix(self) -> Node:
        node = self._primary()
        while self._at("PERCENT"):
            self._advance()
            node = Percent(node)
        return node

    def _primary(self) -> Node:
        token = self._peek()
        if token is None:
            raise UnsupportedFormula(f"{self._formula!r} ends unexpectedly")

        if token.kind == "NUMBER":
            return Number(float(self._advance().text))

        if token.kind == "STRING":
            raw = self._advance().text
            return Text(raw[1:-1].replace('""', '"'))

        if token.kind == "BOOLEAN":
            return Boolean(self._advance().text.upper() == "TRUE")

        if token.kind == "REFERENCE":
            return _reference_node(self._advance().text)

        if token.kind == "LPAREN":
            self._advance()
            node = self._comparison()
            self._expect("RPAREN")
            return node

        if token.kind == "FUNCTION":
            return self._function_call()

        raise UnsupportedFormula(
            f"{self._formula!r} has {token.text!r} at position {token.position} "
            "where a value was expected"
        )

    def _function_call(self) -> Node:
        name_token = self._advance()
        name = name_token.text.upper()

        if name not in FUNCTIONS:
            raise UnsupportedFormula(
                f"{self._formula!r} uses {name}, which is outside the "
                "supported grammar in README section 6",
                function=name,
            )

        self._expect("LPAREN")
        arguments: list[Node] = []
        if not self._at("RPAREN"):
            arguments.append(self._comparison())
            while self._at("COMMA"):
                self._advance()
                arguments.append(self._comparison())
        self._expect("RPAREN")

        minimum, maximum = FUNCTIONS[name]
        if len(arguments) < minimum or (maximum is not None and len(arguments) > maximum):
            expected = (
                f"at least {minimum}" if maximum is None
                else str(minimum) if minimum == maximum
                else f"{minimum} or {maximum}"
            )
            raise UnsupportedFormula(
                f"{self._formula!r} calls {name} with {len(arguments)} "
                f"argument(s), but {name} takes {expected}"
            )

        return FunctionCall(name, tuple(arguments))


def _reference_node(text: str) -> Node:
    start, _, end = text.partition(":")
    if not end:
        return CellRef(parse_reference(start))

    first = parse_reference(start)
    last = parse_reference(end)
    if last.sheet is None and first.sheet is not None:
        last = type(last)(
            column=last.column,
            row=last.row,
            absolute_column=last.absolute_column,
            absolute_row=last.absolute_row,
            sheet=first.sheet,
        )
    return RangeRef(first, last)

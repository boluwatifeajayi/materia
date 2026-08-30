"""Abstract syntax tree for the supported formula grammar.

One node type per construct in README section 6. Nodes are frozen, so an AST
can be compared, hashed and cached, and nothing downstream can mutate a
formula it was handed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Union

from materia.parse import Reference


@dataclass(frozen=True)
class Number:
    value: float


@dataclass(frozen=True)
class Text:
    value: str


@dataclass(frozen=True)
class Boolean:
    value: bool


@dataclass(frozen=True)
class CellRef:
    reference: Reference


@dataclass(frozen=True)
class RangeRef:
    start: Reference
    end: Reference


@dataclass(frozen=True)
class UnaryOp:
    operator: str
    operand: "Node"


@dataclass(frozen=True)
class Percent:
    operand: "Node"


@dataclass(frozen=True)
class BinaryOp:
    operator: str
    left: "Node"
    right: "Node"


@dataclass(frozen=True)
class FunctionCall:
    name: str
    arguments: tuple["Node", ...]


Node = Union[
    Number, Text, Boolean, CellRef, RangeRef, UnaryOp, Percent, BinaryOp, FunctionCall
]


def walk(node: Node) -> Iterator[Node]:
    """Every node in the tree, parents before children."""
    yield node
    if isinstance(node, (UnaryOp, Percent)):
        yield from walk(node.operand)
    elif isinstance(node, BinaryOp):
        yield from walk(node.left)
        yield from walk(node.right)
    elif isinstance(node, FunctionCall):
        for argument in node.arguments:
            yield from walk(argument)


def references(node: Node) -> Iterator[CellRef | RangeRef]:
    """Every reference in the tree, which is what the dependency graph is built from."""
    for found in walk(node):
        if isinstance(found, (CellRef, RangeRef)):
            yield found

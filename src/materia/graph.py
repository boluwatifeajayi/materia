"""Cell level dependency graph.

One node per non empty cell, edges from precedent to dependent, cross sheet
edges included. See docs/ARCHITECTURE.md section 3.

Three components need it and each needs a different thing from it:

  the recompute engine   topological order, so a cell is evaluated after
                         everything it reads
  preflight              cycle detection, because a loop has no evaluation
                         order and the engine would never terminate
  the reporter           the path from a candidate cell to a declared output,
                         which is the blast radius a user reads
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator

import networkx as nx

from materia.formula import CellRef, Node, references
from materia.parse import cell_address, normalise_address, range_addresses


def precedent_addresses(node: Node, sheet: str) -> Iterator[str]:
    """Every address a formula reads, resolved against its own sheet.

    Ranges are expanded, so a cell inside the range it aggregates shows up as
    reading itself. That is what makes `=SUM(A1:A10)` sitting in `A5` a
    detectable loop rather than a silent one.
    """
    for reference in references(node):
        if isinstance(reference, CellRef):
            yield cell_address(reference.reference, sheet)
        else:
            yield from range_addresses(reference.start, reference.end, sheet)


@dataclass(frozen=True)
class Cycle:
    """A reference loop, as the sequence of cells that closes it."""

    cells: tuple[str, ...]

    def __str__(self) -> str:
        return " -> ".join(self.cells)


class DependencyGraph:
    """A directed graph of cells, edges pointing precedent to dependent."""

    def __init__(self, graph: nx.DiGraph) -> None:
        self._graph = graph

    @classmethod
    def build(
        cls, formulas: dict[str, Node], constants: Iterable[str] = ()
    ) -> "DependencyGraph":
        """Build from parsed formulas plus the addresses that hold values.

        Only cells that exist become nodes. A formula reading an empty cell
        creates no edge, because an empty cell cannot change and so cannot
        carry impact.
        """
        graph = nx.DiGraph()
        for address in constants:
            graph.add_node(normalise_address(address), kind="constant")
        for address in formulas:
            graph.add_node(normalise_address(address), kind="formula")

        for address, node in formulas.items():
            address = normalise_address(address)
            sheet = address.split("!", 1)[0]
            for precedent in precedent_addresses(node, sheet):
                if precedent in graph:
                    graph.add_edge(precedent, address)
        return cls(graph)

    @classmethod
    def of(cls, model) -> "DependencyGraph":
        """Build from a recompute Model."""
        return cls.build(model.formulas, model.constants)

    # --- inspection ---

    def __contains__(self, address: str) -> bool:
        return normalise_address(address) in self._graph

    def __len__(self) -> int:
        return self._graph.number_of_nodes()

    @property
    def edge_count(self) -> int:
        return self._graph.number_of_edges()

    def kind(self, address: str) -> str:
        return self._graph.nodes[normalise_address(address)]["kind"]

    # --- neighbours ---

    def precedents(self, address: str) -> set[str]:
        """Cells this one reads directly."""
        return set(self._graph.predecessors(normalise_address(address)))

    def dependents(self, address: str) -> set[str]:
        """Cells that read this one directly."""
        return set(self._graph.successors(normalise_address(address)))

    def all_precedents(self, address: str) -> set[str]:
        """Everything this cell depends on, at any depth."""
        return nx.ancestors(self._graph, normalise_address(address))

    def all_dependents(self, address: str) -> set[str]:
        """Everything affected if this cell changes, at any depth.

        This is the blast radius. A cell with no dependents cannot move any
        output, whatever else is wrong with it.
        """
        return nx.descendants(self._graph, normalise_address(address))

    # --- ordering ---

    def topological_order(self) -> list[str]:
        """Evaluation order: every cell after everything it reads."""
        try:
            return list(nx.topological_sort(self._graph))
        except nx.NetworkXUnfeasible as unfeasible:
            raise ValueError(f"the graph has a cycle: {self.find_cycle()}") from unfeasible

    def find_cycle(self) -> Cycle | None:
        """One reference loop, or None if the graph is acyclic."""
        try:
            edges = nx.find_cycle(self._graph)
        except nx.NetworkXNoCycle:
            return None
        return Cycle(tuple(edge[0] for edge in edges) + (edges[-1][1],))

    # --- paths ---

    def path_to(self, source: str, target: str) -> list[str] | None:
        """The shortest chain of cells from one to another, or None.

        Shortest rather than every path on purpose. A report showing how a
        cell reaches enterprise value needs one legible chain, not the
        combinatorial set of routes between them.
        """
        source, target = normalise_address(source), normalise_address(target)
        if source not in self._graph or target not in self._graph:
            return None
        try:
            return nx.shortest_path(self._graph, source, target)
        except nx.NetworkXNoPath:
            return None

    def paths_to_outputs(
        self, source: str, outputs: Iterable[str]
    ) -> dict[str, list[str]]:
        """The path to each declared output this cell can actually reach.

        Outputs it cannot reach are left out rather than mapped to an empty
        path, so the caller cannot mistake "no route" for "route of length
        zero".
        """
        found = {}
        for output in outputs:
            path = self.path_to(source, output)
            if path is not None:
                found[normalise_address(output)] = path
        return found

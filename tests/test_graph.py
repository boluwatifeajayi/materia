"""Dependency graph tests.

The structure asserted here is hand counted from the three_statement_mini
fixture, so a change in how references are resolved shows up as a failure
rather than as a quietly different graph.
"""

import pytest

from materia.formula import parse_formula
from materia.graph import DependencyGraph
from materia.recompute import Model


def graph_of(cells: dict[str, str], constants: list[str] = ()) -> DependencyGraph:
    """Build a graph straight from formula text, for structural cases."""
    return DependencyGraph.build(
        {address: parse_formula(text) for address, text in cells.items()}, constants
    )

# three_statement_mini, by hand:
#
#   Assumptions  A1..A5 labels, B1..B5 values          10 cells
#   Model        A2..A6 labels, B2..B6 formulas        10 cells
#   Valuation    A3 label, B3 formula                   2 cells
#                                                      22 nodes
#
#   Model!B2 reads Assumptions!B1, Assumptions!B2        2
#   Model!B3 reads Model!B2, Assumptions!B3              2
#   Model!B4 reads Model!B2, Model!B3                    2
#   Model!B5 reads Model!B4, Assumptions!B4              2
#   Model!B6 reads Model!B4, Model!B5                    2
#   Valuation!B3 reads Model!B6, Assumptions!B5          2
#                                                       12 edges
NODES = 22
EDGES = 12
OUTPUTS = ["Model!B6", "Valuation!B3"]


@pytest.fixture
def graph(workbooks) -> DependencyGraph:
    return DependencyGraph.of(Model.load(workbooks["three_statement_mini"]))


class TestStructure:
    def test_one_node_per_non_empty_cell(self, graph):
        assert len(graph) == NODES

    def test_edge_count(self, graph):
        assert graph.edge_count == EDGES

    def test_labels_and_values_are_nodes_too(self, graph):
        """docs/ARCHITECTURE.md says one node per non empty cell, not per
        formula. A constant is where a hardcode lives, so it has to be in
        the graph for anything to be traceable back to it."""
        assert "Assumptions!B1" in graph
        assert graph.kind("Assumptions!B1") == "constant"
        assert graph.kind("Model!B2") == "formula"

    def test_empty_cells_are_not_nodes(self, graph):
        """A formula reading an empty cell gets no edge. An empty cell cannot
        change, so it cannot carry impact."""
        assert "Model!Z99" not in graph

    def test_addresses_match_regardless_of_dollars_or_quotes(self, graph):
        assert "Model!$B$6" in graph
        assert graph.precedents("Model!$B$6") == graph.precedents("Model!B6")


class TestNeighbours:
    def test_direct_precedents(self, graph):
        assert graph.precedents("Model!B4") == {"Model!B2", "Model!B3"}

    def test_direct_dependents(self, graph):
        assert graph.dependents("Model!B4") == {"Model!B5", "Model!B6"}

    def test_a_constant_has_no_precedents(self, graph):
        assert graph.precedents("Assumptions!B1") == set()

    def test_transitive_precedents(self, graph):
        assert graph.all_precedents("Valuation!B3") == {
            "Assumptions!B1",
            "Assumptions!B2",
            "Assumptions!B3",
            "Assumptions!B4",
            "Assumptions!B5",
            "Model!B2",
            "Model!B3",
            "Model!B4",
            "Model!B5",
            "Model!B6",
        }

    def test_blast_radius(self, graph):
        """Everything downstream of the units assumption."""
        assert graph.all_dependents("Assumptions!B1") == {
            "Model!B2",
            "Model!B3",
            "Model!B4",
            "Model!B5",
            "Model!B6",
            "Valuation!B3",
        }

    def test_a_label_has_no_blast_radius(self, graph):
        """Nothing reads the row labels, so nothing they do matters."""
        assert graph.all_dependents("Assumptions!A1") == set()


class TestCrossSheet:
    def test_edges_cross_sheets(self, graph):
        assert "Assumptions!B1" in graph.precedents("Model!B2")
        assert "Model!B6" in graph.precedents("Valuation!B3")

    def test_a_cross_sheet_precedent_reaches_a_different_sheet_output(self, graph):
        """The multiple lives on Assumptions and only valuation reads it."""
        assert graph.all_dependents("Assumptions!B5") == {"Valuation!B3"}


class TestPaths:
    def test_a_cell_reaching_an_output_through_three_hops(self, graph):
        """Units to EBITDA: revenue, gross profit, EBITDA."""
        path = graph.path_to("Assumptions!B1", "Model!B6")
        assert path == ["Assumptions!B1", "Model!B2", "Model!B4", "Model!B6"]
        assert len(path) - 1 == 3

    def test_a_path_that_crosses_a_sheet_boundary(self, graph):
        assert graph.path_to("Assumptions!B1", "Valuation!B3") == [
            "Assumptions!B1",
            "Model!B2",
            "Model!B4",
            "Model!B6",
            "Valuation!B3",
        ]

    def test_no_path_gives_none_rather_than_an_empty_list(self, graph):
        """An empty list would read as a path of length zero, which is what
        you get from a cell to itself. They are different answers."""
        assert graph.path_to("Model!B6", "Assumptions!B1") is None

    def test_a_path_from_a_cell_to_itself(self, graph):
        assert graph.path_to("Model!B6", "Model!B6") == ["Model!B6"]

    def test_an_unknown_address_gives_none(self, graph):
        assert graph.path_to("Model!Z99", "Model!B6") is None

    def test_paths_to_every_reachable_output(self, graph):
        paths = graph.paths_to_outputs("Assumptions!B1", OUTPUTS)
        assert set(paths) == {"Model!B6", "Valuation!B3"}
        assert paths["Model!B6"][-1] == "Model!B6"

    def test_outputs_that_cannot_be_reached_are_left_out(self, graph):
        """The multiple only reaches valuation. Leaving EBITDA out is the
        answer; mapping it to an empty path would not be."""
        paths = graph.paths_to_outputs("Assumptions!B5", OUTPUTS)
        assert set(paths) == {"Valuation!B3"}

    def test_a_cell_that_reaches_no_output_at_all(self, graph):
        assert graph.paths_to_outputs("Assumptions!A1", OUTPUTS) == {}


class TestOrdering:
    def test_every_cell_comes_after_everything_it_reads(self, graph):
        order = graph.topological_order()
        position = {address: index for index, address in enumerate(order)}
        assert len(order) == NODES
        for address in order:
            for precedent in graph.precedents(address):
                assert position[precedent] < position[address], address

    def test_the_order_the_engine_uses_is_the_graph_order(self, workbooks):
        """The engine reads its evaluation order from this graph. If they
        could differ, a cell could be evaluated before a value it reads."""
        model = Model.load(workbooks["three_statement_mini"])
        order = model.graph().topological_order()
        position = {address: index for index, address in enumerate(order)}
        for address in model.formulas:
            for precedent in model.graph().precedents(address):
                assert position[precedent] < position[address]


class TestRanges:
    def test_a_range_creates_an_edge_from_every_cell_in_it(self, workbooks):
        graph = DependencyGraph.of(Model.load(workbooks["clean"]))
        assert graph.precedents("Model!B1") == {
            "Model!A1",
            "Model!A2",
            "Model!A3",
            "Model!A4",
        }

    def test_a_range_only_creates_edges_for_cells_that_exist(self):
        graph = graph_of({"Sheet1!B1": "=SUM(A1:A10)"}, ["Sheet1!A1"])
        assert graph.precedents("Sheet1!B1") == {"Sheet1!A1"}


class TestCycles:
    def test_no_cycle_in_a_sound_workbook(self, graph):
        assert graph.find_cycle() is None

    def test_a_two_cell_loop(self):
        cycle = graph_of({"Sheet1!A1": "=A2+1", "Sheet1!A2": "=A1+1"}).find_cycle()
        assert cycle is not None
        assert set(cycle.cells) == {"Sheet1!A1", "Sheet1!A2"}
        assert str(cycle).count("->") == 2

    def test_a_total_that_sits_inside_the_range_it_totals(self):
        """The common real version of this error, and the one a naive range
        expansion misses."""
        cycle = graph_of({"Sheet1!A5": "=SUM(A1:A10)"}, ["Sheet1!A1"]).find_cycle()
        assert cycle is not None
        assert cycle.cells[0] == "Sheet1!A5"

    def test_topological_order_refuses_a_cyclic_graph(self):
        graph = graph_of({"Sheet1!A1": "=A2+1", "Sheet1!A2": "=A1+1"})
        with pytest.raises(ValueError, match="cycle"):
            graph.topological_order()

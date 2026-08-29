"""Agent tool tests.

Tested as plain Python functions, before any model sees them. If these are
wrong then every impact figure the system reports is wrong, and no amount of
prompt work would show it.
"""

import hashlib
import json
import shutil

import pytest

from materia.corpus.layout import DECLARED_OUTPUTS
from materia.llm import ToolCall
from materia.tools import MAX_INSPECT_CELLS, TOOL_DEFINITIONS, Toolbox

CORPUS = "corpus"
EBITDA, ENTERPRISE_VALUE = DECLARED_OUTPUTS


@pytest.fixture(scope="module")
def box():
    return Toolbox(f"{CORPUS}/C03.xlsx", DECLARED_OUTPUTS)


@pytest.fixture(scope="module")
def clean():
    """C09 has no mutations, so its formulas are the ones the model intended."""
    return Toolbox(f"{CORPUS}/C09.xlsx", DECLARED_OUTPUTS)


@pytest.fixture(scope="module")
def c10():
    return Toolbox(f"{CORPUS}/C10.xlsx", DECLARED_OUTPUTS)


class TestThereAreExactlyTwo:
    def test_the_signatures_match_the_architecture(self):
        assert [tool.name for tool in TOOL_DEFINITIONS] == [
            "recompute_with_patch",
            "inspect_range",
        ]

    def test_each_tool_describes_its_arguments(self):
        for tool in TOOL_DEFINITIONS:
            assert tool.parameters["type"] == "object"
            assert tool.parameters["required"]
            for name in tool.parameters["required"]:
                assert tool.parameters["properties"][name]["description"]

    def test_the_recompute_tool_says_the_file_is_not_modified(self):
        """The model is told the same thing the code guarantees."""
        tool = TOOL_DEFINITIONS[0]
        assert "never modified" in tool.description


class TestRecomputeWithPatch:
    def test_it_returns_a_delta_per_declared_output(self, box):
        result = box.recompute_with_patch(EBITDA, "=SUM(C15:Y15)")
        assert set(result) == set(DECLARED_OUTPUTS)
        assert isinstance(result[EBITDA], float)

    def test_a_patch_that_changes_nothing_returns_zero(self, clean):
        """Zero, not an error. An unchanged output is a real measurement.

        Patched with the formula already there, so the answer has to be zero
        whatever the workbook contains.
        """
        current = clean.cells["P&L"][EBITDA].formula
        assert clean.recompute_with_patch(EBITDA, current)[EBITDA] == 0.0

    def test_a_plain_number_is_accepted_as_a_patch(self, box):
        """Mutation family M1 is a formula replaced by a value, so the model
        has to be able to test that hypothesis too."""
        result = box.recompute_with_patch("Assumptions!B4", "9000")
        assert result[EBITDA] != 0.0

    def test_repeated_calls_measure_against_the_same_baseline(self, box):
        """A tool that drifted between calls would make every later delta a
        measurement against an unknown starting point."""
        first = box.recompute_with_patch("Assumptions!B4", "9000")
        box.recompute_with_patch("Assumptions!B5", "0.5")
        assert box.recompute_with_patch("Assumptions!B4", "9000") == first

    def test_the_source_workbook_is_never_modified(self, tmp_path):
        """The invariant in docs/ARCHITECTURE.md, checked on the bytes."""
        subject = tmp_path / "C03.xlsx"
        shutil.copy(f"{CORPUS}/C03.xlsx", subject)
        before = hashlib.sha256(subject.read_bytes()).hexdigest()

        tools = Toolbox(subject, DECLARED_OUTPUTS)
        tools.recompute_with_patch(EBITDA, "=SUM(C15:Y15)")
        tools.recompute_with_patch("Assumptions!B1", "1")

        assert hashlib.sha256(subject.read_bytes()).hexdigest() == before


class TestRecomputeRefusesRatherThanGuesses:
    """Every failure is answered, not raised. A model that proposed something
    impossible should be told so and get to try again."""

    def test_an_unknown_cell(self, box):
        assert "not a populated cell" in box.recompute_with_patch("Model!ZZ999", "=1")["error"]

    def test_something_that_is_not_an_address(self, box):
        assert "not a cell address" in box.recompute_with_patch("the total row", "=1")["error"]

    def test_a_formula_outside_the_grammar(self, box):
        result = box.recompute_with_patch(EBITDA, "=VLOOKUP(A1,A1:B3,2,FALSE)")
        assert "outside the supported grammar" in result["error"]

    def test_a_formula_that_creates_a_cycle(self, box):
        """Enterprise value sums the last twelve months of EBITDA, so pointing
        a month inside that window back at enterprise value closes the loop."""
        result = box.recompute_with_patch("P&L!Z15", "=Valuation!B7")
        assert "circular reference" in result["error"]

    def test_an_output_that_stops_being_a_number(self, box):
        """No delta exists. Saying zero would claim it did not move."""
        result = box.recompute_with_patch(EBITDA, "=1/0")
        assert "DIV/0" in str(result[EBITDA])


class TestInspectRange:
    def test_it_returns_formulas_and_values(self, box):
        result = box.inspect_range("Revenue", "C15:F15")
        assert result["sheet"] == "Revenue"
        assert len(result["cells"]) == 4
        assert all("formula" in cell for cell in result["cells"])

    def test_it_returns_row_labels(self, box):
        """The label is what tells a reader what a row is. Without it the
        agent is guessing from cell references."""
        result = box.inspect_range("Costs", "A12:C12")
        assert any(cell.get("value") == "Overhead" for cell in result["cells"])

    def test_it_returns_cell_comments(self, c10):
        """The evidence that separates a deliberate override from a mistake."""
        result = c10.inspect_range("Costs", "I12")
        [cell] = result["cells"]
        assert "board" in cell["comment"].lower()
        assert "formula" not in cell  # it was overwritten with a value

    def test_a_single_cell_is_a_valid_range(self, box):
        assert len(box.inspect_range("Revenue", "C15")["cells"]) == 1

    def test_dollars_and_lower_case_are_accepted(self, box):
        assert box.inspect_range("Revenue", "$c$15:$f$15")["cells"] == (
            box.inspect_range("Revenue", "C15:F15")["cells"]
        )

    def test_empty_cells_are_left_out(self, box):
        result = box.inspect_range("Revenue", "A1:B4")
        assert all(cell.get("value") is not None or cell.get("formula") for cell in result["cells"])

    def test_an_unknown_sheet_lists_the_real_ones(self, box):
        result = box.inspect_range("Balance Sheet", "A1:B2")
        assert "no sheet named" in result["error"]
        assert "Revenue" in result["sheets"]

    def test_a_malformed_range(self, box):
        assert "not a range" in box.inspect_range("Revenue", "the whole sheet")["error"]

    def test_a_range_that_would_return_the_workbook_again(self, box):
        """Context, not a second copy of the model."""
        result = box.inspect_range("Revenue", "A1:Z100")
        assert f"{MAX_INSPECT_CELLS} cell limit" in result["error"]


class TestDispatch:
    def test_it_runs_a_recompute_call(self, clean):
        current = clean.cells["P&L"][EBITDA].formula
        call = ToolCall("1", "recompute_with_patch", {"cell": EBITDA, "proposed_formula": current})
        assert clean.run(call)[EBITDA] == 0.0

    def test_it_runs_an_inspect_call(self, box):
        call = ToolCall("1", "inspect_range", {"sheet": "Revenue", "range": "C15"})
        assert len(box.run(call)["cells"]) == 1

    def test_an_unknown_tool_is_answered_not_raised(self, box):
        result = box.run(ToolCall("1", "read_the_manifest", {}))
        assert "no tool named" in result["error"]
        assert result["tools"] == ["inspect_range", "recompute_with_patch"]

    def test_missing_arguments_are_answered_not_raised(self, box):
        result = box.run(ToolCall("1", "recompute_with_patch", {"cell": EBITDA}))
        assert "wrong arguments" in result["error"]

    def test_every_result_is_json_serialisable(self, box):
        """It has to go back to the model and into a trace record."""
        for call in [
            ToolCall("1", "recompute_with_patch", {"cell": EBITDA, "proposed_formula": "=SUM(C15:Y15)"}),
            ToolCall("2", "inspect_range", {"sheet": "Costs", "range": "A12:D12"}),
            ToolCall("3", "nope", {}),
        ]:
            json.loads(json.dumps(box.run(call)))


class TestAgainstGroundTruth:
    def test_the_tool_reproduces_the_manifest_delta(self):
        """The manifest's numbers and the tool's numbers come from the same
        engine, so they have to agree. If they ever did not, one of them would
        be wrong and there would be no way to tell which."""
        import json as _json
        from pathlib import Path

        manifest = _json.loads(Path(f"{CORPUS}/manifest.json").read_text())
        checked = 0
        for entry in manifest["workbooks"]:
            # Only the single mutation workbooks. Where a workbook carries two,
            # the shipped file has both, so reverting one is not the same
            # measurement the manifest recorded against the clean model.
            if len(entry["mutations"]) != 1:
                continue
            tools = Toolbox(f"{CORPUS}/{entry['file']}", DECLARED_OUTPUTS)
            for mutation in entry["mutations"]:
                # Reverting the mutation should undo exactly the delta it made.
                result = tools.recompute_with_patch(
                    mutation["address"], str(mutation["original"])
                )
                for output, delta in mutation["deltas"].items():
                    if delta is None:
                        continue
                    checked += 1
                    assert result[output] == pytest.approx(-delta, abs=1e-4), (
                        entry["id"],
                        mutation["family"],
                        output,
                    )
        assert checked >= 12, f"only {checked} deltas compared"


class TestRemainingRefusals:
    def test_a_definitions_list_is_handed_out_by_value(self, box):
        """The agent loop gets its own copy, so it cannot edit the shared one."""
        definitions = box.definitions
        definitions.clear()
        assert len(box.definitions) == 2

    def test_an_address_with_a_sheet_but_no_valid_cell(self, box):
        assert "not a cell address" in box.recompute_with_patch("Revenue!total", "=1")["error"]

    def test_a_patch_that_is_neither_a_formula_nor_a_number(self, box):
        """Text is a legitimate cell value, so it is applied rather than
        refused, and the output stops being a number as a result."""
        result = box.recompute_with_patch(EBITDA, "not a number")
        assert "stops being a number" in str(result[EBITDA])

    def test_text_upstream_produces_an_excel_error_downstream(self, clean):
        """Excel reports #VALUE! rather than failing, and so does the engine."""
        result = clean.recompute_with_patch("Assumptions!B4", "about nine thousand")
        assert "#VALUE!" in str(result[EBITDA])

    def test_a_workbook_with_no_declared_outputs_says_so(self, tmp_path):
        import shutil

        subject = tmp_path / "C09.xlsx"
        shutil.copy(f"{CORPUS}/C09.xlsx", subject)
        tools = Toolbox(subject, [])
        assert "no declared output" in tools.recompute_with_patch("Assumptions!B4", "9000")["error"]


class TestAHardcodeSeversTheChain:
    """Worth pinning, because it is the reason a pasted value is expensive.

    C03 carries an M1 mutation: opening customers at month 6 replaced by a
    hardcoded number. Anything wrong upstream of that cell stops there. The
    hardcode does not just lose one formula, it disconnects everything before
    it from everything after it, and the model still reconciles.
    """

    def test_an_upstream_error_does_not_reach_the_outputs(self, box):
        result = box.recompute_with_patch("Assumptions!B4", "about nine thousand")
        assert "#VALUE!" in str(result[EBITDA])
        assert result[ENTERPRISE_VALUE] == 0.0

    def test_the_same_change_does_reach_them_without_the_hardcode(self, clean):
        result = clean.recompute_with_patch("Assumptions!B4", "about nine thousand")
        assert "#VALUE!" in str(result[EBITDA])
        assert "#VALUE!" in str(result[ENTERPRISE_VALUE])

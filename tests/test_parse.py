"""Parser and R1C1 normaliser tests.

The normaliser is what makes peer comparison possible, so the tests are
mostly about one property: cells that were correctly copied must collapse to
one identical token, and a cell that was not must not.

Absolute and mixed references get their own tests because they are the
easiest thing to get wrong. The failure is silent: treat `$` as decoration
and `A1` and `$A$1` produce the same token, which quietly destroys the signal
every detector depends on.
"""

import hashlib
import shutil

import pytest

from materia.parse import (
    FormulaCell,
    InvalidReference,
    Reference,
    normalise,
    parse_reference,
    read_formulas,
    references_in,
    to_r1c1,
)


@pytest.fixture(scope="module")
def tokens(workbooks) -> dict[str, str]:
    """Every formula in the copied_formulas fixture, by address."""
    return {cell.address: cell.r1c1 for cell in read_formulas(workbooks["copied_formulas"])}


def test_the_architecture_example():
    """docs/ARCHITECTURE.md section 2 states this exact pair and token."""
    expected = "RC[-1]*(1+R[-12]C[-1])"
    assert normalise("=F17*(1+F5)", row=17, column=7) == expected
    assert normalise("=G17*(1+G5)", row=17, column=8) == expected


# row -> what the block is demonstrating
COPIED_BLOCKS = [
    (19, "absolute reference, unchanged by the copy"),
    (21, "mixed reference, locked row"),
    (23, "mixed reference, locked column"),
    (25, "cross sheet reference"),
]


@pytest.mark.parametrize("row,description", COPIED_BLOCKS)
def test_a_copied_block_collapses_to_one_token(tokens, row, description):
    block = {
        address: token for address, token in tokens.items() if address.endswith(str(row))
    }
    assert len(block) == 6, f"expected six cells in row {row}"
    assert len(set(block.values())) == 1, f"{description} did not collapse: {block}"


def test_a_correctly_copied_row_collapses_to_one_token(tokens):
    correct = ["C17", "D17", "E17", "G17", "H17"]
    produced = {tokens[f"Model!{cell}"] for cell in correct}
    assert produced == {"RC[-1]*(1+R[-12]C[-1])"}


def test_the_cell_filled_from_the_wrong_origin_does_not_match(tokens):
    """F17 was dragged from D17 instead of E17.

    This is the whole point of the module. In A1 every cell in the row looks
    different, so there is nothing to compare. In R1C1 the break is the only
    cell that differs from its peers.
    """
    peers = {tokens[f"Model!{cell}"] for cell in ["C17", "D17", "E17", "G17", "H17"]}
    assert tokens["Model!F17"] not in peers
    assert tokens["Model!F17"] == "RC[-2]*(1+R[-12]C[-1])"


def test_a_copied_column_collapses_to_one_token(tokens):
    """The same has to hold down a column, not just across a row."""
    column = {tokens[f"Model!J{row}"] for row in range(10, 15)}
    assert column == {"RC[-1]*2"}


def test_cross_sheet_references_keep_their_qualifier(tokens):
    assert tokens["Model!C25"] == "Assumptions!R2C2*R[-8]C"


class TestReferenceRendering:
    """Every combination of absolute and relative, rendered from C3.

    C3 is row 3, column 3, so a reference to A1 is two up and two left.
    """

    ORIGIN = {"row": 3, "column": 3}

    def test_relative(self):
        assert to_r1c1("A1", **self.ORIGIN) == "R[-2]C[-2]"

    def test_fully_absolute(self):
        assert to_r1c1("$A$1", **self.ORIGIN) == "R1C1"

    def test_absolute_column_relative_row(self):
        assert to_r1c1("$A1", **self.ORIGIN) == "R[-2]C1"

    def test_relative_column_absolute_row(self):
        assert to_r1c1("A$1", **self.ORIGIN) == "R1C[-2]"

    def test_the_four_forms_are_all_different(self):
        """The silent failure is rendering these the same. Assert they are not."""
        rendered = {
            to_r1c1(text, **self.ORIGIN) for text in ("A1", "$A$1", "$A1", "A$1")
        }
        assert len(rendered) == 4

    def test_self_reference_has_no_offsets(self):
        assert to_r1c1("C3", **self.ORIGIN) == "RC"

    def test_absolute_self_reference_is_not_the_same_as_relative(self):
        assert to_r1c1("$C$3", **self.ORIGIN) == "R3C3"
        assert to_r1c1("$C$3", **self.ORIGIN) != to_r1c1("C3", **self.ORIGIN)

    def test_offsets_run_both_ways(self):
        assert to_r1c1("D3", **self.ORIGIN) == "RC[1]"
        assert to_r1c1("C4", **self.ORIGIN) == "R[1]C"

    def test_range(self):
        assert to_r1c1("A1:B2", **self.ORIGIN) == "R[-2]C[-2]:R[-1]C[-1]"

    def test_cross_sheet(self):
        assert to_r1c1("Sheet2!A1", **self.ORIGIN) == "Sheet2!R[-2]C[-2]"

    def test_quoted_sheet_name(self):
        assert to_r1c1("'My Sheet'!$B$2", **self.ORIGIN) == "'My Sheet'!R2C2"

    def test_lowercase_column_letters(self):
        assert to_r1c1("a1", **self.ORIGIN) == to_r1c1("A1", **self.ORIGIN)


class TestParseReference:
    def test_decomposes_a_mixed_reference(self):
        reference = parse_reference("$B5")
        assert reference == Reference(column=2, row=5, absolute_column=True)

    def test_decomposes_a_cross_sheet_reference(self):
        assert parse_reference("Assumptions!C7").sheet == "Assumptions"

    @pytest.mark.parametrize("text", ["A1", "$A$1", "$A1", "A$1", "Sheet1!ZZ99"])
    def test_round_trips_through_a1(self, text):
        assert parse_reference(text).a1 == text

    @pytest.mark.parametrize("text", ["", "SUM", "A", "1", "hello world"])
    def test_rejects_what_is_not_a_reference(self, text):
        with pytest.raises(InvalidReference):
            parse_reference(text)


class TestNormalisation:
    def test_drops_the_leading_equals(self):
        assert not normalise("=A1", row=1, column=2).startswith("=")

    def test_ignores_insignificant_whitespace(self):
        spaced = normalise("= A1 + B1 ", row=1, column=3)
        tight = normalise("=A1+B1", row=1, column=3)
        assert spaced == tight

    def test_uppercases_function_names(self):
        assert normalise("=sum(A1:A3)", row=5, column=1) == normalise(
            "=SUM(A1:A3)", row=5, column=1
        )

    def test_preserves_string_literals_exactly(self):
        """Two formulas carrying different text are genuinely different."""
        token = normalise('=IF(A1>0,"up (good)","down")', row=2, column=2)
        assert '"up (good)"' in token
        assert '"down"' in token

    def test_a_paren_inside_a_literal_is_not_a_function_call(self):
        token = normalise('=IF(A1>0,"up (good)","down")', row=2, column=2)
        other = normalise('=IF(A1>0,"up (bad)","down")', row=2, column=2)
        assert token != other

    def test_a_reference_shaped_function_name_is_left_alone(self):
        """LOG10 is a valid cell reference. Followed by "(" it is a call."""
        assert "LOG10(" in normalise("=LOG10(A1)", row=1, column=2)

    def test_numbers_are_not_mistaken_for_references(self):
        """Scientific notation puts a letter next to digits."""
        assert normalise("=1E5+A1", row=1, column=2) == "1E5+RC[-1]"


class TestReferencesIn:
    def test_finds_both_ends_of_a_range(self):
        found = references_in("=SUM(A1:B10)")
        assert [reference.a1 for reference in found] == ["A1", "B10"]

    def test_skips_references_inside_string_literals(self):
        assert references_in('=IF(A1>0,"see B99","")') == [
            Reference(column=1, row=1)
        ]


class TestReadFormulas:
    def test_returns_populated_cells(self, workbooks):
        cells = read_formulas(workbooks["copied_formulas"])
        by_address = {cell.address: cell for cell in cells}
        cell = by_address["Model!C17"]
        assert isinstance(cell, FormulaCell)
        assert cell.sheet == "Model"
        assert cell.coordinate == "C17"
        assert (cell.row, cell.column) == (17, 3)
        assert cell.formula == "=B17*(1+B5)"
        assert cell.r1c1 == "RC[-1]*(1+R[-12]C[-1])"

    def test_skips_cells_that_hold_values(self, workbooks):
        addresses = {cell.address for cell in read_formulas(workbooks["copied_formulas"])}
        assert "Model!B17" not in addresses  # the seed value
        assert "Assumptions!B2" not in addresses

    def test_does_not_modify_the_workbook(self, workbooks, tmp_path):
        original = tmp_path / "subject.xlsx"
        shutil.copy(workbooks["copied_formulas"], original)
        before = hashlib.sha256(original.read_bytes()).hexdigest()
        read_formulas(original)
        assert hashlib.sha256(original.read_bytes()).hexdigest() == before

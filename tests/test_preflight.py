"""Preflight validator tests.

One case per rejection reason, plus the positive control. The positive
control matters as much as the rejections: a validator that rejects
everything would pass every rejection test.
"""

import hashlib
import shutil

import pytest

from materia.preflight import (
    PreflightRejected,
    Reason,
    _functions_in,
    preflight,
)

# fixture name -> the code the user should see
REJECTIONS = [
    ("vba", "VBA_PRESENT"),
    ("external_link_part", "EXTERNAL_LINK"),
    ("external_link_formula", "EXTERNAL_LINK"),
    ("array_formula", "ARRAY_FORMULA"),
    ("circular", "CIRCULAR_REFERENCE"),
    ("circular_via_range", "CIRCULAR_REFERENCE"),
    ("circular_cross_sheet", "CIRCULAR_REFERENCE"),
    ("unsupported_function", "UNSUPPORTED_FUNCTION(VLOOKUP)"),
    ("unsupported_function_lookalike", "UNSUPPORTED_FUNCTION(LOG10)"),
    ("unsupported_function_nested", "UNSUPPORTED_FUNCTION(SQRT)"),
]


@pytest.mark.parametrize("name,expected_code", REJECTIONS)
def test_rejects_with_the_right_reason(workbooks, name, expected_code):
    with pytest.raises(PreflightRejected) as raised:
        preflight(workbooks[name])
    assert raised.value.code == expected_code


@pytest.mark.parametrize("name,_code", REJECTIONS)
def test_rejection_message_is_actionable(workbooks, name, _code):
    """Every rejection names the reason, and formula level ones name a cell."""
    with pytest.raises(PreflightRejected) as raised:
        preflight(workbooks[name])
    rejection = raised.value
    assert rejection.code in rejection.message
    assert len(rejection.detail) > 20
    if rejection.reason in (Reason.ARRAY_FORMULA, Reason.UNSUPPORTED_FUNCTION):
        assert "!" in rejection.location


def test_clean_workbook_is_accepted(workbooks):
    report = preflight(workbooks["clean"])
    assert report.sheet_names == ["Assumptions", "Model"]
    assert report.formula_count == 16
    assert report.value_cell_count == 8


def test_clean_workbook_uses_every_supported_function(workbooks):
    """The positive control has to actually exercise the grammar.

    Without this, the clean fixture could quietly drift into something
    trivial and still pass, and then it would prove nothing.
    """
    import openpyxl

    workbook = openpyxl.load_workbook(workbooks["clean"], read_only=True)
    formulas = " ".join(
        cell.value
        for sheet in workbook.sheetnames
        for row in workbook[sheet].iter_rows()
        for cell in row
        if isinstance(cell.value, str) and cell.value.startswith("=")
    )
    workbook.close()
    for function in ("SUM", "AVERAGE", "MIN", "MAX", "IF", "ROUND", "ABS", "SUMIF"):
        assert f"{function}(" in formulas, f"{function} missing from the control"


def test_deep_chains_are_not_mistaken_for_cycles(workbooks):
    """The negative control for circular detection.

    Real forecast models are deep by construction. Flagging depth as a loop
    would reject every workbook the tool exists to audit.
    """
    report = preflight(workbooks["deep_chain"])
    assert report.formula_count == 15


def test_unsupported_function_names_the_function(workbooks):
    with pytest.raises(PreflightRejected) as raised:
        preflight(workbooks["unsupported_function"])
    rejection = raised.value
    assert rejection.reason is Reason.UNSUPPORTED_FUNCTION
    assert rejection.function == "VLOOKUP"
    assert "VLOOKUP" in rejection.message


def test_circular_message_shows_the_loop(workbooks):
    with pytest.raises(PreflightRejected) as raised:
        preflight(workbooks["circular"])
    assert "Sheet!A1" in raised.value.message
    assert "Sheet!A2" in raised.value.message


def test_input_workbook_is_never_modified(workbooks, tmp_path):
    """Data flow invariant 1 in docs/ARCHITECTURE.md: opened read only."""
    original = tmp_path / "subject.xlsx"
    shutil.copy(workbooks["clean"], original)
    before = hashlib.sha256(original.read_bytes()).hexdigest()
    preflight(original)
    assert hashlib.sha256(original.read_bytes()).hexdigest() == before


def test_workbook_with_no_formulas_is_accepted(tmp_path):
    """A workbook of pure constants has nothing to reject."""
    import openpyxl

    path = tmp_path / "constants.xlsx"
    workbook = openpyxl.Workbook()
    workbook.active["A1"] = 1
    workbook.active["A2"] = "a label"
    workbook.save(path)

    report = preflight(path)
    assert report.formula_count == 0
    assert report.value_cell_count == 2


def test_a_file_that_is_not_a_workbook_is_not_a_preflight_rejection(tmp_path):
    """PreflightRejected means a real workbook we cannot evaluate.

    A .csv renamed to .xlsx is a different problem, and reporting it under one
    of the five reason codes would tell the user something untrue.
    """
    path = tmp_path / "not_really.xlsx"
    path.write_text("a,b,c\n1,2,3\n")

    with pytest.raises(ValueError) as raised:
        preflight(path)
    assert not isinstance(raised.value, PreflightRejected)


def test_missing_file_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        preflight(tmp_path / "nope.xlsx")


class TestFunctionScanning:
    """The function scanner decides what gets rejected, so it is tested directly."""

    def test_finds_nested_calls(self):
        assert _functions_in("=IF(A1>0,ROUND(B1,2),0)") == ["IF", "ROUND"]

    def test_ignores_text_inside_string_literals(self):
        """A paren in a label must not read as a function call."""
        assert _functions_in('=IF(A1>0,"up (good)","down")') == ["IF"]

    def test_ignores_cell_and_sheet_references(self):
        assert _functions_in("='My Sheet'!A1+Sheet2!$B$2") == []

    def test_reads_a_reference_shaped_name_as_a_function(self):
        """LOG10 is a valid cell reference and a function name."""
        assert _functions_in("=LOG10(A1)") == ["LOG10"]

    def test_does_not_confuse_sum_and_sumif(self):
        assert _functions_in('=SUMIF(A1:A4,">2")') == ["SUMIF"]

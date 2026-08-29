"""Recompute engine tests.

Every impact figure in the submission and the ground truth materiality of
every seeded mutation come from this module, so every expected value below is
hand computed and written out, not derived from the engine itself.

Where Excel and Python disagree, the Excel answer is the correct one and the
test says so.
"""

import hashlib
import shutil
from pathlib import Path

import pytest

from materia.corpus.layout import DECLARED_OUTPUTS
from materia.recompute import (
    CircularReference,
    EvaluationError,
    ExcelError,
    Model,
    _excel_round,
)


def value_of(formula: str, cells: dict | None = None):
    """Evaluate one formula on Sheet1, with optional supporting cells."""
    model = Model.from_cells({**(cells or {}), "Sheet1!Z99": formula})
    return model.value("Sheet1!Z99")


NUMBERS = {f"Sheet1!A{row}": value for row, value in enumerate([1, 2, 3, 4], start=1)}


class TestArithmetic:
    """Hand computed, one per operator."""

    @pytest.mark.parametrize(
        "formula,expected",
        [
            ("=2+3", 5.0),
            ("=10-4", 6.0),
            ("=6*7", 42.0),
            ("=9/2", 4.5),
            ("=2^10", 1024.0),
            ("=-5", -5.0),
            ("=--5", 5.0),
            ("=+5", 5.0),
            ("=50%", 0.5),
            ("=200*15%", 30.0),
            ("=10-3-2", 5.0),
            ("=100/5/2", 10.0),
            ("=2+3*4", 14.0),
            ("=(2+3)*4", 20.0),
            ("=1.5+2.25", 3.75),
        ],
    )
    def test_expression(self, formula, expected):
        assert value_of(formula) == pytest.approx(expected)

    def test_unary_minus_binds_tighter_than_exponent(self):
        """Excel gives 4. Python's -2**2 gives -4."""
        assert value_of("=-2^2") == 4.0

    def test_exponent_is_left_associative(self):
        """Excel gives 64. Python's 2**3**2 gives 512."""
        assert value_of("=2^3^2") == 64.0

    @pytest.mark.parametrize(
        "formula,expected",
        [
            ("=1=1", True),
            ("=1=2", False),
            ("=1<>2", True),
            ("=1<2", True),
            ("=2<=2", True),
            ("=3>2", True),
            ("=2>=3", False),
        ],
    )
    def test_comparison(self, formula, expected):
        assert value_of(formula) is expected


class TestFunctions:
    """One block per function, every expected value computed by hand."""

    def test_sum_of_a_range(self):
        assert value_of("=SUM(A1:A4)", NUMBERS) == 10.0

    def test_sum_of_scalars(self):
        assert value_of("=SUM(1,2,3)") == 6.0

    def test_sum_mixes_ranges_and_scalars(self):
        assert value_of("=SUM(A1:A4,100)", NUMBERS) == 110.0

    def test_sum_skips_empty_cells(self):
        cells = {"Sheet1!A1": 1, "Sheet1!A3": 3}  # A2 left empty
        assert value_of("=SUM(A1:A3)", cells) == 4.0

    def test_sum_skips_text_inside_a_range(self):
        cells = {"Sheet1!A1": 1, "Sheet1!A2": "label", "Sheet1!A3": 3}
        assert value_of("=SUM(A1:A3)", cells) == 4.0

    def test_sum_skips_booleans_inside_a_range_but_coerces_them_directly(self):
        """Excel's asymmetry, and it is not obvious.

        A TRUE sitting in a range is ignored. The same TRUE passed as an
        argument counts as 1.
        """
        cells = {"Sheet1!A1": 1, "Sheet1!A2": True, "Sheet1!A3": 3}
        assert value_of("=SUM(A1:A3)", cells) == 4.0
        assert value_of("=SUM(TRUE)") == 1.0

    def test_average(self):
        assert value_of("=AVERAGE(A1:A4)", NUMBERS) == 2.5

    def test_average_divides_by_the_count_of_numbers_only(self):
        cells = {"Sheet1!A1": 1, "Sheet1!A2": "label", "Sheet1!A3": 3}
        assert value_of("=AVERAGE(A1:A3)", cells) == 2.0  # not 4/3

    def test_average_of_nothing_is_an_error(self):
        assert value_of("=AVERAGE(A1:A3)") is ExcelError.DIV0

    def test_min_and_max(self):
        assert value_of("=MIN(A1:A4)", NUMBERS) == 1.0
        assert value_of("=MAX(A1:A4)", NUMBERS) == 4.0

    def test_min_and_max_of_an_empty_range_are_zero(self):
        """Excel gives 0 rather than an error."""
        assert value_of("=MIN(A1:A3)") == 0.0
        assert value_of("=MAX(A1:A3)") == 0.0

    def test_abs(self):
        assert value_of("=ABS(-7.5)") == 7.5
        assert value_of("=ABS(7.5)") == 7.5

    @pytest.mark.parametrize(
        "formula,expected",
        [
            ("=ROUND(3.14159,2)", 3.14),
            ("=ROUND(2.5,0)", 3.0),
            ("=ROUND(-2.5,0)", -3.0),
            ("=ROUND(2.675,2)", 2.68),
            ("=ROUND(1234,-2)", 1200.0),
            ("=ROUND(1.4,0)", 1.0),
        ],
    )
    def test_round(self, formula, expected):
        assert value_of(formula) == pytest.approx(expected)

    def test_round_absorbs_binary_representation_error(self):
        """2.675 is stored as 2.67499999... so a naive scale and round gives
        2.67 where Excel gives 2.68."""
        assert _excel_round(2.675, 2) == 2.68
        assert _excel_round(1.005, 2) == 1.01

    def test_round_does_not_push_a_value_that_is_genuinely_below_the_boundary(self):
        """The correction for the case above must not overshoot.

        A fixed epsilon big enough to fix 2.675 also rounds 0.4999999995 up
        to 1, which is wrong. The epsilon scales with magnitude instead.
        """
        assert _excel_round(0.4999999995, 0) == 0.0

    def test_round_is_half_away_from_zero_not_half_to_even(self):
        """Python's round(2.5) is 2 and round(3.5) is 4. Excel gives 3 and 4.

        Banker's rounding here would bias every rounded figure in the corpus
        in a direction nobody would think to look for.
        """
        assert _excel_round(2.5, 0) == 3.0
        assert _excel_round(3.5, 0) == 4.0
        assert round(2.5) == 2  # what we are deliberately not doing

    def test_if_takes_the_true_branch(self):
        assert value_of("=IF(1>0,10,20)") == 10.0

    def test_if_takes_the_false_branch(self):
        assert value_of("=IF(1>2,10,20)") == 20.0

    def test_if_returns_false_when_the_third_argument_is_omitted(self):
        assert value_of("=IF(1>2,10)") is False

    def test_if_treats_a_nonzero_number_as_true(self):
        assert value_of("=IF(5,10,20)") == 10.0
        assert value_of("=IF(0,10,20)") == 20.0

    def test_if_does_not_propagate_an_error_from_the_branch_not_taken(self):
        assert value_of("=IF(1>0,10,1/0)") == 10.0

    def test_sumif_with_a_comparison(self):
        assert value_of('=SUMIF(A1:A4,">2")', NUMBERS) == 7.0  # 3 + 4

    def test_sumif_with_equality(self):
        assert value_of("=SUMIF(A1:A4,2)", NUMBERS) == 2.0

    def test_sumif_with_not_equal(self):
        assert value_of('=SUMIF(A1:A4,"<>2")', NUMBERS) == 8.0  # 1 + 3 + 4

    def test_sumif_with_less_than_or_equal(self):
        assert value_of('=SUMIF(A1:A4,"<=2")', NUMBERS) == 3.0  # 1 + 2

    def test_sumif_with_a_separate_sum_range(self):
        cells = {**NUMBERS, **{f"Sheet1!B{r}": v for r, v in enumerate([10, 20, 30, 40], 1)}}
        assert value_of('=SUMIF(A1:A4,">2",B1:B4)', cells) == 70.0  # 30 + 40


class TestEmptyAndText:
    def test_an_empty_cell_is_zero_in_arithmetic(self):
        assert value_of("=A9+5") == 5.0

    def test_text_that_looks_like_a_number_is_coerced(self):
        """Excel gives 6 for ="5"+1."""
        assert value_of('="5"+1') == 6.0

    def test_text_that_is_not_a_number_is_an_error(self):
        assert value_of('="abc"+1') is ExcelError.VALUE

    def test_a_range_used_where_one_value_belongs_is_an_error(self):
        assert value_of("=A1:A4*2", NUMBERS) is ExcelError.VALUE


class TestErrors:
    def test_division_by_zero(self):
        assert value_of("=1/0") is ExcelError.DIV0

    def test_zero_to_a_negative_power(self):
        assert value_of("=0^-1") is ExcelError.DIV0

    def test_negative_base_with_a_fractional_exponent(self):
        assert value_of("=(-8)^0.5") is ExcelError.NUM

    def test_an_error_propagates_along_a_chain(self):
        model = Model.from_cells(
            {
                "Sheet1!A1": "=1/0",
                "Sheet1!A2": "=A1*2",
                "Sheet1!A3": "=A2+100",
            }
        )
        assert model.value("Sheet1!A3") is ExcelError.DIV0

    def test_an_error_inside_a_range_propagates_through_sum(self):
        model = Model.from_cells(
            {"Sheet1!A1": 1, "Sheet1!A2": "=1/0", "Sheet1!B1": "=SUM(A1:A2)"}
        )
        assert model.value("Sheet1!B1") is ExcelError.DIV0

    def test_a_range_above_the_size_limit_is_refused(self):
        """Refused by name rather than folded into a general failure, since
        the fix is to narrow the range."""
        from materia.parse import RangeTooLarge

        with pytest.raises(RangeTooLarge, match="above the"):
            Model.from_cells({"Sheet1!A1": "=SUM(B1:B100000)"})


class TestComparisonSemantics:
    """Excel compares across types in a fixed order: number, then text, then
    FALSE, then TRUE. An empty cell equals both 0 and the empty string, and
    text comparison ignores case. None of this is Python's behaviour."""

    def test_text_compares_case_insensitively(self):
        assert value_of('="ABC"="abc"') is True
        assert value_of('="abc"<"abd"') is True

    def test_booleans_compare(self):
        assert value_of("=TRUE>FALSE") is True
        assert value_of("=TRUE=TRUE") is True

    def test_a_number_sorts_below_text(self):
        assert value_of('=1<"a"') is True

    def test_text_sorts_below_a_boolean(self):
        assert value_of('="zzz"<TRUE') is True

    def test_an_empty_cell_equals_zero(self):
        assert value_of("=A9=0") is True

    def test_an_empty_cell_equals_the_empty_string(self):
        assert value_of('=A9=""') is True

    def test_comparing_with_an_error_gives_the_error(self):
        assert value_of("=(1/0)>1") is ExcelError.DIV0
        assert value_of("=1>(1/0)") is ExcelError.DIV0

    def test_a_condition_that_is_text_is_an_error(self):
        assert value_of('=IF("abc",1,2)') is ExcelError.VALUE


class TestErrorPropagationPaths:
    """One test per route an error can take through the evaluator."""

    def test_through_unary_minus(self):
        assert value_of('=-"abc"') is ExcelError.VALUE

    def test_through_percent(self):
        assert value_of('="abc"%') is ExcelError.VALUE

    def test_from_the_right_hand_side_of_an_operator(self):
        assert value_of('=1+"abc"') is ExcelError.VALUE

    def test_through_a_scalar_argument_to_sum(self):
        assert value_of("=SUM(1/0)") is ExcelError.DIV0

    def test_through_average_min_and_max(self):
        cells = {"Sheet1!A1": 1, "Sheet1!A2": "=1/0"}
        for function in ("AVERAGE", "MIN", "MAX"):
            assert value_of(f"={function}(A1:A2)", cells) is ExcelError.DIV0

    def test_through_abs(self):
        assert value_of('=ABS("abc")') is ExcelError.VALUE

    def test_through_both_arguments_of_round(self):
        assert value_of('=ROUND("abc",2)') is ExcelError.VALUE
        assert value_of('=ROUND(1.234,"abc")') is ExcelError.VALUE

    def test_an_overflowing_power_is_a_number_error(self):
        assert value_of("=1E308^2") is ExcelError.NUM

    def test_an_empty_cell_used_as_a_condition_is_false(self):
        assert value_of("=IF(A9,1,2)") == 2.0

    def test_text_compared_against_an_empty_cell(self):
        assert value_of('="abc">A9') is True
        assert value_of('=""=A9') is True

    def test_an_output_that_becomes_text_has_no_delta(self):
        """Not every non-numeric output is an error. Text has no delta either."""
        model = Model.from_cells(
            {"Sheet1!A1": 1, "Sheet1!B1": "=A1*2"}, outputs=["Sheet1!B1"]
        )
        result = model.patch("Sheet1!B1", "a label")
        assert result.outputs["Sheet1!B1"].after == "a label"
        assert result.outputs["Sheet1!B1"].delta is None

    def test_an_unknown_operator_is_refused_rather_than_ignored(self):
        """Same guard as the node type one. Falling through a chain of
        operator branches would return None and read as an empty cell."""
        from materia.formula import BinaryOp, Number
        from materia.recompute import _Evaluator

        with pytest.raises(EvaluationError, match="unknown operator"):
            _Evaluator({}, set(), "Sheet1").run(
                BinaryOp("&", Number(1.0), Number(2.0))
            )

    def test_an_unknown_node_type_is_refused_rather_than_ignored(self):
        """The evaluator has a branch per node type. A new one arriving
        without a branch must raise, not silently evaluate to nothing."""
        from materia.recompute import _Evaluator

        with pytest.raises(EvaluationError, match="cannot evaluate"):
            _Evaluator({}, set(), "Sheet1").run(object())


class TestSumifEdges:
    def test_scalar_arguments_rather_than_ranges(self):
        assert value_of('=SUMIF(5,">2")') == 5.0
        assert value_of('=SUMIF(5,">2",100)') == 100.0

    def test_a_sum_range_shorter_than_the_criteria_range(self):
        """Excel takes the shape of the criteria range, so pairs that have no
        counterpart contribute nothing."""
        cells = {**NUMBERS, "Sheet1!B1": 10, "Sheet1!B2": 20}
        assert value_of('=SUMIF(A1:A4,">0",B1:B2)', cells) == 30.0

    def test_text_and_empty_cells_in_the_sum_range_are_skipped(self):
        cells = {**NUMBERS, "Sheet1!B1": 10, "Sheet1!B2": "label", "Sheet1!B4": True}
        assert value_of('=SUMIF(A1:A4,">0",B1:B4)', cells) == 10.0

    def test_an_error_in_the_sum_range_propagates(self):
        cells = {**NUMBERS, "Sheet1!B1": "=1/0"}
        assert value_of('=SUMIF(A1:A4,">0",B1:B4)', cells) is ExcelError.DIV0

    def test_a_non_numeric_criterion_after_an_operator(self):
        cells = {"Sheet1!A1": "apple", "Sheet1!A2": "banana", "Sheet1!B1": 10, "Sheet1!B2": 20}
        assert value_of('=SUMIF(A1:A2,">apple",B1:B2)', cells) == 20.0

    def test_cells_that_cannot_be_compared_do_not_match(self):
        cells = {"Sheet1!A1": "=1/0", "Sheet1!A2": 5, "Sheet1!B1": 10, "Sheet1!B2": 20}
        assert value_of('=SUMIF(A1:A2,">0",B1:B2)', cells) == 20.0


class TestPropagation:
    """The behaviour the whole engine exists for."""

    @pytest.fixture
    def model(self, workbooks):
        return Model.load(
            workbooks["three_statement_mini"], outputs=["Model!B6", "Valuation!B3"]
        )

    def test_the_baseline_is_correct(self, model):
        """Hand computed from the fixture.

        revenue 100 * 10          = 1000
        cost    1000 * 0.3        = 300
        gross   1000 - 300        = 700
        bonus   700 > 500 so 10%  = 70
        EBITDA  700 - 70          = 630
        EV      630 * 8           = 5040
        """
        assert model.value("Model!B2") == 1000.0
        assert model.value("Model!B3") == 300.0
        assert model.value("Model!B4") == 700.0
        assert model.value("Model!B5") == 70.0
        assert model.value("Model!B6") == 630.0
        assert model.value("Valuation!B3") == 5040.0

    def test_a_patch_four_levels_deep_reaches_the_output(self, model):
        """Units feed revenue, cost, gross profit, EBITDA, then valuation.

        units 50 gives revenue 500, cost 150, gross 350. 350 is below the
        bonus threshold of 500, so the bonus becomes 0 and EBITDA is 350.
        EV is 350 * 8 = 2800.
        """
        result = model.patch("Assumptions!B1", 50)
        assert result.outputs["Model!B6"].after == 350.0
        assert result.outputs["Model!B6"].delta == -280.0
        assert result.outputs["Valuation!B3"].after == 2800.0
        assert result.outputs["Valuation!B3"].delta == -2240.0

    def test_cross_sheet_propagation(self, model):
        """The multiple lives on Assumptions and only valuation reads it."""
        result = model.patch("Assumptions!B5", 10)
        assert result.outputs["Model!B6"].delta == 0.0
        assert result.outputs["Valuation!B3"].after == 6300.0
        assert result.outputs["Valuation!B3"].delta == 1260.0

    def test_the_if_condition_flipping_to_false(self, model):
        """Raising the threshold above gross profit removes the bonus.

        gross profit stays 700, bonus becomes 0, so EBITDA rises to 700 and
        EV to 5600.
        """
        result = model.patch("Assumptions!B4", 800)
        assert model.value("Model!B5") == 70.0  # baseline still has the bonus
        assert result.outputs["Model!B6"].after == 700.0
        assert result.outputs["Model!B6"].delta == 70.0
        assert result.outputs["Valuation!B3"].after == 5600.0

    def test_the_if_condition_staying_true(self, model):
        """Lowering the threshold keeps the bonus, so nothing moves."""
        result = model.patch("Assumptions!B4", 100)
        assert result.outputs["Model!B6"].delta == 0.0

    def test_replacing_a_formula_with_a_constant(self, model):
        """Mutation family M1: someone pasted a value over a formula."""
        result = model.patch("Model!B4", 900)
        assert result.outputs["Model!B6"].after == 810.0  # 900 - 90 bonus
        assert result.outputs["Model!B6"].delta == 180.0

    def test_replacing_a_constant_with_a_formula(self, model):
        result = model.patch("Assumptions!B1", "=50*2")
        assert result.outputs["Model!B6"].delta == 0.0

    def test_a_patch_that_changes_nothing_gives_a_zero_delta(self, model):
        """Zero, not an error and not None. An unchanged output is a real
        measurement and the gate has to be able to read it."""
        result = model.patch("Assumptions!B1", 100)
        for output in result.outputs.values():
            assert output.delta == 0.0
        assert result.as_tool_result() == {"Model!B6": 0.0, "Valuation!B3": 0.0}

    def test_relative_change_is_reported_for_the_materiality_gate(self, model):
        result = model.patch("Assumptions!B5", 16)  # multiple doubles
        assert result.outputs["Valuation!B3"].relative == pytest.approx(1.0)
        assert result.outputs["Model!B6"].relative == 0.0

    def test_patching_does_not_change_the_model(self, model):
        """Every patch is measured against the same baseline, so the engine
        has to be reusable. A patch that leaked would silently compound."""
        before = dict(model.values)
        model.patch("Assumptions!B1", 50)
        model.patch("Model!B4", 900)
        assert model.values == before
        assert model.patch("Assumptions!B1", 50).outputs["Model!B6"].delta == -280.0

    def test_an_output_that_becomes_an_error_has_no_delta(self, model):
        """None rather than 0.0. An output that turned into #DIV/0! has not
        stayed the same, and saying it did would be the exact failure the
        design exists to prevent."""
        result = model.patch("Model!B4", "=1/0")
        assert result.outputs["Model!B6"].after is ExcelError.DIV0
        assert result.outputs["Model!B6"].delta is None
        assert result.outputs["Model!B6"].relative is None

    def test_relative_change_is_undefined_when_the_baseline_is_zero(self, model):
        """Division by a zero baseline is not a 100% move, it is undefined.

        The materiality gate has to see None here rather than a number it
        would otherwise compare against a threshold.
        """
        zeroed = Model.from_cells(
            {"Sheet1!A1": 0, "Sheet1!B1": "=A1*2"}, outputs=["Sheet1!B1"]
        )
        result = zeroed.patch("Sheet1!A1", 5)
        assert result.outputs["Sheet1!B1"].delta == 10.0
        assert result.outputs["Sheet1!B1"].relative is None

    def test_a_patch_that_creates_a_cycle_is_refused(self, model):
        with pytest.raises(CircularReference):
            model.patch("Assumptions!B1", "=Valuation!B3")

    def test_patching_without_declared_outputs_is_refused(self, workbooks):
        model = Model.load(workbooks["three_statement_mini"])
        with pytest.raises(EvaluationError, match="no declared output"):
            model.patch("Assumptions!B1", 50)


class TestLoading:
    def test_load_does_not_modify_the_workbook(self, workbooks, tmp_path):
        original = tmp_path / "subject.xlsx"
        shutil.copy(workbooks["three_statement_mini"], original)
        before = hashlib.sha256(original.read_bytes()).hexdigest()
        model = Model.load(original, outputs=["Model!B6"])
        model.patch("Assumptions!B1", 50)
        assert hashlib.sha256(original.read_bytes()).hexdigest() == before

    def test_addresses_are_matched_regardless_of_dollars_or_case(self, workbooks):
        model = Model.load(workbooks["three_statement_mini"], outputs=["Model!$B$6"])
        assert model.value("model!b6".replace("model", "Model")) == 630.0
        assert model.patch("Assumptions!$B$1", 50).outputs["Model!B6"].delta == -280.0

    def test_every_corpus_grammar_formula_evaluates(self, workbooks):
        """The clean fixture uses all eight functions and every reference
        style, so loading it exercises the whole grammar end to end."""
        model = Model.load(workbooks["clean"])
        assert model.value("Model!B1") == 520.0  # SUM(100,120,140,160)
        assert model.value("Model!B2") == 130.0  # AVERAGE
        assert model.value("Model!B3") == 100.0  # MIN
        assert model.value("Model!B4") == 160.0  # MAX
        assert model.value("Model!B5") == 130.0  # ROUND(130,2)
        assert model.value("Model!B6") == 60.0  # ABS(100-160)
        assert model.value("Model!B7") == 300.0  # SUMIF > 120: 140 + 160
        assert model.value("Model!B8") == "up (good)"


class TestAgreementWithLibreOffice:
    """An external oracle for the engine every impact figure depends on.

    The baseline agent, given a shell and left to its own methods, found the
    headless LibreOffice on the machine, wrote patched copies of `C03` and had
    LibreOffice recalculate them. Those numbers are in its committed
    trajectory. They are the only figures in this project produced by a real
    spreadsheet application rather than by our own code, so they are worth
    pinning: if the engine ever drifts, this fails against something that was
    never written to agree with it.

    Source: trajectories/baseline/C03_baseline_openai.jsonl, step 37.
    """

    TRACE = Path("trajectories/baseline/C03_baseline_openai.jsonl")
    LIBREOFFICE = {
        "unpatched": {"P&L!AA15": 14816742, "Valuation!B7": 143535444},
        ("Revenue!H5", "=G9"): {"P&L!AA15": 23521315, "Valuation!B7": 236288274},
        ("P&L!AA15", "=SUM(C15:Z15)"): {"P&L!AA15": 16367624, "Valuation!B7": 143535444},
    }

    def test_the_unpatched_workbook_matches(self):
        model = Model.load("corpus/C03.xlsx", outputs=list(DECLARED_OUTPUTS))
        for cell, expected in self.LIBREOFFICE["unpatched"].items():
            assert round(model.value(cell)) == expected

    @pytest.mark.parametrize("cell,formula", [
        ("Revenue!H5", "=G9"),
        ("P&L!AA15", "=SUM(C15:Z15)"),
    ])
    def test_each_patch_matches(self, cell, formula):
        model = Model.load("corpus/C03.xlsx", outputs=list(DECLARED_OUTPUTS))
        outputs = model.patch(cell, formula).outputs
        for output, expected in self.LIBREOFFICE[(cell, formula)].items():
            assert round(outputs[output].after) == expected, f"{cell} -> {formula}, {output}"

    def test_the_numbers_are_still_in_the_trajectory_they_came_from(self):
        """So this cannot quietly become a set of figures somebody typed in."""
        text = self.TRACE.read_text()
        for group in self.LIBREOFFICE.values():
            for expected in group.values():
                assert str(expected) in text, expected

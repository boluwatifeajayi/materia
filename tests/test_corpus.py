"""Corpus generator tests.

The important one is test_the_engine_reproduces_every_cached_value. The
workbook is built twice from the same design, once as Excel formulas and once
as a plain Python month loop, and the second result is written into the file
as the cached values. That test asserts the recompute engine, reading the
formulas, lands on the numbers the loop produced.

A generated workbook has no Excel written values in it, so checking the engine
against a file it produced itself would prove nothing. Two independent
implementations agreeing is a real signal, and either one being wrong shows up
as a failure here.
"""

import hashlib

import openpyxl
import pytest

from materia.corpus import generate
from materia.corpus.layout import (
    DECLARED_OUTPUTS,
    MONTHS,
    PL_SHEET,
    TOTAL,
    month_column,
)
from materia.formula import parse_formula
from materia.parse import read_formulas
from materia.preflight import preflight
from materia.recompute import Model

SEED = 20260828


@pytest.fixture(scope="module")
def workbook(tmp_path_factory):
    return generate(tmp_path_factory.mktemp("corpus") / "C01.xlsx", SEED)


@pytest.fixture(scope="module")
def cached(workbook) -> dict[str, float]:
    """Every numeric value openpyxl reads back out of the file."""
    values = {}
    book = openpyxl.load_workbook(workbook, data_only=True)
    for name in book.sheetnames:
        for row in book[name].iter_rows():
            for cell in row:
                if isinstance(cell.value, (int, float)):
                    values[f"{name}!{cell.coordinate}"] = float(cell.value)
    book.close()
    return values


class TestTheWorkbookIsUsable:
    def test_it_passes_preflight(self, workbook):
        report = preflight(workbook)
        assert report.sheet_names == [
            "Assumptions",
            "Revenue",
            "Costs",
            PL_SHEET,
            "Valuation",
        ]

    def test_every_formula_parses(self, workbook):
        formulas = read_formulas(workbook)
        assert formulas
        for cell in formulas:
            parse_formula(cell.formula)

    def test_the_formula_count_is_in_range(self, workbook):
        """docs/EVALUATION.md section 2 asks for 400 to 1500 formulas."""
        count = preflight(workbook).formula_count
        assert 400 <= count <= 1500, count

    def test_it_has_twenty_four_monthly_columns(self, workbook):
        """Months run C to Z, then the column after them is the totals
        column, so the month row has to stop exactly where the totals
        column starts."""
        book = openpyxl.load_workbook(workbook)
        revenue, profit_and_loss = book["Revenue"], book[PL_SHEET]

        assert revenue[f"{month_column(1)}3"].value is not None
        assert revenue[f"{month_column(MONTHS)}3"].value is not None
        assert revenue[f"{month_column(MONTHS + 1)}3"].value is None

        assert month_column(MONTHS + 1) == TOTAL
        assert profit_and_loss[f"{TOTAL}3"].value == "Total"
        book.close()

    def test_it_uses_every_function_in_the_grammar(self, workbook):
        """A corpus that only used arithmetic would leave most of the engine
        untested by the thing it exists to test."""
        text = " ".join(cell.formula for cell in read_formulas(workbook))
        for function in ("SUM", "AVERAGE", "MIN", "MAX", "IF", "ROUND", "ABS", "SUMIF"):
            assert f"{function}(" in text, f"{function} is not exercised by the corpus"


class TestDeterminism:
    def test_the_same_seed_gives_a_byte_identical_file(self, tmp_path):
        first = generate(tmp_path / "first.xlsx", SEED)
        second = generate(tmp_path / "second.xlsx", SEED)
        assert (
            hashlib.sha256(first.read_bytes()).hexdigest()
            == hashlib.sha256(second.read_bytes()).hexdigest()
        )

    def test_a_different_seed_gives_a_different_file(self, tmp_path):
        first = generate(tmp_path / "first.xlsx", SEED)
        other = generate(tmp_path / "other.xlsx", SEED + 1)
        assert first.read_bytes() != other.read_bytes()

    def test_regenerating_over_an_existing_file_is_stable(self, tmp_path):
        path = tmp_path / "same.xlsx"
        generate(path, SEED)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        generate(path, SEED)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


class TestTheEngineAgreesWithTheModel:
    def test_the_file_actually_carries_cached_values(self, cached):
        """Without this the comparison below could pass by comparing nothing.

        openpyxl cannot write a formula and its result together, so the
        generator injects the values into the saved XML. If that ever stops
        working, this fails rather than the comparison silently going green.
        """
        assert len(cached) > 700
        assert f"{PL_SHEET}!{TOTAL}15" in cached

    def test_every_formula_cell_carries_a_value(self, workbook, cached):
        """No formula cell may be left without one.

        A missing value does not fail loudly on its own: the comparison below
        would skip that cell and still pass. So the generator raises, and this
        checks the two counts line up.
        """
        formulas = {cell.address for cell in read_formulas(workbook)}
        assert formulas
        assert formulas <= set(cached)

    def test_a_formula_with_no_computed_value_is_refused(self, tmp_path):
        """The guard itself, exercised by withholding one value."""
        from materia.corpus.generate import (
            Assumptions,
            MissingComputedValue,
            build_workbook,
            compute_values,
            save,
        )

        assumptions = Assumptions.from_seed(SEED)
        values = compute_values(assumptions)
        values.cells.pop(f"{PL_SHEET}!{TOTAL}15")
        with pytest.raises(MissingComputedValue, match="AA15"):
            save(build_workbook(assumptions), tmp_path / "broken.xlsx", values)

    def test_the_engine_reproduces_every_cached_value(self, workbook, cached):
        """The cross check the whole task exists for."""
        model = Model.load(workbook)
        compared = 0
        mismatches = []
        for address, expected in cached.items():
            actual = model.value(address)
            if not isinstance(actual, (int, float)):
                continue
            compared += 1
            if abs(float(actual) - expected) > 1e-6:
                mismatches.append((address, expected, float(actual)))

        assert compared > 700, f"only {compared} cells compared"
        assert not mismatches, mismatches[:10]

    def test_the_declared_outputs_are_present_and_numeric(self, workbook, cached):
        model = Model.load(workbook, outputs=DECLARED_OUTPUTS)
        for output in DECLARED_OUTPUTS:
            assert output in cached
            assert isinstance(model.value(output), (int, float))

    def test_a_patch_moves_the_declared_outputs(self, workbook):
        """A corpus workbook has to be one where errors have consequences.

        If the outputs did not move, every mutation would be immaterial and
        the corpus could not test the materiality gate at all.
        """
        model = Model.load(workbook, outputs=DECLARED_OUTPUTS)
        result = model.patch("Assumptions!B4", 1)  # opening customers
        for output in DECLARED_OUTPUTS:
            assert result.outputs[output].delta != 0.0


class TestTheModelIsRealistic:
    def test_it_is_profitable_and_growing(self, cached):
        """A forecast nobody would apply a multiple to is not a realistic
        subject for an audit."""
        first = cached[f"{PL_SHEET}!{month_column(1)}15"]
        last = cached[f"{PL_SHEET}!{month_column(MONTHS)}15"]
        assert first > 0
        assert last > first

    def test_revenue_grows_every_month(self, cached):
        revenue = [cached[f"{PL_SHEET}!{month_column(m)}5"] for m in range(1, MONTHS + 1)]
        assert all(later > earlier for earlier, later in zip(revenue, revenue[1:]))

    def test_the_total_row_equals_the_sum_of_the_months(self, cached):
        months = sum(cached[f"{PL_SHEET}!{month_column(m)}15"] for m in range(1, MONTHS + 1))
        assert cached[f"{PL_SHEET}!{TOTAL}15"] == pytest.approx(months)

    @pytest.mark.parametrize("seed", [1, 20260828, 99999, 424242])
    def test_any_seed_produces_a_sound_workbook(self, tmp_path, seed):
        """The generator has to hold up across seeds, since T08 needs twelve."""
        path = generate(tmp_path / f"seed_{seed}.xlsx", seed)
        report = preflight(path)
        assert 400 <= report.formula_count <= 1500
        model = Model.load(path, outputs=DECLARED_OUTPUTS)
        for output in DECLARED_OUTPUTS:
            assert isinstance(model.value(output), (int, float))

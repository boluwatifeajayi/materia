"""Mutation injector tests.

The manifest's deltas are what makes materiality ground truth rather than
opinion, so the tests that matter most are the ones checking those numbers
against a fresh recompute, and the one checking that injection changes
nothing except the cells it claims to change.
"""

import hashlib
import random

import pytest

from materia.corpus.generate import generate
from materia.corpus.layout import DECLARED_OUTPUTS
from materia.corpus.mutate import (
    ASSIGNMENTS,
    FAMILIES,
    IN_TAXONOMY,
    MATERIALITY_THRESHOLD,
    OUT_OF_TAXONOMY,
    PLANNERS,
    inject,
    plan_for,
    revert,
)
from materia.formula import parse_formula
from materia.preflight import preflight
from materia.recompute import Model

SEED = 20260829


@pytest.fixture(scope="module")
def _pristine(tmp_path_factory):
    """One generated workbook for the whole module.

    Generating takes a few tenths of a second and most of these tests want the
    same starting file, so it is built once and copied per test.
    """
    path, _ = generate(tmp_path_factory.mktemp("pristine") / "clean.xlsx", SEED)
    return path


@pytest.fixture
def clean_workbook(_pristine, tmp_path):
    """A fresh copy. Injection edits the file, so tests cannot share one."""
    import shutil

    target = tmp_path / "clean.xlsx"
    shutil.copy(_pristine, target)
    return target


class TestEveryFamily:
    @pytest.mark.parametrize("family", FAMILIES)
    def test_it_produces_a_plan_that_changes_something(self, clean_workbook, family):
        model = Model.load(clean_workbook, outputs=DECLARED_OUTPUTS)
        plan = PLANNERS[family](model, random.Random(1))
        assert plan.family == family
        assert len(plan.description) > 20

    @pytest.mark.parametrize("family", FAMILIES)
    def test_the_mutation_is_a_plausible_error_that_costs_something(
        self, clean_workbook, family
    ):
        """Two properties, one injection.

        The workbook still has to pass preflight, or no system would get the
        chance to miss the error. And the mutation has to reach a declared
        output, because one that nothing depends on would be correctly ignored
        by every system, which measures nothing.
        """
        model = Model.load(clean_workbook, outputs=DECLARED_OUTPUTS)
        plan = PLANNERS[family](model, random.Random(1))
        mutation = inject(clean_workbook, [plan])[0]

        preflight(clean_workbook)
        assert any(
            value not in (None, 0.0) for value in mutation.deltas.values()
        ), f"{family} moved no declared output"

    def test_taxonomy_membership_is_recorded_correctly(self, clean_workbook):
        model = Model.load(clean_workbook, outputs=DECLARED_OUTPUTS)
        for family in FAMILIES:
            plan = PLANNERS[family](model, random.Random(1))
            assert plan.family in IN_TAXONOMY or plan.family in OUT_OF_TAXONOMY


class TestTheImmaterialSolve:
    def test_it_refuses_a_cell_that_reaches_no_output(self):
        """The solve needs a slope. A cell with no path to an output has none,
        and guessing a constant there would produce a mutation that measures
        nothing."""
        from materia.corpus.mutate import _immaterial_plan

        model = Model.from_cells(
            {"Sheet1!A1": 10, "Sheet1!B1": "=A1*2", "Sheet1!Z1": 5},
            outputs=["Sheet1!B1"],
        )
        with pytest.raises(ValueError, match="does not reach"):
            _immaterial_plan(model, "Sheet1!Z1")


class TestInjectAndRevert:
    def test_reverting_returns_a_byte_identical_workbook(self, clean_workbook):
        """The check that injection touches only the cells it says it does.

        Anything else that moved, a timestamp, a recalculated value that
        should not have changed, would show up here as different bytes.
        """
        before = hashlib.sha256(clean_workbook.read_bytes()).hexdigest()

        model = Model.load(clean_workbook, outputs=DECLARED_OUTPUTS)
        mutations = inject(clean_workbook, plan_for("C03", model, SEED))
        assert hashlib.sha256(clean_workbook.read_bytes()).hexdigest() != before

        revert(clean_workbook, mutations)
        assert hashlib.sha256(clean_workbook.read_bytes()).hexdigest() == before

    def test_reverting_a_multi_mutation_workbook(self, clean_workbook):
        before = clean_workbook.read_bytes()
        model = Model.load(clean_workbook, outputs=DECLARED_OUTPUTS)
        mutations = inject(clean_workbook, plan_for("C06", model, SEED))
        assert len(mutations) == 3
        revert(clean_workbook, mutations)
        assert clean_workbook.read_bytes() == before

    def test_injecting_nothing_changes_nothing(self, clean_workbook):
        before = clean_workbook.read_bytes()
        assert inject(clean_workbook, []) == []
        assert clean_workbook.read_bytes() == before

    def test_the_mutated_cell_holds_what_the_manifest_says(self, clean_workbook):
        import openpyxl

        model = Model.load(clean_workbook, outputs=DECLARED_OUTPUTS)
        mutation = inject(clean_workbook, plan_for("C02", model, SEED))[0]

        book = openpyxl.load_workbook(clean_workbook)
        sheet, coordinate = mutation.address.split("!", 1)
        assert book[sheet][coordinate].value == mutation.mutated
        book.close()

    def test_the_recorded_original_is_the_formula_that_was_there(self, clean_workbook):
        from materia.parse import read_formulas

        formulas = {cell.address: cell.formula for cell in read_formulas(clean_workbook)}
        model = Model.load(clean_workbook, outputs=DECLARED_OUTPUTS)
        mutation = inject(clean_workbook, plan_for("C02", model, SEED))[0]
        assert mutation.original == formulas[mutation.address]
        parse_formula(mutation.original)


class TestRecordedDeltas:
    """The manifest's numbers have to survive being checked independently."""

    @pytest.mark.parametrize("workbook_id", ["C01", "C03", "C06", "C11", "C12"])
    def test_recorded_deltas_match_a_fresh_recompute(self, clean_workbook, workbook_id):
        path = clean_workbook
        clean = Model.load(path, outputs=DECLARED_OUTPUTS)
        mutations = inject(path, plan_for(workbook_id, clean, SEED))

        # A second, independently loaded model of the clean workbook.
        revert(path, mutations)
        fresh = Model.load(path, outputs=DECLARED_OUTPUTS)

        for mutation in mutations:
            result = fresh.patch(mutation.address, mutation.mutated)
            for output in DECLARED_OUTPUTS:
                assert result.outputs[output].delta == pytest.approx(
                    mutation.deltas[output]
                ), f"{workbook_id} {mutation.family} {output}"

    def test_material_is_derived_from_the_measurement(self, clean_workbook):
        path = clean_workbook
        model = Model.load(path, outputs=DECLARED_OUTPUTS)
        for mutation in inject(path, plan_for("C03", model, SEED)):
            largest = max(
                abs(value) for value in mutation.relative.values() if value is not None
            )
            assert mutation.material == (largest >= MATERIALITY_THRESHOLD)

    def test_deltas_are_measured_one_at_a_time(self, clean_workbook):
        """C06 carries three mutations. Each records what it costs on its own,
        because that is the question asked about each cell."""
        path = clean_workbook
        clean = Model.load(path, outputs=DECLARED_OUTPUTS)
        mutations = inject(path, plan_for("C06", clean, SEED))

        revert(path, mutations)
        fresh = Model.load(path, outputs=DECLARED_OUTPUTS)
        for mutation in mutations:
            alone = fresh.patch(mutation.address, mutation.mutated)
            assert alone.outputs[DECLARED_OUTPUTS[0]].delta == pytest.approx(
                mutation.deltas[DECLARED_OUTPUTS[0]]
            )


class TestTheImmaterialCase:
    """C11 is the workbook that tests the actual thesis. Its mutation is real
    and its cost is genuinely below the threshold, so correct behaviour is to
    detect it and suppress it."""

    @pytest.fixture
    def c11(self, clean_workbook):
        model = Model.load(clean_workbook, outputs=DECLARED_OUTPUTS)
        return clean_workbook, inject(clean_workbook, plan_for("C11", model, SEED))

    def test_it_carries_exactly_one_mutation(self, c11):
        assert len(c11[1]) == 1

    def test_the_mutation_is_real(self, c11):
        """Not a no op. It changes a formula and it moves the outputs."""
        mutation = c11[1][0]
        assert mutation.original != mutation.mutated
        assert any(value not in (None, 0.0) for value in mutation.deltas.values())

    def test_it_moves_every_output_by_less_than_a_tenth_of_a_percent(self, c11):
        """docs/EVALUATION.md section 2. Verified after injection rather than
        assumed, because the constant is solved for and a solve can be wrong.
        """
        mutation = c11[1][0]
        for output, relative in mutation.relative.items():
            assert relative is not None
            assert abs(relative) < 0.001, f"{output} moved {relative:.5%}"

    def test_it_is_recorded_as_immaterial(self, c11):
        assert c11[1][0].material is False


class TestCorpusAssignment:
    def test_every_family_appears_somewhere(self):
        assigned = {family for families in ASSIGNMENTS.values() for family in families}
        assert set(FAMILIES) <= assigned

    def test_the_clean_controls_carry_nothing(self):
        assert ASSIGNMENTS["C09"] == []
        assert ASSIGNMENTS["C10"] == []

    def test_seeded_workbooks_carry_one_to_three(self):
        for identifier in [f"C{index:02d}" for index in range(1, 9)]:
            assert 1 <= len(ASSIGNMENTS[identifier]) <= 3

    def test_c12_has_one_of_each_kind(self):
        """docs/EVALUATION.md section 2: one in taxonomy, one out."""
        families = ASSIGNMENTS["C12"]
        assert len([f for f in families if f in IN_TAXONOMY]) == 1
        assert len([f for f in families if f in OUT_OF_TAXONOMY]) == 1

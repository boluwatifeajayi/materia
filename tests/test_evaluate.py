"""Evaluator tests.

Every metric is checked against a hand built manifest and result set, so the
numbers in the submission rest on arithmetic somebody can follow rather than
on the evaluator agreeing with itself.

The last class is the thesis checkpoint. If detector only precision ever comes
back high, the premise of the project is wrong and that test fails rather than
the result quietly changing.
"""

import json
from pathlib import Path

import pytest

from materia.evaluate import (
    Finding,
    Scores,
    changelog_evidence,
    detector_results,
    headline_table,
    per_workbook_table,
    run_cost,
    score,
    update_changelog,
    update_results_table,
    write_results,
)

CORPUS = Path("corpus")

# Detector only precision above this would mean structural detection alone is
# already good enough, and the project has no case to make.
THESIS_PRECISION_CEILING = 0.50


def manifest_of(*workbooks) -> dict:
    return {
        "version": 1,
        "seed": 1,
        "months": 24,
        "declared_outputs": ["S!A1", "S!A2"],
        "workbooks": list(workbooks),
    }


def workbook(identifier, role="seeded", mutations=()):
    return {
        "id": identifier,
        "file": f"{identifier}.xlsx",
        "seed": 1,
        "role": role,
        "declared_outputs": ["S!A1", "S!A2"],
        "output_values": {"S!A1": 1.0, "S!A2": 2.0},
        "formula_count": 10,
        "sha256": "0" * 64,
        "legitimate_breaks": [],
        "mutations": list(mutations),
    }


def mutation(address, material=True, original="=A1+A2"):
    return {
        "family": "M1",
        "address": address,
        "original": original,
        "mutated": 5,
        "description": "a seeded error for testing purposes",
        "in_taxonomy": True,
        "deltas": {"S!A1": 1.0, "S!A2": 1.0},
        "relative": {"S!A1": 0.5 if material else 0.0001, "S!A2": 0.0},
        "material": material,
    }


class TestMetrics:
    def test_a_perfect_system(self):
        data = manifest_of(workbook("C01", mutations=[mutation("Sheet!B2")]))
        result = score("perfect", {"C01": [Finding("Sheet!B2")]}, data)
        assert result.material_precision == 1.0
        assert result.material_recall == 1.0
        assert result.raw_anomaly_recall == 1.0

    def test_precision_counts_only_material_hits(self):
        """Two findings, one real and material, one invented. 50 percent."""
        data = manifest_of(workbook("C01", mutations=[mutation("Sheet!B2")]))
        result = score(
            "half", {"C01": [Finding("Sheet!B2"), Finding("Sheet!Z9")]}, data
        )
        assert result.material_precision == 0.5

    def test_an_immaterial_hit_does_not_count_towards_precision(self):
        """Finding a real error that does not matter is not a win. The user
        opened a cell for nothing."""
        data = manifest_of(
            workbook("C01", mutations=[mutation("Sheet!B2", material=False)])
        )
        result = score("immaterial", {"C01": [Finding("Sheet!B2")]}, data)
        assert result.material_precision == 0.0
        assert result.raw_anomaly_recall == 1.0  # it was still found

    def test_material_recall_ignores_immaterial_mutations(self):
        data = manifest_of(
            workbook(
                "C01",
                mutations=[mutation("Sheet!B2"), mutation("Sheet!B3", material=False)],
            )
        )
        result = score("partial", {"C01": [Finding("Sheet!B2")]}, data)
        assert result.material_recall == 1.0
        assert result.raw_anomaly_recall == 0.5

    def test_false_positives_are_averaged_over_the_clean_workbooks(self):
        data = manifest_of(
            workbook("C09", role="clean_control"),
            workbook("C10", role="clean_control"),
        )
        result = score(
            "noisy",
            {"C09": [Finding("S!A1"), Finding("S!A2")], "C10": [Finding("S!A3")]},
            data,
        )
        assert result.false_positives_per_clean_workbook == 1.5

    def test_localisation_separates_right_sheet_from_right_cell(self):
        """A finding on the right sheet but the wrong cell is not useful to
        somebody who has to go and look at it."""
        data = manifest_of(
            workbook(
                "C01", mutations=[mutation("Sheet!B2"), mutation("Sheet!B3")]
            )
        )
        result = score("vague", {"C01": [Finding("Sheet!B2"), Finding("Sheet!Q9")]}, data)
        assert result.localisation_accuracy == 0.5

    def test_repair_accuracy_compares_against_the_original_formula(self):
        data = manifest_of(
            workbook("C01", mutations=[mutation("Sheet!B2", original="=A1+A2")])
        )
        results = {
            "C01": [
                Finding("Sheet!B2", proposed_formula="=A1+A2"),
                Finding("Sheet!B9", proposed_formula="=SUM(A1:A9)"),
            ]
        }
        assert score("repair", results, data).repair_accuracy == 0.5

    def test_repair_comparison_ignores_spacing_and_case(self):
        data = manifest_of(
            workbook("C01", mutations=[mutation("Sheet!B2", original="=A1+A2")])
        )
        results = {"C01": [Finding("Sheet!B2", proposed_formula="= a1 + a2")]}
        assert score("repair", results, data).repair_accuracy == 1.0

    def test_a_repair_can_be_compared_against_a_constant(self):
        """M6 mutates an assumption value, so the original is a number rather
        than a formula and the comparison has to cope."""
        data = manifest_of(workbook("C01", mutations=[mutation("Sheet!B2", original=0.24)]))
        results = {"C01": [Finding("Sheet!B2", proposed_formula="0.24")]}
        assert score("constant", results, data).repair_accuracy == 1.0

    def test_addresses_match_regardless_of_dollars(self):
        data = manifest_of(workbook("C01", mutations=[mutation("Sheet!B2")]))
        result = score("dollars", {"C01": [Finding("Sheet!$B$2")]}, data)
        assert result.material_recall == 1.0


class TestMissingDenominators:
    """A metric with nothing to divide by is reported as not applicable.

    Zero would be a claim. There is no claim to make."""

    def test_a_system_that_reported_nothing_has_no_precision(self):
        data = manifest_of(workbook("C01", mutations=[mutation("Sheet!B2")]))
        assert score("silent", {"C01": []}, data).material_precision is None

    def test_a_corpus_with_no_material_mutations_has_no_material_recall(self):
        data = manifest_of(workbook("C09", role="clean_control"))
        assert score("clean", {"C09": []}, data).material_recall is None

    def test_a_system_that_proposes_no_repairs_has_no_repair_accuracy(self):
        data = manifest_of(workbook("C01", mutations=[mutation("Sheet!B2")]))
        assert score("no repairs", {"C01": [Finding("Sheet!B2")]}, data).repair_accuracy is None

    def test_not_applicable_renders_as_such(self):
        data = manifest_of(workbook("C01", mutations=[mutation("Sheet!B2")]))
        table = headline_table([score("no repairs", {"C01": [Finding("Sheet!B2")]}, data)])
        assert "| Repair accuracy | n/a |" in table


class TestRendering:
    @pytest.fixture
    def scored(self):
        data = manifest_of(
            workbook("C01", mutations=[mutation("Sheet!B2")]),
            workbook("C09", role="clean_control"),
        )
        return score("System", {"C01": [Finding("Sheet!B2")], "C09": [Finding("S!Z1")]}, data)

    def test_the_headline_table_has_a_row_per_metric(self, scored):
        table = headline_table([scored])
        for label in (
            "Material finding precision",
            "Material recall",
            "Raw anomaly recall",
            "False positives per clean workbook",
            "Localisation accuracy",
            "Repair accuracy",
        ):
            assert f"| {label} |" in table

    def test_the_per_workbook_table_has_a_row_per_workbook(self, scored):
        table = per_workbook_table([scored])
        assert "| C01 | seeded |" in table
        assert "| C09 | clean control |" in table

    def test_no_em_dashes_anywhere(self, scored):
        assert "—" not in headline_table([scored])
        assert "—" not in per_workbook_table([scored])

    def test_write_results_produces_the_three_files(self, scored, tmp_path):
        written = write_results([scored], tmp_path)
        assert set(written) == {"headline", "per_workbook", "scores"}
        for path in written.values():
            assert path.exists()
        data = json.loads((tmp_path / "scores.json").read_text())
        assert data[0]["system"] == "System"


class TestChangelog:
    def test_it_fills_the_matching_row(self, tmp_path):
        readme = tmp_path / "README.md"
        readme.write_text(
            "| Stage | What | Evidence | Decision |\n"
            "| --- | --- | --- | --- |\n"
            "| **Iteration 1** | detectors only | `[TBD]` | expect poor precision |\n"
        )
        data = manifest_of(workbook("C01", mutations=[mutation("Sheet!B2")]))
        scored = score("Detectors only", {"C01": [Finding("Sheet!B2")]}, data)

        assert update_changelog(readme, "Iteration 1", scored) is True
        text = readme.read_text()
        assert "`[TBD]`" not in text
        assert "100% material precision" in text
        assert "| expect poor precision |" in text  # the other cells are untouched

    def test_it_refuses_to_invent_a_row(self, tmp_path):
        """A changelog that grew its own rows would be a changelog nobody
        wrote."""
        readme = tmp_path / "README.md"
        readme.write_text("no table here\n")
        data = manifest_of(workbook("C01", mutations=[mutation("Sheet!B2")]))
        scored = score("Detectors only", {"C01": [Finding("Sheet!B2")]}, data)
        assert update_changelog(readme, "Iteration 1", scored) is False
        assert readme.read_text() == "no table here\n"

    def test_it_refuses_a_malformed_row(self, tmp_path):
        readme = tmp_path / "README.md"
        readme.write_text("| **Iteration 1** | only two cells |\n")
        data = manifest_of(workbook("C01", mutations=[mutation("Sheet!B2")]))
        scored = score("Detectors only", {"C01": [Finding("Sheet!B2")]}, data)
        assert update_changelog(readme, "Iteration 1", scored) is False

    def test_the_evidence_cell_names_every_headline_number(self):
        data = manifest_of(workbook("C01", mutations=[mutation("Sheet!B2")]))
        text = changelog_evidence(score("x", {"C01": [Finding("Sheet!B2")]}, data))
        for word in ("precision", "recall", "false positives", "findings reported"):
            assert word in text


class TestTheDetectorOnlyRun:
    @staticmethod
    @pytest.fixture(scope="class")
    def scored():
        data = json.loads((CORPUS / "manifest.json").read_text())
        return score("Detectors only", detector_results(CORPUS, data), data)

    def test_one_finding_per_cell_not_per_candidate(self, scored):
        """A cell caught by two detectors is one thing for a user to open."""
        from materia.detect import detect, load

        candidates = detect(load(CORPUS / "C03.xlsx"))
        cells = len({item.address for item in candidates})
        row = next(r for r in scored.per_workbook if r.workbook == "C03")
        assert row.reported == cells
        assert len(candidates) >= cells

    def test_recall_is_high(self, scored):
        """Detection is the part that is already solved."""
        assert scored.material_recall >= 0.80

    def test_the_clean_controls_are_noisy(self, scored):
        assert scored.false_positives_per_clean_workbook > 10

    def test_repair_accuracy_is_not_applicable(self, scored):
        """Detectors propose nothing, so there is nothing to be right about.
        Reporting zero would imply they tried and failed."""
        assert scored.repair_accuracy is None


class TestTheThesisCheckpoint:
    """TASKS.md stops the build here if structural detection is already good
    enough on its own. This test is that checkpoint, kept in code so it cannot
    be forgotten."""

    def test_detector_only_precision_is_low(self):
        data = json.loads((CORPUS / "manifest.json").read_text())
        scored = score("Detectors only", detector_results(CORPUS, data), data)
        assert scored.material_precision is not None
        assert scored.material_precision < THESIS_PRECISION_CEILING, (
            f"Detector only precision is {scored.material_precision:.0%}. "
            "If structural detection alone is this good, the premise of this "
            "project is wrong and the framing needs to change."
        )


class TestTheResultsTableInTheDoc:
    """Rule 7 of the working agreement: no number that belongs in results/ is
    typed into a doc by hand."""

    TABLE = (
        "| Metric | Detectors only | Baseline | Materia |\n"
        "| --- | --- | --- | --- |\n"
        "| Material finding precision | 5% | `[TBD]` | `[TBD]` |\n"
        "| Material recall | 93% | `[TBD]` | `[TBD]` |\n"
        "| Cost per workbook | none | `[TBD]` | `[TBD]` |\n"
    )

    def _scores(self):
        return Scores(
            system="Baseline agent", material_precision=0.83, material_recall=0.71,
            raw_anomaly_recall=0.73, false_positives_per_clean_workbook=0.5,
            localisation_accuracy=1.0, repair_accuracy=0.92, reported=12,
            per_workbook=(),
        )

    def test_it_fills_the_named_column_only(self, tmp_path):
        document = tmp_path / "EVALUATION.md"
        document.write_text(self.TABLE)
        filled = update_results_table(document, "Baseline", self._scores(),
                                      {"cost": "$0.41"})
        text = document.read_text()
        assert "| Material finding precision | 5% | 83% | `[TBD]` |" in text
        assert "| Material recall | 93% | 71% | `[TBD]` |" in text
        assert "| Cost per workbook | none | $0.41 | `[TBD]` |" in text
        assert len(filled) == 3

    def test_an_absent_column_changes_nothing(self, tmp_path):
        document = tmp_path / "EVALUATION.md"
        document.write_text(self.TABLE)
        assert update_results_table(document, "Nonexistent", self._scores()) == []
        assert document.read_text() == self.TABLE

    def test_a_row_it_does_not_know_is_left_alone(self, tmp_path):
        """So a row somebody added by hand is not overwritten with a guess."""
        document = tmp_path / "EVALUATION.md"
        document.write_text(self.TABLE + "| Something else | a | b | c |\n")
        update_results_table(document, "Baseline", self._scores())
        assert "| Something else | a | b | c |" in document.read_text()

    def test_an_unpriced_model_reports_no_cost(self, tmp_path):
        (tmp_path / "provider.json").write_text(json.dumps({"model": "not-a-real-model"}))
        (tmp_path / "C01.json").write_text(json.dumps({"tokens": {"in": 1000, "out": 100}}))
        assert run_cost(tmp_path) == {}

    def test_the_cost_is_the_average_of_what_the_runs_spent(self, tmp_path):
        (tmp_path / "provider.json").write_text(json.dumps({"model": "gpt-5.6-terra"}))
        for name in ("C01", "C02"):
            (tmp_path / f"{name}.json").write_text(
                json.dumps({"tokens": {"in": 1_000_000, "out": 100_000}})
            )
        # $2.00 per million in, $12.00 per million out, same both runs.
        assert run_cost(tmp_path) == {"cost": "$3.20 on `gpt-5.6-terra`"}

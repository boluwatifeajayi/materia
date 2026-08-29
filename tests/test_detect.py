"""Detector tests.

Two things are asserted here and the second matters as much as the first.
Each detector fires on the mutation family it targets, and the detectors also
fire on C10's legitimate pattern breaks. The second is not a defect being
documented, it is the premise of the whole project: structural detection
cannot tell a deliberate override from a mistake, so something downstream has
to.
"""

import json
from pathlib import Path

import pytest

from materia.detect import DETECTOR_FAMILIES, DETECTORS, Candidate, detect, load
from materia.detect.detectors import _is_operator_change, _is_period_shift

CORPUS = Path("corpus")


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads((CORPUS / "manifest.json").read_text())


@pytest.fixture(scope="module")
def candidates(manifest) -> dict[str, list[Candidate]]:
    return {
        entry["id"]: detect(load(CORPUS / entry["file"]))
        for entry in manifest["workbooks"]
    }


def mutations_of(manifest: dict, family: str) -> list[tuple[str, str]]:
    """Every (workbook id, cell) carrying a given mutation family."""
    return [
        (entry["id"], mutation["address"])
        for entry in manifest["workbooks"]
        for mutation in entry["mutations"]
        if mutation["family"] == family
    ]


class TestEachDetectorFiresOnItsOwnFamily:
    @pytest.mark.parametrize("detector,family", sorted(DETECTOR_FAMILIES.items()))
    def test_it_fires_somewhere_in_the_corpus(
        self, manifest, candidates, detector, family
    ):
        seeded = mutations_of(manifest, family)
        assert seeded, f"no {family} mutation in the corpus to detect"

        hits = [
            (workbook_id, address)
            for workbook_id, address in seeded
            if any(
                item.address == address and item.detector == detector
                for item in candidates[workbook_id]
            )
        ]
        assert hits, f"{detector} never fired on a {family} mutation"


class TestRecall:
    def test_every_in_taxonomy_mutation_is_flagged(self, manifest, candidates):
        """By some detector, not necessarily the matching one. Recall is what
        this layer is for."""
        missed = []
        for entry in manifest["workbooks"]:
            flagged = {item.address for item in candidates[entry["id"]]}
            for mutation in entry["mutations"]:
                if mutation["in_taxonomy"] and mutation["address"] not in flagged:
                    missed.append((entry["id"], mutation["family"], mutation["address"]))
        assert not missed, missed

    def test_the_out_of_taxonomy_assumption_error_is_missed(self, manifest, candidates):
        """docs/EVALUATION.md section 3 predicts this and we report it.

        A wrong assumption value is structurally perfect. There is no peer
        group to disagree with it, so nothing structural can see it. This is
        the honest limit of the approach.
        """
        for workbook_id, address in mutations_of(manifest, "M6"):
            flagged = {item.address for item in candidates[workbook_id]}
            assert address not in flagged, f"M6 at {address} was unexpectedly flagged"


class TestThePrecisionProblem:
    """The measurements that make the case for an agent layer."""

    def test_the_clean_control_produces_many_candidates(self, candidates):
        """C09 has nothing wrong with it at all."""
        assert len(candidates["C09"]) > 15

    def test_candidates_vastly_outnumber_real_errors(self, manifest, candidates):
        total = sum(len(items) for items in candidates.values())
        seeded = sum(len(entry["mutations"]) for entry in manifest["workbooks"])
        assert total > seeded * 10, (total, seeded)


class TestC10LegitimateBreaks:
    """The detectors fire on all three. That is expected and documented."""

    @pytest.fixture
    def c10(self, manifest, candidates):
        entry = next(e for e in manifest["workbooks"] if e["id"] == "C10")
        return entry, candidates["C10"]

    def test_the_hardcoded_actuals_row_is_flagged(self, c10):
        entry, items = c10
        actuals = next(
            b for b in entry["legitimate_breaks"] if b["kind"] == "hardcoded_actuals"
        )
        flagged = {item.address for item in items if item.detector == "D1"}
        assert set(actuals["cells"]) <= flagged

    def test_the_manual_override_is_flagged(self, c10):
        entry, items = c10
        override = next(
            b for b in entry["legitimate_breaks"] if b["kind"] == "manual_override"
        )
        flagged = {item.address for item in items}
        assert set(override["cells"]) <= flagged

    def test_the_first_period_column_is_flagged(self, c10):
        entry, items = c10
        first = next(
            b for b in entry["legitimate_breaks"] if b["kind"] == "first_period"
        )
        flagged = {item.address for item in items}
        assert flagged & set(first["cells"]), "no first period cell was flagged"

    def test_a_clean_control_is_not_reported_clean(self, c10):
        """The whole point. C10 contains no errors and the detectors have
        plenty to say about it."""
        _, items = c10
        assert len(items) > 15


class TestCandidateEvidence:
    def test_every_candidate_carries_a_structural_reason(self, candidates):
        for items in candidates.values():
            for item in items:
                assert len(item.reason) > 25
                assert item.detector in DETECTORS

    def test_peer_based_candidates_carry_peer_evidence(self, candidates):
        """A hypothesis with no supporting pattern is not a hypothesis. The
        adjudicator is told to return INCONCLUSIVE without one, so the
        detector has to supply it."""
        for items in candidates.values():
            for item in items:
                if item.detector in ("D2", "D4", "D5"):
                    assert item.peers, item.address
                    assert item.expected_r1c1
                    assert item.r1c1 != item.expected_r1c1

    def test_candidates_are_ordered_stably(self, candidates):
        for items in candidates.values():
            assert items == sorted(items, key=lambda c: (c.address, c.detector))


class TestClassification:
    """D4 and D5 are narrower readings of the same non conforming cell."""

    @pytest.mark.parametrize(
        "token,mode,expected",
        [
            ("RC[-1]*(1+R[-12]C[-1])", "RC[-2]*(1+R[-12]C[-1])", True),
            ("R[-4]C+R[-3]C", "R[-4]C[-1]+R[-3]C", True),
            ("RC[-1]", "RC[-9]", False),  # too far to be a period slip
            ("RC[-1]+RC[-2]", "RC[-1]-RC[-2]", False),  # an operator, not an offset
            ("Assumptions!R4C2", "R[4]C[-1]", False),
        ],
    )
    def test_period_shift(self, token, mode, expected):
        assert _is_period_shift(token, mode) is expected

    @pytest.mark.parametrize(
        "token,mode,expected",
        [
            ("RC[-1]+RC[-2]", "RC[-1]-RC[-2]", True),
            ("R[-2]C-R[-1]C", "R[-2]C+R[-1]C", True),
            ("RC[-1]+RC[-2]", "RC[-1]+RC[-3]", False),  # a reference moved
            ("RC[-1]+RC[-2]", "RC[-1]+RC[-2]", False),  # identical
        ],
    )
    def test_operator_change(self, token, mode, expected):
        assert _is_operator_change(token, mode) is expected


class TestDetectorsAreNotSmart:
    def test_they_never_read_the_ground_truth(self):
        """The detectors must not know what was seeded.

        If they could read the manifest the corpus would be measuring whether
        we built a mutation recogniser, which is the threat to validity named
        in docs/EVALUATION.md section 6.
        """
        for path in Path("src/materia/detect").rglob("*.py"):
            text = path.read_text().lower()
            assert "manifest" not in text, f"{path} refers to the manifest"
            assert "corpus" not in text, f"{path} refers to the corpus"


class TestD3AlongAColumn:
    """The corpus only aggregates across rows, so the column path needs a
    workbook of its own or it ships untested."""

    @staticmethod
    def _workbook(tmp_path, total_formula: str):
        import openpyxl

        path = tmp_path / "column.xlsx"
        book = openpyxl.Workbook()
        sheet = book.active
        sheet.title = "Sheet1"
        for row in range(1, 9):
            sheet[f"A{row}"] = row * 10
            sheet[f"B{row}"] = f"=A{row}*2"
        sheet["B10"] = total_formula
        book.save(path)
        return load(path)

    def test_a_range_that_stops_short_is_flagged(self, tmp_path):
        workbook = self._workbook(tmp_path, "=SUM(B1:B7)")
        flagged = [c for c in detect(workbook) if c.detector == "D3"]
        assert any(c.address == "Sheet1!B10" for c in flagged)
        assert "rows 1:7" in next(c for c in flagged if c.address == "Sheet1!B10").reason

    def test_a_range_that_covers_the_block_is_not_flagged(self, tmp_path):
        workbook = self._workbook(tmp_path, "=SUM(B1:B8)")
        assert not [c for c in detect(workbook) if c.detector == "D3"]

    def test_a_range_that_misses_rows_above_it_is_flagged(self, tmp_path):
        """A range can be short at either end. Extending only downwards would
        miss the case where a row was inserted at the top of the block."""
        workbook = self._workbook(tmp_path, "=SUM(B3:B8)")
        flagged = [c for c in detect(workbook) if c.detector == "D3"]
        assert any("rows 3:8" in c.reason and "1:8" in c.reason for c in flagged)

    def test_a_rectangular_range_is_left_alone(self, tmp_path):
        """A block with no single axis has no run to compare against, so
        guessing at one would be inventing evidence."""
        workbook = self._workbook(tmp_path, "=SUM(A1:B4)")
        assert not [c for c in detect(workbook) if c.detector == "D3"]


class TestHelpers:
    def test_candidate_reports_its_sheet(self):
        assert Candidate("D1", "P&L!AA15", "x" * 30).sheet == "P&L"

    def test_a_token_that_is_not_a_reference_has_no_axes(self):
        from materia.detect.detectors import _axis_values

        assert _axis_values("SUM") == (None, None)

    def test_a_different_number_of_references_is_not_a_period_shift(self):
        assert _is_period_shift("RC[-1]+RC[-2]", "RC[-1]") is False

    def test_an_identical_token_is_not_a_period_shift(self):
        assert _is_period_shift("RC[-1]", "RC[-1]") is False

    def test_an_absolute_reference_moving_far_is_not_a_period_shift(self):
        assert _is_period_shift("Assumptions!R4C2", "Assumptions!R18C2") is False

"""The materiality gate. docs/ARCHITECTURE.md section 7.

This is the change the project's central claim rests on, so the tests here
are about the boundary and the bookkeeping rather than the happy path: a
finding that sits exactly on the threshold, a finding with no measurable
relative impact, and the invariant that every candidate ends up in exactly
one bucket.
"""


import pytest

from materia.audit import BucketsDoNotSum, check_buckets, from_trajectories
from materia.report import (
    DEFAULT_THRESHOLD,
    CrossCheck,
    Finding,
    apply_materiality,
    load_threshold,
)

TRACES = "trajectories/solution_scored"


def finding(address: str, relative: dict) -> Finding:
    return Finding(
        address=address, detector="D1", confidence="high",
        current_formula="1", proposed_formula="=A1", reasoning="",
        evidence=(),
        deltas={k: (v * 100 if v is not None else None) for k, v in relative.items()},
        relative=relative,
    )


def result(*findings, intentional=(), inconclusive=()) -> CrossCheck:
    return CrossCheck(
        findings=tuple(findings), violations=(),
        intentional=tuple(intentional), inconclusive=tuple(inconclusive),
    )


class TestTheThreshold:
    def test_a_large_impact_is_kept(self):
        gated = apply_materiality(result(finding("A1", {"Out": 0.5})), 0.01)
        assert [f.address for f in gated.findings] == ["A1"]
        assert gated.immaterial == ()

    def test_a_small_impact_is_moved_not_dropped(self):
        gated = apply_materiality(result(finding("A1", {"Out": 0.0001})), 0.01)
        assert gated.findings == ()
        assert [f.address for f in gated.immaterial] == ["A1"]

    def test_exactly_on_the_threshold_is_suppressed(self):
        """The spec says a finding is shown only if its delta *exceeds* the
        threshold. Equal is not greater."""
        gated = apply_materiality(result(finding("A1", {"Out": 0.01})), 0.01)
        assert gated.findings == ()
        assert len(gated.immaterial) == 1

    def test_a_hair_over_is_kept(self):
        gated = apply_materiality(result(finding("A1", {"Out": 0.010001})), 0.01)
        assert len(gated.findings) == 1

    def test_one_output_over_the_line_is_enough(self):
        """At least one declared output, not all of them."""
        gated = apply_materiality(
            result(finding("A1", {"Small": 0.0001, "Big": 0.4})), 0.01
        )
        assert len(gated.findings) == 1

    def test_sign_does_not_matter(self):
        gated = apply_materiality(result(finding("A1", {"Out": -0.5})), 0.01)
        assert len(gated.findings) == 1

    def test_an_output_that_broke_is_not_counted_as_movement(self):
        """A delta of None means the output became an error or text. It is not
        a large move and it is not a small one."""
        gated = apply_materiality(result(finding("A1", {"Out": None})), 0.01)
        assert len(gated.immaterial) == 1

    def test_only_the_gate_assigns_immaterial(self):
        """INTENTIONAL and INCONCLUSIVE never reach it: a candidate the model
        did not call an error has no delta to weigh."""
        before = result(intentional=("a", "b"), inconclusive=("c",))
        after = apply_materiality(before, 0.01)
        assert after.intentional == before.intentional
        assert after.inconclusive == before.inconclusive
        assert after.immaterial == ()


class TestTheThresholdComesFromConfig:
    def test_it_reads_the_published_value(self):
        assert load_threshold() == 0.01

    def test_a_missing_config_falls_back_rather_than_crashing(self, tmp_path):
        assert load_threshold(tmp_path / "nothing.yaml") == DEFAULT_THRESHOLD

    def test_a_caller_can_override_it(self):
        """`--materiality` and `results/sensitivity.md` both depend on this."""
        one = apply_materiality(result(finding("A1", {"Out": 0.05})), 0.10)
        assert one.findings == ()
        two = apply_materiality(result(finding("A1", {"Out": 0.05})), 0.001)
        assert len(two.findings) == 1


class TestTheBucketsSum:
    def test_it_accepts_a_complete_account(self):
        check_buckets(result(finding("A1", {"Out": 0.5}), intentional=("b",)), 2)

    def test_it_refuses_a_candidate_that_fell_out_of_every_bucket(self):
        with pytest.raises(BucketsDoNotSum) as raised:
            check_buckets(result(finding("A1", {"Out": 0.5})), 5)
        assert "5 candidates were adjudicated" in str(raised.value)

    def test_a_suppressed_finding_still_counts(self):
        gated = apply_materiality(result(finding("A1", {"Out": 0.0001})), 0.01)
        check_buckets(gated, 1)

    @pytest.mark.parametrize("identifier", [f"C{n:02d}" for n in range(1, 13)])
    def test_it_holds_on_every_workbook_of_the_scored_run(self, identifier):
        """Not just in a unit test. This reads the committed trajectories of
        the real run and checks the invariant on all 267 candidates."""
        rebuilt = from_trajectories(f"corpus/{identifier}.xlsx", TRACES)
        check_buckets(rebuilt.result, len(rebuilt.verdicts))
        assert rebuilt.funnel.adjudicated == rebuilt.result.accounted


class TestTheKnownTarget:
    """`Costs!Z12` in `C11` is a real mutation that moves the largest declared
    output by 3.0 basis points. Before the gate it was reported, and it was
    the only thing costing Iteration 2 its precision."""

    def test_it_is_suppressed_rather_than_reported(self):
        rebuilt = from_trajectories("corpus/C11.xlsx", TRACES)
        assert [f.address for f in rebuilt.result.findings] == []
        assert [f.address for f in rebuilt.result.immaterial] == ["Costs!Z12"]

    def test_it_is_not_missing_it_is_accounted_for(self):
        """Detected and suppressed, versus never found, are opposite
        outcomes. The funnel has to tell them apart."""
        rebuilt = from_trajectories("corpus/C11.xlsx", TRACES)
        assert rebuilt.funnel.survived == 1
        assert rebuilt.funnel.findings == 0
        assert rebuilt.funnel.suppressed == 1

    def test_the_report_names_it_rather_than_only_counting_it(self):
        rendered = from_trajectories("corpus/C11.xlsx", TRACES).render()
        assert "suppressed as immaterial" in rendered
        assert "Costs!Z12" in rendered
        assert "below the 1.00% threshold" in rendered

    def test_its_measured_size(self):
        rebuilt = from_trajectories("corpus/C11.xlsx", TRACES)
        suppressed = rebuilt.result.immaterial[0]
        assert suppressed.largest_relative == pytest.approx(0.0003, abs=5e-6)
        assert suppressed.deltas["P&L!AA15"] == pytest.approx(-4164.8, abs=0.1)

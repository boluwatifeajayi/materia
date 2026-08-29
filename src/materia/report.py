"""Report rendering and the cross check.

The cross check is the enforcement mechanism for the whole design. The
adjudicator's instructions say, as rule 1, never state an impact figure you
did not obtain from `recompute_with_patch`. That is a request. This module is
the constraint: every figure that reaches a user is read out of a
`tool_result` record in the trajectory, and a finding whose impact cannot be
traced to one is dropped.

This is not hypothetical. On the first candidate of the first live run the
model called the tool, received 8704573.0, and reported -6102169. See
README section 8. A prompt cannot stop that. Code can.

Findings are ordered by measured impact, largest first, because the reader
cares that enterprise value is overstated. The cell address is how they check
it, not why they opened the report.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from materia.adjudicate import Verdict
from materia.detect import Candidate
from materia.graph import DependencyGraph
from materia.trace import Record, read, tool_results

# Two figures are the same measurement if they agree this closely. Deltas are
# rounded on the way out of the tool, so an exact match is too strict.
TOLERANCE = 0.01


@dataclass(frozen=True)
class Violation:
    """A figure that did not survive the cross check."""

    address: str
    kind: str
    detail: str

    def __str__(self) -> str:
        return f"{self.address}: {self.kind}. {self.detail}"


@dataclass(frozen=True)
class Finding:
    """One verified finding, ready to render."""

    address: str
    detector: str
    confidence: str
    current_formula: str | None
    proposed_formula: str | None
    reasoning: str
    evidence: tuple[str, ...]
    deltas: dict[str, float]
    relative: dict[str, float | None]
    peers: tuple[Any, ...] = ()
    paths: dict[str, list[str]] = field(default_factory=dict)
    corrected: bool = False

    @property
    def largest_relative(self) -> float:
        values = [abs(v) for v in self.relative.values() if v is not None]
        return max(values) if values else 0.0


@dataclass(frozen=True)
class CrossCheck:
    """What survived, and what did not."""

    findings: tuple[Finding, ...]
    violations: tuple[Violation, ...]
    intentional: tuple[Verdict, ...]
    inconclusive: tuple[Verdict, ...]

    @property
    def dropped(self) -> int:
        return len({violation.address for violation in self.violations
                    if violation.kind == "unverifiable impact"})


def _measured_deltas(records: list[Record], cell: str, formula: str | None) -> dict | None:
    """The tool result for this exact hypothesis, if the model ran it.

    Matched on the call that produced it, not on the numbers, so a model
    cannot pass the check by reporting figures that happen to match some other
    patch it tried.
    """
    calls = {
        record.content.get("id"): record.content.get("arguments", {})
        for record in records
        if record.type == "tool_call"
    }
    for record in tool_results(records, "recompute_with_patch"):
        arguments = calls.get(record.content.get("id"), {})
        result = record.content.get("result")
        if not isinstance(result, dict) or "error" in result:
            continue
        if str(arguments.get("cell", "")).replace("$", "").upper() != cell.replace("$", "").upper():
            continue
        # The formula has to be the one being proposed. A model that measured
        # a different hypothesis and then proposed this one has not measured
        # this one, and reporting the other result against it would attach a
        # real number to a claim it does not belong to.
        if formula is not None and str(arguments.get("proposed_formula", "")).strip() != formula.strip():
            continue
        return result
    return None


def _agree(claimed: dict, measured: dict) -> bool:
    if not claimed:
        return True
    for output, value in claimed.items():
        if output not in measured:
            return False
        try:
            if abs(float(value) - float(measured[output])) > TOLERANCE:
                return False
        except (TypeError, ValueError):
            return False
    return True


def cross_check(
    verdicts: list[Verdict],
    model=None,
    graph: DependencyGraph | None = None,
    candidates: dict[str, Candidate] | None = None,
) -> CrossCheck:
    """Turn verdicts into findings, dropping anything unverifiable.

    Two outcomes are possible when a figure does not match the trajectory:

    - No tool result for the proposed repair at all. The impact is
      unverifiable and the finding is dropped, as docs/ARCHITECTURE.md
      section 5 requires.
    - A tool result exists and the model reported something else. The finding
      survives with the measured figure substituted, and the discrepancy is
      logged. Throwing away a correct finding because the model mis-stated a
      number it did have would lose a real error to a reporting mistake, and
      the number the user sees is measured either way.
    """
    findings: list[Finding] = []
    violations: list[Violation] = []
    intentional: list[Verdict] = []
    inconclusive: list[Verdict] = []
    candidates = candidates or {}

    for verdict in verdicts:
        if verdict.verdict == "INTENTIONAL":
            intentional.append(verdict)
            continue
        if verdict.verdict != "ERROR":
            inconclusive.append(verdict)
            continue

        records = read(verdict.trace_path) if verdict.trace_path else []
        measured = _measured_deltas(records, verdict.address, verdict.proposed_formula)

        if measured is None:
            violations.append(
                Violation(
                    verdict.address,
                    "unverifiable impact",
                    "the trajectory holds no recompute_with_patch result for the "
                    "proposed formula, so the reported impact cannot be checked.",
                )
            )
            continue

        corrected = not _agree(verdict.measured_deltas, measured)
        if corrected:
            violations.append(
                Violation(
                    verdict.address,
                    "impact figures did not match the trajectory",
                    f"reported {verdict.measured_deltas}, the engine returned "
                    f"{measured}. The measured figures are used.",
                )
            )

        candidate = candidates.get(verdict.address)
        findings.append(
            Finding(
                address=verdict.address,
                detector=verdict.detector,
                confidence=verdict.confidence,
                current_formula=(candidate.formula if candidate else None),
                proposed_formula=verdict.proposed_formula,
                reasoning=verdict.reasoning,
                evidence=verdict.evidence,
                deltas={k: float(v) for k, v in measured.items() if _is_number(v)},
                relative=_relative(measured, model),
                peers=(candidate.peers if candidate else ()),
                paths=(
                    graph.paths_to_outputs(verdict.address, list(measured))
                    if graph is not None
                    else {}
                ),
                corrected=corrected,
            )
        )

    findings.sort(key=lambda finding: finding.largest_relative, reverse=True)
    return CrossCheck(
        findings=tuple(findings),
        violations=tuple(violations),
        intentional=tuple(intentional),
        inconclusive=tuple(inconclusive),
    )


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _relative(measured: dict, model) -> dict[str, float | None]:
    """The change as a share of the output, for the gate and for the reader."""
    relative: dict[str, float | None] = {}
    for output, delta in measured.items():
        if not _is_number(delta):
            relative[output] = None
            continue
        baseline = model.value(output) if model is not None else None
        relative[output] = (
            abs(float(delta)) / abs(float(baseline))
            if _is_number(baseline) and float(baseline) != 0
            else None
        )
    return relative


# --- rendering -------------------------------------------------------------


# The model writes non ASCII dashes. CLAUDE.md section 5 bans them from
# anything a person reads, and a quoted sentence is still something a person
# reads, so they are normalised on the way out rather than left in.
_HYPHENS = "\u2010\u2011\u2012\u2013"
_EM_DASH = re.compile(r"\s*[\u2014\u2015]\s*")


def plain(text: str) -> str:
    """Replace non ASCII dashes.

    An em dash becomes a comma and one space, however it was spaced, since
    that is what it was standing in for. The narrower dashes become hyphens.
    """
    text = _EM_DASH.sub(", ", text)
    for character in _HYPHENS:
        text = text.replace(character, "-")
    return text


def _money(value: float) -> str:
    return f"{value:,.0f}"


def _share(value: float | None) -> str:
    return "not measurable" if value is None else f"{value:.2%}"


@dataclass(frozen=True)
class Funnel:
    """The four numbers from README section 4."""

    formulas: int
    candidates: int
    survived: int
    findings: int
    suppressed: int = 0

    def render(self, workbook: str) -> str:
        lines = [
            f"MODEL HEALTH{'':>22}{workbook}",
            "",
            f"  {self.formulas:>6}  formulas parsed",
            f"  {self.candidates:>6}  structural anomalies detected",
            f"  {self.survived:>6}  survived hypothesis testing",
            f"  {self.findings:>6}  material findings",
        ]
        if self.suppressed:
            lines.append(f"  {self.suppressed:>6}  suppressed as immaterial")
        return "\n".join(lines)


def render_card(finding: Finding, index: int) -> str:
    """One evidence card. Consequence first, cell reference second."""
    output, delta = max(
        finding.deltas.items(), key=lambda item: abs(item[1]), default=("", 0.0)
    )
    direction = "overstated by" if delta < 0 else "understated by"
    share = finding.relative.get(output)

    lines = [
        f"[{index}] {output} is {direction} {_money(abs(delta))}"
        + (f", {_share(share)} of its value" if share is not None else ""),
        f"    because {finding.address} does not match the rest of its row.",
        "",
        f"    Cell            {finding.address}",
        f"    Currently       {finding.current_formula or '(a value, not a formula)'}",
    ]
    if finding.proposed_formula:
        lines.append(f"    Should be       {finding.proposed_formula}")
    lines.append(f"    Confidence      {finding.confidence}")
    lines.append(f"    Detector        {finding.detector}")

    if finding.peers:
        lines.append("")
        lines.append("    Its neighbours")
        for peer in finding.peers[:3]:
            lines.append(f"      {peer.address:16} {peer.formula}")

    if finding.evidence:
        lines.append("")
        lines.append("    Evidence")
        for item in finding.evidence[:4]:
            lines.append(f"      {plain(item)}")

    path = finding.paths.get(output)
    if path and len(path) > 1:
        lines.append("")
        lines.append(f"    Reaches {output} in {len(path) - 1} steps")
        lines.append(f"      {' -> '.join(path[:4])}" + (" -> ..." if len(path) > 4 else ""))
    elif path:
        lines.append("")
        lines.append(f"    This cell is {output}.")

    lines.append("")
    lines.append("    Measured impact")
    for name, value in sorted(finding.deltas.items(), key=lambda i: -abs(i[1])):
        lines.append(f"      {name:20} {_money(value):>18}   {_share(finding.relative.get(name))}")

    if finding.corrected:
        lines.append("")
        lines.append(
            "    Note: the model reported different figures from the ones the "
            "engine returned. The measured figures are shown."
        )
    return "\n".join(lines)


def render(
    workbook: str,
    result: CrossCheck,
    funnel: Funnel,
    show_suppressed: bool = True,
) -> str:
    """The whole report.

    Suppression the user cannot see is indistinguishable from a bug, so what
    was set aside is stated rather than silently omitted.
    """
    blocks = [funnel.render(workbook), ""]

    if not result.findings:
        blocks.append("No material findings.")
    for index, finding in enumerate(result.findings, start=1):
        blocks.append(render_card(finding, index))
        blocks.append("")

    blocks.append("WHAT WAS SET ASIDE")
    blocks.append("")
    blocks.append(
        f"  {len(result.intentional):>6}  deliberate, and the evidence says so"
    )
    blocks.append(f"  {len(result.inconclusive):>6}  not enough evidence to say")
    if funnel.suppressed:
        blocks.append(f"  {funnel.suppressed:>6}  real but below the materiality threshold")
    if result.dropped:
        blocks.append(
            f"  {result.dropped:>6}  dropped: impact could not be traced to a measurement"
        )

    if show_suppressed and result.intentional:
        blocks.append("")
        blocks.append("  Judged deliberate")
        for verdict in result.intentional[:8]:
            blocks.append(f"    {verdict.address:18} {plain(verdict.reasoning)[:90]}")

    if result.violations:
        blocks.append("")
        blocks.append("  Schema violations")
        for violation in result.violations:
            blocks.append(f"    {violation}")

    return plain("\n".join(blocks)) + "\n"

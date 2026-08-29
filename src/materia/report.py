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
from dataclasses import dataclass, field, replace
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

    # Errors that are real and too small to matter. Held rather than
    # discarded: suppression the user cannot see is indistinguishable from a
    # bug, so the report states the count and the gate that produced it.
    immaterial: tuple[Finding, ...] = ()

    @property
    def dropped(self) -> int:
        return len({violation.address for violation in self.violations
                    if violation.kind == "unverifiable impact"})

    @property
    def accounted(self) -> int:
        """Every candidate that reached a conclusion, in exactly one bucket."""
        return (
            len(self.findings) + len(self.immaterial)
            + len(self.intentional) + len(self.inconclusive)
            + self.dropped
        )


def _measured_deltas(records: list[Record], cell: str, formula: str) -> dict | None:
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
        if str(arguments.get("proposed_formula", "")).strip() != formula.strip():
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


DEFAULT_THRESHOLD = 0.01


def load_threshold(path: str | Path = "config.yaml") -> float:
    """The threshold is a published config value, not a constant in here.

    docs/EVALUATION.md section 6 lists choosing it as a threat to validity,
    which is only answerable if a reader can change it and re run.
    """
    try:
        import yaml

        return float(yaml.safe_load(Path(path).read_text())["materiality"]["threshold"])
    except Exception:  # noqa: BLE001 - a missing config falls back, not crashes
        return DEFAULT_THRESHOLD


def apply_materiality(result: CrossCheck, threshold: float | None = None) -> CrossCheck:
    """Reclassify findings that move nothing enough to matter.

    docs/ARCHITECTURE.md section 7. A finding is shown only if its verified
    delta on at least one declared output exceeds the threshold as a fraction
    of that output's value.

    This is the only place `IMMATERIAL` is assigned. The adjudicator judges
    correctness from evidence; consequence is a threshold against a measured
    number, so it is settled here in code, after the fact, where it can be
    audited and re run at a different threshold without another model call.

    `INTENTIONAL` and `INCONCLUSIVE` never reach this function: a candidate
    the model did not call an error has no delta to weigh.
    """
    threshold = load_threshold() if threshold is None else threshold

    material, immaterial = [], []
    for finding in result.findings:
        (material if finding.largest_relative > threshold else immaterial).append(finding)

    return replace(
        result,
        findings=tuple(material),
        immaterial=tuple(result.immaterial) + tuple(immaterial),
    )


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

        if not verdict.proposed_formula:
            # An impact figure is the impact of a specific repair. With no
            # repair proposed there is no hypothesis for a measurement to
            # belong to, and matching on the cell alone would hand this claim
            # whichever hypothesis the model happened to try first. Rule 1 of
            # the adjudicator's instructions says ERROR requires a proposed
            # formula for exactly this reason.
            violations.append(
                Violation(
                    verdict.address,
                    "unverifiable impact",
                    "the verdict is ERROR with no proposed formula, so there is "
                    "nothing for a measurement to be the impact of.",
                )
            )
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
_EM_DASH = re.compile(r"\s*[\u2014\u2015]\s*")
_SUBSTITUTIONS = {
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u2026": "...", "\u2192": "->", "\u2190": "<-", "\u00a0": " ",
    "\u2022": "-", "\u00d7": "x",
}


def plain(text: str) -> str:
    """Make model prose safe to print in a terminal.

    An em dash becomes a comma and one space, however it was spaced, since
    that is what it was standing in for. Everything else in the table maps to
    the ASCII it stands for. Model output arrives full of these: arrows,
    ellipses, curly quotes and non breaking spaces all render inconsistently
    at a demo font size, and CLAUDE.md section 5 bans the dashes outright.
    """
    text = _EM_DASH.sub(", ", text)
    for character, replacement in _SUBSTITUTIONS.items():
        text = text.replace(character, replacement)
    return text


def _money(value: float) -> str:
    return f"{value:,.0f}"


def _share(value: float | None) -> str:
    return "not measurable" if value is None else f"{value:.2%}"


# Wide enough for the cards, narrow enough to read at a demo font size on a
# projector. Anything wider wraps in the terminal and the wrap lands mid number.
WIDTH = 74


@dataclass(frozen=True)
class Funnel:
    """The four numbers from README section 4.

    `adjudicated` exists so a bounded run cannot read as a complete one. A
    funnel that says twenty two anomalies were detected, without saying only
    seventeen were tested, implies the other five were cleared. They were not
    looked at.
    """

    formulas: int
    candidates: int
    survived: int
    findings: int
    suppressed: int = 0
    adjudicated: int | None = None

    @property
    def complete(self) -> bool:
        return self.adjudicated is None or self.adjudicated >= self.candidates

    def render(self, workbook: str) -> str:
        title = "MODEL HEALTH"
        header = title + workbook.rjust(WIDTH - len(title))
        lines = [header, "=" * WIDTH, ""]

        rows = [
            (self.formulas, "formulas parsed"),
            (self.candidates, "structural anomalies detected"),
        ]
        if not self.complete:
            rows.append((self.adjudicated, "tested, this run was limited"))
        rows.append((self.survived, "survived hypothesis testing"))
        rows.append((self.findings, "material findings"))
        if self.suppressed:
            rows.append((self.suppressed, "suppressed as immaterial"))

        width = max(len(f"{value:,}") for value, _ in rows)
        for value, label in rows:
            marker = "   <-- what you read" if label == "material findings" else ""
            lines.append(f"  {value:>{width + 4},}  {label}{marker}")

        if not self.complete:
            lines.append("")
            lines.extend(
                _wrap(
                    f"{self.candidates - self.adjudicated} candidates were not "
                    "examined. They are not cleared, they were not looked at.",
                    indent="  ",
                )
            )
        return "\n".join(lines)


def _wrap(text: str, indent: str = "", width: int = WIDTH) -> list[str]:
    """Wrap to the report width, so nothing breaks mid number in a terminal."""
    import textwrap

    return textwrap.wrap(
        text, width=width, initial_indent=indent, subsequent_indent=indent
    ) or [indent.rstrip()]


def _rule(character: str = "-") -> str:
    return character * WIDTH


def render_card(finding: Finding, index: int) -> str:
    """One evidence card.

    Consequence first. The reader cares that enterprise value is wrong; the
    cell reference is how they go and check it, not why they opened the
    report.
    """
    output, delta = max(
        finding.deltas.items(), key=lambda item: abs(item[1]), default=("", 0.0)
    )
    direction = "overstated by" if delta < 0 else "understated by"
    share = finding.relative.get(output)

    lines = [f"  {index}  {output} is {direction} {_money(abs(delta))}"]
    if share is not None:
        lines.append(f"     {_share(share)} of its current value")
    lines.append("")

    current = finding.current_formula or "a pasted value, not a formula"
    lines.append(f"     Cell         {finding.address}")
    lines.append(f"     Currently    {current}")
    if finding.proposed_formula:
        lines.append(f"     Should be    {finding.proposed_formula}")
    lines.append(
        f"     Confidence   {finding.confidence}"
        f"{'':>{max(1, 12 - len(finding.confidence))}}Detector  {finding.detector}"
    )

    if finding.peers:
        lines.append("")
        lines.append("     Its neighbours")
        for peer in finding.peers[:3]:
            lines.append(f"       {peer.address:<15} {peer.formula}")

    path = finding.paths.get(output)
    if path and len(path) > 1:
        lines.append("")
        lines.append(f"     How it reaches {output}, {len(path) - 1} steps")
        shown = path[:3] + (["..."] if len(path) > 4 else []) + path[-1:]
        lines.append(f"       {' -> '.join(shown)}")

    # Evidence that only restates the neighbour list is not evidence, it is
    # the same three lines again.
    extra = _beyond_the_neighbours(finding)
    if extra:
        lines.append("")
        lines.append("     Evidence")
        for item in extra[:3]:
            lines.extend(_wrap(plain(item), indent="       "))

    lines.append("")
    lines.append("     Measured impact")
    for name, value in sorted(finding.deltas.items(), key=lambda i: -abs(i[1])):
        if value == 0:
            lines.append(f"       {name:<16} {'no change':>16}")
            continue
        lines.append(
            f"       {name:<16} {_money(value):>16}   "
            f"{_share(finding.relative.get(name))}"
        )

    if finding.corrected:
        lines.append("")
        lines.extend(
            _wrap(
                "Note: the model reported different figures from the ones the "
                "engine returned. The figures above are the engine's.",
                indent="     ",
            )
        )
    return "\n".join(lines)


def _beyond_the_neighbours(finding: Finding) -> list[str]:
    """Evidence lines that say something the neighbour list does not."""
    already = {peer.address for peer in finding.peers}
    kept = []
    for item in finding.evidence:
        mentioned = {address for address in already if address in item}
        # A line whose only content is a neighbour address and its formula is
        # already on the card.
        if mentioned and len(item) < 40:
            continue
        kept.append(item)
    return kept


def render(
    workbook: str,
    result: CrossCheck,
    funnel: Funnel,
    show_suppressed: bool = True,
) -> str:
    """The whole report.

    Suppression the user cannot see is indistinguishable from a bug, so what
    was set aside is stated rather than quietly omitted.
    """
    blocks = [funnel.render(workbook), ""]

    if result.findings:
        blocks.append("FINDINGS")
        blocks.append(_rule())
        blocks.append("")
        for index, finding in enumerate(result.findings, start=1):
            blocks.append(render_card(finding, index))
            blocks.append("")
    else:
        blocks.append("FINDINGS")
        blocks.append(_rule())
        blocks.append("")
        blocks.append("  No material findings.")
        blocks.append("")

    blocks.append("WHAT WAS SET ASIDE")
    blocks.append(_rule())
    blocks.append("")

    aside = [
        (len(result.intentional), "deliberate, and the evidence says so"),
        (len(result.inconclusive), "not enough evidence to say"),
    ]
    if funnel.suppressed:
        aside.append((funnel.suppressed, "real but below the materiality threshold"))
    if result.dropped:
        aside.append((result.dropped, "dropped: impact could not be traced to a measurement"))

    width = max(len(str(count)) for count, _ in aside)
    for count, label in aside:
        blocks.append(f"  {count:>{width + 4}}  {label}")

    if result.immaterial:
        blocks.append("")
        blocks.append("  Real, and too small to matter")
        for finding in result.immaterial:
            largest = max(
                ((abs(v), k) for k, v in finding.relative.items() if v is not None),
                default=(0.0, ""),
            )
            blocks.append(
                f"    {finding.address}  moves {largest[1]} by "
                f"{_share(largest[0])}, below the {_share(load_threshold())} threshold"
            )
            if finding.proposed_formula:
                blocks.append(f"      would be {finding.proposed_formula}")

    if show_suppressed and result.intentional:
        blocks.append("")
        blocks.append("  Judged deliberate")
        for verdict in result.intentional:
            blocks.append(f"    {verdict.address}")
            blocks.extend(_wrap(plain(verdict.reasoning), indent="      "))

    if result.violations:
        blocks.append("")
        blocks.append("  Schema violations")
        for violation in result.violations:
            blocks.extend(_wrap(str(violation), indent="    "))

    return plain("\n".join(blocks)) + "\n"

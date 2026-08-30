"""The report writer agent. Built, run, and not shipped.

**This is the removed experiment in the README changelog.** It is kept because
a removed experiment with no artefact is an anecdote. Nothing in the pipeline
calls it: `audit` renders through the deterministic template in `report.py`.

Given the verified findings and told explicitly not to reinterpret any figure,
it printed every number correctly and then wrote that enterprise value was
overstated where the measurement says understated, on all four findings. It
also put the dependency path at 6 steps where it is 21, and described an
`INTENTIONAL` verdict as suppressed. The figures survived the cross check
because they were right. What was said about them was wrong, and nothing
checks sentences. Its trajectory is `trajectories/solution/C03_reporter.jsonl`.

The design below is unchanged from when it ran, so a reader can see what it
was given.

The second agent in the system. One call per workbook, no tools, run after
adjudication and after the gate. It receives verified findings and turns them
into prose.

It never touches a figure. Every number it is handed came out of a
`tool_result` record and was checked against the trajectory before it got
here, and the same cross check runs over what it writes. An agent that could
adjust an impact figure while describing it would put the whole design back
where it started.
"""

from __future__ import annotations

import re
from pathlib import Path

from materia.llm import LLMClient, Message
from materia.prompts.reporter import SYSTEM_PROMPT
from materia.report import CrossCheck, Funnel, plain
from materia.trace import Timer, Trace, new_run_id

# A number in the prose that is not one we handed over is a number the agent
# made up, whatever it says around it.
# A decimal point only counts when digits follow it, so a number at the end
# of a sentence does not pick up the full stop.
_FIGURE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def build_brief(workbook: str, result: CrossCheck, funnel: Funnel) -> str:
    """What the writer is given. Verified findings and nothing else."""
    lines = [
        f"Workbook: {workbook}",
        "",
        f"{funnel.formulas} formulas parsed.",
        f"{funnel.candidates} structural anomalies detected.",
        f"{funnel.findings} material findings.",
        f"{len(result.intentional)} judged deliberate, "
        f"{len(result.inconclusive)} inconclusive"
        + (f", {funnel.suppressed} suppressed as immaterial." if funnel.suppressed else "."),
        "",
        "Findings, already ordered by measured impact:",
    ]
    if not result.findings:
        lines.append("  none")
    for index, finding in enumerate(result.findings, start=1):
        lines.append("")
        lines.append(f"{index}. {finding.address}, confidence {finding.confidence}")
        lines.append(f"   currently: {finding.current_formula or 'a pasted value'}")
        lines.append(f"   should be: {finding.proposed_formula}")
        for output, delta in sorted(finding.deltas.items(), key=lambda i: -abs(i[1])):
            share = finding.relative.get(output)
            share_text = f" ({share:.2%} of it)" if share is not None else ""
            lines.append(f"   measured impact on {output}: {delta:,.0f}{share_text}")
        path = next(iter(finding.paths.values()), None)
        if path and len(path) > 1:
            lines.append(f"   reaches it in {len(path) - 1} steps")
        for item in finding.evidence[:3]:
            lines.append(f"   evidence: {plain(item)}")

    if result.intentional:
        lines.append("")
        lines.append("Judged deliberate and not reported:")
        for verdict in result.intentional:
            lines.append(f"  {verdict.address}: {plain(verdict.reasoning)}")

    lines.append("")
    lines.append(
        "Every impact figure above was measured by a deterministic engine. "
        "Use them exactly as given."
    )
    return "\n".join(lines)


def figures_in(text: str) -> set[str]:
    """Every number in the prose, normalised so 8,704,573 and 8704573 match."""
    return {match.group(0).replace(",", "") for match in _FIGURE.finditer(text)}


def unsupported_figures(prose: str, brief: str) -> set[str]:
    """Numbers the writer produced that were not in what it was given.

    The same rule as the adjudicator's, applied to prose: a figure with no
    source is a figure somebody invented.
    """
    given = figures_in(brief)
    return {
        figure
        for figure in figures_in(prose)
        # Small integers are ordinals and counts, not impact figures.
        if figure not in given and len(figure.lstrip("-").split(".")[0]) > 3
    }


def write_report(
    workbook: str,
    result: CrossCheck,
    funnel: Funnel,
    client: LLMClient,
    trace_directory: str | Path,
    run_prefix: str = "sol",
) -> tuple[str, set[str], str]:
    """Write the report. Returns the prose, any unsupported figures, and the
    trace path."""
    trace_directory = Path(trace_directory)
    path = trace_directory / f"{Path(workbook).stem}_reporter.jsonl"
    brief = build_brief(workbook, result, funnel)

    with Trace(path, new_run_id(run_prefix, Path(workbook).stem), "reporter") as trace:
        trace.run_start(
            workbook=workbook,
            findings=len(result.findings),
            provider=client.provider,
            model=client.model,
        )
        with Timer() as timer:
            response = client.complete(SYSTEM_PROMPT, [Message(role="user", content=brief)])
        trace.model_message(response, latency_ms=timer.elapsed_ms)

        prose = plain((response.text or "").strip())
        invented = unsupported_figures(prose, brief)
        if invented:
            trace.record(
                "verdict",
                {
                    "status": "figures not in the brief",
                    "figures": sorted(invented),
                },
            )
        trace.run_end("ok" if not invented else "schema_violation")

    return prose, invented, str(path)

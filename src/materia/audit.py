"""The whole pipeline, end to end.

    preflight -> parse -> graph -> detect -> adjudicate -> cross check -> report

Each stage is a module of its own and this is the only place that knows the
order. See docs/ARCHITECTURE.md.

The materiality gate belongs between the cross check and the report. It lands
in T21; until then every verified finding is reported and the suppressed count
is zero.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from materia.adjudicate import Verdict, adjudicate
from materia.detect import Candidate, detect, load
from materia.graph import DependencyGraph
from materia.llm import LLMClient, get_client, write_provenance
from materia.preflight import PreflightReport, preflight
from materia.report import CrossCheck, Funnel, cross_check, render
from materia.tools import Toolbox


@dataclass(frozen=True)
class Audit:
    """One workbook, audited."""

    workbook: str
    preflight: PreflightReport
    candidates: dict[str, Candidate]
    verdicts: tuple[Verdict, ...]
    result: CrossCheck
    funnel: Funnel
    provider: str
    model: str

    def render(self) -> str:
        return render(self.workbook, self.result, self.funnel)

    def as_dict(self) -> dict:
        """The result set the evaluator scores."""
        return {
            "workbook": self.workbook,
            "provider": self.provider,
            "model": self.model,
            "formulas": self.preflight.formula_count,
            "candidates": len(self.candidates),
            "adjudicated": len(self.verdicts),
            "findings": [
                {
                    "address": finding.address,
                    "detector": finding.detector,
                    "confidence": finding.confidence,
                    "proposed_formula": finding.proposed_formula,
                    "impact": finding.deltas,
                    "relative": finding.relative,
                    "evidence": list(finding.evidence),
                    "corrected": finding.corrected,
                }
                for finding in self.result.findings
            ],
            "intentional": [v.address for v in self.result.intentional],
            "inconclusive": [v.address for v in self.result.inconclusive],
            "violations": [str(v) for v in self.result.violations],
        }


def outputs_for(path: Path, corpus: Path = Path("corpus")) -> list[str]:
    """The declared outputs for a workbook.

    Taken from the corpus manifest when the workbook is one of ours. For any
    other file the caller has to say, because guessing which cells a decision
    rests on is precisely the judgement this tool must not make on its own.
    """
    manifest = corpus / "manifest.json"
    if manifest.exists():
        data = json.loads(manifest.read_text())
        for entry in data["workbooks"]:
            if entry["file"] == path.name:
                return list(entry["declared_outputs"])
    raise ValueError(
        f"{path.name} is not in {manifest}, so its declared output cells are "
        "unknown. Pass --outputs, for example --outputs \"P&L!AA15,Valuation!B7\"."
    )


def audit(
    path: str | Path,
    outputs: list[str] | None = None,
    client: LLMClient | None = None,
    trace_directory: str | Path = "trajectories/solution",
    max_candidates: int | None = None,
    corpus: str | Path = "corpus",
) -> Audit:
    """Run the pipeline over one workbook."""
    path = Path(path)
    name = path.stem

    report = preflight(path)
    declared = outputs or outputs_for(path, Path(corpus))

    tools = Toolbox(path, declared)
    graph = DependencyGraph.of(tools.model)

    found = detect(load(path))
    candidates: dict[str, Candidate] = {}
    for candidate in found:
        candidates.setdefault(candidate.address, candidate)

    chosen = list(candidates.values())
    if max_candidates is not None:
        chosen = chosen[:max_candidates]

    client = client or get_client()
    verdicts = adjudicate(chosen, client, tools, graph, name, trace_directory)
    result = cross_check(verdicts, tools.model, graph, candidates)

    return Audit(
        workbook=path.name,
        preflight=report,
        candidates=candidates,
        verdicts=tuple(verdicts),
        result=result,
        funnel=Funnel(
            formulas=report.formula_count,
            candidates=len(candidates),
            # Survived hypothesis testing means the agent concluded the cell
            # was wrong and the impact was verifiable. An INTENTIONAL verdict
            # was dismissed and an INCONCLUSIVE one established nothing, so
            # neither survived. Until the gate lands in T21 this equals the
            # finding count, and that is the honest reading: with no
            # materiality filter, everything that survives is reported.
            survived=len(result.findings),
            findings=len(result.findings),
            adjudicated=len(verdicts),
        ),
        provider=client.provider,
        model=client.model,
    )


def write_result(audit_result: Audit, directory: str | Path) -> Path:
    """Save the result set, with a note of who produced it."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{Path(audit_result.workbook).stem}.json"
    path.write_text(json.dumps(audit_result.as_dict(), indent=2, sort_keys=True) + "\n")
    write_provenance(
        directory,
        type("Client", (), {"provider": audit_result.provider, "model": audit_result.model})(),
    )
    return path

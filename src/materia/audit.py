"""The whole pipeline, end to end.

    preflight -> parse -> graph -> detect -> adjudicate -> cross check
              -> materiality gate -> report

Each stage is a module of its own and this is the only place that knows the
order. See docs/ARCHITECTURE.md.

The gate sits after the cross check on purpose. A finding has to be verified
before its size can be weighed, and weighing it is a threshold against a
measured number rather than a judgement, so it happens in code afterwards
where it can be re run at a different threshold without another model call.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from materia.adjudicate import AdjudicationStopped, Verdict, adjudicate
from materia.detect import Candidate, detect, load
from materia.graph import DependencyGraph
from materia.llm import LLMClient, get_client, write_provenance
from materia.preflight import PreflightReport, preflight
from materia.report import (
    CrossCheck,
    Funnel,
    apply_materiality,
    cross_check,
    render,
)
from materia.tools import Toolbox


class BucketsDoNotSum(AssertionError):
    """The report's buckets do not account for every candidate examined.

    Raised rather than logged. The buckets are the only thing telling a user
    what happened to each anomaly, so a candidate that fell out of all of them
    is one the report silently lost, and silent loss is the failure this
    project exists to prevent.
    """


def check_buckets(result: CrossCheck, adjudicated: int) -> None:
    """Every candidate that was examined is in exactly one bucket.

    docs/ARCHITECTURE.md data flow constraints. Checked here, on every audit
    and every rebuild, because an invariant that only holds on fixtures is not
    an invariant.
    """
    if result.accounted != adjudicated:
        raise BucketsDoNotSum(
            f"{adjudicated} candidates were adjudicated but "
            f"{result.accounted} are accounted for: "
            f"{len(result.findings)} findings, {len(result.immaterial)} immaterial, "
            f"{len(result.intentional)} intentional, "
            f"{len(result.inconclusive)} inconclusive, {result.dropped} unverifiable."
        )


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
    stopped: str | None = None

    def render(self) -> str:
        report = render(self.workbook, self.result, self.funnel)
        if self.stopped:
            report += f"\nThis run stopped early: {self.stopped}\n"
        return report

    def as_dict(self) -> dict:
        """The result set the evaluator scores."""
        return {
            "workbook": self.workbook,
            "provider": self.provider,
            "model": self.model,
            # What the run spent, so results/ can say what it cost without
            # anyone reading it back out of the trajectories by hand.
            "tokens": {
                "in": sum(v.tokens.get("in", 0) for v in self.verdicts),
                "out": sum(v.tokens.get("out", 0) for v in self.verdicts),
            },
            "formulas": self.preflight.formula_count,
            "candidates": len(self.candidates),
            "adjudicated": len(self.verdicts),
            "stopped": self.stopped,
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
            "immaterial": [
                {
                    "address": f.address,
                    "impact": f.deltas,
                    "relative": f.relative,
                    "proposed_formula": f.proposed_formula,
                }
                for f in self.result.immaterial
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
    threshold: float | None = None,
) -> Audit:
    """Run the pipeline over one workbook.

    preflight, parse, graph, detect, adjudicate, cross check, then the
    materiality gate. `threshold` overrides the value in `config.yaml` for
    this run only, which is what `--materiality` and a sensitivity sweep use.

    Every candidate that is adjudicated leaves in exactly one bucket, and
    `check_buckets` raises rather than returns if that is not true.
    """
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
    stopped: str | None = None
    try:
        verdicts = adjudicate(chosen, client, tools, graph, name, trace_directory)
    except AdjudicationStopped as cut_short:
        # Report what was established rather than nothing at all. The funnel
        # says how many were tested, so a shortened run cannot read as a
        # clean bill of health for the cells nobody looked at.
        verdicts = list(cut_short.verdicts)
        stopped = cut_short.reason

    result = cross_check(verdicts, tools.model, graph, candidates)
    survived = len(result.findings)
    result = apply_materiality(result, threshold)
    check_buckets(result, len(verdicts))

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
            # neither survived. The gate runs after this point, so `survived`
            # counts what reached it and `findings` counts what came out.
            survived=survived,
            findings=len(result.findings),
            suppressed=len(result.immaterial),
            adjudicated=len(verdicts),
        ),
        provider=client.provider,
        model=client.model,
        stopped=stopped,
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


def from_trajectories(
    path: str | Path,
    trace_directory: str | Path,
    outputs: list[str] | None = None,
    corpus: str | Path = "corpus",
    threshold: float | None = None,
) -> Audit:
    """Rebuild an audit from trajectories already on disk.

    Rendering is deterministic, so a report can be produced again from the
    record without paying for the run twice. It is also the honest way to
    re-render: the figures come from the same `tool_result` records the
    original report was checked against.
    """
    from materia.trace import read, total_tokens

    path = Path(path)
    report = preflight(path)
    declared = outputs or outputs_for(path, Path(corpus))
    tools = Toolbox(path, declared)
    graph = DependencyGraph.of(tools.model)

    candidates: dict[str, Candidate] = {}
    for candidate in detect(load(path)):
        candidates.setdefault(candidate.address, candidate)

    verdicts: list[Verdict] = []
    provider = model = ""
    for trace in sorted(Path(trace_directory).glob("*.jsonl")):
        records = read(trace)
        entry = next((r for r in records if r.type == "verdict"), None)
        if entry is None:
            continue
        start = records[0]
        # A sweep puts every workbook's trajectories in one directory. Without
        # this, rebuilding C10 picked up all twelve workbooks' verdicts and
        # reported them as C10's.
        if start.content.get("workbook") not in (None, path.stem):
            continue
        provider = provider or start.content.get("provider", "")
        model = model or start.content.get("model", "")
        verdicts.append(
            Verdict(
                address=start.content["cell"],
                detector=start.content["detector"],
                verdict=entry.content["verdict"],
                confidence=entry.content["confidence"],
                proposed_formula=entry.content.get("proposed_formula"),
                evidence=tuple(entry.content.get("evidence") or ()),
                reasoning=entry.content.get("reasoning", ""),
                measured_deltas=entry.content.get("measured_deltas") or {},
                tokens=total_tokens(records),
                turns=len([r for r in records if r.type == "model_message"]),
                tool_calls=len([r for r in records if r.type == "tool_call"]),
                trace_path=str(trace),
            )
        )

    result = cross_check(verdicts, tools.model, graph, candidates)
    survived = len(result.findings)
    result = apply_materiality(result, threshold)
    check_buckets(result, len(verdicts))

    return Audit(
        workbook=path.name,
        preflight=report,
        candidates=candidates,
        verdicts=tuple(verdicts),
        result=result,
        funnel=Funnel(
            formulas=report.formula_count,
            candidates=len(candidates),
            survived=survived,
            findings=len(result.findings),
            suppressed=len(result.immaterial),
            adjudicated=len(verdicts),
        ),
        provider=provider,
        model=model,
    )

"""Repair mode.

Writing to somebody's financial model is consequential, so it is gated behind
a person saying yes, finding by finding. The brief requires that and it is the
right shape anyway: the tool can prove a cell is inconsistent with its peers,
it cannot prove what the author intended.

Three things hold regardless of what the user answers:

- The input workbook is opened read only and is never written. The corrected
  copy goes to a new path.
- Nothing is written until every finding has been asked about. A run that is
  interrupted half way leaves no half repaired file.
- Every answer is recorded as a `human_checkpoint`, declines included. A
  decline is a decision about the model and is worth as much in the record as
  an approval.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

import openpyxl

from materia.report import Finding
from materia.trace import Trace, new_run_id

Ask = Callable[[Finding], bool]


@dataclass(frozen=True)
class Decision:
    """What a person said about one finding."""

    address: str
    approved: bool
    proposed_formula: str | None

    @property
    def decision(self) -> str:
        return "approved" if self.approved else "declined"


@dataclass(frozen=True)
class RepairResult:
    """What a repair run did."""

    source: Path
    written: Path | None
    decisions: tuple[Decision, ...] = ()
    trace_path: str | None = None

    @property
    def approved(self) -> tuple[Decision, ...]:
        return tuple(d for d in self.decisions if d.approved)

    @property
    def declined(self) -> tuple[Decision, ...]:
        return tuple(d for d in self.decisions if not d.approved)

    def render(self) -> str:
        lines = ["REPAIR", "=" * 74, ""]
        if not self.decisions:
            lines.append("  Nothing to repair.")
            return "\n".join(lines) + "\n"

        for decision in self.decisions:
            lines.append(f"  {decision.decision:<9} {decision.address}")
        lines.append("")
        if self.written is None:
            lines.append("  Nothing was approved, so no file was written.")
        else:
            lines.append(f"  {len(self.approved)} change(s) written to {self.written}")
        lines.append(f"  {self.source} was not modified.")
        return "\n".join(lines) + "\n"


def default_target(source: Path) -> Path:
    """Where a corrected copy goes. Never over the original."""
    return source.with_name(f"{source.stem}.repaired{source.suffix}")


def prompt_for(finding: Finding) -> bool:
    """Ask on the terminal. The default is no.

    An unattended run, or someone pressing return to get through it, must not
    end up writing changes nobody agreed to.
    """
    output, delta = max(
        finding.deltas.items(), key=lambda item: abs(item[1]), default=("", 0.0)
    )
    print()
    print(f"  {finding.address}")
    print(f"    currently  {finding.current_formula or 'a pasted value, not a formula'}")
    print(f"    change to  {finding.proposed_formula}")
    print(f"    moves {output} by {delta:,.0f}")
    answer = input("    apply this change? [y/N] ").strip().lower()
    return answer in ("y", "yes")


def repair(
    source: str | Path,
    findings: Iterable[Finding],
    target: str | Path | None = None,
    ask: Ask = prompt_for,
    trace_directory: str | Path = "trajectories/solution",
) -> RepairResult:
    """Apply approved repairs to a copy, asking per finding.

    Every answer is collected before anything is written, so an interrupted
    run leaves the source untouched and no partially repaired file behind.
    """
    source = Path(source)
    findings = list(findings)
    target = Path(target) if target else default_target(source)

    if target.resolve() == source.resolve():
        raise ValueError(
            f"{target} is the input workbook. Repairs are written to a copy, "
            "never over the original."
        )

    trace_path = Path(trace_directory) / f"{source.stem}_repair.jsonl"
    decisions: list[Decision] = []

    with Trace(trace_path, new_run_id("repair", source.stem), "repair") as trace:
        trace.run_start(workbook=source.name, findings=len(findings), target=str(target))

        for finding in findings:
            approved = bool(ask(finding))
            decisions.append(
                Decision(finding.address, approved, finding.proposed_formula)
            )
            trace.human_checkpoint(
                "repair_approval",
                "approved" if approved else "declined",
                cell=finding.address,
                proposed_formula=finding.proposed_formula,
                impact=finding.deltas,
            )

        approved = [d for d in decisions if d.approved and d.proposed_formula]
        if not approved:
            trace.run_end("ok", written=None, approved=0)
            return RepairResult(source, None, tuple(decisions), str(trace_path))

        written = _write_copy(source, target, approved)
        trace.run_end("ok", written=str(written), approved=len(approved))

    return RepairResult(source, written, tuple(decisions), str(trace_path))


def _write_copy(source: Path, target: Path, approved: list[Decision]) -> Path:
    """Copy the workbook, then edit the copy.

    The source is read by shutil and by openpyxl's loader, and written by
    neither. Everything after the copy touches `target` only.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)

    workbook = openpyxl.load_workbook(target)
    try:
        for decision in approved:
            sheet, coordinate = decision.address.split("!", 1)
            workbook[sheet][coordinate.replace("$", "")] = decision.proposed_formula
        workbook.save(target)
    finally:
        workbook.close()
    return target

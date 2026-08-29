"""Rendering a trajectory as markdown.

micro1 buys agent traces, so these are read by people who know what good
looks like. The rendering shows the sequence as it happened: what the model
was asked, what it decided to do, what came back, and what it concluded. A
reader should be able to follow it without running anything and without
knowing this codebase.

Every figure in a report traces to a `tool_result` record in one of these
files. That is the point of committing them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from materia.report import plain
from materia.trace import Record, read, total_tokens

WIDTH = 74


@dataclass(frozen=True)
class Featured:
    """One trajectory chosen for the index, and why a reader should open it."""

    number: int
    slug: str
    title: str
    shows: str
    preamble: str
    path: str | None = None

    @property
    def available(self) -> bool:
        return self.path is not None and Path(self.path).exists()


def _fence(text: str, language: str = "") -> str:
    return f"```{language}\n{text}\n```"


def _summarise(records: list[Record]) -> str:
    start = records[0]
    tokens = total_tokens(records)
    tool_calls = [r for r in records if r.type == "tool_call"]
    content = start.content

    lines = [
        f"- Run `{start.run_id}`, agent `{start.agent}`",
        f"- Workbook `{content.get('workbook', 'unknown')}`",
    ]
    if content.get("cell"):
        lines.append(f"- Cell `{content['cell']}`, flagged by detector `{content.get('detector')}`")
    if content.get("provider"):
        lines.append(f"- Provider `{content['provider']}`, model `{content['model']}`")
    lines.append(
        f"- {len(records)} steps, {len(tool_calls)} tool calls, "
        f"{tokens['in']:,} tokens in and {tokens['out']:,} out"
    )
    if content.get("detector_reason"):
        lines.append("")
        lines.append(f"> {plain(content['detector_reason'])}")
    return "\n".join(lines)


def _render_record(record: Record) -> str:
    """One step, as a reader would want to see it."""
    content = record.content
    heading = f"### Step {record.step}, {record.type.replace('_', ' ')}"

    if record.type == "run_start":
        return ""  # already in the summary above

    if record.type == "model_message":
        parts = [heading]
        if content.get("error"):
            parts.append(f"The provider refused this request: {plain(content['error'])}")
            return "\n\n".join(parts)
        if record.latency_ms:
            parts.append(
                f"_{record.tokens.get('in', 0):,} tokens in, "
                f"{record.tokens.get('out', 0):,} out, {record.latency_ms:,} ms_"
            )
        if content.get("text"):
            parts.append(plain(content["text"]))
        for call in content.get("tool_calls", []):
            parts.append(f"Asks for `{call['name']}`:")
            parts.append(_fence(json.dumps(call["arguments"], indent=2), "json"))
        if not content.get("text") and not content.get("tool_calls"):
            parts.append("_No text and no tool call._")
        return "\n\n".join(parts)

    if record.type == "tool_call":
        # The arguments were already shown on the model message that asked for
        # this. Printing the same JSON twice in a row makes the sequence harder
        # to follow rather than easier.
        return f"### Step {record.step}, tool call\n\nRunning `{content['name']}`."

    if record.type == "tool_result":
        parts = [heading, f"`{content['name']}` returned:"]
        parts.append(_fence(json.dumps(content.get("result"), indent=2), "json"))
        if content.get("error"):
            parts.append(f"The tool reported an error: {plain(str(content['error']))}")
        return "\n\n".join(parts)

    if record.type == "verdict":
        parts = [heading, f"**{content.get('verdict', 'unknown')}**, "
                          f"confidence {content.get('confidence', 'unknown')}"]
        if content.get("proposed_formula"):
            parts.append(f"Proposed formula: `{content['proposed_formula']}`")
        if content.get("reasoning"):
            parts.append(plain(content["reasoning"]))
        if content.get("evidence"):
            parts.append("Evidence given:")
            parts.append("\n".join(f"- {plain(str(item))}" for item in content["evidence"]))
        if content.get("measured_deltas"):
            parts.append("Impact the model reported:")
            parts.append(_fence(json.dumps(content["measured_deltas"], indent=2), "json"))
        if content.get("status"):
            parts.append(f"Status: {content['status']}")
        return "\n\n".join(parts)

    if record.type == "human_checkpoint":
        parts = [
            heading,
            f"**{content.get('decision', 'unknown')}** at a `{content.get('kind')}` checkpoint",
        ]
        if content.get("cell"):
            parts.append(f"Cell `{content['cell']}`")
        if content.get("proposed_formula"):
            parts.append(f"Change offered: `{content['proposed_formula']}`")
        return "\n\n".join(parts)

    if record.type == "run_end":
        detail = ", ".join(f"{k} {v}" for k, v in content.items() if k != "status")
        return f"### Step {record.step}, run end\n\nStatus `{content.get('status')}`" + (
            f". {detail}." if detail else "."
        )

    return f"{heading}\n\n{_fence(json.dumps(content, indent=2), 'json')}"


def render(path: str | Path, preamble: str | None = None) -> str:
    """A whole trajectory as markdown."""
    path = Path(path)
    records = read(path)
    if not records:
        raise ValueError(f"{path} holds no records")

    start = records[0]
    cell = start.content.get("cell")
    title = f"{start.content.get('workbook', path.stem)} {start.agent}"
    if cell:
        title += f", {cell}"

    blocks = [f"# {title}", "", _summarise(records)]
    if preamble:
        blocks += ["", "## What to watch for", "", preamble]
    blocks += ["", "## The run", ""]

    for record in records:
        rendered = _render_record(record)
        if rendered:
            blocks.append(rendered)
            blocks.append("")

    blocks.append("---")
    blocks.append("")
    blocks.append(f"Raw trajectory: `{path}`")
    return plain("\n".join(blocks)) + "\n"


# --- the featured trajectories ---------------------------------------------
#
# Chosen to show the system working, the system declining, the system
# correcting itself, and the safety mechanism firing on a real failure. The
# table in docs/TRAJECTORIES.md names four. A fifth was earned rather than
# planned, and it is the one to read first.
#
# `path` is None where the trajectory does not exist in any run yet. The index
# says so rather than leaving a gap, because a missing trajectory and a
# trajectory nobody looked for read the same way otherwise.

SOLUTION = "trajectories/solution"

FEATURED: list[Featured] = [
    Featured(
        number=1,
        slug="clean-win",
        title="The clean win",
        shows="A detector fires, the model forms a hypothesis, tests it, and reports what the engine measured.",
        path=f"{SOLUTION}/C03_adjudicator_P&L_AA15_D2.jsonl",
        preamble=(
            "The EBITDA total sums twenty three of the twenty four monthly "
            "columns. The detector can see that the cell does not match its "
            "row, and nothing more.\n\n"
            "Watch the order of operations. The model calls `inspect_range` "
            "first and reads the neighbouring total cells, which all sum `C` "
            "to `Z`. That is where the hypothesis comes from: not from knowing "
            "what a total should look like, but from the four cells above it "
            "that do the same job. It then calls `recompute_with_patch` with "
            "`=SUM(C15:Z15)` and gets `1550882` on EBITDA and `0` on "
            "enterprise value.\n\n"
            "Both figures appear unchanged in its verdict, and both appear "
            "unchanged in the report. The zero matters as much as the other "
            "one: the EBITDA total is not on the path to enterprise value, so "
            "correcting it moves one output and not the other. The model did "
            "not have to reason about that. It asked."
        ),
    ),
    Featured(
        number=2,
        slug="declining-to-flag",
        title="Declining to flag a deliberate break",
        shows="An INTENTIONAL verdict on a legitimate pattern break, which is the behaviour that separates this from a linter.",
        path=None,
        preamble=(
            "This one is meant to come from `C10`, the clean control carrying "
            "three deliberate pattern breaks: a hardcoded actuals row, a first "
            "period column, and a manual override with a comment explaining "
            "it.\n\n"
            "No adjudication has been run against `C10`. The equivalent "
            "behaviour is on record in `C03`, where the model declined four "
            "first period breaks, and those trajectories are in the index "
            "below. But the `C10` run is the one the table asks for and it "
            "does not exist, so this entry says so rather than substituting "
            "a near miss and hoping nobody checks."
        ),
    ),
    Featured(
        number=3,
        slug="self-correction",
        title="A hypothesis that failed, and a second that worked",
        shows="A retry. The first proposed formula returns a near zero delta, the model recognises the hypothesis was wrong and forms another.",
        path=None,
        preamble=(
            "No trajectory in any run so far calls `recompute_with_patch` more "
            "than once on a single candidate. Every adjudication either tested "
            "one hypothesis and kept it, or tested none and returned "
            "`INTENTIONAL` or `INCONCLUSIVE`.\n\n"
            "So the retry the table describes has not happened. It is a "
            "plausible path through the loop and the loop supports it, but "
            "supporting a path is not the same as having walked it, and a "
            "trajectory written to demonstrate a capability nobody exercised "
            "would be a fabrication."
        ),
    ),
    Featured(
        number=4,
        slug="baseline-false-positive",
        title="The baseline reporting errors in a clean workbook",
        shows="The failure the whole project is about: a capable general agent reporting confident findings on a workbook with nothing wrong in it.",
        path=None,
        preamble=(
            "The baseline harness is T18 and has not been built. There is no "
            "baseline run, so there is no baseline trajectory.\n\n"
            "The equivalent measurement does exist without an agent: the "
            "detectors alone report twenty one findings on `C09` and twenty "
            "five on `C10`, both of which contain no errors at all. That is "
            "in the changelog as Iteration 1. It is not this trajectory."
        ),
    ),
    Featured(
        number=5,
        slug="the-check-firing",
        title="The cross check catching an invented figure",
        shows="The safety mechanism firing on a real failure rather than a constructed one.",
        path=f"{SOLUTION}/C03_adjudicator_Revenue_H5_D1.jsonl",
        preamble=(
            "This one is not in the table in `docs/TRAJECTORIES.md` because "
            "nobody planned it. It is the most important trajectory in the "
            "submission and it should be read before the others.\n\n"
            "The verdict is correct. `Revenue!H5` really is a pasted value "
            "where its neighbours hold formulas, `=G9` really is the right "
            "repair, and the model reasoned its way there from the peer group "
            "without being told what to look for.\n\n"
            "Now read step 4 and step 7 together. At step 4 the tool returns "
            "`{\"P&L!AA15\": 8704573.0, \"Valuation!B7\": 92752830.0}`. At step "
            "7 the model reports `{\"P&L!AA15\": -6102169, \"Valuation!B7\": "
            "-50782614}`. Different magnitudes, and the signs are flipped. It "
            "had the numbers. It reported different ones.\n\n"
            "Rule 1 of its own instructions says never state an impact figure "
            "you did not obtain from the tool. It agreed to that rule at the "
            "top of the same conversation and then broke it on the first "
            "candidate of the first live run, unprompted.\n\n"
            "That is why the cross check is code and not a line in a prompt. "
            "The renderer reads the figure out of the trajectory rather than "
            "out of the verdict, so the report shows `8,704,573` and the "
            "invented number never reaches a reader. The finding survives with "
            "the measured figures substituted and the discrepancy printed under "
            "`Schema violations`, because dropping it would have lost a real "
            "error to a reporting mistake.\n\n"
            "This is the mechanism being tested by reality rather than "
            "demonstrated on a case built to make it look good."
        ),
    ),
]


# --- the index -------------------------------------------------------------


def _row(path: Path, root: Path) -> dict:
    records = read(path)
    start = records[0]
    verdict = next((r for r in records if r.type == "verdict"), None)
    tokens = total_tokens(records)
    return {
        "run_id": start.run_id,
        "agent": start.agent,
        "workbook": start.content.get("workbook", ""),
        "cell": start.content.get("cell", ""),
        "steps": len(records),
        "tool_calls": len([r for r in records if r.type == "tool_call"]),
        "verdict": (verdict.content.get("verdict", "") if verdict else ""),
        "tokens": tokens["in"] + tokens["out"],
        "file": str(path.relative_to(root)),
    }


def write_featured(directory: str | Path = "trajectories") -> list[Path]:
    """Write the rendered markdown for every featured trajectory that exists."""
    directory = Path(directory)
    written = []
    for item in FEATURED:
        if not item.available:
            continue
        target = directory / "featured" / f"{item.number}-{item.slug}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render(item.path, item.preamble))
        written.append(target)
    return written


def build_index(directory: str | Path = "trajectories") -> str:
    """The map a reader uses to find the trajectory behind any number."""
    directory = Path(directory)
    traces = sorted(p for p in directory.rglob("*.jsonl"))

    lines = [
        "# Agent trajectories",
        "",
        "Generated by `make trace-index`. Every agent call in every run is here.",
        "",
        "Each rendered markdown file under `featured/` carries a preamble saying "
        "what to watch for. The raw JSONL sits beside it, so nothing has to be "
        "run to read any of this.",
        "",
        "## Start here",
        "",
    ]

    available = [item for item in FEATURED if item.available]
    missing = [item for item in FEATURED if not item.available]

    for item in available:
        target = f"featured/{item.number}-{item.slug}.md"
        lines.append(f"### {item.number}. {item.title}")
        lines.append("")
        lines.append(f"{item.shows}")
        lines.append("")
        lines.append(f"[Read it]({target}), raw trajectory `{item.path}`")
        lines.append("")

    if missing:
        lines.append("## Not present, and why")
        lines.append("")
        lines.append(
            "The table in `docs/TRAJECTORIES.md` names four featured "
            "trajectories. These are the ones no run has produced. A missing "
            "trajectory and a trajectory nobody looked for read the same way "
            "unless one of them says so."
        )
        lines.append("")
        for item in missing:
            lines.append(f"### {item.number}. {item.title}")
            lines.append("")
            lines.append(item.preamble)
            lines.append("")

    lines.append("## Every trajectory")
    lines.append("")
    lines.append(
        "The same cell appears more than once where it was adjudicated in more "
        "than one run. The run column tells them apart, and the file column "
        "says which run each came from."
    )
    lines.append("")
    lines.append(
        "| Run | Agent | Cell | Steps | Tool calls | Verdict | Tokens | File |"
    )
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for path in traces:
        row = _row(path, directory)
        lines.append(
            f"| `{row['run_id']}` | {row['agent']} | {row['cell'] or '-'} | "
            f"{row['steps']} | {row['tool_calls']} | {row['verdict'] or '-'} | "
            f"{row['tokens']:,} | `{row['file']}` |"
        )

    lines.append("")
    lines.append(f"{len(traces)} trajectories.")
    return plain("\n".join(lines)) + "\n"


def write_index(directory: str | Path = "trajectories") -> Path:
    directory = Path(directory)
    path = directory / "index.md"
    path.write_text(build_index(directory))
    return path

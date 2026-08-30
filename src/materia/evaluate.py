"""Scoring a result set against the corpus manifest.

The metrics are defined in docs/EVALUATION.md section 1. The manifest is
ground truth: which cells were mutated, and by how much each mutation moves a
declared output. Nothing here decides what counts as material, it reads the
measurement the recompute engine made at injection time.

Where a metric has no denominator, it is reported as not applicable rather
than as zero. A system that reported nothing has not achieved a precision of
zero, it has no precision to report, and the difference matters.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from materia.parse import normalise_address

RESULTS = Path("results")


@dataclass(frozen=True)
class Finding:
    """One thing a system reported to the user."""

    address: str
    proposed_formula: str | None = None
    impact: dict[str, float] = field(default_factory=dict)
    confidence: str | None = None

    @property
    def key(self) -> str:
        return normalise_address(self.address)


ResultSet = dict[str, list[Finding]]


@dataclass(frozen=True)
class WorkbookScore:
    """What a system did on one workbook."""

    workbook: str
    role: str
    reported: int
    seeded_total: int
    seeded_material: int
    found_total: int
    found_material: int
    false_positives: int


@dataclass(frozen=True)
class Scores:
    """A system's result across the whole corpus."""

    system: str
    material_precision: float | None
    material_recall: float | None
    raw_anomaly_recall: float | None
    false_positives_per_clean_workbook: float | None
    localisation_accuracy: float | None
    repair_accuracy: float | None
    reported: int
    per_workbook: tuple[WorkbookScore, ...]

    # Real errors the gate held back. Carried into the table because a
    # suppressed mutation and a missed one look identical in a recall figure,
    # and they are opposite outcomes. docs/ARCHITECTURE.md section 7.
    suppressed: int = 0


def _ratio(numerator: int, denominator: int) -> float | None:
    """None rather than zero when there is nothing to divide by."""
    return None if denominator == 0 else numerator / denominator


def _mutations(entry: dict) -> dict[str, dict]:
    return {normalise_address(m["address"]): m for m in entry["mutations"]}


def score(
    system: str,
    results: ResultSet,
    manifest: dict,
    suppressed: int = 0,
) -> Scores:
    """Score one system's findings against the manifest.

    `suppressed` is passed in rather than derived: only the system that has a
    materiality gate has anything to report there, and the scorer sees
    findings rather than the pipeline that produced them.
    """
    per_workbook: list[WorkbookScore] = []

    reported = 0
    correct_material = 0
    material_seeded = 0
    material_found = 0
    total_seeded = 0
    total_found = 0
    clean_false_positives = 0
    clean_workbooks = 0

    on_right_sheet = 0
    on_right_cell = 0
    repairs_offered = 0
    repairs_correct = 0

    for entry in manifest["workbooks"]:
        identifier = entry["id"]
        findings = results.get(identifier, [])
        seeded = _mutations(entry)
        material = {k: v for k, v in seeded.items() if v["material"]}

        keys = {finding.key for finding in findings}
        hits = keys & set(seeded)
        material_hits = keys & set(material)

        reported += len(findings)
        correct_material += len(material_hits)
        material_seeded += len(material)
        material_found += len(material_hits)
        total_seeded += len(seeded)
        total_found += len(hits)

        if entry["role"] == "clean_control":
            clean_workbooks += 1
            clean_false_positives += len(findings)

        # Localisation: of the mutations a system got into the right sheet,
        # how many did it pin to the right cell. Right area, wrong cell is not
        # useful to somebody who has to go and look.
        reported_sheets = {key.split("!", 1)[0] for key in keys}
        for address in seeded:
            if address.split("!", 1)[0] in reported_sheets:
                on_right_sheet += 1
                if address in keys:
                    on_right_cell += 1

        for finding in findings:
            if finding.proposed_formula is None:
                continue
            repairs_offered += 1
            mutation = seeded.get(finding.key)
            if mutation and _same_formula(finding.proposed_formula, mutation["original"]):
                repairs_correct += 1

        per_workbook.append(
            WorkbookScore(
                workbook=identifier,
                role=entry["role"],
                reported=len(findings),
                seeded_total=len(seeded),
                seeded_material=len(material),
                found_total=len(hits),
                found_material=len(material_hits),
                false_positives=len(findings) - len(hits),
            )
        )

    return Scores(
        system=system,
        material_precision=_ratio(correct_material, reported),
        material_recall=_ratio(material_found, material_seeded),
        raw_anomaly_recall=_ratio(total_found, total_seeded),
        false_positives_per_clean_workbook=_ratio(clean_false_positives, clean_workbooks),
        localisation_accuracy=_ratio(on_right_cell, on_right_sheet),
        repair_accuracy=_ratio(repairs_correct, repairs_offered),
        reported=reported,
        per_workbook=tuple(per_workbook),
        suppressed=suppressed,
    )


def _same_formula(proposed: str, original) -> bool:
    """Compare a proposed repair against the formula that was there."""
    if not isinstance(original, str):
        return str(proposed).strip() == str(original).strip()
    return proposed.replace(" ", "").upper() == original.replace(" ", "").upper()


# --- rendering -------------------------------------------------------------


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def _number(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


METRIC_ROWS = [
    ("Material finding precision", "material_precision", _percent),
    ("Material recall", "material_recall", _percent),
    ("Raw anomaly recall", "raw_anomaly_recall", _percent),
    ("False positives per clean workbook", "false_positives_per_clean_workbook", _number),
    ("Localisation accuracy", "localisation_accuracy", _percent),
    ("Repair accuracy", "repair_accuracy", _percent),
]


def headline_table(scores: list[Scores]) -> str:
    """The table in docs/EVALUATION.md section 5, one column per system."""
    names = [item.system for item in scores]
    lines = [
        "| Metric | " + " | ".join(names) + " |",
        "| --- | " + " | ".join("---" for _ in names) + " |",
    ]
    for label, attribute, render in METRIC_ROWS:
        cells = [render(getattr(item, attribute)) for item in scores]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines.append(
        "| Findings reported | " + " | ".join(str(item.reported) for item in scores) + " |"
    )
    if any(item.suppressed for item in scores):
        lines.append(
            "| Suppressed as immaterial | "
            + " | ".join(str(item.suppressed) for item in scores) + " |"
        )
    return "\n".join(lines) + "\n"


def per_workbook_table(scores: list[Scores]) -> str:
    """Per case breakdown, so a reader can see where a number came from."""
    names = [item.system for item in scores]
    header = ["ID", "Role", "Seeded", "Material"]
    for name in names:
        header += [f"{name} found", f"{name} reported", f"{name} FP"]

    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]

    by_workbook = {
        item.system: {row.workbook: row for row in item.per_workbook} for item in scores
    }
    for row in scores[0].per_workbook:
        cells = [row.workbook, row.role.replace("_", " "), str(row.seeded_total), str(row.seeded_material)]
        for name in names:
            here = by_workbook[name][row.workbook]
            cells += [str(here.found_total), str(here.reported), str(here.false_positives)]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def as_dict(item: Scores) -> dict:
    """The same numbers as data, so a doc can be filled in without retyping."""
    return {
        "system": item.system,
        "reported": item.reported,
        **{attribute: getattr(item, attribute) for _, attribute, _ in METRIC_ROWS},
        "per_workbook": [
            {
                "id": row.workbook,
                "role": row.role,
                "reported": row.reported,
                "seeded_total": row.seeded_total,
                "seeded_material": row.seeded_material,
                "found_total": row.found_total,
                "found_material": row.found_material,
                "false_positives": row.false_positives,
            }
            for row in item.per_workbook
        ],
    }


def write_results(scores: list[Scores], directory: str | Path = RESULTS) -> dict[str, Path]:
    """Write the tables and the raw numbers. Nothing here is typed by hand."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    written = {}
    written["headline"] = directory / "headline.md"
    written["headline"].write_text(
        "# Headline results\n\n"
        "Generated by `make eval`. Do not edit: every figure comes from "
        "`corpus/manifest.json` and the result sets in this directory.\n\n"
        + headline_table(scores)
    )

    written["per_workbook"] = directory / "per_workbook.md"
    written["per_workbook"].write_text(
        "# Per workbook results\n\n"
        "`Seeded` counts every mutation, `Material` counts the ones that move "
        "a declared output by at least one percent. `FP` is findings that do "
        "not correspond to a seeded mutation.\n\n"
        + per_workbook_table(scores)
    )

    written["scores"] = directory / "scores.json"
    written["scores"].write_text(
        json.dumps([as_dict(item) for item in scores], indent=2, sort_keys=True) + "\n"
    )
    return written


# --- the detector only run -------------------------------------------------


def detector_results(corpus: str | Path, manifest: dict) -> ResultSet:
    """Run the detectors alone over the corpus, with no model involved.

    One finding per flagged cell rather than per candidate: a cell caught by
    two detectors is one thing for a user to open, and counting it twice would
    flatter the noise figure rather than describe it.

    Detectors propose no repair and measure no impact, so repair accuracy is
    reported as not applicable rather than as zero.
    """
    from materia.detect import detect, load

    corpus = Path(corpus)
    results: ResultSet = {}
    for entry in manifest["workbooks"]:
        candidates = detect(load(corpus / entry["file"]))
        results[entry["id"]] = [
            Finding(address=address)
            for address in sorted({item.address for item in candidates})
        ]
    return results


def baseline_results(directory: str | Path) -> ResultSet:
    """Read what the baseline agent reported, from the files its runs wrote.

    The agent writes its own schema, so this is deliberately forgiving about
    shape and strict about nothing being invented: a finding with no cell is
    dropped rather than guessed at, and an impact that is not a mapping of
    numbers is discarded rather than coerced. What it reported is what it is
    scored on.
    """
    directory = Path(directory)
    results: ResultSet = {}
    for path in sorted(directory.glob("*.json")):
        if path.name == "provider.json":
            continue
        data = json.loads(path.read_text())
        identifier = Path(data.get("workbook", path.stem)).stem
        findings = []
        for item in data.get("findings") or []:
            if not isinstance(item, dict):
                continue
            cell = item.get("cell")
            if not cell:
                continue
            sheet = item.get("sheet")
            impact = item.get("impact")
            findings.append(
                Finding(
                    address=f"{sheet}!{cell}" if sheet else str(cell),
                    proposed_formula=item.get("proposed_formula"),
                    impact={
                        str(k): float(v)
                        for k, v in (impact or {}).items()
                        if isinstance(v, (int, float)) and not isinstance(v, bool)
                    }
                    if isinstance(impact, dict)
                    else {},
                    confidence=item.get("confidence"),
                )
            )
        results[identifier] = findings
    return results


def solution_results(directory: str | Path) -> ResultSet:
    """Read what Materia reported, from the result sets an audit wrote.

    Only `findings` are scored. `intentional` and `inconclusive` are verdicts
    the user never sees, and counting them would score the system on what it
    considered rather than on what it said.
    """
    directory = Path(directory)
    results: ResultSet = {}
    for path in sorted(directory.glob("*.json")):
        if path.name == "provider.json":
            continue
        data = json.loads(path.read_text())
        identifier = Path(data.get("workbook", path.stem)).stem
        results[identifier] = [
            Finding(
                address=item["address"],
                proposed_formula=item.get("proposed_formula"),
                impact={
                    str(k): float(v)
                    for k, v in (item.get("impact") or {}).items()
                    if isinstance(v, (int, float)) and not isinstance(v, bool)
                },
                confidence=item.get("confidence"),
            )
            for item in data.get("findings") or []
            if item.get("address")
        ]
    return results


def suppressed_count(directory: str | Path) -> int:
    """How many real errors the gate held back, across a result directory."""
    directory = Path(directory)
    total = 0
    for path in sorted(directory.glob("*.json")):
        if path.name == "provider.json":
            continue
        total += len(json.loads(path.read_text()).get("immaterial") or [])
    return total


FUNNEL_MARKER = "<!-- funnel -->"


def update_funnel(readme: str | Path, corpus: str | Path = "corpus",
                  results: str | Path = RESULTS) -> str | None:
    """Rewrite the funnel block in README section 4 from a real result set.

    Rule 7: no number that belongs in `results/` is typed into a doc. The
    block is rendered by the same `Funnel` the CLI prints, so the README shows
    what a reader will actually see rather than an artist's impression of it.

    `C11` is the workbook used, because it is the only one whose funnel
    exercises every row including the suppressed count, which is the row the
    product exists for.
    """
    from materia.report import Funnel

    readme = Path(readme)
    text = readme.read_text()
    if text.count(FUNNEL_MARKER) != 2:
        return None

    data = json.loads((Path(results) / "solution" / "C11.json").read_text())
    rendered = Funnel(
        formulas=data["formulas"],
        candidates=data["candidates"],
        survived=len(data["findings"]) + len(data["immaterial"]),
        findings=len(data["findings"]),
        suppressed=len(data["immaterial"]),
        adjudicated=data["adjudicated"],
    ).render(data["workbook"])

    before, _, rest = text.partition(FUNNEL_MARKER)
    _, _, after = rest.partition(FUNNEL_MARKER)
    block = f"{FUNNEL_MARKER}\n```\n{rendered}\n```\n{FUNNEL_MARKER}"
    readme.write_text(before + block + after)
    return rendered


# --- changelog -------------------------------------------------------------

CHANGELOG_MARKER = "| **{stage}** |"


def changelog_evidence(item: Scores) -> str:
    """The evidence cell for a changelog row, built from the scores.

    Written rather than typed, because a changelog entry reconstructed by hand
    is not evidence. See CLAUDE.md section 7.
    """
    return (
        f"{_percent(item.material_precision)} material precision, "
        f"{_percent(item.material_recall)} material recall, "
        f"{_number(item.false_positives_per_clean_workbook)} false positives "
        f"per clean workbook, {item.reported} findings reported"
    )


def update_changelog(readme: str | Path, stage: str, item: Scores) -> bool:
    """Fill one changelog row's evidence cell in place.

    Returns False if the row is not there, rather than writing a row of its
    own: a changelog that grew rows automatically would be a changelog nobody
    wrote.
    """
    readme = Path(readme)
    text = readme.read_text()
    marker = CHANGELOG_MARKER.format(stage=stage)

    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if not line.startswith(marker):
            continue
        cells = line.rstrip("\n").split(" | ")
        if len(cells) < 4:
            return False
        cells[2] = changelog_evidence(item)
        lines[index] = " | ".join(cells) + "\n"
        readme.write_text("".join(lines))
        return True
    return False


# --- the results table in docs/EVALUATION.md -------------------------------

# Row label to the value that fills it. Keyed by label so the table can be
# reordered or reworded without this silently filling the wrong row.
_DOC_ROWS = {
    "Material finding precision": lambda i, e: _percent(i.material_precision),
    "Material recall": lambda i, e: _percent(i.material_recall),
    "Raw anomaly recall": lambda i, e: _percent(i.raw_anomaly_recall),
    "False positives per clean workbook": lambda i, e: _number(
        i.false_positives_per_clean_workbook
    ),
    "Localisation accuracy": lambda i, e: _percent(i.localisation_accuracy),
    "Repair accuracy": lambda i, e: _percent(i.repair_accuracy),
    "Human time per workbook": lambda i, e: "not measured",
    "Cost per workbook": lambda i, e: e.get("cost", "not measured"),
    "Suppressed as immaterial": lambda i, e: str(i.suppressed),
}


def run_cost(directory: str | Path) -> dict[str, str]:
    """What one workbook cost, averaged over the runs in a result directory.

    Read from the token counts each run wrote and the rate for the model that
    produced them, so the figure in the doc is the figure that was spent.
    Returns an empty mapping when the model has no published rate here, rather
    than a number nobody can check.
    """
    from materia.__main__ import RATES_USD_PER_MILLION

    directory = Path(directory)
    provider = directory / "provider.json"
    if not provider.exists():
        return {}
    model = json.loads(provider.read_text()).get("model")
    rate = RATES_USD_PER_MILLION.get(model)
    if rate is None:
        return {}

    runs = [p for p in sorted(directory.glob("*.json")) if p.name != "provider.json"]
    if not runs:
        return {}
    spent = 0.0
    for path in runs:
        tokens = json.loads(path.read_text()).get("tokens") or {}
        spent += tokens.get("in", 0) * rate[0] / 1e6 + tokens.get("out", 0) * rate[1] / 1e6
    return {"cost": f"${spent / len(runs):.2f} on `{model}`"}


def update_results_table(document: str | Path, column: str, item: Scores,
                         extras: dict[str, str] | None = None) -> list[str]:
    """Fill one column of the results table in docs/EVALUATION.md in place.

    Rule 7 of the working agreement: no number that belongs in `results/` is
    typed into a doc by hand. Returns the row labels it filled.
    """
    document = Path(document)
    lines = document.read_text().splitlines(keepends=True)

    header = next(
        (i for i, line in enumerate(lines)
         if line.startswith("| Metric |") and column in line),
        None,
    )
    if header is None:
        return []
    index = [cell.strip() for cell in lines[header].split("|")[1:-1]].index(column)

    filled = []
    for position in range(header + 2, len(lines)):
        line = lines[position]
        if not line.startswith("|"):
            break
        cells = line.rstrip("\n").split("|")
        label = cells[1].strip()
        value = _DOC_ROWS.get(label)
        if value is None:
            continue
        cells[index + 1] = f" {value(item, extras or {})} "
        lines[position] = "|".join(cells) + "\n"
        filled.append(label)

    document.write_text("".join(lines))
    return filled

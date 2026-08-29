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


def _ratio(numerator: int, denominator: int) -> float | None:
    """None rather than zero when there is nothing to divide by."""
    return None if denominator == 0 else numerator / denominator


def _mutations(entry: dict) -> dict[str, dict]:
    return {normalise_address(m["address"]): m for m in entry["mutations"]}


def score(system: str, results: ResultSet, manifest: dict) -> Scores:
    """Score one system's findings against the manifest."""
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

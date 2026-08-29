"""Build the twelve workbook corpus.

The roster is in docs/EVALUATION.md section 2. Eight seeded workbooks, two
clean controls and two hard cases. Mutations are injected separately in T09;
what this module produces is the unmutated base of each workbook plus the
manifest and checksums that make the corpus reproducible.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from materia.corpus.generate import LegitimateBreak, generate
from materia.corpus.layout import DECLARED_OUTPUTS, MONTHS
from materia.corpus.manifest import MANIFEST_VERSION, write_manifest
from materia.preflight import preflight
from materia.recompute import Model

# docs/REPRODUCTION.md section 9. Every workbook seed derives from this one, so
# the whole corpus reproduces from a single number.
CORPUS_SEED = 20260828

MANIFEST_NAME = "manifest.json"
CHECKSUMS_NAME = "checksums.txt"


@dataclass(frozen=True)
class WorkbookSpec:
    identifier: str
    role: str
    note: str
    legitimate_breaks: bool = False

    @property
    def file_name(self) -> str:
        return f"{self.identifier}.xlsx"

    @property
    def seed(self) -> int:
        return CORPUS_SEED + int(self.identifier[1:])


CORPUS: list[WorkbookSpec] = [
    *(
        WorkbookSpec(f"C{index:02d}", "seeded", "One to three seeded mutations")
        for index in range(1, 9)
    ),
    WorkbookSpec("C09", "clean_control", "No mutations and no pattern breaks"),
    WorkbookSpec(
        "C10",
        "clean_control",
        "No mutations, three legitimate pattern breaks. The workbook that "
        "breaks naive tools.",
        legitimate_breaks=True,
    ),
    WorkbookSpec("C11", "hard_case", "A real mutation that is immaterial"),
    WorkbookSpec("C12", "hard_case", "One in taxonomy and one out of taxonomy mutation"),
]


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _entry(spec: WorkbookSpec, path: Path, breaks: list[LegitimateBreak]) -> dict:
    report = preflight(path)
    model = Model.load(path, outputs=DECLARED_OUTPUTS)
    return {
        "id": spec.identifier,
        "file": spec.file_name,
        "seed": spec.seed,
        "role": spec.role,
        "note": spec.note,
        "declared_outputs": list(DECLARED_OUTPUTS),
        "output_values": {
            output: float(model.value(output)) for output in DECLARED_OUTPUTS
        },
        "formula_count": report.formula_count,
        "sha256": sha256_of(path),
        "legitimate_breaks": [
            {"kind": item.kind, "cells": list(item.cells), "why": item.why}
            for item in breaks
        ],
        # Filled in by the mutation injector in T09.
        "mutations": [],
    }


def write_checksums(directory: Path, entries: list[dict]) -> Path:
    """sha256sum format, sorted by file name, so the file is stable and the
    output of `sha256sum -c` is meaningful to anyone who prefers that."""
    path = Path(directory) / CHECKSUMS_NAME
    lines = sorted(f"{entry['sha256']}  {entry['file']}" for entry in entries)
    path.write_text("\n".join(lines) + "\n")
    return path


def build_corpus(directory: str | Path) -> dict:
    """Generate all twelve workbooks, the manifest and the checksums."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    entries = []
    for spec in CORPUS:
        path, breaks = generate(
            directory / spec.file_name, spec.seed, spec.legitimate_breaks
        )
        entries.append(_entry(spec, path, breaks))

    manifest = {
        "version": MANIFEST_VERSION,
        "seed": CORPUS_SEED,
        "months": MONTHS,
        "declared_outputs": list(DECLARED_OUTPUTS),
        "workbooks": entries,
    }
    write_manifest(directory / MANIFEST_NAME, manifest)
    write_checksums(directory, entries)
    return manifest


@dataclass(frozen=True)
class CheckResult:
    """What `make corpus-check` found."""

    matched: list[str]
    mismatched: list[str]
    missing: list[str]

    @property
    def ok(self) -> bool:
        return not self.mismatched and not self.missing


def check_corpus(directory: str | Path) -> CheckResult:
    """Compare the workbooks on disk against the committed checksums."""
    directory = Path(directory)
    checksums = directory / CHECKSUMS_NAME
    if not checksums.exists():
        raise FileNotFoundError(checksums)

    matched, mismatched, missing = [], [], []
    for line in checksums.read_text().splitlines():
        if not line.strip():
            continue
        digest, name = line.split(maxsplit=1)
        path = directory / name.strip()
        if not path.exists():
            missing.append(name.strip())
        elif sha256_of(path) == digest:
            matched.append(name.strip())
        else:
            mismatched.append(name.strip())
    return CheckResult(matched, mismatched, missing)

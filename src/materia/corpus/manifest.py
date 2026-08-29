"""The corpus manifest: what is in each workbook and what it is measured on.

The manifest is ground truth for the evaluator. If it is wrong, every metric
in the submission is wrong, so it is validated on write and on read rather
than trusted.

Validation is a written out check rather than a JSON Schema document, so the
project keeps its dependency list short and the rules stay readable next to
the code that produces them.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

MANIFEST_VERSION = 1

ROLES = {"seeded", "clean_control", "hard_case"}
BREAK_KINDS = {"hardcoded_actuals", "first_period", "manual_override"}

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ADDRESS = re.compile(r"^[^!]+![A-Z]+[0-9]+$")
_WORKBOOK_ID = re.compile(r"^C[0-9]{2}$")


class InvalidManifest(ValueError):
    """The manifest does not describe a corpus the evaluator can score."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise InvalidManifest(message)


def validate_manifest(data: Any) -> None:
    """Raise unless the manifest is complete and internally consistent."""
    _require(isinstance(data, dict), "manifest must be an object")
    for key in ("version", "seed", "months", "declared_outputs", "workbooks"):
        _require(key in data, f"manifest is missing {key!r}")

    _require(data["version"] == MANIFEST_VERSION, f"unsupported version {data['version']!r}")
    _require(isinstance(data["seed"], int), "seed must be an integer")
    _require(isinstance(data["months"], int) and data["months"] > 0, "months must be positive")

    outputs = data["declared_outputs"]
    _require(
        isinstance(outputs, list) and len(outputs) == 2 and all(map(_ADDRESS.match, outputs)),
        "declared_outputs must be two Sheet!Cell addresses",
    )

    workbooks = data["workbooks"]
    _require(isinstance(workbooks, list) and workbooks, "workbooks must be a non empty list")

    seen = set()
    for entry in workbooks:
        _validate_workbook(entry, outputs)
        _require(entry["id"] not in seen, f"duplicate workbook id {entry['id']!r}")
        seen.add(entry["id"])


def _validate_workbook(entry: Any, declared_outputs: list[str]) -> None:
    _require(isinstance(entry, dict), "each workbook must be an object")
    identifier = entry.get("id")
    _require(
        isinstance(identifier, str) and bool(_WORKBOOK_ID.match(identifier)),
        f"bad workbook id {identifier!r}",
    )

    where = f"workbook {identifier}"
    for key in (
        "file",
        "seed",
        "role",
        "declared_outputs",
        "output_values",
        "formula_count",
        "sha256",
        "legitimate_breaks",
        "mutations",
    ):
        _require(key in entry, f"{where} is missing {key!r}")

    _require(entry["file"] == f"{identifier}.xlsx", f"{where} file name does not match its id")
    _require(isinstance(entry["seed"], int), f"{where} seed must be an integer")
    _require(entry["role"] in ROLES, f"{where} has unknown role {entry['role']!r}")
    _require(
        entry["declared_outputs"] == declared_outputs,
        f"{where} declares different outputs from the corpus",
    )
    _require(
        isinstance(entry["formula_count"], int) and entry["formula_count"] > 0,
        f"{where} formula_count must be positive",
    )
    _require(
        isinstance(entry["sha256"], str) and bool(_SHA256.match(entry["sha256"])),
        f"{where} sha256 is not a sha256 digest",
    )

    values = entry["output_values"]
    _require(isinstance(values, dict), f"{where} output_values must be an object")
    _require(
        set(values) == set(declared_outputs),
        f"{where} output_values must cover exactly the declared outputs",
    )
    for address, value in values.items():
        _require(
            isinstance(value, (int, float)) and not isinstance(value, bool),
            f"{where} output {address} is not a number",
        )

    breaks = entry["legitimate_breaks"]
    _require(isinstance(breaks, list), f"{where} legitimate_breaks must be a list")
    for item in breaks:
        _require(isinstance(item, dict), f"{where} has a malformed legitimate break")
        for key in ("kind", "cells", "why"):
            _require(key in item, f"{where} legitimate break is missing {key!r}")
        _require(
            item["kind"] in BREAK_KINDS,
            f"{where} has unknown break kind {item['kind']!r}",
        )
        _require(
            isinstance(item["cells"], list)
            and item["cells"]
            and all(_ADDRESS.match(cell) for cell in item["cells"]),
            f"{where} legitimate break cells must be Sheet!Cell addresses",
        )
        _require(
            isinstance(item["why"], str) and len(item["why"]) > 20,
            f"{where} legitimate break needs an explanation",
        )

    _require(isinstance(entry["mutations"], list), f"{where} mutations must be a list")


def write_manifest(path: str | Path, data: dict) -> Path:
    """Validate then write. Sorted and indented, so the file is diffable."""
    validate_manifest(data)
    path = Path(path)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    return path


def read_manifest(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text())
    validate_manifest(data)
    return data

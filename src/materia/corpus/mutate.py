"""Mutation injector.

The taxonomy is in docs/EVALUATION.md section 3, grounded in the published
spreadsheet error classifications rather than invented. Five families the
detectors target, and two they deliberately do not, so recall is measured
honestly rather than against a mirror of our own detectors.

Every mutation records the true delta on each declared output, measured with
the recompute engine against the unmutated model. That is what makes
materiality ground truth rather than opinion: the manifest does not say a
mutation matters, it says by how much.

Deltas are measured one mutation at a time against the clean baseline. A
workbook carrying three mutations still records what each one costs on its
own, because that is the question the adjudicator is asked about each cell.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl

from materia.corpus.generate import FIXED_DATETIME, save
from materia.corpus.layout import (
    ASSUMPTION_ROWS,
    ASSUMPTIONS_SHEET,
    COST_ROWS,
    COSTS_SHEET,
    DECLARED_OUTPUTS,
    MONTHS,
    PL_ROWS,
    PL_SHEET,
    REVENUE_ROWS,
    REVENUE_SHEET,
    TOTAL,
    VALUATION_ROWS,
    VALUATION_SHEET,
    month_column,
)
from materia.corpus.generate import ModelValues
from materia.parse import read_formulas
from materia.recompute import Model

IN_TAXONOMY = ("M1", "M2", "M3", "M4", "M5")
OUT_OF_TAXONOMY = ("M6", "M7")
FAMILIES = IN_TAXONOMY + OUT_OF_TAXONOMY

# A finding has to move a declared output by at least this much to count as
# material. Matches the gate default in config.yaml.
MATERIALITY_THRESHOLD = 0.01

# C11 exists to be detected and suppressed, so its mutation is aimed at a
# change this small. docs/EVALUATION.md section 2.
IMMATERIAL_TARGET = 0.0003


@dataclass(frozen=True)
class MutationPlan:
    """A mutation before its cost is known."""

    family: str
    address: str
    mutated: str | float
    description: str


@dataclass(frozen=True)
class Mutation:
    """A mutation and what it actually costs."""

    family: str
    address: str
    original: str | float
    mutated: str | float
    description: str
    in_taxonomy: bool
    deltas: dict[str, float | None] = field(default_factory=dict)
    relative: dict[str, float | None] = field(default_factory=dict)
    material: bool = False

    def as_manifest_entry(self) -> dict:
        return {
            "family": self.family,
            "address": self.address,
            "original": self.original,
            "mutated": self.mutated,
            "description": self.description,
            "in_taxonomy": self.in_taxonomy,
            "deltas": self.deltas,
            "relative": self.relative,
            "material": self.material,
        }


# --- family planners -------------------------------------------------------
#
# Each returns the cell to change and what to put in it. Targets are chosen to
# reach a declared output: a mutation nothing depends on would be correctly
# ignored by every system, which measures nothing.


def _m1_stale_paste(model: Model, rng: random.Random) -> MutationPlan:
    """A formula replaced by a value that was right earlier and is not now.

    Aimed at the customer roll forward, so the stale figure carries through
    every later month, which is what makes the paste expensive rather than
    a one month rounding difference.
    """
    month = rng.randint(5, 9)
    address = f"{REVENUE_SHEET}!{month_column(month)}{REVENUE_ROWS['opening_customers']}"
    stale = model.value(
        f"{REVENUE_SHEET}!{month_column(1)}{REVENUE_ROWS['opening_customers']}"
    )
    return MutationPlan(
        "M1",
        address,
        float(stale),
        "Opening customers pasted as a value from month 1 and never restored.",
    )


def _m2_wrong_fill_origin(model: Model, rng: random.Random) -> MutationPlan:
    """A copied formula dragged from the wrong origin.

    One reference in the closing customers roll forward points a column too
    far left, so the cell no longer matches its peers in R1C1.
    """
    month = rng.randint(6, 14)
    here, previous = month_column(month), month_column(month - 1)
    rows = REVENUE_ROWS
    address = f"{REVENUE_SHEET}!{here}{rows['closing_customers']}"
    mutated = (
        f"={previous}{rows['opening_customers']}+{here}{rows['new_customers']}"
        f"+{here}{rows['churned_customers']}"
    )
    return MutationPlan(
        "M2", address, mutated, "Fill handle dragged from the column to the left."
    )


def _m3_truncated_range(model: Model, rng: random.Random) -> MutationPlan:
    """An aggregation that misses the last row or column of its block.

    The classic cause is a row inserted at the boundary of a range.
    """
    row = PL_ROWS["ebitda"]
    address = f"{PL_SHEET}!{TOTAL}{row}"
    mutated = f"=SUM({month_column(1)}{row}:{month_column(MONTHS - 1)}{row})"
    return MutationPlan(
        "M3", address, mutated, "Total range stops one month short of the block."
    )


def _m4_off_by_one_period(model: Model, rng: random.Random) -> MutationPlan:
    """A cell reading the prior period where its peers read the current one.

    Applied to the opening balance, so the model permanently loses a period
    of growth rather than being wrong for one month.
    """
    month = rng.randint(6, 12)
    address = f"{REVENUE_SHEET}!{month_column(month)}{REVENUE_ROWS['opening_customers']}"
    mutated = f"={month_column(month - 2)}{REVENUE_ROWS['closing_customers']}"
    return MutationPlan(
        "M4",
        address,
        mutated,
        "Opening balance reads two months back, not the month before.",
    )


def _m5_operator_flip(model: Model, rng: random.Random) -> MutationPlan:
    """A sign flipped in a subtotal.

    Cost lines are already negative on the P&L, so adding where the model
    subtracts turns a cost into income.
    """
    month = rng.randint(4, 20)
    here = month_column(month)
    rows = PL_ROWS
    address = f"{PL_SHEET}!{here}{rows['gross_profit']}"
    mutated = f"={here}{rows['revenue']}-{here}{rows['cogs']}"
    return MutationPlan(
        "M5", address, mutated, "Cost of sales added back instead of deducted."
    )


def _m6_wrong_assumption(model: Model, rng: random.Random) -> MutationPlan:
    """A structurally perfect number that is wrong by a decimal place.

    No peer group signal exists for this, which is the point. We expect to
    miss it and we say so.
    """
    address = f"{ASSUMPTIONS_SHEET}!B{ASSUMPTION_ROWS['cogs_rate']}"
    original = float(model.value(address))
    return MutationPlan(
        "M6",
        address,
        round(original / 10, 5),
        "Cost ratio entered a decimal place out.",
    )


def _m7_wrong_sheet_reference(model: Model, rng: random.Random) -> MutationPlan:
    """A correct formula pointing at the wrong row of the right sheet.

    Opening headcount reads the hires per month row instead. They are
    adjacent, both are headcount figures, and either is plausible in the
    formula bar. It also sits in the first period column, where breaking the
    pattern is legitimate, so there is nothing structural to notice.
    """
    address = f"{COSTS_SHEET}!{month_column(1)}{COST_ROWS['headcount']}"
    mutated = f"={ASSUMPTIONS_SHEET}!$B${ASSUMPTION_ROWS['hires_per_month']}"
    return MutationPlan(
        "M7",
        address,
        mutated,
        "Opening headcount reads the hires per month row, one row below.",
    )


PLANNERS = {
    "M1": _m1_stale_paste,
    "M2": _m2_wrong_fill_origin,
    "M3": _m3_truncated_range,
    "M4": _m4_off_by_one_period,
    "M5": _m5_operator_flip,
    "M6": _m6_wrong_assumption,
    "M7": _m7_wrong_sheet_reference,
}


def _immaterial_plan(model: Model, address: str | None = None) -> MutationPlan:
    """A real error whose cost is genuinely below the threshold.

    Aimed at the last month's overhead, which nothing after it depends on, so
    its effect stays small and stays linear. The constant is solved for rather
    than guessed: measure the slope with a probe patch, then pick the value
    that lands the largest relative move on the target. The result is verified
    after injection, in `inject`, rather than trusted.
    """
    address = address or f"{COSTS_SHEET}!{month_column(MONTHS)}{COST_ROWS['overhead']}"
    current = float(model.value(address))

    probe = model.patch(address, current + 1000.0)
    slopes = {
        output: (delta.relative or 0.0) / 1000.0
        for output, delta in probe.outputs.items()
    }
    steepest = max(slopes.values(), key=abs)
    if steepest == 0:
        raise ValueError(f"{address} does not reach any declared output")

    offset = IMMATERIAL_TARGET / steepest
    return MutationPlan(
        "M1",
        address,
        round(current + offset, 2),
        "Final month overhead pasted as a value, slightly off.",
    )


# --- corpus assignment -----------------------------------------------------
#
# Written out rather than drawn at random so the corpus composition is visible
# and stable. Every family appears, and C09 and C10 stay clean.
ASSIGNMENTS: dict[str, list[str]] = {
    "C01": ["M1"],
    "C02": ["M2"],
    "C03": ["M1", "M3"],
    "C04": ["M4"],
    "C05": ["M5"],
    "C06": ["M2", "M5", "M7"],
    "C07": ["M3"],
    "C08": ["M1", "M4"],
    "C09": [],
    "C10": [],
    "C11": ["immaterial"],
    "C12": ["M2", "M6"],
}


def plan_for(workbook_id: str, model: Model, seed: int) -> list[MutationPlan]:
    """The mutations a workbook should carry."""
    rng = random.Random(seed)
    plans = []
    for family in ASSIGNMENTS.get(workbook_id, []):
        if family == "immaterial":
            plans.append(_immaterial_plan(model))
        else:
            plans.append(PLANNERS[family](model, rng))
    return plans


# --- injection -------------------------------------------------------------


def _measure(model: Model, plan: MutationPlan) -> tuple[dict, dict, bool]:
    """What this one mutation costs, on its own, against the clean model."""
    result = model.patch(plan.address, plan.mutated)
    deltas = {name: delta.delta for name, delta in result.outputs.items()}
    relative = {name: delta.relative for name, delta in result.outputs.items()}
    material = any(
        value is not None and abs(value) >= MATERIALITY_THRESHOLD
        for value in relative.values()
    )
    return deltas, relative, material


def _edited_model(
    path: Path, changes: dict[str, str | float], base: Model | None = None
) -> Model:
    """The model as it will be once the changes are applied.

    `base` is the model of the file as it stands. Callers that already have one
    pass it in: loading and evaluating a 739 formula workbook is the expensive
    part of building the corpus, and it was being done three times per file.
    """
    from materia.formula import parse_formula
    from materia.parse import normalise_address

    base = base if base is not None else Model.load(path)
    formulas = dict(base.formulas)
    constants = dict(base.constants)

    for address, content in changes.items():
        key = normalise_address(address)
        if isinstance(content, str) and content.startswith("="):
            formulas[key] = parse_formula(content)
            constants.pop(key, None)
        else:
            formulas.pop(key, None)
            constants[key] = content

    return Model(constants, formulas)


def _rewrite(
    path: Path, changes: dict[str, str | float], base: Model | None = None
) -> None:
    """Apply cell changes and save, with the cached values recalculated.

    A spreadsheet recalculates after an edit, so the values in the file have
    to follow the formulas rather than stay as they were.
    """
    edited = _edited_model(path, changes, base)

    values = ModelValues()
    for address, value in edited.values.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            sheet, coordinate = address.split("!", 1)
            values.set(sheet, coordinate, float(value))

    workbook = openpyxl.load_workbook(path)
    for address, content in changes.items():
        sheet, coordinate = address.split("!", 1)
        workbook[sheet][coordinate] = content
    workbook.properties.creator = "materia"
    workbook.properties.lastModifiedBy = "materia"
    workbook.properties.created = FIXED_DATETIME
    workbook.properties.modified = FIXED_DATETIME

    save(workbook, path, values)


def inject(
    path: str | Path, plans: list[MutationPlan], clean: Model | None = None
) -> list[Mutation]:
    """Apply mutations to a workbook and record what each one costs.

    The workbook must be the unmutated original. Deltas are measured against
    it before anything is written. `clean` lets a caller that already loaded
    that model hand it over rather than pay for it twice.
    """
    path = Path(path)
    if not plans:
        return []

    formulas = {cell.address: cell.formula for cell in read_formulas(path)}
    clean = clean if clean is not None else Model.load(path, outputs=DECLARED_OUTPUTS)

    mutations = []
    for plan in plans:
        deltas, relative, material = _measure(clean, plan)
        mutations.append(
            Mutation(
                family=plan.family,
                address=plan.address,
                original=formulas.get(plan.address, clean.value(plan.address)),
                mutated=plan.mutated,
                description=plan.description,
                in_taxonomy=plan.family in IN_TAXONOMY,
                deltas=deltas,
                relative=relative,
                material=material,
            )
        )

    _rewrite(path, {plan.address: plan.mutated for plan in plans}, clean)
    return mutations


def revert(path: str | Path, mutations: list[Mutation]) -> Path:
    """Put the original formulas back.

    Reverting a workbook returns it byte for byte, which is the check that
    injection changes nothing except the cells it says it changed.
    """
    path = Path(path)
    if mutations:
        _rewrite(path, {item.address: item.original for item in mutations})
    return path

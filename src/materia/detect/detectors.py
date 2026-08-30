"""The five structural detectors.

One per in taxonomy mutation family (docs/EVALUATION.md section 3). They are
tuned for recall, not precision, and they are expected to fire on legitimate
pattern breaks: a hardcoded actuals row, a first period column with no prior
to reference, a deliberate override. That is correct behaviour, not a bug to
fix. Their noise is what the agent layer exists to filter, and a detector that
tried to be clever here would be making a judgement it has no evidence for.

See docs/ARCHITECTURE.md section 4.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from materia.detect.peers import MINIMUM_PEERS, PeerCell, PeerGroup, Workbook
from materia.formula import FunctionCall, RangeRef, parse_formula, walk

# A group where fewer than this share of cells agree has no pattern to break.
MINIMUM_MODE_SHARE = 0.6

# How far a reference can shift and still read as a period slip rather than a
# different formula entirely.
MAXIMUM_PERIOD_SHIFT = 2

DETECTOR_FAMILIES = {
    "D1": "M1",
    "D2": "M2",
    "D3": "M3",
    "D4": "M4",
    "D5": "M5",
}


@dataclass(frozen=True)
class Candidate:
    """One anomaly, with the evidence that triggered it.

    A candidate is not a finding. It is a cell worth asking about, and most of
    them will turn out to be deliberate.
    """

    detector: str
    address: str
    reason: str
    formula: str | None = None
    r1c1: str | None = None
    expected_r1c1: str | None = None
    peer_axis: str | None = None
    peers: tuple[PeerCell, ...] = field(default_factory=tuple)

    @property
    def sheet(self) -> str:
        return self.address.split("!", 1)[0]


# --- R1C1 comparison -------------------------------------------------------

_REFERENCE_TOKEN = re.compile(
    r"(?:(?:'[^']+'|[A-Za-z_][A-Za-z0-9_.]*)!)?R(?:\[-?\d+\]|\d+)?C(?:\[-?\d+\]|\d+)?"
)
_OFFSET = re.compile(r"R(\[-?\d+\]|\d+)?C(\[-?\d+\]|\d+)?$")


def _skeleton(token: str) -> str:
    """The formula with every reference replaced, so only structure is left."""
    return _REFERENCE_TOKEN.sub("@", token)


def _references(token: str) -> list[str]:
    return _REFERENCE_TOKEN.findall(token)


def _axis_values(reference: str) -> tuple[int | None, int | None]:
    """The row and column parts of one R1C1 reference, as numbers."""
    match = _OFFSET.search(reference)
    if match is None:
        return None, None

    def value(part: str | None) -> int | None:
        if part is None:
            return 0
        return int(part.strip("[]"))

    return value(match.group(1)), value(match.group(2))


def _is_period_shift(token: str, mode: str) -> bool:
    """True when the only difference is a reference sliding a period or two."""
    if _skeleton(token) != _skeleton(mode):
        return False
    here, there = _references(token), _references(mode)
    if len(here) != len(there) or here == there:
        return False
    for one, other in zip(here, there):
        row_a, column_a = _axis_values(one)
        row_b, column_b = _axis_values(other)
        if None in (row_a, column_a, row_b, column_b):
            return False
        if abs(row_a - row_b) > MAXIMUM_PERIOD_SHIFT:
            return False
        if abs(column_a - column_b) > MAXIMUM_PERIOD_SHIFT:
            return False
    return True


def _is_operator_change(token: str, mode: str) -> bool:
    """True when the references are the same and only an operator moved."""
    return _references(token) == _references(mode) and _skeleton(token) != _skeleton(mode)


# --- detectors -------------------------------------------------------------


def _usable(group: PeerGroup) -> bool:
    return len(group.members) >= MINIMUM_PEERS and group.mode_share >= MINIMUM_MODE_SHARE


def d1_hardcoded_value(workbook: Workbook) -> list[Candidate]:
    """A value sitting where its neighbours hold formulas.

    Someone pasted a number over a formula, or the row is deliberately entered
    rather than calculated. The detector cannot tell those apart, and does not
    try to.
    """
    candidates = []
    for sheet in workbook.sheets.values():
        rows = {row for row, _ in sheet.formulas}
        for row in sorted(rows):
            formula_columns = sorted(c for r, c in sheet.formulas if r == row)
            if len(formula_columns) < MINIMUM_PEERS:
                continue

            first, last = _contiguous_span(sheet, row, formula_columns)
            for column in range(first, last + 1):
                if (row, column) not in sheet.values:
                    continue
                address = f"{sheet.name}!{_column_letter(column)}{row}"
                # The cells either side, not the leftmost three. In a monthly
                # model the first column is legitimately different, so showing
                # it as the peer group would be misleading evidence.
                nearest = sorted(formula_columns, key=lambda c: abs(c - column))[:3]
                neighbours = tuple(
                    PeerCell(
                        sheet.formulas[(row, c)].address,
                        sheet.formulas[(row, c)].formula,
                        sheet.formulas[(row, c)].r1c1,
                    )
                    for c in sorted(nearest)
                )
                candidates.append(
                    Candidate(
                        detector="D1",
                        address=address,
                        reason=(
                            f"Holds a value, but {len(formula_columns)} other cells "
                            f"in row {row} of {sheet.name} hold formulas."
                        ),
                        peer_axis="row",
                        peers=neighbours,
                    )
                )
    return candidates


def _contiguous_span(sheet, row: int, formula_columns: list[int]) -> tuple[int, int]:
    """The unbroken run of occupied cells the formulas sit in."""
    first = min(formula_columns)
    last = max(formula_columns)
    while sheet.occupied(row, first - 1):
        first -= 1
    while sheet.occupied(row, last + 1):
        last += 1
    return first, last


def _column_letter(index: int) -> str:
    from openpyxl.utils import get_column_letter

    return get_column_letter(index)


def _non_conforming(workbook: Workbook):
    """Every cell that does not match the token its peer group agrees on."""
    for group in workbook.groups:
        if not _usable(group):
            continue
        mode = group.mode
        for member in group.members:
            if member.r1c1 != mode:
                yield group, member, mode


def d2_inconsistent_formula(workbook: Workbook) -> list[Candidate]:
    """A formula that does not match the one its peers agree on.

    Every copied row should normalise to a single R1C1 token. This fires on
    anything that does not, which includes every first period column in the
    model, because month one has no prior month to read.
    """
    candidates = []
    for group, member, mode in _non_conforming(workbook):
        share = group.tokens[mode] / len(group.members)
        candidates.append(
            Candidate(
                detector="D2",
                address=member.address,
                reason=(
                    f"Normalises to {member.r1c1}, where {group.tokens[mode]} of "
                    f"{len(group.members)} cells in {group.describe()} normalise to "
                    f"{mode} ({share:.0%})."
                ),
                formula=member.formula,
                r1c1=member.r1c1,
                expected_r1c1=mode,
                peer_axis=group.axis,
                peers=group.conforming(),
            )
        )
    return candidates


def d3_aggregation_range(workbook: Workbook) -> list[Candidate]:
    """An aggregation whose range does not cover the block it sits against.

    The usual cause is a row or column inserted at the boundary of a range.
    A deliberately partial window, such as a last twelve months subtotal,
    looks identical from here.
    """
    candidates = []
    for sheet in workbook.sheets.values():
        for (row, column), cell in sorted(sheet.formulas.items()):
            # Preflight guarantees every formula parses, so a failure here is
            # a bug worth surfacing rather than skipping past.
            for node in walk(parse_formula(cell.formula)):
                if not isinstance(node, FunctionCall):
                    continue
                for argument in node.arguments:
                    if not isinstance(argument, RangeRef):
                        continue
                    finding = _range_gap(workbook, sheet, cell, node, argument)
                    if finding is not None:
                        candidates.append(finding)
    return candidates


def _range_gap(workbook: Workbook, sheet, cell, call, span: RangeRef):
    """Compare a range against the run of cells it sits against."""
    target_sheet_name = (span.start.sheet or sheet.name).strip("'")
    target = workbook.sheets.get(target_sheet_name)
    if target is None:
        return None

    first_row, last_row = sorted((span.start.row, span.end.row))
    first_column, last_column = sorted((span.start.column, span.end.column))
    if first_row != last_row and first_column != last_column:
        return None  # a rectangle has no single axis to extend along

    self_cell = (cell.row, cell.column) if target_sheet_name == sheet.name else None

    def occupied(row: int, column: int) -> bool:
        return (row, column) != self_cell and target.occupied(row, column)

    if first_row == last_row:
        low, high = first_column, last_column
        while occupied(first_row, low - 1):
            low -= 1
        while occupied(first_row, high + 1):
            high += 1
        if (low, high) == (first_column, last_column):
            return None
        actual = f"{_column_letter(first_column)}:{_column_letter(last_column)}"
        block = f"{_column_letter(low)}:{_column_letter(high)}"
        axis = "columns"
    else:
        low, high = first_row, last_row
        while occupied(low - 1, first_column):
            low -= 1
        while occupied(high + 1, first_column):
            high += 1
        if (low, high) == (first_row, last_row):
            return None
        actual, block = f"{first_row}:{last_row}", f"{low}:{high}"
        axis = "rows"

    return Candidate(
        detector="D3",
        address=cell.address,
        reason=(
            f"{call.name} covers {axis} {actual} on {target_sheet_name}, but the "
            f"populated block runs {block}."
        ),
        formula=cell.formula,
        r1c1=cell.r1c1,
        peer_axis=axis,
    )


def d4_period_offset(workbook: Workbook) -> list[Candidate]:
    """A cell reading one period away from where its peers read.

    Narrower than D2: the formula has the same shape as its peers and only a
    reference has slid. That is what a copy paste across a period boundary
    looks like.
    """
    candidates = []
    for group, member, mode in _non_conforming(workbook):
        if not _is_period_shift(member.r1c1, mode):
            continue
        candidates.append(
            Candidate(
                detector="D4",
                address=member.address,
                reason=(
                    f"Same shape as its peers but a reference is offset: "
                    f"{member.r1c1} against {mode} in {group.describe()}."
                ),
                formula=member.formula,
                r1c1=member.r1c1,
                expected_r1c1=mode,
                peer_axis=group.axis,
                peers=group.conforming(),
            )
        )
    return candidates


def d5_operator_flip(workbook: Workbook) -> list[Candidate]:
    """A cell reading the same cells as its peers but combining them differently.

    A cost added where the model subtracts, or a sign the wrong way round.
    """
    candidates = []
    for group, member, mode in _non_conforming(workbook):
        if not _is_operator_change(member.r1c1, mode):
            continue
        candidates.append(
            Candidate(
                detector="D5",
                address=member.address,
                reason=(
                    f"Reads the same cells as its peers but combines them "
                    f"differently: {member.r1c1} against {mode} in "
                    f"{group.describe()}."
                ),
                formula=member.formula,
                r1c1=member.r1c1,
                expected_r1c1=mode,
                peer_axis=group.axis,
                peers=group.conforming(),
            )
        )
    return candidates


DETECTORS = {
    "D1": d1_hardcoded_value,
    "D2": d2_inconsistent_formula,
    "D3": d3_aggregation_range,
    "D4": d4_period_offset,
    "D5": d5_operator_flip,
}


def detect(workbook: Workbook) -> list[Candidate]:
    """Run every detector. Candidates are sorted for a stable report order.

    One cell can be flagged by more than one detector, which is deliberate:
    they are separate signals and suppressing the overlap here would be a
    judgement the detector layer has no business making.
    """
    candidates = []
    for detector in sorted(DETECTORS):
        candidates.extend(DETECTORS[detector](workbook))
    return sorted(candidates, key=lambda c: (c.address, c.detector))

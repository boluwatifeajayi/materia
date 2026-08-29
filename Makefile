# Materia. Targets match docs/REPRODUCTION.md.
#
# Everything except "verify" is a stub until its task lands. Stubs exit 1 so a
# half built pipeline cannot look like it succeeded.

PYTHON ?= python
N ?= 3
CONCURRENCY ?= 4

.PHONY: all verify corpus corpus-check baseline solution eval eval-repeat trace-index

## Full pipeline, docs/REPRODUCTION.md section 7
all: verify corpus baseline solution eval

## Unit tests. No API key needed.
verify:
	$(PYTHON) -m pytest

## Generate the 12 workbook corpus from seed.
corpus:
	$(PYTHON) -m materia corpus build

## Compare corpus checksums against corpus/checksums.txt.
corpus-check:
	$(PYTHON) -m materia corpus check

## Baseline agent over the corpus. T18, T19.
baseline:
	@echo "make baseline: not implemented (T18, T19)" >&2
	@exit 1

## Materia over the corpus. T17, T20, T21.
solution:
	@echo "make solution: not implemented (T17, T20, T21)" >&2
	@exit 1

## Score both result sets into results/. T11.
eval:
	@echo "make eval: not implemented (T11)" >&2
	@exit 1

## Repeat runs for the variance table, N=3 by default. T26.
eval-repeat:
	@echo "make eval-repeat N=$(N): not implemented (T26)" >&2
	@exit 1

## Generate trajectories/index.md. T24.
trace-index:
	@echo "make trace-index: not implemented (T24)" >&2
	@exit 1

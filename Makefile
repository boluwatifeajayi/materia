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

## Baseline agent over the corpus. The scored sweep is T19.
baseline:
	$(PYTHON) -m materia baseline corpus/C03.xlsx \
	  --traces trajectories/baseline --results results/baseline

## Materia over the corpus. Full corpus lands with T20.
solution:
	$(PYTHON) -m materia audit corpus/C03.xlsx --explain \
	  --traces trajectories/solution --results results/solution

## Score result sets into results/.
eval:
	$(PYTHON) -m materia eval --changelog README.md

## Repeat runs for the variance table, N=3 by default. T26.
eval-repeat:
	@echo "make eval-repeat N=$(N): not implemented (T26)" >&2
	@exit 1

## Render the featured trajectories and generate trajectories/index.md.
trace-index:
	$(PYTHON) -m materia trace index

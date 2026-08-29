# Reproduction guide

Written for someone starting from a clean machine with nothing installed. Follow top to bottom and you will reproduce the headline table in `EVALUATION.md`.

Total wall clock: about 25 minutes, of which roughly 20 is the two agent runs. Total API cost: approximately `[TBD]` USD.

> **Every runtime and cost figure in this guide is an estimate written before the pipeline existed.** T25 runs `make all` from a fresh clone and replaces each one with a measured value. Until then, treat them as rough expectations, not results.

---

## 1. Requirements

| Component | Version | Notes |
| --- | --- | --- |
| Python | 3.11 or 3.12 | 3.13 untested |
| `uv` | 0.4+ | Or use `venv` + `pip`, both paths given below |
| OS | macOS, Linux, or WSL2 | No Excel or Windows required at any point |
| Anthropic API key | any | Needed for the agent and the baseline |
| Disk | ~200 MB | |

No Microsoft Excel, no LibreOffice, no headless office suite. The recompute engine is our own and runs in pure Python. This is deliberate: it removes the single most common reproduction failure for spreadsheet tooling.

## 2. Setup

```bash
git clone <REPO_URL>
cd materia

# with uv (recommended)
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"

# or with stdlib venv
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

Set your key. It is read from the environment only and never written to disk.

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

Verify the install. This runs the unit tests for the recompute engine and the graph builder, needs no API key, and takes about 20 seconds.

```bash
make verify
```

Expected output ends with:

```
recompute engine:  [N] passed
dependency graph:  [N] passed
preflight:         [N] passed
OK
```

## 3. Build the corpus

The workbooks are generated from a fixed seed rather than committed as binaries, so you can inspect exactly how each one was made.

```bash
make corpus
```

Runtime: about 15 seconds. Cost: none, no API calls.

Produces:

```
corpus/
  C01.xlsx ... C12.xlsx        12 workbooks
  manifest.json                mutations, declared output cells, true deltas
```

Verify the corpus matches the published one:

```bash
make corpus-check
```

Compares SHA256 of each workbook against `corpus/checksums.txt`. If these match, you have byte identical inputs to ours.

## 4. Run the baseline

```bash
make baseline
```

Runtime: about 10 minutes for 12 workbooks. Cost: approximately `[TBD]` USD.

A general purpose coding agent is given each workbook and a sandboxed shell, and is free to write its own analysis code. Instructions in `docs/AGENT_INSTRUCTIONS.md`, section 3.

Output: `results/baseline/C{01..12}.json`, plus full trajectories in `trajectories/baseline/`.

## 5. Run the solution

```bash
make solution
```

Runtime: about 8 minutes for 12 workbooks. Cost: approximately `[TBD]` USD.

Output: `results/solution/C{01..12}.json`, trajectories in `trajectories/solution/`.

To watch a single workbook run with the full report rendered:

```bash
python -m materia audit corpus/C03.xlsx --explain
```

This is the command used in the demo video.

## 6. Score both

```bash
make eval
```

Runtime: under 5 seconds. Cost: none.

Scores both result sets against `corpus/manifest.json` and writes:

```
results/headline.md          the table in EVALUATION.md section 5
results/per_workbook.md      per case breakdown
results/sensitivity.md       metrics at three materiality thresholds
results/findings.jsonl       every finding from both systems, for inspection
```

`results/headline.md` is pasted directly into `EVALUATION.md`. Nothing in that table is hand typed.

## 7. Everything at once

```bash
make all
```

Runs verify, corpus, baseline, solution, eval in sequence. About 20 minutes.

---

## 8. What you should see

- Both systems report findings on all 12 workbooks.
- The baseline reports substantially more findings than Materia, most of them on `C09` and `C10`, the clean controls.
- Materia's report on `C11` shows the mutation in the **suppressed** count, not the findings list. That is correct behaviour, not a miss.
- Both systems miss the `M6` mutation in `C12`. Expected and documented.

## 9. Determinism

The corpus is fully deterministic from seed `20260828`.

The agent runs are not. Model sampling means finding counts vary between runs. To characterise this rather than ignore it:

```bash
make eval-repeat N=3
```

Runs both systems three times over the corpus and reports mean and range per metric. Published numbers are from a single seeded run, with the variance from this command reported alongside in `results/variance.md`.

## 10. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `PreflightRejected` on your own workbook | Contains VBA, external links, array formulas, or an unsupported function | Expected. The reason is named in the error. See README section 6. |
| `make corpus-check` mismatch | Different `openpyxl` version writing different XML | Pin with `uv pip install -e ".[dev]" --exact`. Content is still equivalent. |
| Baseline run exceeds budget | Agent looping on a large workbook | Per workbook cap is in `config.yaml` as `baseline.max_turns`. Caps are identical for both systems. |
| Rate limit errors | Concurrency | `make baseline CONCURRENCY=1` |

## 11. Running on your own workbook

```bash
python -m materia audit path/to/your_model.xlsx \
  --outputs "P&L!H48,Valuation!C12" \
  --materiality 0.01
```

`--outputs` declares the cells that matter. `--materiality` is the fraction of an output's value a correction must move for a finding to be shown. Default 0.01, meaning 1%.

Materia never writes to the input file. `--repair` writes a corrected copy to a new path and requires an interactive confirmation per finding.

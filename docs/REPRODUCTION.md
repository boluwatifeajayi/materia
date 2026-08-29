# Reproduction guide

Written for someone starting from a clean machine with nothing installed. Follow top to bottom and you will reproduce the headline table in `EVALUATION.md`.

Every figure below was measured from a fresh clone into an empty directory, not estimated. Where a step is not built yet, this guide says so rather than describing it as though it were.

> **`make all` runs end to end but does not produce the full results yet.** `make baseline` and `make solution` each audit a single workbook, not the corpus. The twelve workbook sweeps are T19 and T20. Everything else works as described: `verify`, `corpus`, `corpus-check`, and `eval`.

---

## 1. Requirements

| Component | Version | Notes |
| --- | --- | --- |
| Python | 3.11 or 3.12 | 3.13 is refused by `pyproject.toml`, not merely untested |
| `uv` | optional | The `venv` and `pip` path below is what was tested. Nothing needs `uv`. |
| OS | macOS, Linux, or WSL2 | No Excel or Windows required at any point |
| API key | Groq, or OpenAI | See section 2. Which one you need depends on `MATERIA_PROVIDER`, and the default is Groq. |
| Disk | 115 MB | 8 MB of repo and corpus, 107 MB of virtualenv |

No Microsoft Excel, no LibreOffice, no headless office suite. The recompute engine is our own and runs in pure Python. This is deliberate: it removes the single most common reproduction failure for spreadsheet tooling.

## 2. Setup

```bash
git clone https://github.com/boluwatifeajayi/materia.git
cd materia

python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

`python3` has to be 3.11 or 3.12. If your `python3` is something else, name the one you want: `python3.12 -m venv .venv`.

Takes about 30 seconds and pulls roughly 107 MB of dependencies.

**Set the key for the provider you are using.** `MATERIA_PROVIDER` selects between them and **defaults to `groq`**, so setting only an OpenAI key leaves `make solution` failing with `GROQ_API_KEY is not set`.

```bash
# the default: the dev loop provider
export GROQ_API_KEY="gsk_..."

# or the scored provider
export MATERIA_PROVIDER=openai
export OPENAI_API_KEY="sk-..."
```

Keys are read from the environment only and never written to disk. `python -m materia llm check` makes one trivial call and confirms the configured model is real before anything depends on it.

Verify the install. This runs the unit tests, needs no API key, and takes about 60 seconds measured on a fresh clone. Most of that is building corpus workbooks, which the tests do from scratch rather than reading the committed ones.

```bash
make verify
```

Expected output ends with:

```
792 passed, 1 skipped
```

The skip is a live API check that only runs with `MATERIA_LIVE_TESTS=1` set.

Every module is covered. If a test fails here, stop: nothing downstream is trustworthy, because the recompute engine is where every impact figure in the results comes from.

## 3. Build the corpus

The workbooks are generated from a fixed seed rather than committed as binaries, so you can inspect exactly how each one was made.

```bash
make corpus
```

Runtime: 6 seconds, measured. Cost: none, no API calls.

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

**One workbook, not twelve.** The harness is built and audits `corpus/C03.xlsx`. The scored sweep over the corpus is T19, and until it runs there is no baseline column in the headline table: `docs/EVALUATION.md` section 5 shows it as `[TBD]` rather than as a number.

The baseline is a general purpose coding agent given the workbook as `model.xlsx` in a sandboxed directory, a shell, and `openpyxl` already installed. It is free to write whatever analysis code it wants. Its instructions are in `docs/AGENT_INSTRUCTIONS.md` section 3, and they name the error families, declare the output cells, and say precision counts. It is not a strawman.

**Its toolset is fixed rather than inherited from your machine**, which is what makes the comparison reproduce. `PATH` is built from an allowlist: Python with `openpyxl`, and the standard text utilities. No spreadsheet application, no format converter, no network, no package installation, and the excluded programs are refused by name as well as being off `PATH`. The reasoning is in `docs/EVALUATION.md` section 4, and the short version is that a baseline which quietly uses whatever the host has installed produces a headline number that partly measures the host.

This is why section 1 lists no office suite as a requirement and means it. An earlier run of this harness inherited the host `PATH`, found the headless LibreOffice on the development machine and recalculated with it. On a machine without one, the same code is a different and weaker baseline.

Its caps come from `config.yaml` and are derived rather than chosen:

| | |
| --- | --- |
| Model calls | 67 per workbook |
| Tokens | 211,000 per workbook |

Those are the solution's own measured spend: 3.0 model calls and 9,463 tokens per candidate on `gpt-5.6-terra`, times the 22.2 candidates the corpus averages. Both systems get the same room, which is what `docs/EVALUATION.md` section 4 requires for the comparison to mean anything.

Output: `results/baseline/C03.json` and a full trajectory in `trajectories/baseline/`.

Measured on `gpt-5.6-terra`, the full twelve workbook run:

| | |
| --- | --- |
| Runtime | about 20 minutes |
| Tokens | 2,207,561 |
| Cost | $4.95, at $2.00 per million input and $12.00 per million output |
| Per workbook | $0.41 |
| Findings | 12 |
| Runs that used the whole token budget | 5 of 12 |

Output: `results/baseline/C01.json` through `C12.json` with `provider.json` beside them recording the model, and one trajectory per workbook in `trajectories/baseline/`.

`make eval` scores it. The baseline column of the headline table in `docs/EVALUATION.md` section 5 is filled from these files, and so is the Baseline row of the changelog in `README.md`. Nothing in either is typed by hand.

**What it found.** 83% material finding precision against the detectors' 5%, and one false positive across the two clean workbooks against the detectors' 46. It is a strong baseline, which is what `docs/AGENT_INSTRUCTIONS.md` section 3 set out to build.

Every impact figure it reported, 21 of them, is exactly right when checked against the recompute engine. It got there by reading the values cached in each workbook and computing the change on the affected chain, rather than reimplementing the model. The discussion in `docs/EVALUATION.md` section 5 covers what that means for the project's framing, including where it cuts against it.

The one false positive is worth opening: `Costs!I12` in `C10`, a deliberate override carrying a comment that says not to restore the formula, reported at high confidence with a proposal to restore the formula. Its impact figure is correct. It is featured trajectory 4 in `trajectories/index.md`.

## 5. Run the solution

```bash
make solution
```

**One workbook, not twelve.** `make solution` audits `corpus/C03.xlsx`. The full corpus sweep arrives with T20.

Measured on the free Groq tier, for the 22 candidates in C03:

| | |
| --- | --- |
| Model calls | about 69, at 3.1 per candidate |
| Tokens | about 142,000, at 6,400 per candidate |
| Runtime | about 2 hours on the free tier, most of it waiting |
| Cost | none on the free tier, but see the limits below |

**Almost all of that runtime is waiting, not working.** Groq's free tier caps this account at 8,000 tokens a minute, which a single adjudication exceeds on its own. Each request that would break the cap is held back for up to a minute, so the wall clock is governed by the number of model calls rather than by the token total: roughly 69 calls, most of them waiting out a window, is about an hour and a half of pausing plus the model's own latency.

You will see `groq: N tokens used in the last minute, waiting 60s` on stderr throughout. That is the rate limiter holding a request back so it is not refused, not a hang. On a paid tier or a provider without this cap the same run takes minutes.

The daily cap is 200,000 tokens, so **one full C03 audit uses about seventy percent of a day's free quota** and two do not fit. `--max-candidates N` bounds a run, and a bounded run says so in its own funnel rather than reading as a clean bill of health.

Output: `results/solution/C03.json` with `provider.json` beside it recording which model produced it, and trajectories in `trajectories/solution/`.

To watch one workbook with the full report rendered, which is the command used in the demo video:

```bash
python -m materia audit corpus/C03.xlsx --explain
```

To re-render a report from trajectories already on disk, with no model calls at all:

```bash
python -m materia report corpus/C03.xlsx --traces trajectories/solution
```

## 6. Score both

```bash
make eval
```

Runtime: 1 second, measured. Cost: none.

Scores the detector only run against `corpus/manifest.json` and writes:

```
results/headline.md          the table in docs/EVALUATION.md section 5
results/per_workbook.md      per case breakdown
results/scores.json          the same numbers as data, so a doc can be filled in without retyping
```

`results/sensitivity.md` arrives with T26 and is not produced yet.

`make eval` also fills the Iteration 1 row of the changelog in `README.md` in place. Nothing in that row is hand typed.

Needs no API key. The whole thing is deterministic, and on a fresh clone it reproduces these exactly:

```
| Material finding precision | 5% |
| Material recall | 93% |
| Raw anomaly recall | 93% |
| False positives per clean workbook | 23.00 |
| Localisation accuracy | 100% |
| Repair accuracy | n/a |
| Findings reported | 267 |
```

## 7. Everything at once

```bash
make all
```

Runs verify, corpus, baseline, solution, eval in sequence. Both agent steps need a key, and on the free Groq tier both are slow for the reason in section 5.

For the parts that need no key and no model at all:

```bash
make verify && make corpus && make corpus-check && make eval
```

That is about 70 seconds end to end and reproduces the Iteration 1 numbers above.

---

## 8. What you should see

What holds today:

- `make corpus-check` reports 12 workbooks matching the committed checksums. That is the reproducibility claim: byte identical inputs to ours, from a seed, on your machine.
- `make eval` reproduces the Iteration 1 table above exactly. It is deterministic and involves no model.
- The detectors alone report 21 findings on `C09` and 25 on `C10`, both of which contain nothing wrong at all. That is the precision problem the project is about, in one number.
- `make solution` over the corpus reports 13 findings from 267 candidates, declines 253 as deliberate, and suppresses one as immaterial. It reports nothing on either clean control, where the detectors that feed it report 46 findings between them.
- `Costs!Z12` in `C11` is a real error worth 3.0 basis points. It appears in the suppressed count with its measured impact, not in the findings and not missing.

Known limits, reported rather than worked around:

- Both systems miss the `M6` mutation in `C12`. Nothing structural can see a number that is semantically wrong and structurally perfect.
- Agent runs are not deterministic. See section 9.

## 9. Determinism

The corpus is fully deterministic from seed `20260828`.

The agent runs are not. Model sampling means finding counts vary between runs. To characterise this rather than ignore it:

```bash
make eval-repeat N=3
```

Runs both systems three times over the corpus and reports mean and range per metric. Published numbers are from a single seeded run, with the variance from this command reported alongside in `results/variance.md`.

## 10. Troubleshooting

Everything here was hit during an actual clean clone run, except the last two rows.

| Symptom | Cause | Fix |
| --- | --- | --- |
| `command not found: python3.11` | An earlier version of this guide hardcoded 3.11 | Use whichever of 3.11 or 3.12 you have: `python3.12 -m venv .venv` |
| `GROQ_API_KEY is not set` after exporting an OpenAI key | `MATERIA_PROVIDER` defaults to `groq` | Either set `GROQ_API_KEY`, or set `MATERIA_PROVIDER=openai` as well as the OpenAI key |
| `baseline: stopped early: turn cap of 67 reached` | The agent used its whole budget without writing `findings.json` | Expected and left as is. The cap is the solution's own measured average and is what makes the comparison fair. A baseline that ran out of room reported nothing, and that is a result about the baseline. |
| `groq: N tokens used in the last minute, waiting 60s` | The free tier caps this account at 8,000 tokens a minute | Nothing. That is the rate limiter holding a request back so it does not get refused. A full C03 audit takes about 25 minutes, most of it waiting. |
| `Groq rate limit reached ... tokens per day (TPD)` | The free tier caps 200,000 tokens a day and one C03 audit uses about 142,000 | Wait for the reset, or bound the run with `--max-candidates N`. A run cut short keeps the verdicts it earned and says in its funnel how many were not examined. |
| `Groq request failed ... tool_use_failed` | The dev loop model sometimes emits a malformed tool call | Nothing. That candidate is recorded as `INCONCLUSIVE` with the reason and the run continues. See `docs/ARCHITECTURE.md` section 9. |
| `PreflightRejected` on your own workbook | Contains VBA, external links, array formulas, a defined name, or an unsupported function | Expected. The reason is named in the error. See README section 6. |
| `make corpus-check` mismatch | Different `openpyxl` version writing different XML | `openpyxl` is pinned exactly in `pyproject.toml` for this reason, so first check your install honoured the pin: `python -c "import openpyxl; print(openpyxl.__version__)"` must print `3.1.5`. |
| Baseline run exceeds budget | It cannot. The run stops at the cap and records why | Per workbook caps are in `config.yaml` under `baseline`. They are derived from the solution's measured spend, so raising them for one side breaks the comparison. |

## 11. Running on your own workbook

```bash
python -m materia audit path/to/your_model.xlsx \
  --outputs "P&L!H48,Valuation!C12"
```

`--outputs` declares the cells that matter, and is required for any workbook outside the corpus. There is no default: guessing which cells a decision rests on is the one judgement this tool must not make on its own.

`--materiality` sets the fraction of a declared output a correction must move to be shown, and defaults to the threshold in `config.yaml`, currently 1%. Findings below it are counted and named in the report as suppressed rather than dropped.

Materia never writes to the input file. `--repair` asks about each finding, defaults to no, and writes approved changes to a copy at a new path:

```bash
python -m materia audit path/to/your_model.xlsx \
  --outputs "P&L!H48,Valuation!C12" --repair
```

Declining everything writes no file at all. Every answer, declines included, is recorded as a `human_checkpoint` in the trajectory.

# Evaluation

The whole point of this project is a precision claim. So the evaluation is built to be able to **falsify** that claim, not to flatter it.

Three design choices make it honest, and each one is there because a reasonable judge would otherwise object:

1. **Clean control workbooks with zero injected bugs.** Without these, false positive rate is unmeasurable and any precision number is meaningless.
2. **Out of taxonomy mutations that no detector is designed to catch.** Without these, we would be measuring whether we built a mutation recogniser rather than an auditor. Recall is expected to drop here and we report it.
3. **A strong baseline, not a strawman.** See below.

---

## 1. The primary metric

**Material finding precision:** of the findings the tool reports to the user, what fraction correspond to a genuine material error?

```
precision = correct material findings / all reported findings
```

This is the primary metric because it is the one the user's behaviour actually depends on. A tool with perfect recall and 10% precision gets uninstalled after one session. It is also the metric on which existing deterministic tools are weakest, so it is the honest place to compete.

### What good looks like, defined before running

Stated in advance so the result cannot be graded to fit:

| Metric | Target | Rationale |
| --- | --- | --- |
| Material finding precision | >= 0.80 | Four out of five findings worth opening. Below this, trust breaks. |
| Material recall | >= 0.70 | Missing errors is bad, but a missed error leaves the user no worse than today. A false one costs them time and credibility. |
| False positives per clean workbook | <= 1.0 | A clean model should come back essentially clean. |
| Localisation accuracy | >= 0.90 | Finding the wrong cell but the right sheet is not useful. |

### Secondary metrics

| Metric | Definition |
| --- | --- |
| Material recall | seeded material mutations found / seeded material mutations |
| Raw anomaly recall | all seeded mutations found before materiality filtering |
| FP per clean workbook | findings reported on the two control workbooks |
| Localisation accuracy | exact cell reference correct |
| Repair accuracy | proposed formula matches the pre-mutation formula |
| Human time per workbook | measured, see protocol below |
| Cost per workbook | total token spend at published rates |

### Human time protocol

Measured, not estimated. For each of three workbooks, a reviewer starts a timer, works from the tool's output, and stops when they can state which cells are wrong and what the impact is. Same task for the baseline output. Three workbooks, both conditions, reported with the raw numbers rather than an average alone. Small sample, stated as such.

---

## 2. The corpus

**12 workbooks.** Ten or more is the brief's target, and twelve gives room for the two controls.

| ID | Type | Contents |
| --- | --- | --- |
| `C01` to `C08` | Seeded | 1 to 3 mutations each, drawn from the taxonomy below |
| `C09` | Clean control | No mutations |
| `C10` | Clean control | No mutations, but contains three *legitimate* pattern breaks (hardcoded actuals row, first period column, deliberate manual override with a comment) |
| `C11` | Hard case | Contains a real mutation that is genuinely immaterial. Generator target: moves a declared output by less than 0.1%. Actual delta `[TBD]`, measured by the recompute engine at injection time and recorded in the manifest. Correct behaviour is to detect and suppress it. |
| `C12` | Hard case | Contains one in taxonomy mutation and one out of taxonomy mutation |

`C10` is the workbook that breaks naive tools, and `C11` is the one that tests the actual thesis. Both are discussed explicitly in the write up.

### Workbook construction

Generated programmatically by `src/materia/corpus/generate.py` so the corpus is reproducible from a seed rather than shipped as opaque binaries. Each is a small but realistic three statement forecast:

- Assumptions sheet (growth rates, margins, headcount)
- Revenue build, 24 monthly columns
- Cost build with headcount driven staff costs
- P&L rolling up to EBITDA
- Simple valuation tab applying a multiple to EBITDA

Between 400 and 1,500 formulas each. Small enough to run fast, structured enough that peer group reasoning is meaningful.

Each workbook is built twice. `_write_formulas` writes the Excel formulas; `compute_values` computes the same model again as a plain Python month loop, touching neither the parser nor the evaluator, and that second result is written into the file as each formula's cached value. This is what makes the engine testable against the corpus: a generated workbook contains no Excel written values, so checking the engine against a file it produced itself would prove nothing. Two independent implementations of the same model agreeing is a real signal, and a formula cell left without a computed value raises rather than being skipped, because a skipped cell would make the cross check pass by comparing nothing.

**Declared output cells** (what materiality is measured against) are recorded per workbook in `corpus/manifest.json`: EBITDA total (`P&L!AA15`, the total column of the EBITDA row) and enterprise value (`Valuation!B7`). Two outputs, both produced by the sheets the generator builds. In production this would be a user input. For evaluation it is fixed and published.

---

## 3. Mutation taxonomy

Grounded in the published spreadsheet error classifications (Panko's taxonomy, EuSpRIG error categories) rather than invented, so the corpus reflects errors that occur in the wild.

Mutations are injected programmatically by `src/materia/corpus/mutate.py`. The agent never sees the manifest. Each mutation records: cell, original formula, mutated formula, family, and the true delta on each declared output cell (computed by the recompute engine, so materiality is known ground truth).

Deltas are measured one mutation at a time against the clean workbook, even where a workbook carries three. What each error costs on its own is the question the adjudicator is asked about each cell, so that is what the manifest records. `material` is derived from the measurement rather than asserted alongside it, and the manifest validator rejects a mutation whose flag disagrees with its own numbers.

Every mutation is aimed at a cell that reaches a declared output. One that nothing depends on would be correctly ignored by every system, and would measure nothing.

### In taxonomy: the detectors target these

| ID | Family | Example | Real world origin |
| --- | --- | --- | --- |
| `M1` | Formula replaced by constant | `=SUM(G30:G41)` becomes `1284000` | Someone pasted a value to "check something" and never restored it |
| `M2` | Inconsistent copied formula | `H17 = F17*(1+G5)` where peers are `G17*(1+G5)` | Fill handle dragged from the wrong origin |
| `M3` | Truncated or overrun aggregation range | `=SUM(H10:H19)` where the block is `H10:H20` | Row inserted at the boundary of a range |
| `M4` | Off by one period reference | Column references prior period throughout one cell | Copy paste across a period boundary |
| `M5` | Sign or operator flip | `-` where peers use `+` | Typo, or a cost entered as a negative in a sheet that already negates |

### Out of taxonomy: deliberately not targeted

Included to measure honest recall and to prevent the corpus from being a mirror of the detectors.

| ID | Family | Why it is hard |
| --- | --- | --- |
| `M6` | Wrong assumption value | `12%` should be `1.2%`. Structurally perfect. No peer group signal exists. |
| `M7` | Correct formula, wrong sheet reference | `Assumptions!B5` instead of `Assumptions!B6`, both plausible |

**We expect recall on `M6` to be near zero and we say so.** A structural detector cannot see a semantically wrong but structurally valid number. This is a limitation of the approach, reported rather than hidden, and it feeds the hot take.

---

## 4. The baseline

Rejected baseline: "flatten the workbook to CSV and ask an LLM to find errors". This is a strawman. Flattening destroys formulas, so it guarantees a large win that proves nothing.

**Chosen baseline:** a general purpose coding agent, explicitly listed as a valid baseline in the micro1 brief ("one general purpose agent with basic tools").

- Same underlying model as the solution
- Given the `.xlsx` file and a shell with `openpyxl`, free to write its own analysis code
- Given the same declared output cells and the same output schema
- Given the same task description and the same run budget
- Same 12 workbooks

Differences in available resources, stated for fairness: the baseline does not receive our dependency graph, our detectors, or our recompute engine. It is free to build equivalents. It has the same wall clock and token budget. That is the comparison we want: does purpose built agent tooling beat a capable general agent given the same file and the same freedom?

Baseline instructions are in `docs/AGENT_INSTRUCTIONS.md` alongside the solution's, so the fairness of the comparison is auditable.

### The toolset is fixed, not inherited

"Stated for fairness" has to mean a list. The first completed baseline run made that concrete: given a shell and left to pick its own method, the agent ran `which soffice`, found the headless LibreOffice installed on that machine, wrote patched copies of the workbook, had LibreOffice recalculate them and read the values back. Its impact figures were measured rather than estimated, and they were right.

That is good work by the agent and it cannot be allowed to stand as part of the comparison, for two reasons.

The headline number would partly measure the host. A reproducer with an office suite installed gets a baseline that can recalculate; one without gets a baseline that has to reason its way to an impact figure. Those are different systems, they will score differently, and neither the number nor the gap between the columns would mean what the table says it means.

It also contradicts what this project claims about itself. `docs/REPRODUCTION.md` section 1 says no Excel, no LibreOffice, no headless office suite is needed anywhere. That was written about our pipeline and it is still true of our pipeline, but a baseline that silently depends on one makes the claim false of the submission.

So the baseline's shell has a fixed toolset: Python with `openpyxl`, and the standard text utilities. No spreadsheet application, no format converter, no network, no package installation. `PATH` is built from an allowlist rather than inherited, and because a `PATH` restriction does not stop an absolute path, the excluded programs are named and refused wherever they are invoked from. The list is `ALLOWED_BINARIES` and `DENIED_BINARIES` in `src/materia/baseline.py`, and the prompt in `docs/AGENT_INSTRUCTIONS.md` section 3 tells the agent exactly what it has, so it does not waste turns looking for tools that are not there.

This is a restriction on capability, so it is worth being precise about what it removes. It does not remove the ability to determine impact: the agent has a full programming language and the library that reads the file format, which is what our own recompute engine is built from. It removes the ability to have a different program do it, where "a different program" varies by machine.

**What it does not close:** code running inside `python` could invoke an absolute path we have not named. This is an evaluation harness pointed at a corpus we generated, not a security boundary, and it is not treated as one.

---

## 5. Results

> Populated by `make eval`. Every number here is regenerated from `results/` and nothing is typed by hand.

### Headline

> Paste from `results/headline.md`, which `make eval` regenerates. The baseline and Materia columns arrive with T19 and T21. What is below is the detector only run, which is the starting point rather than a result.

| Metric | Detectors only | Baseline | Materia |
| --- | --- | --- | --- |
| Material finding precision | 5% | 100% | 100% |
| Material recall | 93% | 7% | 7% |
| Raw anomaly recall | 93% | 7% | 7% |
| False positives per clean workbook | 23.00 | 0.00 | 0.00 |
| Localisation accuracy | 100% | 100% | 100% |
| Repair accuracy | n/a | n/a | 100% |
| Human time per workbook | not measured | not measured | not measured |
| Cost per workbook | none, no model involved | not measured | not measured |

Repair accuracy is not applicable for the detector only run rather than zero: the detectors propose nothing, so there is nothing for them to be right or wrong about. Reporting zero would imply they tried.

### What the baseline run showed, including the part that cuts against us

The baseline column above is a strong result and it is worth saying plainly rather than burying: 83% material precision against the detectors' 5%, one false positive across two clean workbooks against the detectors' 46, and every proposed repair but one correct.

**It did not invent a single impact figure.** All 21 figures it reported across the twelve workbooks were checked against the recompute engine and all 21 are exact. The project's framing is that a general agent asked for an impact will assert one it did not measure. On this corpus, with this budget, that is not what happened. It anchored on the values cached in each workbook, read with `data_only=True`, and computed the change on the affected chain rather than reimplementing the model. That is a sound method and it worked.

The failure mode is real and we have it on record twice: once in our own adjudicator, which called `recompute_with_patch`, received 8,704,573 and reported -6,102,169, and once in an earlier standalone baseline run on `C03` that reached for the cached values with a positional argument, got formula strings back, reimplemented the model instead and published a figure 139% wrong. Both trajectories are committed. But it is not what separates the two columns on this corpus, and the submission should not claim it is.

What the run does show, from `C10`, is a different and harder problem: the baseline's numbers were right and its judgement was wrong, because whether a cell is an error is a question about intent rather than arithmetic. That is the claim the materiality gate and the `INTENTIONAL` verdict are built to answer, and it is now a measured observation rather than a prediction.

**Where its recall goes.** Four seeded mutations were not reported. Three of them are on workbooks where the run used its entire token budget and stopped: `C01` and `C03`, which reported nothing at all. The fourth is `M6` in `C12`, which nothing structural can see. So on the workbooks where it had room, the baseline missed only the mutation everyone expects to be missed. Five of the twelve runs used the whole budget. That budget is the solution's own measured average, which is the point of setting it that way, but it means the recall gap between the columns will partly be a statement about how the two systems spend a fixed allowance rather than about how well they judge.

### Per workbook

Paste from `results/per_workbook.md`, regenerated by `make eval`.

### The hard cases, discussed

**`C10` (legitimate pattern breaks):** The detectors report 25 findings on a workbook containing no errors. The baseline reports one: `Costs!I12`, the manual override, at high confidence, proposing to restore the inflation formula. The cell carries a comment saying the figure is held on purpose and the formula should not be restored, and the agent never reads a comment at any point in the run. Its impact figure for the change is correct. Materia's column is pending T20. The trajectory is featured as number 4.

**`C11` (real but immaterial mutation):** The baseline reports it, as an error, with a correct impact of -4,165 on total EBITDA against a base of about 14 million. That is the right detection and the wrong conclusion, because a third of a basis point is not something to put in front of a user. The baseline has no notion of materiality to apply, which is the gap the gate in T21 exists to fill. Whether Materia detects and suppresses it, rather than missing it, is the distinction that matters and it is pending T20 and T21.

**`C12` (out of taxonomy):** The baseline reports the in taxonomy mutation and misses `M6`, the assumption that should be 1.2% and says 12%. As expected: nothing structural can see it, and this run is the first evidence for that rather than an assertion of it. Materia's half is pending T20.

---

## 6. Threats to validity

Stated rather than left for a judge to find.

- **Synthetic corpus.** The workbooks are generated, not harvested from real companies. Structure is realistic and mutations come from a published taxonomy, but real models are messier. Mitigation: the supported grammar is documented, and one public template workbook is included if licensing allows.
- **Small n.** Twelve workbooks, three timed human trials. Enough to show a direction, not enough for a confidence interval. We report raw numbers rather than significance claims.
- **We chose the materiality threshold.** A different threshold changes precision and recall. The threshold is a published config value and `results/sensitivity.md` reports the metrics at three thresholds so the result is not a single tuned point.
- **We wrote both the detectors and the mutations.** Partially mitigated by out of taxonomy mutations and clean controls, but not eliminated. This is the honest limit of a synthetic benchmark and it is why the out of taxonomy families are in there.
- **Same model on both sides.** Deliberate. It isolates the contribution of the workflow rather than the model.
- **The recall comparison is not a clean measurement of judgement.** Five of the twelve baseline runs used their entire token budget and stopped. Two of those, `C01` and `C03`, reported nothing at all, so three of the four seeded mutations the baseline missed were missed because it ran out of allowance rather than because it judged them wrong. The budget is identical for both systems by design, and spending it well is a real property of a system rather than an accident, but the consequence is that the recall column measures judgement and budget efficiency together and cannot separate them. Any recall gap between the columns should be read that way. Precision, false positives per clean workbook, and the accuracy of the reported impact figures are not affected, because those are computed over what each system did report.

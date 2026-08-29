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
- Given the `.xlsx` file and a shell
- May install and use `openpyxl` or anything else, and may write its own analysis code
- Given the same declared output cells and the same output schema
- Given the same task description and the same run budget
- Same 12 workbooks

Differences in available resources, stated for fairness: the baseline does not receive our dependency graph, our detectors, or our recompute engine. It is free to build equivalents. It has the same wall clock and token budget. That is the comparison we want: does purpose built agent tooling beat a capable general agent given the same file and the same freedom?

Baseline instructions are in `docs/AGENT_INSTRUCTIONS.md` alongside the solution's, so the fairness of the comparison is auditable.

---

## 5. Results

> Populated by `make eval`. Every number here is regenerated from `results/` and nothing is typed by hand.

### Headline

| Metric | Baseline | Materia | Change |
| --- | --- | --- | --- |
| Material finding precision | `[TBD]` | `[TBD]` | `[TBD]` |
| Material recall | `[TBD]` | `[TBD]` | `[TBD]` |
| False positives per clean workbook | `[TBD]` | `[TBD]` | `[TBD]` |
| Localisation accuracy | `[TBD]` | `[TBD]` | `[TBD]` |
| Repair accuracy | `[TBD]` | `[TBD]` | `[TBD]` |
| Human time per workbook | `[TBD]` | `[TBD]` | `[TBD]` |
| Cost per workbook | `[TBD]` | `[TBD]` | `[TBD]` |

### Per workbook

| ID | Seeded | Baseline found | Baseline FP | Materia found | Materia FP | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| `C01` .. `C12` | `[TBD]` | | | | | |

### The hard cases, discussed

**`C10` (legitimate pattern breaks):** `[TBD]` What did each system report? This is where a naive tool produces three confident false findings.

**`C11` (real but immaterial mutation):** `[TBD]` Did Materia detect it and suppress it, or miss it entirely? These are very different outcomes and the distinction is the product. The report must show it in the suppressed count.

**`C12` (out of taxonomy):** `[TBD]` Expected: both systems miss `M6`. Reported as a limitation.

---

## 6. Threats to validity

Stated rather than left for a judge to find.

- **Synthetic corpus.** The workbooks are generated, not harvested from real companies. Structure is realistic and mutations come from a published taxonomy, but real models are messier. Mitigation: the supported grammar is documented, and one public template workbook is included if licensing allows.
- **Small n.** Twelve workbooks, three timed human trials. Enough to show a direction, not enough for a confidence interval. We report raw numbers rather than significance claims.
- **We chose the materiality threshold.** A different threshold changes precision and recall. The threshold is a published config value and `results/sensitivity.md` reports the metrics at three thresholds so the result is not a single tuned point.
- **We wrote both the detectors and the mutations.** Partially mitigated by out of taxonomy mutations and clean controls, but not eliminated. This is the honest limit of a synthetic benchmark and it is why the out of taxonomy families are in there.
- **Same model on both sides.** Deliberate. It isolates the contribution of the workflow rather than the model.

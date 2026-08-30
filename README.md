# Materia

**An agent that finds silent errors in financial models, proves what they cost, and throws away the ones that do not matter.**


> Name is swappable. It is used consistently across code and docs, so a find and replace on `materia` / `Materia` renames the whole project.

---

## 1. Who has this problem

The person who inherits a spreadsheet they did not build and has to make a decision with it.

Concretely, three users with the same bottleneck:

- **A finance analyst** handed the previous analyst's three statement model two days before a board meeting.
- **A founder** sending an investor a forecast built up over eleven months by four different people.
- **A grant or budget holder** signing off a spend plan assembled from department submissions.

They all share one property: **they did not write the formulas, and they are accountable for the output.**

## 2. The bottleneck

A spreadsheet does not fail loudly. It fails silently and confidently.

If one cell in a forecast row references the prior period instead of the current one, Excel shows no error, every sheet still reconciles, and the model still produces a clean number. The only signal is that the number is wrong, and there is nothing on screen to indicate it.

Checking manually means opening cells one at a time and reading formulas across thousands of them. Nobody does this. What people actually do is trust the model, or spot check five cells and hope.

The tooling that exists does not solve it either. Deterministic spreadsheet linters (formula inconsistency checkers, hardcode detectors, precedent tracers) have existed for two decades. They are not bad at finding anomalies. They are bad at **ranking** them. A real model returns hundreds of flagged cells, most of which are deliberate: an actuals row that is legitimately hardcoded, a first column that legitimately breaks the pattern because there is no prior period, a manual override the CFO put in on purpose. The output is a wall of noise, the user checks the first fifteen, finds them all benign, and never opens the tool again.

**So the bottleneck is not detection. It is precision.** And precision here is not a structural question, it is a question about consequence: does this anomaly change a number a decision rests on?

## 3. Why solving it is valuable

Because the cost of the miss is asymmetric and large. Spreadsheet error research (Panko, EuSpRIG) has documented significant error rates in operational workbooks for over twenty years, and the published horror stories are financial rather than cosmetic. A single reference error in a forecast row propagates through gross profit, EBITDA and any multiple applied to it.

The value of a tool here is not "finds errors". It is **finds the few that matter and proves it, so that checking the model takes minutes instead of hours.** That is a claim to be measured rather than asserted. The human time protocol is in `docs/EVALUATION.md` section 1 and the trials were not run, so it is reported as not measured rather than estimated. What is measured is the proxy: 267 structural anomalies across the corpus reduced to 13 findings, with the other 254 accounted for rather than dropped.

## 4. What we are building

Materia takes an `.xlsx` model and returns a small number of evidence backed findings. For each one:

- the anomalous cell and the formula it contains
- the formula its peer group implies it should contain
- the dependency path from that cell to a decision relevant output
- **the quantified change in that output if the cell is corrected**
- a confidence level and the evidence behind it
- a proposed repair, applied only to a copy, only after human approval

And critically, the count of anomalies it **suppressed** and why.

Written by `make eval` from `results/solution/`, not typed. This is `C11`, the workbook carrying a real error too small to matter:

<!-- funnel -->
```
MODEL HEALTH                                                      C11.xlsx
==========================================================================

      738  formulas parsed
       22  structural anomalies detected
        1  survived hypothesis testing
        0  material findings   <-- what you read
        1  suppressed as immaterial
```
<!-- funnel -->

That funnel is the product. Everything above the last two lines is what existing tools already give you. `material findings` is what makes anyone use it twice, and `suppressed as immaterial` is what makes them trust it: the error in `C11` is real, it is 3.0 basis points, and the tool says so rather than either hiding it or putting it in front of you.

### The core design decision

The LLM is not used to find bugs. Code finds bugs. The LLM is used to **judge which bugs matter**, and its judgement is then verified by code.

```
                        .xlsx
                          |
                   Preflight validator          reject unsupported workbooks, loudly
                          |
                    Parser + R1C1 normaliser
                          |
                  Cell dependency graph
                          |
              Deterministic detectors           high recall, deliberately low precision
                          |                     emits CANDIDATES, not findings
                          v
           +-----------------------------+
           |        AGENT LOOP           |     for each candidate:
           |                             |
           |   1. read peer evidence     |
           |   2. hypothesise intent     |
           |   3. call recompute tool    |     <-- deterministic, not the model
           |   4. read the delta         |
           |   5. keep / discard         |
           +-----------------------------+
                          |
                  Materiality gate              threshold on declared output cells
                          |
                   Evidence report
                          |
              Human approval  ->  repaired COPY
```

Step 3 is the part that makes this an agent rather than a prompt. The model states a hypothesis, a deterministic engine tests it, and the result of that test decides whether the finding survives. The model does not get to assert an impact. It has to earn one.

## 4b. Prior art

Spreadsheet auditing is a mature product category and this project does not claim otherwise. Claiming novelty here would be false and a judge would catch it in one search.

| Tool | What it does |
| --- | --- |
| Operis Analysis Kit | Financial model auditing: formula reconstruction, precedent and dependent tracing, inconsistency detection |
| Macabacus | Formula auditing, dependency tracing, hardcode and broken link detection, model checks |
| ExceLint | Academic Excel plugin that locates suspected formula errors and highlights related cells |
| Spreadsheet Detective, Excel Formula Auditor, various free web auditors | Structural checks, error detection, data flow analysis |
| `audit-xls` (Anthropic financial services skill) | Prompt level formula and data audit, scoped from a selection up to a full model integrity audit |

Every one of these is primarily a **detector**. They find anomalies well, and their existence is the best available evidence that the detection half of this problem is solved.

This project's focus is narrower and sits downstream of that: **impact verified auditing.** Every material finding is backed by a recomputation of the model showing how the suspected error changes a declared output, and every candidate that fails that test is suppressed and counted rather than reported.

We make no claim about what any commercial tool does or does not do internally, and none is needed. The comparison that matters is the one we can actually run, and it is in the changelog: structural detection alone versus structural detection plus verified impact reasoning, on the same corpus with the same metric.

The `audit-xls` skill is worth singling out because it is the closest agentic comparison in public. It reasons about a workbook at the prompt level rather than through a recompute loop, which is the specific distinction this project is built around.

## 5. What existed before this hackathon

| Component | Status |
| --- | --- |
| `openpyxl`, `networkx`, `openai` SDK, `pytest` | Third party, pre-existing |
| Spreadsheet error taxonomy (Panko / EuSpRIG literature) | Pre-existing research, cited not authored |
| Everything in `src/` | Built during the hackathon |
| Corpus generator, mutation harness, evaluator | Built during the hackathon |
| Recompute engine | Built during the hackathon |
| Agent instructions in `docs/AGENT_INSTRUCTIONS.md` | Built during the hackathon |

## 6. Scope, stated up front

Materia refuses workbooks it cannot reason about faithfully, rather than guessing. The preflight validator rejects, with a named reason:

- VBA macros
- external workbook links
- dynamic array formulas and legacy CSE array formulas
- circular references
- functions outside the supported grammar
- user defined names, because a defined name and a cell reference are the same thing in formula text
- anything else the parser cannot read

**Supported grammar (v1):** arithmetic operators (`+ - * / ^`, unary minus, postfix `%`), comparison operators (`= <> < <= > >=`, which `IF` conditions need), number, text and boolean literals, cell and range references (relative, absolute, mixed), cross sheet references, and `SUM`, `AVERAGE`, `MIN`, `MAX`, `IF`, `ROUND`, `ABS`, `SUMIF`.

This is a deliberate choice, not a gap we are hiding. A tool that silently mis-evaluates a formula it does not understand is worse than one that declines the file. See `docs/ARCHITECTURE.md` for why we wrote our own evaluator over this grammar rather than depending on a general Excel calculation engine.

## 7. Improvement changelog

Full evaluation methodology in `docs/EVALUATION.md`. Every row below is scored on the **same 12 case corpus** with the **same metric**.

Every Evidence cell is written by `make eval` from `results/`. None of it is typed.

| Stage | What was tried and why | Evidence | Decision / learning |
| --- | --- | --- | --- |
| **Baseline** | General purpose coding agent, same model, given the `.xlsx` and a shell, free to write its own `openpyxl` code and report errors in the required schema. Chosen over a "dump to CSV" baseline because a CSV strawman would inflate our result. | 83% material precision, 71% material recall, 0.50 false positives per clean workbook, 12 findings reported | Establishes the starting point |
| **Iteration 1** | Deterministic detectors only, no model. This is a deliberate stand-in for the existing commercial category (Operis, Macabacus, ExceLint): structural detection with no impact reasoning. Tests the claim that the hard part is precision, not detection. | 5% material precision, 93% material recall, 23.00 false positives per clean workbook, 267 findings reported | Expect high recall, poor precision. **This row is the numeric answer to "doesn't Macabacus already do this".** If precision is already good, the thesis is wrong and we pivot the framing. |
| **Iteration 2** | Add the agent loop with the recompute tool, no materiality gate. Every surviving candidate is reported. | 93% material precision, 93% material recall, 0.00 false positives per clean workbook, 14 findings reported | Isolates the contribution of hypothesis testing from the contribution of materiality filtering |
| **Iteration 3** | Add the materiality gate over declared output cells. | 100% material precision, 93% material recall, 0.00 false positives per clean workbook, 13 findings reported | Isolates the single change we claim is the differentiator |
| **Removed** | A report writer agent, given the verified findings and asked to render the report, with explicit instructions not to reinterpret any figure. | Printed every number correctly, then wrote that enterprise value was overstated where the measurement says understated, flipping the direction of all four findings. Also put the dependency path at 6 steps where it is 21, and called an `INTENTIONAL` verdict "suppressed". | Removed. The deterministic renderer ships instead. Guarding the figures does not guard the sentences around them. |
| **Final** | Iteration 3. Detectors, the adjudicator loop with the recompute tool, the materiality gate, and a template renderer. | 100% material precision, 93% material recall, 0.00 false positives per clean workbook, 13 findings reported | Largest single contributor: the adjudicator loop, worth 5% to 93% precision. The gate is worth the last 7 points. |

There was no Iteration 4. Peer group evidence was in the adjudicator's context from the first version rather than added in response to a failure, so there is no before and after to report, and inventing one would make this table fiction.

**On the Final row's attribution.** Iteration 1 to 2 is the agent loop arriving as a whole: hypothesis testing, the recompute gate on impact figures, and `INTENTIONAL` as a first class verdict, all at once. That step is worth 88 points of precision and we did not run an ablation separating its three parts, so the honest claim is that the loop did it, not that any one piece did. Iteration 2 to 3 is a single change and its 7 points are attributable.

The one other thing tried and dropped was not an iteration and is not in the table: `gpt-5.6-terra` on `/v1/chat/completions` refuses function tools unless `reasoning_effort` is set to `none`. Both routes were tested live. `/v1/responses` was chosen because turning reasoning off removes exactly the capability the tier was picked for.

## 8. Main failure mode

**Confident output that nobody checked against evidence that was already available.**

This was predicted as "the agent rationalises", which turned out to be half right and half too narrow. It happened three times during the build, and only two of the three involve a model at all.

**The adjudicator invented figures it had already been given.** On the first candidate of the first live run, it called `recompute_with_patch`, received `{"P&L!AA15": 8704573.0, "Valuation!B7": 92752830.0}` at step 4, and reported `{"P&L!AA15": -6102169, "Valuation!B7": -50782614}` at step 7. Opposite signs, different magnitudes. The verdict was right and the proposed repair was right. Rule 1 of its own instructions says never state an impact figure you did not obtain from the tool, and it had the figure, in its own context window, three steps earlier. Trajectory: `trajectories/solution/C03_adjudicator_Revenue_H5_D1.jsonl`, steps 4 and 7.

**The baseline judged intent without looking for it.** On `C10`, a workbook with no errors, it reported `Costs!I12` as an error at high confidence and proposed restoring the inflation formula. That cell carries a comment: "One off office move approved by the board in month 7. Held at this figure on purpose, do not restore the inflation formula." The string `.comment` does not appear anywhere in that trajectory. It was using `openpyxl` all run, one attribute away. Its impact figure for the change it proposed was, incidentally, correct. Trajectory: `trajectories/baseline/C10_baseline_openai.jsonl`.

**Our own code did the same thing.** `from_trajectories` rebuilt a workbook's audit by globbing every `.jsonl` in a directory. That was true while a directory held one workbook. When the corpus sweep put twelve in one place, rebuilding any single workbook silently picked up all 267 verdicts and reported them as that workbook's, and it wrote twelve result files that way before anything noticed. Every trace record carries the workbook name in `run_start`. The function read past it.

**And a fourth, found while auditing this list.** The results table in `docs/EVALUATION.md` reported the baseline at 100% precision and 7% recall for six commits. `results/headline.md`, written by the same command in the same run, said 83% and 71%. A test was passing `--results` to a temporary directory while leaving `--document` pointing at the real file, so every `make verify` quietly overwrote the doc with a one finding fixture's scores. It was found only because the audit re-derived every published figure from `results/` instead of reading the file and believing it. This one is worth the most of the four: the wrong numbers were in the submission, they were wrong in our favour, and the tooling built to stop exactly this had been pointed at the model and never at itself.

The common shape is the same in all four. The information needed to be correct was present and reachable. Nothing compared the output against it. That the last two cases are ordinary Python with no model involved is the useful part: this is not a property of language models, it is a property of unverified assertions, and a codebase built to distrust a model's numbers has no excuse for trusting its own.

Each fix is the same move, a mechanical check that reads the source of truth and compares:

1. **The cross check**, worth the most. Every figure in a report is read back out of a `tool_result` record in the trajectory. If there is no measurement behind a number, the finding is dropped and counted as a schema violation. If a measurement exists and the model said something else, the measured figure is substituted and the discrepancy is logged. The number a user sees is measured whatever the model claims. Across the 267 adjudications of the scored run this fired zero times, which is a better result than the first run and not a reason to remove it.
2. **Put the evidence in front of the model rather than hoping it looks.** The cell comment, the row label, the peer group and the normalised peer formula are all in the adjudicator's opening context. On the same `Costs!I12` the baseline got wrong, the adjudicator returned `INTENTIONAL` in one turn, quoting the comment. It did not need to call `inspect_range` to find it, because it was never given the chance to fail to.
3. **`INTENTIONAL` as a first class verdict.** If the only way to complete the task is to produce a finding, the model produces findings. Giving "this is fine, and here is why" the same standing as a finding is what makes declining possible, and it is what 253 of 267 candidates got.

Ordering by measured contribution is only possible for the third of these against the second and first together: the loop that contains all three took precision from 5% to 93% in one step, and no ablation was run to separate them. That is stated in the changelog rather than guessed at here.

## 9. Hot take

**Deterministic tools do not fail at recall. They fail at precision, and precision is a judgement about consequence rather than about structure.**

Every anomaly detector for spreadsheets is essentially solved and has been for years. What nobody shipped is the layer that decides which anomalies deserve a human's attention, because that decision requires knowing what the model is *for*, and that has never been expressible in a rule.

The generalisable lesson: when you add an agent to a mature deterministic pipeline, the agent's job is usually not to replace the detector. It is to be the **judgement layer the detector never had**, sitting between raw signal and human attention. And the moment you give the agent that job, you have to take away its ability to assert consequences, because it will assert them fluently and incorrectly. Route detection to code, route judgement to the model, then route verification of that judgement back to code.

What we would build differently next time: design the verification tool before writing a single line of the agent's instructions. The tool defines what claims the agent is even able to make, and that constrains the failure surface far more effectively than the prompt does.

## 10. Repository map

```
README.md                     this file
CLAUDE.md                     working agreement for the build
TASKS.md                      task list and status
PROMPTS.md                    the prompt for each task
docs/REPRODUCTION.md          clean environment to reproduced result
docs/EVALUATION.md            corpus, mutation taxonomy, metrics, results
docs/ARCHITECTURE.md          component design and the decisions behind it
docs/AGENT_INSTRUCTIONS.md    the instructions that shape each agent
docs/TRAJECTORIES.md          trajectory capture format and index
docs/SUBMISSION_CHECKLIST.md  rubric line to artifact map
docs/VIDEO_SCRIPT.md          5 minute solution video script
docs/BUILD_PLAN.md            72 hour sprint plan (working doc, not a deliverable)
src/                          implementation
corpus/                       generated workbooks and mutation manifests
results/                      evaluation output, committed
trajectories/                 captured agent runs, committed
```

## 11. Responsible use

- Materia never writes to the input workbook. Repairs are applied to a copy, and only after explicit human approval.
- Materia reports evidence and an estimated impact. It does not assert that a formula is business-correct. A dependency graph can prove a cell is inconsistent with its peers. It cannot prove what the author intended.
- The corpus is synthetic or public. No private or client financial data is included in this submission.
- No credentials are committed. API keys are read from the environment only.

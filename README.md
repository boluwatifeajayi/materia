# Materia

**An agent that finds silent errors in financial models, proves what they cost, and throws away the ones that do not matter.**

Submission for the micro1 Frontier Engineering Challenge 2026 (Agentic Workflows Hackathon), 28 to 31 August 2026.

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

The value of a tool here is not "finds errors". It is **finds the few that matter and proves it, so that checking the model takes minutes instead of hours.** That is a claim to be measured rather than asserted: the human time protocol is in `docs/EVALUATION.md` section 1 and the result is `[TBD]` until the timed trials run.

## 4. What we are building

Materia takes an `.xlsx` model and returns a small number of evidence backed findings. For each one:

- the anomalous cell and the formula it contains
- the formula its peer group implies it should contain
- the dependency path from that cell to a decision relevant output
- **the quantified change in that output if the cell is corrected**
- a confidence level and the evidence behind it
- a proposed repair, applied only to a copy, only after human approval

And critically, the count of anomalies it **suppressed** and why.

> **Layout only. Every figure below is a `[TBD]` placeholder.** Nothing in this block has been measured. `make eval` fills it from `results/`, and until it does, no number here may be quoted anywhere.

```
MODEL HEALTH                      <workbook>.xlsx

  [TBD]  formulas parsed
  [TBD]  structural anomalies detected
  [TBD]  survived hypothesis testing
  [TBD]  material findings               <-- what the user reads
```

That funnel is the product. Everything above the last line is what existing tools already give you. The last line is what makes anyone use it twice.

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
| `openpyxl`, `networkx`, `anthropic` SDK, `pytest` | Third party, pre-existing |
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

**Supported grammar (v1):** arithmetic operators, cell and range references (relative, absolute, mixed), cross sheet references, and `SUM`, `AVERAGE`, `MIN`, `MAX`, `IF`, `ROUND`, `ABS`, `SUMIF`.

This is a deliberate choice, not a gap we are hiding. A tool that silently mis-evaluates a formula it does not understand is worse than one that declines the file. See `docs/ARCHITECTURE.md` for why we wrote our own evaluator over this grammar rather than depending on a general Excel calculation engine.

## 7. Improvement changelog

Full evaluation methodology in `docs/EVALUATION.md`. Every row below is scored on the **same 12 case corpus** with the **same metric**.

> Fill `[TBD]` as each experiment completes. Do not reconstruct this at the end. An entry written after the fact is not evidence.

| Stage | What was tried and why | Evidence | Decision / learning |
| --- | --- | --- | --- |
| **Baseline** | General purpose coding agent, same model, given the `.xlsx` and a shell, free to write its own `openpyxl` code and report errors in the required schema. Chosen over a "dump to CSV" baseline because a CSV strawman would inflate our result. | `[TBD]` precision, `[TBD]` recall, `[TBD]` FP per clean workbook | Establishes the starting point |
| **Iteration 1** | Deterministic detectors only, no model. This is a deliberate stand-in for the existing commercial category (Operis, Macabacus, ExceLint): structural detection with no impact reasoning. Tests the claim that the hard part is precision, not detection. | `[TBD]` | Expect high recall, poor precision. **This row is the numeric answer to "doesn't Macabacus already do this".** If precision is already good, the thesis is wrong and we pivot the framing. |
| **Iteration 2** | Add the agent loop with the recompute tool, no materiality gate. Every surviving candidate is reported. | `[TBD]` | Isolates the contribution of hypothesis testing from the contribution of materiality filtering |
| **Iteration 3** | Add the materiality gate over declared output cells. | `[TBD]` | Isolates the single change we claim is the differentiator |
| **Iteration 4** | Add peer group evidence to the agent context after observing `[failure mode]`. | `[TBD]` | `[kept / revised / removed]` |
| **Iteration 5 (removed)** | `[experiment that was tried and cut]` | `[TBD]` | What it taught us about the problem |
| **Final** | Combination of the changes that survived | `[TBD]` | Largest single contributor: `[TBD]` |

## 8. Main failure mode

**The agent rationalises.**

Given a candidate cell and asked what the intended formula was, the model will almost always produce a plausible answer, including for cells that were deliberately different. It does not naturally say "this looks intentional". A hardcoded actuals row, a first period column with no prior to reference, a manual override: the model will invent an intent for all of them and report a finding.

This is not a prompting problem that goes away with a better instruction. It is structural. The model is being asked to explain an anomaly, and explanation is the thing it is best at, so it explains regardless of whether an explanation is warranted.

Three mitigations, in order of how much they helped: `[TBD, fill from evaluation]`

1. The recompute gate. The model cannot report an impact it has not had verified.
2. Requiring peer group evidence in the finding, so a hypothesis with no supporting pattern is rejected structurally.
3. Explicit "intentional override" as a first class output class, so declining to flag is a success state rather than a non-answer.

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

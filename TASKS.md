# Tasks

29 tasks, empty repo to submitted project. Work them in order, one per session. See `CLAUDE.md` for the working agreement and `PROMPTS.md` for the prompt to paste for each.

Status values: `TODO`, `IN PROGRESS`, `DONE`, `CUT`.

**Phase deadlines map to `docs/BUILD_PLAN.md`.** If you are behind at a phase boundary, use the cut list at the bottom rather than dropping tasks arbitrarily.

---

## Phase 0: Foundation
> Target: Friday night. Nothing here calls a model.

| ID | Task | Status | Acceptance |
| --- | --- | --- | --- |
| T01 | Repo scaffold | DONE | `pyproject.toml`, package layout, `Makefile` with all targets from `docs/REPRODUCTION.md` stubbed, `.gitignore`, `.env.example`, `pytest` runs and finds zero tests without erroring |
| T02 | Preflight validator | DONE | Rejects VBA, external links, array formulas, circular refs, unsupported functions, each with a named reason. Unit tested against a fixture workbook per rejection type. |
| T03 | Parser and R1C1 normaliser | DONE | Reads formulas via `openpyxl`, normalises A1 to R1C1. Tested: a row of copied formulas all normalise to one identical token, and a broken one does not. |

## Phase 1: The engine
> The load bearing part. Everything downstream depends on these numbers being right.

| ID | Task | Status | Acceptance |
| --- | --- | --- | --- |
| T04 | Formula tokeniser and AST | DONE | Parses the supported grammar (`README.md` section 6) into an AST. Rejects anything outside it. Tested per operator and per function. |
| T05 | Recompute engine | DONE | Evaluates the AST, applies a single cell patch, recomputes in topological order, returns deltas on declared outputs. **Densest test coverage in the repo.** Tested against hand computed expected values for every supported function. |

## Phase 2: Graph and corpus

| ID | Task | Status | Acceptance |
| --- | --- | --- | --- |
| T06 | Dependency graph | DONE | `networkx` DiGraph, cell nodes, precedent to dependent edges, cross sheet edges. Can return the path from any cell to a declared output. Tested on a known workbook. |
| T07 | Corpus generator, one workbook | DONE | Generates one realistic three statement model per `docs/EVALUATION.md` section 2. Assumptions, revenue build, cost build, P&L to EBITDA, valuation. Deterministic from seed. Passes preflight. |
| T08 | Corpus generator, all twelve | DONE | `C01` to `C12` per the table in `docs/EVALUATION.md` section 2, including the two clean controls and the two hard cases. `corpus/manifest.json` with declared output cells. `corpus/checksums.txt`. `make corpus` and `make corpus-check` work. |
| T09 | Mutation injector | DONE | Five in taxonomy families plus two out of taxonomy, per `docs/EVALUATION.md` section 3. Injected programmatically. **True materiality per mutation computed via the recompute engine and stored in the manifest.** Tested: injecting and reverting returns the original workbook. |

## Phase 3: Detection and first evidence
> By the end of this phase you have a real number for the changelog.

| ID | Task | Status | Acceptance |
| --- | --- | --- | --- |
| T10 | Five detectors | DONE | One per in taxonomy family. Emit candidates with a structural reason and peer group evidence. Tuned for recall. Tested: each detector fires on its own mutation family in the corpus. |
| T11 | Evaluator and Iteration 1 run | DONE | Scores a result set against the manifest. Produces `results/headline.md` and `results/per_workbook.md`. **Then: run detectors alone over the corpus and record the result as changelog Iteration 1 in `README.md`.** This is the number that answers "doesn't Macabacus already do this". |

> **Checkpoint.** If detector-only precision comes back high, the thesis is wrong. Stop and tell me before continuing.

## Phase 4: Agent layer

| ID | Task | Status | Acceptance |
| --- | --- | --- | --- |
| T12 | LLM provider abstraction | DONE (Anthropic model id unverified, no key) | `LLMClient` protocol plus Groq and Anthropic adapters per `docs/ARCHITECTURE.md` section 9. `MATERIA_PROVIDER` env var. Tool schemas translated per provider. Tested with one trivial call per provider. **The trivial Anthropic call must confirm `claude-sonnet-5` is a valid model id before anything depends on it. If it errors, stop and report rather than guessing another string.** |
| T13 | Trace capture | DONE | Every model call, tool call and tool result logged to JSONL per `docs/TRAJECTORIES.md`. Written as the run proceeds, not reconstructed. Tested: a run produces a well formed trace with correct step ordering. |
| T14 | Agent tools | DONE | `recompute_with_patch` and `inspect_range` wired as callable tools with the right schemas. Tested directly, outside the agent loop, before any model sees them. |
| T15 | Adjudicator agent | DONE | Prompt from `docs/AGENT_INSTRUCTIONS.md` section 1, loaded from `src/materia/prompts/`. Three verdict schema (`ERROR`, `INTENTIONAL`, `INCONCLUSIVE`; the gate owns `IMMATERIAL`). **Run on Groq against one workbook only.** Tested: produces valid verdicts, calls the recompute tool, trace is readable. |
| T16 | Report renderer and cross check | DONE | Deterministic renderer, no model call. Renders findings into evidence cards. **Drops any finding whose delta has no matching tool result in the trace**, and logs the violation. Tested with a deliberately fabricated delta that must get dropped. The report writer agent is T22, not here. |
| T17 | Solution end to end, one workbook | DONE (full sweep blocked: Groq daily token quota) | `python -m materia audit corpus/C03.xlsx --explain` runs the full pipeline on Groq and prints a report. Rough output is fine. |

## Phase 5: Baseline and measurement
> Baseline runs **before** the solution is finished, so nobody can say it was tuned down.

| ID | Task | Status | Acceptance |
| --- | --- | --- | --- |
| T18 | Baseline agent harness | TODO | Sandboxed working dir, workbook copied in, shell tool, prompt from `docs/AGENT_INSTRUCTIONS.md` section 3. Turn and token caps equal to the solution's. Traced. Tested on one workbook on Groq. |
| T19 | Baseline scored run | TODO | **Anthropic. Full 12 workbook corpus.** `make baseline`. Results to `results/baseline/`, traces to `trajectories/baseline/`. Tell me the estimated cost before running. |
| T20 | Solution scored run, no gate | TODO | **Anthropic. Full corpus, materiality gate disabled.** Record as changelog Iteration 2. This isolates hypothesis testing from materiality filtering. |

## Phase 6: The thesis

| ID | Task | Status | Acceptance |
| --- | --- | --- | --- |
| T21 | Materiality gate | TODO | Threshold on declared outputs, default 1%. Suppressed candidates counted and shown, never silently dropped. Buckets sum to the detector count. **Rerun on Anthropic, record as changelog Iteration 3.** This is the single change the whole project claims. |
| T22 | CLI report output and report writer agent | DONE (writer built and run, not shipped, cut list item 4) | The funnel from `README.md` section 4, evidence cards, consequence first ordering, suppressed count. Readable in a terminal at demo font sizes. No emoji, no AI voice. **Plus the report writer agent from `docs/AGENT_INSTRUCTIONS.md` section 2**, prompt loaded from `src/materia/prompts/`, one call per workbook, no tools, rendering over the verified findings the T16 renderer produced. It never recomputes or adjusts a figure. Traced like the adjudicator, since the trajectory deliverable requires one per agent. |

## Phase 7: Deliverables
> These are worth 35 rubric points and are the ones that usually get rushed.

| ID | Task | Status | Acceptance |
| --- | --- | --- | --- |
| T23 | Repair mode | TODO | `--repair` writes a corrected copy, never the original, and prompts per finding. Declines logged as `human_checkpoint` records. Tested: original file byte identical after a repair run. |
| T24 | Trajectory rendering | TODO | `materia trace render` produces readable markdown. Pick and render the four featured trajectories from `docs/TRAJECTORIES.md`, write their preambles. `make trace-index` generates `trajectories/index.md`. |
| T25 | Clean clone reproduction | TODO | **Fresh clone, fresh venv, `make all` start to finish.** Fix whatever breaks. This is 15 rubric points and the only way to know is to actually do it. |
| T26 | Variance and sensitivity | TODO | `make eval-repeat N=3` producing `results/variance.md`. Sensitivity at three materiality thresholds to `results/sensitivity.md`. Cuttable if behind. |
| T27 | Docs finalisation | TODO | Every `[TBD]` filled from `results/`. Changelog complete including the removed experiment. Failure mode section written from what actually happened, not from the prediction currently in the doc. |
| T28 | Submission audit | TODO | Work `docs/SUBMISSION_CHECKLIST.md` top to bottom. Repo public. No keys committed. No `[TBD]` anywhere. Answer the six self check questions. |
| T29 | HTML report view | TODO (optional) | Only if time remains after T28. Static HTML report of the findings. This is the one task where Playwright testing applies. **Do not start this before T28 is done.** |

---

## Cut list

If behind, cut in this order. Do not improvise cuts.

1. T29 HTML report
2. T26 variance and sensitivity
3. Detectors `M4` and `M5` in T10, and the corresponding corpus mutations. Reduce and say so in the docs.
4. The report writer agent in T22, render from the T16 deterministic template instead
5. Human time measurement in T27, report as not measured rather than estimated

**Never cut:** the two clean control workbooks, the out of taxonomy mutations, T19 baseline run, the recompute cross check in T16, T25 clean clone test.

## Progress

| Phase | Tasks | Done |
| --- | --- | --- |
| 0 Foundation | T01 to T03 | 3/3 |
| 1 Engine | T04 to T05 | 2/2 |
| 2 Graph and corpus | T06 to T09 | 4/4 |
| 3 Detection | T10 to T11 | 2/2 |
| 4 Agent layer | T12 to T17 | 6/6 |
| 5 Baseline | T18 to T20 | 0/3 |
| 6 Thesis | T21 to T22 | 1/2 |
| 7 Deliverables | T23 to T29 | 0/7 |

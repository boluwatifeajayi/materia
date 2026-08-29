# Architecture

The organising principle: **code finds, the model judges, code verifies the judgement.**

Every component below exists because it either produces evidence or constrains what the model is allowed to claim. If a component does neither, it is not in the system.

---

## Pipeline

```
.xlsx
  |
  |  1. Preflight validator          reject, with a named reason
  v
  |  2. Parser + R1C1 normaliser     formulas as comparable tokens
  v
  |  3. Dependency graph             cell level DAG
  v
  |  4. Detectors                    -> CANDIDATES (high recall, low precision)
  v
  |  5. Agent loop                   -> hypothesis + verified delta
  v
  |  6. Materiality gate             -> FINDINGS + suppressed count
  v
  |  7. Reporter                     evidence cards
  v
  |  8. Repair (opt-in, copy only)   human approval per finding
```

---

## 1. Preflight validator

Rejects rather than guesses. Named reasons: `VBA_PRESENT`, `EXTERNAL_LINK`, `ARRAY_FORMULA`, `CIRCULAR_REFERENCE`, `UNSUPPORTED_FUNCTION(<name>)`, `DEFINED_NAME`, `UNPARSEABLE_FORMULA`.

**Why:** a tool that mis-evaluates a formula it does not understand produces confident wrong impact numbers, which is worse than producing nothing. Rejection is also what makes the recompute engine tractable in a weekend: we only have to be correct over a grammar we control.

`DEFINED_NAME` exists because a defined name is indistinguishable from a cell reference in formula text. If a workbook defines `Q1` as a name, `=Q1*2` reads as a reference to cell Q1, and every figure downstream is wrong with nothing looking wrong. The check is deliberately strict, rejecting on a name being defined rather than used, since deciding which one a bare identifier meant is the ambiguity itself.

Preflight runs the real parser from `src/materia/formula/` over every formula rather than pattern matching for function names. That is what makes acceptance mean something: a workbook that passes preflight is one the rest of the pipeline can read, by construction rather than by hope. `UNSUPPORTED_FUNCTION` and `UNPARSEABLE_FORMULA` are the two ways a parse can fail, kept separate because "we do not support this function" and "we cannot read this at all" are different problems for the user.

Those seven are the complete list. `PreflightRejected` means "a real workbook containing something we cannot evaluate faithfully", so a file that is not a readable `.xlsx` at all raises an ordinary `ValueError` instead of being forced into one of the codes. Reporting a renamed CSV as `VBA_PRESENT` would tell the user something untrue.

Preflight runs before the dependency graph exists, so it cannot use it. Cycle detection here is a small separate pass over the same formulas: ranges are resolved against the set of formula cells rather than expanded, since only a formula cell can carry a cycle. That keeps it bounded by the number of formulas rather than by how large a range someone wrote.

## 2. Parser and R1C1 normaliser

`openpyxl` in formula mode gives raw A1 strings. A row of copied formulas looks like twelve different strings in A1 and one identical string in R1C1. Peer group comparison is only possible after normalisation, so this happens before anything else.

`G17 = F17*(1+F5)` and `H17 = G17*(1+G5)` both normalise to `RC[-1]*(1+R[-12]C[-1])`. A cell in that row that does not normalise to the same token is the signal every detector is built on.

Absolute and mixed references are the part worth care. `$B$5` becomes a fixed `R5C2` and `B5` becomes an offset, so the four forms of one address produce four different tokens. Treating the dollar sign as decoration would collapse them into one and silently destroy the signal, which is why each form has its own test.

`src/materia/parse.py` owns the definition of what a reference looks like. Preflight imports it rather than carrying a second copy, so the grammar cannot drift between the component that rejects formulas and the component that reads them.

## 3. Dependency graph

`networkx` DiGraph, one node per non empty cell, edges from precedent to dependent, cross sheet edges included.

Used for three things:
- tracing the path from a candidate cell to the declared output cells
- computing topological order for the recompute engine
- producing the blast radius shown in the report

Preflight uses it too, for cycle detection. It is the same graph in all four cases on purpose. If preflight resolved a range differently from the engine, a workbook could pass preflight and then have no evaluation order, and the run would fail late rather than being refused up front.

A range expands to every cell in it that exists, so a total sitting inside the range it totals shows up as a cell reading itself. Ranges are resolved against cells that exist rather than expanded blindly, and a range above 65,536 cells is refused rather than walked.

## 4. Detectors

Five detectors, one per in taxonomy mutation family (see `EVALUATION.md` section 3). Each emits candidates with a structural reason and the peer group evidence that triggered it.

**Detectors are tuned for recall, not precision.** They are expected to fire on legitimate pattern breaks. That is fine, because they are not the output of the system. Turning candidates into findings is the agent's job, and the deliberately noisy detector layer is what makes the funnel in the README meaningful.

## 5. Agent loop

Per candidate, the model receives: the cell, its formula, its normalised form, its peer group with their formulas, its position in the block, any cell comment, and the dependency path to each declared output.

It has exactly two tools for gathering evidence, and a third that is how it answers:

| Tool | Signature | Purpose |
| --- | --- | --- |
| `recompute_with_patch` | `(cell, proposed_formula) -> {output: delta}` | Test a hypothesis and get the true impact |
| `inspect_range` | `(sheet, range) -> cells with formulas` | Pull more surrounding context |
| `submit_verdict` | `(verdict, confidence, ...) -> end of turn` | Return the answer as structured data |

`submit_verdict` is the output channel, not a source of evidence. Asking for JSON in prose makes the schema a request; making it a tool call makes it a constraint the provider enforces, and puts the verdict in the trajectory as data rather than as text somebody has to parse.

It must return exactly one of three verdicts:

- `ERROR` with a proposed formula and a verified delta
- `INTENTIONAL` with the evidence that suggests the difference is deliberate
- `INCONCLUSIVE`

**The adjudicator never returns `IMMATERIAL`.** That verdict exists, but only the materiality gate assigns it (section 7). The model judges correctness. The gate judges consequence. Splitting them this way is what keeps the four report buckets mutually exclusive, so they sum exactly to the detector candidate count. If the model could also call something immaterial, a candidate could be counted twice or fall between the two, and the invariant in the data flow constraints below would not be checkable.

**`INTENTIONAL` is a first class success state.** This is the single most important detail in the design. If the only way to complete the task is to produce a finding, the model produces findings, including for the hardcoded actuals row in `C10`. Giving "this is fine, and here is why" the same status as a finding is what makes declining possible.

**The model cannot state an impact it has not measured.** Any number in a report comes from `recompute_with_patch`, not from the model's text. This constraint is enforced in the renderer, which reads the figure out of the trajectory rather than out of the verdict.

Two outcomes are possible when a reported figure does not line up:

- **No tool result for the proposed repair.** The impact is unverifiable, and the finding is dropped and counted as a schema violation. A tool result for the same cell but a different formula does not count: a model that measured one hypothesis and proposed another has not measured the one it proposed.
- **A tool result exists and the model reported something else.** The finding survives with the measured figure substituted, and the discrepancy is logged. Dropping it would lose a real error to a reporting mistake, and the number the user sees is measured either way, which is the actual guarantee.

The second case is not hypothetical. It happened on the first candidate of the first live run: the model called the tool, received 8704573.0, and reported -6102169. See README section 8.

## 6. Recompute engine

A small deterministic evaluator over the supported grammar. Applies a patch to one cell, recomputes in topological order, returns the delta on each declared output.

It evaluates the AST produced by `src/materia/formula/`, which follows Excel's operator precedence rather than the more familiar one from programming languages. Unary minus binds tighter than exponentiation, so `-2^2` is 4, and `^` is left associative, so `2^3^2` is 64. Both differ from Python, and either one implemented the usual way would put a silently wrong number into an impact figure.

Faithfulness means following Excel where it differs from Python, and it differs in places that are easy to miss. Each of these is implemented deliberately and has its own test:

- `ROUND` rounds half away from zero. Python rounds half to even, so `round(2.5)` is 2 where Excel gives 3. Using the Python default would bias every rounded figure in the corpus in one direction.
- `SUM`, `AVERAGE`, `MIN` and `MAX` skip text and booleans found inside a range, but coerce them when passed directly as arguments. `SUM(A1:A3)` with `TRUE` in `A2` is not the same as `SUM(TRUE)`.
- Empty cells are 0 in arithmetic and skipped by the aggregates. `MIN` of an empty range is 0, not an error.
- Text that looks like a number is coerced, so `="5"+1` is 6.
- Errors are values, not exceptions. `#DIV/0!` propagates through a chain the way it does in a spreadsheet, so a bad patch produces an error in an output rather than ending a run.

An output that becomes an error or text has a delta of `None` rather than 0. Reporting an output that broke as unchanged would be exactly the kind of confident wrong number the design exists to prevent.

**Why not use a real Excel calculation engine.** Three reasons, in order of weight:
1. Reproducibility. A judge on a clean Linux box with no office suite must be able to run this. That is 15 points of the rubric.
2. Setup risk. Headless office automation is the classic weekend killer.
3. It is unnecessary. We already restrict the grammar at preflight, so a faithful evaluator over that grammar is a few hundred lines and is unit tested against known outputs.

The engine is the load bearing component of the whole submission. Every impact number and the ground truth materiality of every mutation both come from it, so it has the densest test coverage in the repo.

## 7. Materiality gate

A finding is shown only if its verified delta on at least one declared output exceeds the threshold (default 1% of that output's value).

The gate is the only component that assigns `IMMATERIAL`. It takes the candidates the adjudicator returned as `ERROR`, checks each verified delta against the threshold, and reclassifies the ones that fall below it from `ERROR` to `IMMATERIAL`. `INTENTIONAL` and `INCONCLUSIVE` candidates never reach the gate, because a candidate the model did not call an error has no delta to weigh.

That ordering is deliberate. Correctness is a question about the workbook and the model can answer it from evidence. Consequence is a question about a threshold and a measured number, so it is settled in code, after the fact, where it can be audited and re-run at a different threshold without another model call.

Everything below the threshold is counted and summarised, never silently dropped. The report always states how many anomalies were detected, how many survived hypothesis testing, and how many were suppressed as immaterial. Suppression the user cannot see is indistinguishable from a bug.

## 8. Reporter and repair

Two things share the name "reporter" and they are different components. The **renderer** is deterministic code: it builds the evidence cards and runs the trace cross check. The **report writer agent** is an LLM call that turns those verified findings into prose (`docs/AGENT_INSTRUCTIONS.md` section 2). The renderer runs first and constrains what the agent is given, so the cross check applies to both.

Evidence card per finding: cell, current formula, expected formula, peer evidence, dependency path, verified delta per output, confidence, proposed repair.

Repair is opt in, writes to a new file, and asks per finding. The input workbook is opened read only and never written. This satisfies the brief's requirement that consequential actions be gated behind human approval.

Three things hold whatever the user answers:

- The corrected copy goes to a new path, and naming the input as the target is refused rather than obeyed. That one mistake would make every other guarantee here pointless.
- Every answer is collected before anything is written. A run interrupted half way leaves the source untouched and no partly repaired file behind.
- Every answer is recorded as a `human_checkpoint`, declines included. A decline is a decision about the model and is worth as much in the record as an approval.

The prompt defaults to no. An unattended run, or somebody pressing return to get through a list, must not end up writing changes nobody agreed to.

## 9. LLM provider abstraction

The adjudicator and reporter agents (section 5, `docs/AGENT_INSTRUCTIONS.md`) talk to a single interface, never to a provider SDK directly.

```python
class LLMClient(Protocol):
    def complete(self, system: str, messages: list, tools: list) -> AgentResponse: ...
```

Two implementations, used for different purposes and never mixed within one scored run:

| Provider | Role | Why |
| --- | --- | --- |
| **Groq** (`openai/gpt-oss-120b`) | Dev-loop iteration | Fast inference, generous free tier, OpenAI-compatible tool-calling schema, so it is the cheapest place to debug the adjudicator's tool call sequence repeatedly against the corpus |
| **Anthropic (`claude-sonnet-5`)** | Final scored run | Required by `EVALUATION.md`: solution and baseline must run on the same model, so the headline table isolates the workflow's contribution rather than a difference in raw model capability |

Selected by `MATERIA_PROVIDER` env var (`groq` or `anthropic`), read once at startup. `config.yaml` records which provider produced any given `results/` directory, so a stray dev-loop run can never be mistaken for the scored one.

Each provider gets a thin adapter translating `recompute_with_patch` and `inspect_range` into that provider's native tool schema, and normalising the response back into `AgentResponse`. This is the only provider-specific code in the system; the adjudicator, the report writer agent, the gate and the renderer are provider agnostic.

**What this abstraction is not for:** it is not a claim of multi-provider robustness as a feature. It exists purely so development iteration is fast and free, while the number that ships in `EVALUATION.md` comes from one accountable model. Only the Anthropic run is ever cited as a result.

**Groq's free tier only serves open models**, which is fine for exercising the tool-call loop and catching bugs in the adjudication logic, but is not a substitute data point for the headline comparison. Do not report Groq-run numbers anywhere in `README.md` or `EVALUATION.md`.

This project was originally specified against `llama-3.3-70b-versatile`. Groq no longer serves it, so the dev-loop model is `openai/gpt-oss-120b`, chosen from what the provider reports it will actually serve rather than guessed. The distinction matters more for Anthropic, where a scored run against a model nobody chose would not be a result: `ModelNotAvailable` is a separate exception class for exactly that reason, and the adapter says so rather than falling back.

**What the dev loop actually costs, measured.** Two limits bind, and the second is the one that matters. Tokens per minute is 8,000 on this account, which one adjudication exceeds on its own, so requests are paced. Tokens per day is 200,000, which is roughly sixty adjudications. A full twelve workbook run is not possible on the free tier in a single day, and this is a planning fact rather than a bug.

**Tool call formatting failures are the reason this model is dev loop only.** Across two runs `openai/gpt-oss-120b` produced three malformed tool calls that the provider rejected outright: a call to a tool named `json` that did not exist, arguments containing `"P&L!AA15": -610,?` which is not JSON, and a tool named `inspect_range<|channel|>commentary` with a special token leaked into it. None is a reasoning failure and none says anything about the design. They are why the adjudicator loop treats a provider error on one candidate as one lost verdict rather than a lost run, and why the numbers that ship come from a different model.

---

## Data flow constraints

Three invariants, each enforced in code and each testable:

1. **The input workbook is never mutated.** Opened read only. Repairs go to a new path.
2. **Every reported number traces to a tool result.** Enforced in the renderer, verifiable in the trajectory.
3. **Nothing is dropped silently.** Every detected candidate appears in the report in exactly one of four buckets: finding, intentional, inconclusive, immaterial. The first three come from the adjudicator's three verdicts, the fourth is assigned only by the gate reclassifying an `ERROR` that fell below threshold. The buckets are mutually exclusive and sum to the detector count.

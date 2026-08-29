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

Rejects rather than guesses. Named reasons: `VBA_PRESENT`, `EXTERNAL_LINK`, `ARRAY_FORMULA`, `CIRCULAR_REFERENCE`, `UNSUPPORTED_FUNCTION(<name>)`.

**Why:** a tool that mis-evaluates a formula it does not understand produces confident wrong impact numbers, which is worse than producing nothing. Rejection is also what makes the recompute engine tractable in a weekend: we only have to be correct over a grammar we control.

## 2. Parser and R1C1 normaliser

`openpyxl` in formula mode gives raw A1 strings. A row of copied formulas looks like twelve different strings in A1 and one identical string in R1C1. Peer group comparison is only possible after normalisation, so this happens before anything else.

`G17 = F17*(1+F5)` and `H17 = G17*(1+G5)` both normalise to `RC[-1]*(1+R[-12]C[-1])`. A cell in that row that does not normalise to the same token is the signal every detector is built on.

## 3. Dependency graph

`networkx` DiGraph, one node per non empty cell, edges from precedent to dependent, cross sheet edges included.

Used for three things:
- tracing the path from a candidate cell to the declared output cells
- computing topological order for the recompute engine
- producing the blast radius shown in the report

## 4. Detectors

Five detectors, one per in taxonomy mutation family (see `EVALUATION.md` section 3). Each emits candidates with a structural reason and the peer group evidence that triggered it.

**Detectors are tuned for recall, not precision.** They are expected to fire on legitimate pattern breaks. That is fine, because they are not the output of the system. Turning candidates into findings is the agent's job, and the deliberately noisy detector layer is what makes the funnel in the README meaningful.

## 5. Agent loop

Per candidate, the model receives: the cell, its formula, its normalised form, its peer group with their formulas, its position in the block, any cell comment, and the dependency path to each declared output.

It has exactly two tools:

| Tool | Signature | Purpose |
| --- | --- | --- |
| `recompute_with_patch` | `(cell, proposed_formula) -> {output: delta}` | Test a hypothesis and get the true impact |
| `inspect_range` | `(sheet, range) -> cells with formulas` | Pull more surrounding context |

It must return one of four verdicts:

- `ERROR` with a proposed formula and a verified delta
- `INTENTIONAL` with the evidence that suggests the difference is deliberate
- `INCONCLUSIVE`
- `IMMATERIAL` (may also be assigned by the gate)

**`INTENTIONAL` is a first class success state.** This is the single most important detail in the design. If the only way to complete the task is to produce a finding, the model produces findings, including for the hardcoded actuals row in `C10`. Giving "this is fine, and here is why" the same status as a finding is what makes declining possible.

**The model cannot state an impact it has not measured.** Any number in a report comes from `recompute_with_patch`, not from the model's text. This constraint is enforced in the reporter, which drops any finding whose delta does not have a matching tool result in the trajectory.

## 6. Recompute engine

A small deterministic evaluator over the supported grammar. Applies a patch to one cell, recomputes in topological order, returns the delta on each declared output.

**Why not use a real Excel calculation engine.** Three reasons, in order of weight:
1. Reproducibility. A judge on a clean Linux box with no office suite must be able to run this. That is 15 points of the rubric.
2. Setup risk. Headless office automation is the classic weekend killer.
3. It is unnecessary. We already restrict the grammar at preflight, so a faithful evaluator over that grammar is a few hundred lines and is unit tested against known outputs.

The engine is the load bearing component of the whole submission. Every impact number and the ground truth materiality of every mutation both come from it, so it has the densest test coverage in the repo.

## 7. Materiality gate

A finding is shown only if its verified delta on at least one declared output exceeds the threshold (default 1% of that output's value).

Everything below the threshold is counted and summarised, never silently dropped. The report always states how many anomalies were detected, how many survived hypothesis testing, and how many were suppressed as immaterial. Suppression the user cannot see is indistinguishable from a bug.

## 8. Reporter and repair

Evidence card per finding: cell, current formula, expected formula, peer evidence, dependency path, verified delta per output, confidence, proposed repair.

Repair is opt in, writes to a new file, and asks per finding. The input workbook is opened read only and never written. This satisfies the brief's requirement that consequential actions be gated behind human approval.

## 9. LLM provider abstraction

The adjudicator and reporter agents (section 5, `docs/AGENT_INSTRUCTIONS.md`) talk to a single interface, never to a provider SDK directly.

```python
class LLMClient(Protocol):
    def complete(self, system: str, messages: list, tools: list) -> AgentResponse: ...
```

Two implementations, used for different purposes and never mixed within one scored run:

| Provider | Role | Why |
| --- | --- | --- |
| **Groq** | Dev-loop iteration | Fast inference, generous free tier, OpenAI-compatible tool-calling schema, so it is the cheapest place to debug the adjudicator's tool call sequence repeatedly against the corpus |
| **Anthropic (`claude-sonnet-4-6`)** | Final scored run | Required by `EVALUATION.md`: solution and baseline must run on the same model, so the headline table isolates the workflow's contribution rather than a difference in raw model capability |

Selected by `MATERIA_PROVIDER` env var (`groq` or `anthropic`), read once at startup. `config.yaml` records which provider produced any given `results/` directory, so a stray dev-loop run can never be mistaken for the scored one.

Each provider gets a thin adapter translating `recompute_with_patch` and `inspect_range` into that provider's native tool schema, and normalising the response back into `AgentResponse`. This is the only provider-specific code in the system; the adjudicator, reporter, gate, and reporter logic are provider agnostic.

**What this abstraction is not for:** it is not a claim of multi-provider robustness as a feature. It exists purely so development iteration is fast and free, while the number that ships in `EVALUATION.md` comes from one accountable model. Only the Anthropic run is ever cited as a result.

**Groq's free tier only serves open models** (Llama family and similar), which is fine for exercising the tool-call loop and catching bugs in the adjudication logic, but is not a substitute data point for the headline comparison. Do not report Groq-run numbers anywhere in `README.md` or `EVALUATION.md`.

---

## Data flow constraints

Three invariants, each enforced in code and each testable:

1. **The input workbook is never mutated.** Opened read only. Repairs go to a new path.
2. **Every reported number traces to a tool result.** Enforced in the reporter, verifiable in the trajectory.
3. **Nothing is dropped silently.** Every detected candidate appears in the report in one of four buckets: finding, intentional, inconclusive, immaterial. The buckets sum to the detector count.

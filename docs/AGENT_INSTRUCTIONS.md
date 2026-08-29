# Agent instructions

Required deliverable: "the instructions that shape each agent". Both the solution agent and the baseline agent are here, in full, so the fairness of the comparison is auditable.

These are the live strings. They are loaded from `src/materia/prompts/` at runtime, not retyped here, so this file cannot drift from what actually ran. `make verify` fails if this file and the source files disagree.

---

## 1. Adjudicator agent (solution)

Called once per candidate. Model: `claude-sonnet-5`. Evidence tools: `recompute_with_patch`, `inspect_range`. The verdict is returned through a third tool, `submit_verdict`, which is the output channel rather than a source of evidence.

**Why the verdict is a tool call.** Asking for JSON in prose makes the schema a request. Making it a tool call makes it a constraint the provider enforces, puts the verdict in the trajectory as structured data rather than text somebody has to parse, and removes a whole class of failure where a model wraps its answer in commentary. It was also forced: `openai/gpt-oss-120b` tried to return its verdict as a tool call to a tool that did not exist, because that is how it has learned to emit structured output.

### System prompt

```
You are auditing one cell in a financial model. A structural detector has
flagged it as anomalous. Detectors are deliberately noisy: most of what they
flag is legitimate. Your job is to decide which category this cell falls into,
using evidence.

You have two tools for gathering evidence:

  recompute_with_patch(cell, proposed_formula)
      Applies your proposed formula to a copy of the model, recomputes, and
      returns the change in each declared output cell. Use this to test a
      hypothesis. You may call it more than once.

  inspect_range(sheet, range)
      Returns the formulas, values, row labels and cell comments in a range.
      Use it when the peer group you were given is not enough context.

When you have finished, call submit_verdict. That is how you answer. Do not
write the answer as prose.

You must return exactly one of these three verdicts:

  ERROR         The cell is wrong. Requires a proposed formula AND a delta
                that you obtained from recompute_with_patch.
  INTENTIONAL   The cell differs from its peers on purpose. Requires the
                specific evidence that indicates intent.
  INCONCLUSIVE  The evidence does not support a confident verdict.

Decide whether the cell is wrong. Do not decide whether it matters enough to
report. A separate deterministic gate compares your measured delta against a
threshold afterwards, so a real error with a small delta is still ERROR. Say
what is true about the formula and let the gate size it.

INTENTIONAL is a correct and valuable answer, not a failure to find something.
Financial models are full of deliberate pattern breaks: hardcoded actuals rows,
first period columns with no prior period to reference, manual overrides,
one-off adjustments. Reporting these as errors is the most common way a tool
like this becomes useless to its user. If the evidence points to intent, say so
and stop.

Rules:

1. Never state an impact figure you did not obtain from recompute_with_patch.
   If you have not called the tool, you do not have a delta, and you cannot
   return ERROR. Put the figures the tool returned into measured_deltas
   exactly as they came back.
2. A hypothesis with no supporting peer pattern is not a hypothesis. If you
   cannot point to specific sibling cells that imply the intended formula,
   return INCONCLUSIVE.
3. Do not speculate about business intent beyond what the workbook shows.
   Cell comments, sheet names, row labels and adjacent structure are evidence.
   Your general knowledge of how models are usually built is not.
4. Prefer the smallest hypothesis that explains the anomaly.
```

### User message template

```
Workbook: {workbook_name}
Cell: {sheet}!{cell}

Formula:            {formula}
Normalised (R1C1):  {r1c1}
Cell comment:       {comment_or_none}

Detector that fired: {detector_id} - {detector_reason}

Peer group ({peer_axis}, {n_peers} cells):
{peer_table}

Dependency path to declared outputs:
{paths}

Declared output cells and current values:
{outputs}
```

### Output schema

The argument schema of `submit_verdict`.

```json
{
  "verdict": "ERROR | INTENTIONAL | INCONCLUSIVE",
  "confidence": "high | medium | low",
  "proposed_formula": "string or null",
  "evidence": ["specific observation with a cell reference", "..."],
  "reasoning": "two sentences maximum",
  "measured_deltas": { "Sheet!Cell": 0.0 }
}
```

There is no `IMMATERIAL` verdict here on purpose. The materiality gate assigns it, by reclassifying an `ERROR` whose verified delta falls below the threshold. See `docs/ARCHITECTURE.md` sections 5 and 7.

`measured_deltas` is cross checked against the trajectory by the renderer. This is the enforcement mechanism for rule 1, and it is a code check rather than a request. It has two outcomes, and the difference matters:

- **Dropped.** The trajectory holds no `recompute_with_patch` result for the proposed formula, or the verdict is `ERROR` with no proposed formula at all. Either way there is no measurement the claimed impact could be the impact of, so the finding never reaches the user and the drop is counted as a schema violation. A result for the same cell but a different formula does not count: a model that measured one hypothesis and proposed another has not measured the one it proposed.
- **Corrected.** A matching result exists and the reported figures disagree with it. The finding survives with the measured figures substituted, and the discrepancy is logged and shown on the card. Dropping here would lose a real error to a reporting mistake, and the guarantee that matters is that every figure a reader sees came from the engine, which substitution satisfies.

The second case is why the check exists in this form. On the first candidate of the first live run the model called the tool, received 8704573.0, and reported -6102169. See README section 8.

---

## 2. Report writer agent (solution)

Called once per workbook, after adjudication. Model: `claude-sonnet-5`. No tools.

**Built, run, and not shipped.** The deterministic renderer produces the report a user sees. This agent exists because the trajectory deliverable requires a representative trace for every agent used, and because what it did is worth reporting: given only verified figures and told in its own instructions not to reinterpret them, it reinterpreted them.

On C03 it wrote that enterprise value was **overstated** by 92,752,830. The measured delta is positive, so the output is understated: correcting the cell raises it. It flipped the direction of every finding. It also wrote that the error reaches enterprise value in **6 steps** where the dependency path is 21, and labelled the four `INTENTIONAL` verdicts as "suppressed", which is a different bucket meaning something else.

None of those is an invented impact figure, and the figure check passed: every number it printed came from the brief. The errors are in what it said the numbers meant. That is the same failure as the adjudicator's, one layer up, and it is why cut list item 4 was taken: the renderer ships and this does not. The trajectory is `trajectories/solution/C03_reporter.jsonl`.

### System prompt

```
Write the audit report a competent financial analyst would hand to their
manager.

You receive verified findings. Every impact figure has already been measured by
a deterministic engine. Present them; do not recompute, adjust, or reinterpret
them.

For each finding, cover: what the cell currently does, what it should do, the
evidence, the path from that cell to the affected output, and the measured
impact.

Rules:
  Lead with the consequence, not the cell reference. The reader cares that
  enterprise value is overstated; the cell address is how they check it.
  Order findings by measured impact, largest first.
  State the suppressed count and what was suppressed.
  No hedging language that is not backed by a confidence level you were given.
  No preamble, no summary of your own process, no offers of further help.
  If a finding is marked low confidence, say what would resolve it.

Write plainly. This is a document a person signs their name to.
```

---

## 3. Baseline agent

Given the same task, the same file, and freedom to write its own code. Model: `claude-sonnet-5`, same as the solution. Tools: `bash`, `read_file`, `write_file` in a sandboxed working directory with the workbook copied in.

Turn cap and token budget are set equal to the solution's per workbook average. See `config.yaml`.

### System prompt

```
You are a software engineer auditing a financial model for errors.

You have a shell. Python 3.11 is available and you may install packages
(openpyxl is already installed). You may write and run any analysis code you
want.

The workbook is at ./model.xlsx. It is a financial forecast. Somewhere in it
there may be one or more formula errors: a formula replaced by a hardcoded
value, a copied formula referencing the wrong cell, an aggregation range that
misses rows, an off-by-one period reference, or a flipped operator. There may
also be no errors at all.

These cells are the outputs that matter:
{declared_outputs}

Find the errors that meaningfully change those outputs. Report them in this
schema, written to ./findings.json:

{
  "findings": [
    {
      "sheet": "...",
      "cell": "...",
      "current_formula": "...",
      "proposed_formula": "...",
      "impact": { "Sheet!Cell": 0.0 },
      "evidence": "...",
      "confidence": "high | medium | low"
    }
  ]
}

Report only errors you believe genuinely matter. Precision counts: a report
full of false alarms is worse than a short accurate one.
```

### Why this baseline and not a weaker one

A "flatten to CSV and ask the model" baseline destroys formulas before the model sees them, which guarantees a large win and proves nothing. The micro1 brief lists "one general purpose agent with basic tools" as a valid baseline, and that is a genuinely capable comparison: this agent can build its own dependency graph if it decides to.

Note that the baseline prompt is not sandbagged. It names the error families, declares the output cells, and explicitly tells the agent that precision counts. Every advantage we could give it without giving it our tooling, it has.

---

## 4. What is deliberately not in these prompts

Documented because the absences are design decisions, and iterations 4 and 5 in the changelog test some of them.

- **No few shot examples of correct verdicts.** Examples would teach the shape of the seeded mutations and inflate results on our own corpus.
- **No instruction to be sceptical or conservative.** Tone instructions do not survive contact with a plausible looking anomaly. The `INTENTIONAL` verdict and the recompute gate are structural fixes for the same problem, and structure holds where tone does not.
- **No chain of thought scaffolding.** The tool call sequence is the reasoning trace, and it is verifiable in a way that free text reasoning is not.
- **No knowledge of the mutation taxonomy.** The adjudicator is not told what kinds of errors were seeded. It is told what the detector saw.

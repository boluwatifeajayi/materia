# Agent trajectories

Required deliverable: representative trajectories for every agent used, followable from instructions through to final result, showing tool responses, the feedback that shaped the next step, retries, and human checkpoints.

micro1 is an AI data lab that buys agent traces. Assume these are read closely.

---

## Capture

Every agent call is logged automatically by `src/materia/trace.py`. No manual capture, nothing reconstructed after the fact. One JSONL file per workbook per agent, written as the run proceeds.

```
trajectories/
  solution/
    C03_adjudicator_H42.jsonl        one file per candidate adjudicated
    C03_reporter.jsonl
  baseline/
    C03_baseline.jsonl
  index.md                           this table, generated
```

### Record schema

```json
{
  "ts": "2026-08-30T14:22:10.481Z",
  "run_id": "sol-C03-a1f2",
  "agent": "adjudicator",
  "step": 3,
  "type": "tool_call | tool_result | model_message | verdict | human_checkpoint",
  "content": {},
  "tokens": { "in": 0, "out": 0 },
  "latency_ms": 0
}
```

Every field that appears in the final report can be traced to a `tool_result` record in one of these files. That traceability is the point: it is what lets a judge confirm that no impact figure was invented.

### Rendering

```bash
python -m materia trace render trajectories/solution/C03_adjudicator_H42.jsonl
```

Produces a readable markdown transcript with tool calls, responses, and the decision at each step. Rendered versions of the four featured trajectories below are committed as `.md` alongside the raw `.jsonl`, so a judge can read them without running anything.

---

## Featured trajectories

Four, chosen to show the system working, the system declining, the system correcting itself, and the baseline failing. Each has a short written preamble explaining what to watch for.

> The file names and cell references in this table are illustrative. The real ones are whatever the scored runs produce, and T24 fills them in. If one of the four situations does not occur in an actual run, it is reported as absent rather than manufactured.

**A fifth, found during T15 and worth featuring above the others.** Rendered at `trajectories/featured/5-the-check-firing.md`.

<!-- -->
 In `solution/C03_adjudicator_Revenue_H5_D1.jsonl` the adjudicator calls `recompute_with_patch`, receives `{"P&L!AA15": 8704573.0, "Valuation!B7": 92752830.0}` at step 4, and then reports `{"P&L!AA15": -6102169, "Valuation!B7": -50782614}` in its verdict at step 7. Different magnitudes, opposite signs. The verdict itself is correct, the cell really is a hardcoded value and the proposed repair is right, but the impact figures are invented.

This is the failure mode in README section 8 happening on the first candidate of the first live run, unprompted. It is also the reason the reporter's cross check is a code check rather than a line in the prompt: the prompt already says, as rule 1, never state an impact figure you did not obtain from the tool. The model agreed and then did it anyway.

**Two of the four exist. `trajectories/index.md` says which, and why the other two do not.** Trajectory 2 needs an adjudication run against `C10`, which has not happened. Trajectory 4 needs the baseline harness from T18, which has not been built. Neither is written until a run produces it: a trajectory composed to demonstrate a capability nobody exercised is a fabrication, and this is the one deliverable where that would be most obvious to a reader who checks.

| # | File | Agent | Shows |
| --- | --- | --- | --- |
| 1 | `solution/C03_adjudicator_H42` | Adjudicator | The clean win. Detector fires on a formula replaced by a constant, model hypothesises the intended `SUM`, calls `recompute_with_patch`, gets a large verified delta, returns `ERROR`. The impact figure in the final report is visible in the tool response. |
| 2 | `solution/C10_adjudicator_B14` | Adjudicator | **The most important one.** Detector fires on the hardcoded actuals row. Model calls `inspect_range`, finds the row label and the cell comment, returns `INTENTIONAL`. Nothing is reported to the user. This is the behaviour that separates this from a linter, and it is a decision to stay silent. |
| 3 | `solution/C07_adjudicator_F29` | Adjudicator | Self correction. First hypothesis returns a near zero delta, model recognises the hypothesis was wrong, calls `inspect_range` for wider context, forms a second hypothesis, gets a material delta, returns `ERROR` on the second attempt. The retry is the evidence that the loop is doing work. |
| 4 | `baseline/C10_baseline` | Baseline | The failure the whole project is about. The baseline agent writes reasonable `openpyxl` code, finds the same anomalies our detectors find, and reports three of them as errors with **estimated** impacts it calculated by reasoning rather than by recomputing. All three are legitimate. This is the precision problem in one file. |

## Human checkpoints

Two points in the system require a human, both logged as `human_checkpoint` records:

1. **Repair approval.** `--repair` prompts per finding before writing anything, and writes only to a copy. Declines are logged with the finding id.
2. **Low confidence escalation.** Findings marked low confidence are reported in a separate section with what would resolve them, rather than being asserted or dropped.

Neither is decorative. The brief requires consequential actions to be gated behind human approval, and writing to somebody's financial model is consequential.

## Index

`trajectories/index.md` is generated by `make trace-index` and lists every trajectory with: run id, agent, candidate cell, step count, tool call count, final verdict, total tokens, and the file it came from. It is the map a reader uses to find the trace behind any specific number in the results.

The same cell appears more than once where two runs adjudicated it and disagreed. `P&L!AA15` is `ERROR` in one run and `INCONCLUSIVE` in another, the second because the model emitted a malformed tool call rather than because it judged differently. The file column tells them apart.

`make trace-index` also renders the featured trajectories to markdown under `trajectories/featured/`, each with its preamble, so nothing has to be run to read them.

# Solution video script

Target 4:30, hard cap 5:00. Screen recording with voiceover. Record after the results are final so no number has to be re-shot.

Every figure spoken here comes from `results/` or `corpus/manifest.json`. The workbook on screen is `C03` unless a section says otherwise. If a rerun changes a number, change it here before recording.

The brief requires: the problem, the simple baseline, one realistic execution end to end, the final comparison, the changelog, the biggest contributing change, and one experiment that was removed.

**Rule for the whole video: lead with the money, not the spreadsheet.** The first thing on screen is a number a person cares about. Excel appears because it is where the number lives, not because the video is about Excel.

---

## 0:00 to 0:35 | The problem

**On screen:** `C03.xlsx`, scrolled to the Valuation tab. Enterprise value visible: 143,535,444.

> "This model says the company is worth a hundred and forty three million. Every sheet reconciles. Excel shows no errors."

Scroll to `Revenue!H5`. Zoom.

> "This cell should read the previous month's closing customers. It is the number 7,200, pasted from month one and never put back. Enterprise value is understated by ninety two million, and there is nothing on screen that tells you."

> "Whoever inherits this model is accountable for that number and did not write a single formula in it. That is the problem."

## 0:35 to 1:05 | Why the existing answer does not work

**On screen:** a deterministic linter's output. A long scrolling list of flagged cells.

> "Spreadsheet linters have existed for twenty years and they do find this cell. Here it is, one of twenty two flagged in this workbook, and two hundred and sixty seven across the twelve I tested."

Scroll the list. Let it keep going a beat too long.

> "Most of these are deliberate. Hardcoded actuals. First columns with no prior period. Manual overrides. The user checks the first fifteen, finds them all fine, and never opens the tool again."

> "So the bottleneck is not detection. It is precision. And precision here is not a structural question. It is a question about consequence."

## 1:05 to 1:35 | The baseline

**On screen:** terminal, baseline agent running on `C10`, the clean control with legitimate pattern breaks.

> "The obvious thing to try is a general purpose coding agent. Same model I use, given the file and a shell, free to write its own analysis code. It is not a strawman, it can build a dependency graph if it wants to."

Baseline output appears: one finding on a workbook with zero errors.

> "This workbook has no errors in it. The agent reports one, at high confidence: a cell held at a fixed figure on purpose, with a comment on it saying so. It never read the comment. Its impact number is correct. Its judgement is wrong, and a correct number attached to a wrong judgement is the failure this project is about."

## 1:35 to 3:05 | One realistic execution

**On screen:** `python -m materia audit corpus/C03.xlsx --explain`, running live.

> "Same file, my system."

Funnel renders:

```
    738  formulas parsed
     22  structural anomalies detected
      2  survived hypothesis testing
      2  material findings   <-- what you read
```

> "Detectors run first and they are deliberately noisy. Twenty two candidates on this workbook. Then each one goes to an agent that has to do something specific: propose what the formula should have been, and then call a deterministic engine to recompute the model with that patch applied."

**On screen:** live trajectory view of `H42`. Hypothesis, then the `recompute_with_patch` call, then the returned delta.

> "It cannot state an impact it has not measured. That figure comes from the engine, not from the model. If there is no tool result behind a number, the finding gets dropped before the user sees it."

**On screen:** the `C10` adjudication. `INTENTIONAL` verdict.

> "And here is the one that matters more. Same detector fires on a hardcoded actuals row. The agent pulls the row label and the cell comment, and returns intentional. Nothing is reported. Deciding to stay quiet is a success state in this system, and that single design choice is most of the precision difference."

**On screen:** the final report. Evidence card for `H42`.

> "Two findings. Consequence first, cell reference second, the dependency path to enterprise value, and the measured impact. Twenty declined as deliberate, counted and shown, because suppression the user cannot see is indistinguishable from a bug."

## 3:05 to 3:50 | The comparison

**On screen:** the headline table.

| | Baseline | Materia |
| --- | --- | --- |
| Material finding precision | 83% | 100% |
| Material recall | 71% | 93% |
| FP per clean workbook | 0.50 | 0.00 |
| Human time per workbook | not measured | not measured |

> "Twelve workbooks. Ten seeded from a published spreadsheet error taxonomy, two clean controls, and one where the error is real but moves the output by three hundredths of a percent, well under the one percent threshold. Correct behaviour there is to detect it and suppress it, and the report shows it in the suppressed count."

> "Both systems also miss one mutation entirely: an assumption that should be one point two percent and says twelve percent. Structurally perfect, no peer signal exists. Nothing structural can catch that and I report it as a limitation."

## 3:50 to 4:20 | Changelog, biggest change, removed experiment

**On screen:** the changelog table.

> "Detectors alone: ninety three percent recall, five percent precision. Adding the agent loop with the recompute tool took precision to ninety three. Adding the materiality gate took it to a hundred, with recall unchanged."

> "The biggest single contributor was the agent loop, and I have to be honest that it arrived as one change rather than three, so I can tell you it is worth eighty eight points of precision but not how those split between hypothesis testing, the recompute gate, and letting the model say a cell is fine."

> "The experiment I removed was a report writer agent. I gave it the verified findings and told it not to reinterpret any figure. It printed every number correctly and then wrote that enterprise value was overstated where the measurement says understated, on all four findings. Guarding the numbers does not guard the sentences around them, so the renderer that ships is a template."

## 4:20 to 4:40 | Hot take

**On screen:** the funnel, held.

> "The failure mode I kept hitting is that the agent rationalises. Ask it to explain an anomaly and it will, every time, including for cells that were deliberately different. Explaining is the thing it is best at, so it explains whether or not an explanation is warranted."

> "Telling it to be careful did not fix that. Taking away its ability to assert an impact did. Route detection to code, route judgement to the model, then route verification of that judgement back to code. When you bolt an agent onto a mature deterministic pipeline, its job is almost never to replace the detector. It is to be the judgement layer the detector never had."

---

## Production notes

- Record at 1080p minimum, readable font sizes in the terminal. Judges may watch on a laptop.
- Do the live run once beforehand and keep the recording as a fallback in case of an API hiccup during the take.
- Do not speed up the agent run. The pauses while it calls the recompute tool are the visual evidence that it is doing work.
- Cut all setup, installs, and file navigation.
- Total spoken words at this length: about 620. If it runs long, cut from section 0:35 to 1:05 first, never from 1:35 to 3:05.

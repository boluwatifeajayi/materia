# Solution video script

Target 4:30, hard cap 5:00. Screen recording with voiceover. Record after the results are final so no number has to be re-shot.

> **Every `[TBD]` in this script is an unmeasured placeholder.** The first draft of this document carried invented figures that read as real. They have been removed. Fill each one from `results/` after the scored runs, and do not record a take with a `[TBD]` still spoken aloud.

The brief requires: the problem, the simple baseline, one realistic execution end to end, the final comparison, the changelog, the biggest contributing change, and one experiment that was removed.

**Rule for the whole video: lead with the money, not the spreadsheet.** The first thing on screen is a number a person cares about. Excel appears because it is where the number lives, not because the video is about Excel.

---

## 0:00 to 0:35 | The problem

**On screen:** an open workbook, scrolled to the valuation tab. Enterprise value visible: `[TBD]`.

> "This model says the company is worth `[TBD]`. It was built by four people over eleven months. Every sheet reconciles. Excel shows no errors."

Scroll to the seeded cell in the chosen workbook, `[TBD cell ref]`. Zoom.

> "This cell should be a subtotal formula. It is a hardcoded number. Somebody pasted a value to check something and never put the formula back. The model is wrong by `[TBD]` and there is nothing on screen that tells you."

> "Whoever inherits this model is accountable for that number and did not write a single formula in it. That is the problem."

## 0:35 to 1:05 | Why the existing answer does not work

**On screen:** a deterministic linter's output. A long scrolling list of flagged cells.

> "Spreadsheet linters have existed for twenty years and they do find this cell. Here it is, in position `[TBD]` of `[TBD]` flagged cells."

Scroll the list. Let it keep going a beat too long.

> "Most of these are deliberate. Hardcoded actuals. First columns with no prior period. Manual overrides. The user checks the first fifteen, finds them all fine, and never opens the tool again."

> "So the bottleneck is not detection. It is precision. And precision here is not a structural question. It is a question about consequence."

## 1:05 to 1:35 | The baseline

**On screen:** terminal, baseline agent running on `C10`, the clean control with legitimate pattern breaks.

> "The obvious thing to try is a general purpose coding agent. Same model I use, given the file and a shell, free to write its own analysis code. It is not a strawman, it can build a dependency graph if it wants to."

Baseline output appears: `[TBD]` findings on a workbook with zero errors.

> "This workbook has no errors in it. The agent found `[TBD]`, with impact figures it calculated by reasoning rather than by recomputing anything. That is the failure this project is about."

## 1:35 to 3:05 | One realistic execution

**On screen:** `python -m materia audit corpus/C03.xlsx --explain`, running live.

> "Same file, my system."

Funnel renders:

```
[TBD] formulas parsed
[TBD] structural anomalies detected
[TBD] survived hypothesis testing
[TBD] material findings
```

> "Detectors run first and they are deliberately noisy. `[TBD]` candidates. Then each one goes to an agent that has to do something specific: propose what the formula should have been, and then call a deterministic engine to actually recompute the model with that patch applied."

**On screen:** live trajectory view of `H42`. Hypothesis, then the `recompute_with_patch` call, then the returned delta.

> "It cannot state an impact it has not measured. That figure comes from the engine, not from the model. If there is no tool result behind a number, the finding gets dropped before the user sees it."

**On screen:** the `C10` adjudication. `INTENTIONAL` verdict.

> "And here is the one that matters more. Same detector fires on a hardcoded actuals row. The agent pulls the row label and the cell comment, and returns intentional. Nothing is reported. Deciding to stay quiet is a success state in this system, and that single design choice is most of the precision difference."

**On screen:** the final report. Evidence card for `H42`.

> "`[TBD]` findings. Consequence first, cell reference second, the dependency path to enterprise value, and the measured impact. `[TBD]` suppressed, counted, and shown, because suppression the user cannot see is indistinguishable from a bug."

## 3:05 to 3:50 | The comparison

**On screen:** the headline table.

| | Baseline | Materia |
| --- | --- | --- |
| Material finding precision | `[TBD]` | `[TBD]` |
| Material recall | `[TBD]` | `[TBD]` |
| FP per clean workbook | `[TBD]` | `[TBD]` |
| Human time per workbook | `[TBD]` | `[TBD]` |

> "Twelve workbooks. Ten seeded from a published spreadsheet error taxonomy, two clean controls, and one workbook where the error is real but changes the output by `[TBD]` percent, well under the threshold. Correct behaviour there is to detect it and suppress it, and the report shows it in the suppressed count."

> "Both systems also miss one mutation entirely: an assumption that should be one point two percent and says twelve percent. Structurally perfect, no peer signal exists. Nothing structural can catch that and I report it as a limitation."

## 3:50 to 4:20 | Changelog, biggest change, removed experiment

**On screen:** the changelog table.

> "Detectors alone: high recall, precision of `[TBD]`. Adding the agent loop with the recompute tool took it to `[TBD]`. Adding the materiality gate took it to `[TBD]`."

> "The biggest single contributor was `[TBD]`."

> "The experiment I removed: `[TBD]`. What it taught me was `[TBD]`."

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

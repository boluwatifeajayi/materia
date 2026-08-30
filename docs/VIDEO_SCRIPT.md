# Solution video script

Target 4:30, hard cap 5:00. Screen recording with voiceover.

Every figure spoken here comes from `results/` or `corpus/manifest.json` and is checked against them by `tests/test_evaluate.py`. If a rerun changes a number, run `make eval` and change it here before recording.

**Nothing in this video is generated live.** Every terminal output is a file already on disk under `video/`, produced before recording. The agent runs cost money and the account has no credit, so a live run is not possible and a failed live run on camera is not a risk worth taking. `cat` a file, or open it in the editor with a large font. The pauses that would have shown work happening are gone, so do not pretend otherwise in the voiceover, and none of the lines below claim a live run.

The brief requires: the problem, the simple baseline, one realistic execution end to end, the final comparison, the changelog, the biggest contributing change, and one experiment that was removed.

**Rule for the whole video: lead with the money, not the spreadsheet.** The first thing on screen is a number a person cares about. Excel appears because it is where the number lives, not because the video is about Excel.

---

## Before you record

Open these, in this order, before pressing record. Every path is from the repository root.

**Terminal setup**

1. `cd` to the repository root. Confirm with `pwd`.
2. Font size up until a 74 character line fills the window comfortably. The reports are hard wrapped to 74.
3. `clear` between every beat. No scrollback from a previous take on screen.

**Windows to have open, left to right**

| # | What | Exact path or command | Used at |
| --- | --- | --- | --- |
| 1 | The workbook | `corpus/C03.xlsx`, on the `Valuation` tab, cell `B7` selected | 0:00 |
| 2 | The workbook again | `corpus/C03.xlsx`, on the `Revenue` tab, cell `H5` selected | 0:15 |
| 3 | Detector noise | `video/02-detectors-C03.txt` | 0:35 |
| 4 | Detector noise, whole corpus | `video/02b-detectors-corpus.txt` | 0:50 |
| 5 | The baseline's false finding | `video/03-baseline-C10.txt` | 1:05 |
| 6 | The full report | `video/04-audit-C03.txt` | 1:35 |
| 7 | The measurement trajectory | `video/05-trajectory-Revenue-H5.md`, scrolled to line 726 | 2:10 |
| 8 | The declining trajectory | `video/06-trajectory-C10-intentional.md`, scrolled to line 35 | 2:35 |
| 9 | The comparison | `video/07-headline.md` | 3:05 |
| 10 | The changelog | `video/08-changelog.md` | 3:50 |

**Two things to check on the workbook before recording**

- `corpus/C03.xlsx` opens in Excel or LibreOffice without a recalculation prompt. It carries cached values, so it should display numbers immediately.
- `Valuation!B7` reads `143,535,444`. If it does not, the corpus has been regenerated and every figure in this script needs re-checking.

**Do not**

- Do not run `python -m materia audit` on camera. It needs API credit the account does not have and it will fail.
- Do not open `video/05-trajectory-Revenue-H5.md` from the top. It is 780 lines and the interesting part is at 726.

---

## 0:00 to 0:35 | The problem

**On screen:** `corpus/C03.xlsx`, `Valuation` tab, cell `B7` selected, showing `143,535,444`.

> "This model says the company is worth a hundred and forty three million. Every sheet reconciles. Excel shows no errors."

**On screen:** switch to the `Revenue` tab, select cell `H5`. Zoom so the formula bar is legible: it shows the constant `7200`, not a formula. `G5` and `I5` next to it show `=F9` and `=H9`.

> "This cell should read the previous month's closing customers, the way every other cell in the row does. It is the number 7,200, pasted from month one and never put back. Enterprise value is understated by ninety two million, and there is nothing on screen that tells you."

> "Whoever inherits this model is accountable for that number and did not write a single formula in it. That is the problem."

## 0:35 to 1:05 | Why the existing answer does not work

**On screen:** `cat video/02-detectors-C03.txt`. Twenty two flagged cells, one per line, `Revenue!H5` among them.

> "Spreadsheet linters have existed for twenty years and they do find this cell. Here it is, one of twenty two flagged in this workbook."

**On screen:** `cat video/02b-detectors-corpus.txt`. Scroll it. Let it keep going a beat too long. It is 267 lines.

> "Two hundred and sixty seven across the twelve workbooks I tested. Most of these are deliberate. Hardcoded actuals. First columns with no prior period. Manual overrides. Forty six of them are in two workbooks that contain no errors at all. The user checks the first fifteen, finds them all fine, and never opens the tool again."

> "So the bottleneck is not detection. It is precision. And precision here is not a structural question. It is a question about consequence."

## 1:05 to 1:35 | The baseline

**On screen:** `cat video/03-baseline-C10.txt`.

> "The obvious thing to try is a general purpose coding agent. Same model I use, given the file and a shell with openpyxl, free to write its own analysis code. It is not a strawman. It built its own dependency analysis and it scored eighty three percent precision."

**On screen:** hold on the lower half of the file, where the finding sits above the cell's own comment.

> "This workbook has no errors in it. The agent reports one, at high confidence. The cell is held at a fixed figure on purpose and it carries a comment saying so: one off office move approved by the board, do not restore the inflation formula. It proposes restoring the inflation formula. The string dot comment never appears anywhere in its trajectory. Its impact number is correct. Its judgement is wrong, and a correct number attached to a wrong judgement is the failure this project is about."

## 1:35 to 3:05 | One realistic execution

**On screen:** `cat video/04-audit-C03.txt`, held at the top so the funnel fills the frame.

> "Same file, my system. This is the saved output of a run, not a live one."

```
    738  formulas parsed
     22  structural anomalies detected
      2  survived hypothesis testing
      2  material findings   <-- what you read
```

> "Detectors run first and they are deliberately noisy. The same twenty two candidates. Then each one goes to an agent that has to do something specific: propose what the formula should have been, and then call a deterministic engine to recompute the model with that patch applied."

**On screen:** `video/05-trajectory-Revenue-H5.md`, scrolled to line 726. Steps 8, 9 and 12 should be visible together if the font allows, otherwise scroll 726 to 780 slowly.

- Step 8 is the `recompute_with_patch` call: `{"cell": "Revenue!H5", "proposed_formula": "=G9"}`
- Step 9 is the engine's answer: `{"P&L!AA15": 8704573.0, "Valuation!B7": 92752830.0}`
- Step 12 is the verdict, carrying those same two numbers

> "It cannot state an impact it has not measured. The agent proposes the formula, the engine returns the delta, and the number in the report is read back out of that tool result rather than out of the model's answer. If there is no tool result behind a number, the finding is dropped before the user sees it."

**On screen:** `video/06-trajectory-C10-intentional.md`, scrolled to line 35, the verdict block.

> "And here is the one that matters more. This is the same cell the baseline got wrong. The detector fires, and the adjudicator quotes the comment back and returns intentional. Nothing is reported. Deciding to stay quiet is a success state in this system, and it is what happened to two hundred and fifty three of the two hundred and sixty seven candidates."

**On screen:** back to `video/04-audit-C03.txt`, scrolled to the first evidence card, the one for `Revenue!H5`.

> "Two findings. Consequence first, cell reference second, the dependency path to enterprise value, and the measured impact. Twenty declined as deliberate on this workbook, counted and shown, because suppression the user cannot see is indistinguishable from a bug."

## 3:05 to 3:50 | The comparison

**On screen:** `cat video/07-headline.md`, which is a copy of `results/headline.md`.

| | Baseline | Materia |
| --- | --- | --- |
| Material finding precision | 83% | 100% |
| Material recall | 71% | 93% |
| FP per clean workbook | 0.50 | 0.00 |
| Human time per workbook | not measured | not measured |

> "Twelve workbooks. Ten seeded from a published spreadsheet error taxonomy, two clean controls, and one where the error is real but moves the output by three hundredths of a percent, well under the one percent threshold. Correct behaviour there is to detect it and suppress it, and the report shows it in the suppressed count rather than dropping it."

> "Human time is not measured. I did not run the timed trials, so it says not measured rather than a number I would be guessing at."

> "Both systems also miss one mutation entirely: an assumption that should be one point two percent and says twelve percent. Structurally perfect, no peer signal exists. Nothing structural can catch that and I report it as a limitation."

## 3:50 to 4:20 | Changelog, biggest change, removed experiment

**On screen:** `cat video/08-changelog.md`, which is the changelog table from `README.md` section 7.

> "Detectors alone: ninety three percent recall, five percent precision. Adding the agent loop with the recompute tool took precision to ninety three. Adding the materiality gate took it to a hundred, with recall unchanged at ninety three."

> "The biggest single contributor was the agent loop, and I have to be honest that it arrived as one change rather than three, so I can tell you it is worth eighty eight points of precision but not how those split between hypothesis testing, the recompute gate, and letting the model say a cell is fine. I did not run the ablation."

> "The experiment I removed was a report writer agent. I gave it the verified findings and told it not to reinterpret any figure. It printed every number correctly and then wrote that enterprise value was overstated where the measurement says understated, on all four findings. Guarding the figures does not guard the sentences around them, so the renderer that ships is a template."

## 4:20 to 4:40 | Hot take

**On screen:** `video/04-audit-C03.txt` again, funnel held.

> "The failure I kept hitting was not hallucination. It was confident output that nobody checked against evidence that was already sitting there. The adjudicator invented figures it had been handed three steps earlier. The baseline judged intent without reading the comment on the cell. And my own code silently mixed twelve workbooks together because it read past the workbook name in every record it opened."

> "Telling a model to be careful does not fix that. Taking away its ability to assert an unverified number does. Route detection to code, route judgement to the model, then route verification of that judgement back to code. When you bolt an agent onto a mature deterministic pipeline, its job is almost never to replace the detector. It is to be the judgement layer the detector never had. And build the verification tool first, because it defines what claims the agent is able to make at all."

---

## Production notes

- Record at 1080p minimum, readable font sizes in the terminal. Judges may watch on a laptop.
- Cut all setup, installs, and file navigation.
- Total spoken words at this length: about 680. At 150 words a minute that is 4:32. If it runs long, cut the second paragraph of 0:35 to 1:05 first, never anything from 1:35 to 3:05.
- The two trajectory beats at 2:10 and 2:35 are the evidence a judge cannot get from the README. If anything has to be rushed, do not rush those.

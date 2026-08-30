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

**Say:**

- This model says the company is worth a hundred and forty three million.
- Every sheet reconciles.
- Excel shows no errors.

**On screen:** switch to the `Revenue` tab, select cell `H5`. Zoom so the formula bar is legible: it shows the constant `7200`, not a formula. `G5` and `I5` next to it show `=F9` and `=H9`.

**Say:**

- Every other cell in this row reads the previous month's closing customers.
- This one is 7,200, pasted from month one and never put back.
- Enterprise value is understated by ninety two million,
- and nothing on screen tells you.

**Say:**

- Whoever inherits this model is accountable for that number
- and did not write a single formula in it.
- That is the problem.

## 0:35 to 1:05 | Why the existing answer does not work

**On screen:** `cat video/02-detectors-C03.txt`. Twenty two flagged cells, one per line, `Revenue!H5` among them.

**Say:**

- Linters have existed for twenty years
- and they do find this cell.
- Here it is, one of twenty two flagged in this workbook.

**On screen:** `cat video/02b-detectors-corpus.txt`. Scroll it. Let it keep going a beat too long. It is 267 lines.

**Say:**

- Two hundred and sixty seven across twelve workbooks.
- Most are deliberate.
- Hardcoded actuals.
- First columns with no prior period.
- Manual overrides.
- Forty six are in two workbooks with no errors at all.
- The user checks the first fifteen, finds them fine,
- and never opens the tool again.

**Say:**

- So the bottleneck is not detection.
- It is precision.
- And precision is not a structural question.
- It is a question about consequence.

## 1:05 to 1:35 | The baseline

**On screen:** `cat video/03-baseline-C10.txt`.

**Say:**

- The obvious thing to try is a general purpose coding agent.
- Same model, same file, a shell with openpyxl.
- Not a strawman: it built its own dependency analysis
- and scored eighty three percent precision.

**On screen:** hold on the lower half of the file, where the finding sits above the cell's own comment.

**Say:**

- This workbook has no errors in it.
- The agent reports one, at high confidence.
- The cell is held at a fixed figure on purpose,
- and its comment says: do not restore the inflation formula.
- It proposes restoring it. It never read the comment.
- Its number is correct, its judgement is wrong,
- and that is the failure this project is about.

## 1:35 to 3:05 | One realistic execution

**On screen:** `cat video/04-audit-C03.txt`, held at the top so the funnel fills the frame.

**Say:**

- Same file, my system.
- This is saved output, not a live run.

```
    738  formulas parsed
     22  structural anomalies detected
      2  survived hypothesis testing
      2  material findings   <-- what you read
```

**Say:**

- Detectors run first and they are deliberately noisy.
- The same twenty two candidates.
- Each goes to an agent that must propose what the formula should have been,
- then call a deterministic engine to recompute the model with that patch.

**On screen:** `video/05-trajectory-Revenue-H5.md`, scrolled to line 726. Steps 8, 9 and 12 should be visible together if the font allows, otherwise scroll 726 to 780 slowly.

- Step 8 is the `recompute_with_patch` call: `{"cell": "Revenue!H5", "proposed_formula": "=G9"}`
- Step 9 is the engine's answer: `{"P&L!AA15": 8704573.0, "Valuation!B7": 92752830.0}`
- Step 12 is the verdict, carrying those same two numbers

**Say:**

- It cannot state an impact it has not measured.
- The report reads that number out of the tool result,
- not out of the model's answer.
- No tool result, no finding.

**On screen:** `video/06-trajectory-C10-intentional.md`, scrolled to line 35, the verdict block.

**Say:**

- And here is the one that matters more.
- Same cell the baseline got wrong.
- The detector fires, the adjudicator quotes the comment back,
- and returns intentional. Nothing is reported.
- Staying quiet is a success state here,
- and it happened two hundred and fifty three times.

**On screen:** back to `video/04-audit-C03.txt`, scrolled to the first evidence card, the one for `Revenue!H5`.

**Say:**

- Two findings.
- Consequence first, cell reference second,
- the path to enterprise value, the measured impact.
- Twenty declined as deliberate, counted and shown,
- because suppression the user cannot see is indistinguishable from a bug.

## 3:05 to 3:50 | The comparison

**On screen:** `cat video/07-headline.md`, which is a copy of `results/headline.md`.

| | Baseline | Materia |
| --- | --- | --- |
| Material finding precision | 83% | 100% |
| Material recall | 71% | 93% |
| FP per clean workbook | 0.50 | 0.00 |
| Human time per workbook | not measured | not measured |

**Say:**

- Twelve workbooks.
- Ten seeded from a published error taxonomy, two clean controls,
- and one where the error is real but moves the output three hundredths of a percent.
- Correct behaviour is to detect and suppress it,
- and the report shows it in the suppressed count.

**Say:**

- Human time is not measured.
- I did not run the trials,
- so it says so rather than a number I would be guessing at.

**Say:**

- Both systems miss one mutation entirely:
- an assumption that should be one point two percent and says twelve percent.
- Structurally perfect, no peer signal.
- Nothing structural can catch it, and I report that as a limitation.

## 3:50 to 4:20 | Changelog, biggest change, removed experiment

**On screen:** `cat video/08-changelog.md`, which is the changelog table from `README.md` section 7.

**Say:**

- Detectors alone: ninety three percent recall, five percent precision.
- The agent loop with the recompute tool took precision to ninety three.
- The materiality gate took it to a hundred, recall unchanged.

**Say:**

- The biggest single contributor was the agent loop,
- worth eighty eight points of precision.
- It arrived as one change rather than three,
- so I cannot say how that splits. I did not run the ablation.

**Say:**

- The experiment I removed was a report writer agent.
- Given verified findings and told not to reinterpret any figure,
- it printed every number correctly, then flipped the direction on all four.
- Guarding the figures does not guard the sentences around them,
- so the renderer that ships is a template.

## 4:20 to 4:40 | Hot take

**On screen:** `video/04-audit-C03.txt` again, funnel held.

**Say:**

- The failure I kept hitting was not hallucination.
- It was confident output that nobody checked against evidence already sitting there.
- The adjudicator did it. The baseline did it.
- My own code did it too, with no model involved at all.

**Say:**

- Telling a model to be careful does not fix that.
- Taking away its ability to assert an unverified number does.
- Route detection to code, judgement to the model,
- verification of that judgement back to code.
- When you add an agent to a mature deterministic pipeline,
- its job is not to replace the detector.
- It is the judgement layer the detector never had.
- Build the verification tool first: it defines what the agent can claim.

---

## Production notes

- Record at 1080p minimum, readable font sizes in the terminal. Judges may watch on a laptop.
- Cut all setup, installs, and file navigation.
- **Spoken length: 759 words, counted not estimated.** That is 5:04 at a slow 150 words a minute and 4:45 at a normal narration pace of 160. The cap is 5:00, so read it at 155 or above and it lands with room.
- Where the words are, measured: 0:00 80, 0:35 92, 1:05 94, 1:35 159, 3:05 105, 3:50 115, 4:20 114.
- **Rehearse once against a timer before recording.** If it comes in over 4:50, cut the third bullet of the hot take, the one listing where the failure happened. It is the only line in the script that repeats something the viewer has already seen on screen.
- Do not cut anything from 1:35 to 3:05. It is the only part that shows the system working.
- An earlier version of this note claimed 680 words when the script held 986. Both figures here were counted, and a test fails if the stated count and the real one diverge.
- Read the bullets as written. Each one is a breath. They are the exact spoken words, split at the pauses, not a summary.
- The two trajectory beats at 2:10 and 2:35 are the evidence a judge cannot get from the README. If anything has to be rushed, do not rush those.

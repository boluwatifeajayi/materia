# Submission checklist

Two purposes: make sure nothing required is missing, and make sure every rubric line has an artifact pointed at it. Judges score what they can find.

Worked top to bottom in T28. `[x]` means verified, with the check named. `[~]` means partly true and the line says which part. `[ ]` means not done. Nothing is ticked that was not checked.

---

## Required deliverables

### 1. Complete solution code and improvement changelog

- [x] Full project, runnable. `make verify` is 990 passed, 2 skipped. Every `make` target runs; `eval-repeat` exits with the reason it was cut.
- [x] The instructions that shape each agent, in `docs/AGENT_INSTRUCTIONS.md`, both solution and baseline. Both are the strings the code loads: a test asserts the doc and `src/materia/prompts/` match.
- [x] README introduces the intended user and their bottleneck (sections 1 and 2)
- [x] README explains why solving it is valuable (section 3)
- [x] Improvement Changelog, clearly labelled, one entry per meaningful iteration, each connected to evidence (section 7). Six rows, every Evidence cell written by `make eval` from `results/scores.json` and asserted against it by test.
- [~] The cross check demonstrated on a real trajectory. **One branch of two.** The corrected figure branch fired on a real run and is featured trajectory 5: `Revenue!H5`, where the model received 8,704,573 and reported -6,102,169, and the report shows the measured figure. The dropped branch, where no `recompute_with_patch` result matches the proposed formula, has not fired on any of the 291 adjudications on disk. It is covered by unit tests only, and this line says so rather than implying a run demonstrated it.
- [x] At least one removed experiment with what it taught you. The report writer agent, changelog row **Removed**, with its trajectory at `trajectories/solution/C03_reporter.jsonl`.
- [x] Main failure mode (section 8), rewritten from three observed cases rather than the original prediction.
- [x] Hot take (section 9)

### 2. Reproduction guide

- [x] Written for a clean environment
- [x] Exact commands for solution, baseline and evaluation
- [x] Data required and expected output
- [x] Versions, approximate runtime, approximate cost. Cost is measured: $4.95 baseline, $6.42 solution, $0.41 and $0.54 per workbook.
- [~] **Actually tested from a fresh clone in a fresh venv.** Done in T25 for `verify`, `corpus`, `corpus-check` and `eval`, and nine untrue claims were fixed as a result. Not repeated since the baseline and solution sweeps landed, and the agent steps have never been run from a clean clone because that needs API credit this account no longer has.

### 3. Solution video, up to 5 minutes

- [~] Problem and simple baseline first. Scripted with real figures. Not recorded.
- [~] One realistic execution start to finish. Scripted. Not recorded.
- [~] Final comparison shown. Table in the script is filled from `results/` and asserted by test. Not recorded.
- [~] Changelog explained briefly. Scripted. Not recorded.
- [~] The change that contributed most, named, with the honest caveat that the agent loop arrived as one change and no ablation separated its parts. Not recorded.
- [~] One removed experiment, named. Not recorded.
- [ ] Under 5:00. **The video does not exist.** The script is finished and every number in it is real, but nothing has been recorded or uploaded.

### 4. Agent trajectories

- [x] Representative trajectory for every agent used. Adjudicator: 283 across `solution/`, `solution_full/` and `solution_scored/`. Baseline: 14 in `baseline/`. Reporter: `solution/C03_reporter.jsonl`. Repair: `solution_scored/C06_repair.jsonl`.
- [x] Followable from instructions to final result. Every trace opens with a `run_start` naming the workbook, cell, detector and model, and closes with a `verdict` and a `run_end`.
- [x] Tool responses visible, as `tool_result` records carrying the full payload.
- [x] The feedback that shaped the next step visible. Featured trajectory 5 is the clearest: the tool result at step 4 and what the model did with it at step 7.
- [ ] Retries visible. **No run has retried a hypothesis.** No trajectory in 291 adjudications calls `recompute_with_patch` more than once on one candidate. `trajectories/index.md` records this as absent rather than substituting a near miss.
- [x] Human checkpoints visible. `trajectories/solution_scored/C06_repair.jsonl` holds three `human_checkpoint` records from a real `--repair` session, two approved and one declined, and `corpus/C06.xlsx` is byte identical afterwards.
- [x] Rendered markdown versions committed. `trajectories/featured/` holds 1, 4 and 5. 2 and 3 do not exist and the index says why.

---

## Rubric coverage

| Criterion | Points | Where it is earned | Check |
| --- | --- | --- | --- |
| Problem & User Value | 15 | README 1 to 3. Named user, specific bottleneck, asymmetric cost of the miss. | [x] |
| Agent Solution & Engineering | 30 | Two evidence tools plus `submit_verdict`, three verdict schema with four report buckets, `INTENTIONAL` as first class, materiality gate. The recompute cross check enforced in code with both branches: a finding is dropped when no `recompute_with_patch` result matches the proposed formula, and corrected to the measured figure when one exists and disagrees. `docs/ARCHITECTURE.md` explains why each choice exists. **Largest block, weight the write up accordingly.** | [x] |
| End to End Quality | 20 | A real report a person would sign their name to. Consequence first ordering, evidence cards, suppressed count shown, repairs on a copy behind approval. Nothing reads as an AI draft. | [x] |
| Measured Improvement | 15 | `EVALUATION.md` 5 with generated numbers. Changelog isolates each contribution against the same corpus and metric. | [x] |
| Reproducibility | 15 | `REPRODUCTION.md`, no Excel dependency, seeded corpus, committed checksums, `make all`. | [x] |
| Hot Take / Insights | 5 | README 9. Observed failure mode turned into a generalisable rule. | [x] |

## Ground rules

- [x] Clear what existed before the competition and what was added (README 5)
- [x] Every tool used within its licence. Dependencies are `openpyxl`, `networkx`, `openai`, `pyyaml`, `pytest`, all permissively licensed.
- [x] Consequential actions gated. `--repair` defaults to no per finding, refuses a target equal to the source, and the byte identity of the input is asserted by test and confirmed on the real C06 session.
- [x] Human reviewer in the loop. The only action that writes anything is repair, and it prompts per finding.
- [x] Legal and ethical use case, responsible data handling.
- [x] Data is synthetic. Twelve workbooks generated from seed 20260828, no external data of any kind.
- [x] No credentials in the submission. Every blob in all 45 commits scanned for `sk-`, `gsk_` and `sk-ant-` shaped strings: none. `.env` was never committed. Keys are read from `os.environ` only, and provider errors are scrubbed of the account id before they reach a trajectory.
- [x] Every claim about results tied to submitted evidence. The results table, all four changelog rows, the video script comparison and the impact figures quoted in prose are asserted against `results/scores.json` and `corpus/manifest.json` by test.
- [ ] Judges have enough access to run it and reproduce the main result. **The repository is private.** See the final pass.

## Final pass

- [x] No `[TBD]` markers left in README, EVALUATION or REPRODUCTION. Enforced by `tests/test_evaluate.py::TestTheDocsCarryNoUnfilledPlaceholders`, which covers VIDEO_SCRIPT too.
- [x] Every number in every doc regenerated from `results/`, none hand typed. This audit found the opposite was true: the results table in `docs/EVALUATION.md` had read 100% and 7% for the baseline for six commits, against 83% and 71% in `results/headline.md`. Cause was a CLI test that passed `--results` to a temp directory but left `--document` defaulting to the real file, so every `make verify` overwrote the doc with a one finding fixture's scores. Fixed, and both the leak and the agreement are now asserted by test.
- [ ] `make all` run once more from a clean clone. Blocked: `make all` includes `baseline` and `solution`, which need API credit.
- [ ] Repo public or judge access granted. **`gh repo view` reports PRIVATE.** Nothing else on this list matters until this changes.
- [ ] Video uploaded, link works in an incognito window. Not recorded.
- [ ] Submitted with buffer before 18:00 UTC Sunday

## Self check questions from the brief

**1. Who experiences the bottleneck and why does solving it matter?**
The analyst who inherits a financial model they did not build and is accountable for a number they cannot verify by reading, and it matters because the existing tools flag 267 anomalies across twelve workbooks of which 14 are real, so the checking never gets done.

**2. Which design choices helped the agent solve the problem?**
Routing detection to code, judgement to the model, and verification of that judgement back to code: the model cannot state an impact it did not measure, `INTENTIONAL` is a first class verdict so declining is a success state, and the evidence for intent is put in the model's opening context rather than left for it to go looking for.

**3. Would the intended user consider this output high quality, or does it read as clearly AI generated?**
The report is a deterministic template over verified figures with a fixed width layout, no emoji and no model prose in any figure, and the one experiment that let a model write the report was removed for flipping the direction of every finding, so what ships reads as a tool rather than a chatbot.

**4. Which changes truly improved the outcome?**
The adjudicator loop took material precision from 5% to 93% and the materiality gate took it from 93% to 100%, with material recall unchanged at 93% throughout, and the loop's 88 points cannot be split between hypothesis testing, the recompute gate and the `INTENTIONAL` verdict because it arrived as one change and no ablation was run.

**5. Could another person reproduce this from a clean environment?**
The deterministic half yes, verified from a fresh clone in T25: `verify`, `corpus`, `corpus-check` and `eval` reproduce the corpus checksums and the Iteration 1 table exactly. The agent half is unverified from a clean clone, needs API credit, and is non deterministic run to run by an amount nobody has measured.

**6. What did you learn and how would it change what you build next?**
That the failure worth designing against is not hallucination but unchecked assertion, because the same shape appeared in the adjudicator inventing figures it had been handed, in the baseline judging intent without reading the comment on the cell, and in our own code globbing a directory past the workbook name in every record it read, so next time I would write the verification layer before the thing it verifies rather than after.

### Weakest answer

**Number 5.** The other five are backed by measurements in `results/` or code in the repo. Five is half a claim: the reproducibility rubric is worth 15 points and the part a judge is most likely to try, `make all`, has never been run end to end from a clean clone, because it needs API credit this account does not have. It is also the only answer that got worse during the build rather than better, since the corpus grew a baseline and a solution sweep that the T25 clean clone run predates.

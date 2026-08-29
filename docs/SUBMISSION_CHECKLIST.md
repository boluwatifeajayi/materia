# Submission checklist

Two purposes: make sure nothing required is missing, and make sure every rubric line has an artifact pointed at it. Judges score what they can find.

---

## Required deliverables

### 1. Complete solution code and improvement changelog

- [ ] Full project, runnable
- [ ] The instructions that shape each agent, in `docs/AGENT_INSTRUCTIONS.md`, both solution and baseline
- [ ] README introduces the intended user and their bottleneck (sections 1 and 2)
- [ ] README explains why solving it is valuable (section 3)
- [ ] Improvement Changelog, clearly labelled, one entry per meaningful iteration, each connected to evidence (section 7)
- [ ] The cross check demonstrated on a real trajectory, not only a constructed one. Both branches: a dropped finding and a corrected figure
- [ ] At least one removed experiment with what it taught you
- [ ] Main failure mode (section 8)
- [ ] Hot take (section 9)

### 2. Reproduction guide

- [ ] Written for a clean environment
- [ ] Exact commands for solution, baseline and evaluation
- [ ] Data required and expected output
- [ ] Versions, approximate runtime, approximate cost
- [ ] **Actually tested from a fresh clone in a fresh venv**

### 3. Solution video, up to 5 minutes

- [ ] Problem and simple baseline first
- [ ] One realistic execution start to finish
- [ ] Final comparison shown
- [ ] Changelog explained briefly
- [ ] The change that contributed most, named
- [ ] One removed experiment, named
- [ ] Under 5:00

### 4. Agent trajectories

- [ ] Representative trajectory for every agent used, adjudicator, reporter, baseline
- [ ] Followable from instructions to final result
- [ ] Tool responses visible
- [ ] The feedback that shaped the next step visible
- [ ] Retries visible (featured trajectory 3)
- [ ] Human checkpoints visible
- [ ] Rendered markdown versions committed so no judge has to run anything

---

## Rubric coverage

| Criterion | Points | Where it is earned | Check |
| --- | --- | --- | --- |
| Problem & User Value | 15 | README 1 to 3. Named user, specific bottleneck, asymmetric cost of the miss. | [ ] |
| Agent Solution & Engineering | 30 | Two evidence tools plus `submit_verdict`, three verdict schema with four report buckets, `INTENTIONAL` as first class, materiality gate. The recompute cross check enforced in code with both branches: a finding is dropped when no `recompute_with_patch` result matches the proposed formula, and corrected to the measured figure when one exists and disagrees. `docs/ARCHITECTURE.md` explains why each choice exists. **Largest block, weight the write up accordingly.** | [ ] |
| End to End Quality | 20 | A real report a person would sign their name to. Consequence first ordering, evidence cards, suppressed count shown, repairs on a copy behind approval. Nothing reads as an AI draft. | [ ] |
| Measured Improvement | 15 | `EVALUATION.md` 5 with generated numbers. Changelog isolates each contribution against the same corpus and metric. | [ ] |
| Reproducibility | 15 | `REPRODUCTION.md`, no Excel dependency, seeded corpus, committed checksums, `make all`. | [ ] |
| Hot Take / Insights | 5 | README 9. Observed failure mode turned into a generalisable rule. | [ ] |

## Ground rules

- [ ] Clear what existed before the competition and what was added (README 5)
- [ ] Every tool used within its licence
- [ ] Consequential actions gated: repair requires approval, writes to a copy only
- [ ] Human reviewer in the loop for anything that could affect someone
- [ ] Legal and ethical use case, responsible data handling
- [ ] Data is synthetic or public, no client or private financial data
- [ ] No credentials in the submission, API key from environment only
- [ ] Every claim about results tied to submitted evidence
- [ ] Judges have enough access to run it and reproduce the main result

## Final pass

- [ ] No `[TBD]` markers left in README, EVALUATION or REPRODUCTION
- [ ] Every number in every doc regenerated from `results/`, none hand typed
- [ ] `make all` run once more from a clean clone
- [ ] Repo public or judge access granted
- [ ] Video uploaded, link works in an incognito window
- [ ] Submitted with buffer before 18:00 UTC Sunday

## Self check questions from the brief

Answer each in one sentence before submitting. If any answer is weak, that is where to spend remaining time.

1. Who experiences the bottleneck and why does solving it matter?
2. Which design choices helped the agent solve the problem?
3. Would the intended user consider this output high quality, or does it read as clearly AI generated?
4. Which changes truly improved the outcome?
5. Could another person reproduce this from a clean environment?
6. What did you learn and how would it change what you build next?

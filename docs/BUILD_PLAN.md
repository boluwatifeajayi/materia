# Build plan

Working document, not a deliverable. Delete before submitting or leave it, it does no harm.

**Deadline: Sunday 31 August, 18:00 UTC, which is 19:00 UK time.**

Two rules that decide whether this ships:

1. **Harness before agent.** The corpus, the recompute engine and the evaluator come first. If the agent underdelivers you still have a benchmark, a baseline, and a real result. If you build the agent first and run out of time on the harness, you have a demo with no evidence and you lose 30 rubric points.
2. **Every cutoff below is real.** If a component is not working at its cutoff, ship the degraded version and move on. A complete submission with a mediocre component beats an incomplete submission with a beautiful one. Judges cannot score what is not there.

---

## Friday evening (tonight), 4 to 5 hours

Goal: a corpus exists and a deterministic engine can score it.

- [ ] Repo, `pyproject.toml`, `Makefile` skeleton with the targets named in `REPRODUCTION.md`
- [ ] Preflight validator with named rejection reasons
- [ ] Parser plus R1C1 normaliser
- [ ] Recompute engine over the supported grammar, unit tested. **This is the load bearing piece. Test it properly, everything else depends on its numbers being right.**
- [ ] Corpus generator producing one workbook
- [ ] `make verify` passes

**Cutoff 01:00.** Stop. The recompute engine being correct matters more than anything you would gain from another hour tonight.

## Saturday, full day

Morning, goal: full corpus and full evaluation loop, no agent yet.

- [ ] Corpus generator to 12 workbooks with the manifest
- [ ] Mutation injector, five in taxonomy families plus two out of taxonomy
- [ ] True materiality computed per mutation via the recompute engine
- [ ] Dependency graph
- [ ] Five detectors
- [ ] Evaluator producing the headline table
- [ ] **Run the detectors alone against the corpus. That is changelog Iteration 1.**

**Cutoff 14:00.** By this point you must have a real number in the changelog. If detector-only precision comes back high, the thesis is wrong and you reframe now rather than on Sunday night.

Afternoon, goal: baseline measured, agent loop working.

- [ ] Baseline agent harness and prompt
- [ ] **Run the baseline over all 12. Record it. This is your comparison and it is done before your solution exists, which removes any suspicion you tuned it.**
- [ ] Trace capture
- [ ] Adjudicator agent, `recompute_with_patch` and `inspect_range` tools
- [ ] Three verdict schema with `INTENTIONAL` as first class, `IMMATERIAL` owned by the gate
- [ ] Reporter with the tool result cross check that drops unbacked figures

**Cutoff 22:00.** Agent loop runs end to end on one workbook. Not polished, just working.

Evening:

- [ ] Full solution run over 12, no materiality gate. Iteration 2 in the changelog.
- [ ] Add the materiality gate. Run again. Iteration 3.
- [ ] Fill in every changelog row you have evidence for

**Cutoff 01:00.** You now have a complete result. Everything after this is improvement, and improvement is optional.

## Sunday

Morning, goal: the deliverables that are worth 35 points and are usually rushed.

- [ ] Reporter output made presentable, the funnel and evidence cards
- [ ] Pick and render the four featured trajectories, write their preambles
- [ ] `make all` from a clean clone in a fresh venv. **Actually do this. Reproducibility is 15 points and this is the only way to know.**
- [ ] Paste generated results into `EVALUATION.md`
- [ ] Finalise the changelog including the removed experiment

**Cutoff 13:00.** Docs and repo are submission ready. If they are not, stop building and finish them.

Afternoon:

- [ ] `make eval-repeat N=3` for the variance table
- [ ] Record the video. Allow two takes. Do the live run in advance as a fallback.
- [ ] Final read through of README and EVALUATION for `[TBD]` markers

**Cutoff 16:00.** Submit. Two hours of buffer before the deadline, because platforms fail and files are large.

Any time left after 16:00 goes to the terminal report presentation. Not to new features.

---

## Cut list, in order

If you fall behind, cut from the top:

1. `make eval-repeat` variance table. Nice, not scored directly.
2. Detectors `M4` and `M5`. Three detectors is enough to prove the thesis. Reduce the corpus to match and say so.
3. Sensitivity analysis at three thresholds. Report one threshold.
4. The report writer agent. Render findings from a template instead. Saves an agent, costs a little polish.
5. Human time measurement. Report it as not measured rather than estimated. **Never report an unmeasured number.**

**Never cut:** the two clean control workbooks, the out of taxonomy mutations, the baseline run, the recompute gate. Each one of those is load bearing for either the honesty of the result or the core claim.

---

## Claude Code kickoff prompt

Paste this at the start of the first session. Point it at this repo with all docs present.

```
Read README.md, docs/ARCHITECTURE.md, docs/EVALUATION.md and
docs/REPRODUCTION.md before writing any code. They specify what we are building and how it is
evaluated. Treat them as the spec.

Build in this order, and do not skip ahead:

  1. Preflight validator (docs/ARCHITECTURE.md section 1)
  2. Parser and R1C1 normaliser (section 2)
  3. Recompute engine (section 6) with thorough unit tests

The recompute engine is load bearing: every impact figure in the final
report and the ground truth materiality of every seeded mutation both come
from it. Test it against hand-computed expected values for each supported
function before moving on.

Supported grammar is in README.md section 6. Do not extend it. The preflight
validator rejects anything outside it, and that constraint is what makes the
engine tractable.

Stop after these three and show me the test output.
```

Then, per component, work in the same pattern: point at the doc section, build, test, review, next. The docs are the spec, so keep them updated when a design decision changes rather than letting the code and the docs diverge. The docs are also the submission.

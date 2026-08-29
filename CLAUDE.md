# Working agreement

Read this before doing anything. It governs every session in this repo.

---

## 1. One task at a time

`TASKS.md` holds the full task list. Work on **exactly one task per session**.

When you start a task:
1. Read the task's entry in `TASKS.md` and its prompt in `PROMPTS.md`
2. Read any doc sections the task references, before writing code
3. Build it
4. Test it (section 3 below)
5. Commit and push (section 4)
6. Update the task's status in `TASKS.md` to `DONE`
7. **Stop.** Report what you did, what the tests showed, and what the next task is.

Do not start the next task. Wait for me to tell you to move on. If you finish early and see an obvious improvement, say so in your report rather than doing it.

If a task turns out to be bigger than expected, stop and tell me before expanding scope. If a task is blocked by something in an earlier task, stop and tell me rather than working around it.

## 2. The docs are the spec

`README.md`, `docs/ARCHITECTURE.md`, `docs/EVALUATION.md`, `docs/AGENT_INSTRUCTIONS.md` and `docs/REPRODUCTION.md` define what is being built. Treat them as authoritative.

If you need to make a design decision they do not cover, make it, then **update the relevant doc in the same commit**. The docs are also the hackathon submission, so they cannot drift from the code.

If you think a doc is wrong, say so and wait. Do not silently deviate.

## 3. Test everything you build

Every task ends with tests that actually ran. Not "this should work". Ran, with output.

- **Unit tests** with `pytest` for any logic: the recompute engine, the parser, the graph, the detectors, the scorer. The recompute engine in particular needs tests against hand-computed expected values, because every impact figure in the submission depends on it being right.
- **CLI smoke tests** for anything with a command: actually invoke it and show the output.
- **Never report a number you did not measure.** This applies to you as much as to the agent you are building. If you did not run it, say you did not run it.

Playwright and browser testing are not relevant to this project unless an HTML report view gets added (optional task T29). Everything else is pytest plus running the CLI.

Show me the test output in your report. If tests fail and you cannot fix them within the task, stop and tell me.

## 4. Commits and pushes

After each completed task, commit and push.

```bash
git add -A
git commit -m "<type>: <short description>"
git push
```

Types: `feat`, `fix`, `test`, `docs`, `refactor`, `chore`.

**Commit message rules, strictly:**
- No `Co-Authored-By: Claude` trailer
- No `🤖 Generated with Claude Code` footer
- No mention of Claude, AI, or any assistant anywhere in the message
- Subject line under 72 characters, imperative mood
- Body only if the change needs explanation, plain sentences

`gh` is authenticated. You can push, create branches, and read repo state. Work directly on `main` unless I say otherwise, since this is a solo three day sprint and branch overhead is not worth it.

## 5. Writing style for anything a human reads

Applies to README, docs, code comments, CLI output, commit messages, report templates, and anything else with prose in it.

- **No em dashes.** Not anywhere. Use commas, colons, parentheses, or a full stop.
- No "not just X, but Y" constructions
- No "it's worth noting", "delve", "leverage" as a verb, "seamless", "robust" as filler
- Plain sentences. Say the thing.
- CLI output should read like a tool wrote it, not like a chatbot. No emoji unless it carries information.

The submission is judged partly on whether the output "reads as clearly AI generated". Assume every string is judged.

## 6. API keys and cost

- Keys come from environment variables only. `GROQ_API_KEY`, `OPENAI_API_KEY`.
- **Never commit a key.** `.env` is gitignored. Check before every commit.
- `.env.example` holds the variable names with empty values, and is committed.

**Groq free tier care.** Groq is the dev-loop provider. Its free tier is generous but finite, roughly 1M tokens/day with per-minute rate limits.

- During development, run agent loops against **one or two workbooks at most**. Never loop the full 12 workbook corpus on Groq while debugging.
- If you hit a rate limit, back off and tell me. Do not add retry loops that silently burn the quota.
- Before any run that will make more than about 20 model calls, tell me the estimated call count and wait for approval.
- OpenAI is used only for the final scored runs (baseline and solution), which are explicitly flagged in the task list.

## 7. Never do these

- Never commit `.env`, keys, or any credential
- Never write to a workbook in `corpus/` outside the generator, and never modify a user's input workbook anywhere in the product
- Never hand type a number into `README.md` or `docs/EVALUATION.md` that should come from `results/`
- Never report Groq-run numbers as results. Dev loop only. See `docs/ARCHITECTURE.md` section 9.
- Never mark a task `DONE` in `TASKS.md` if its tests did not pass

## 8. Your report format after each task

Keep it short.

```
TASK Tnn: <name> - DONE

Built:
  <two or three lines on what now exists>

Tests:
  <command run>
  <result: N passed, or the actual output>

Committed: <commit subject>
Pushed: yes

Next: Tnn+1 <name>
Notes: <anything I should know, or "none">
```

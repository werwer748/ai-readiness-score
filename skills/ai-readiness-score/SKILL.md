---
name: ai-readiness-score
description: Score how ready a code repository is for AI coding agents (Claude Code, Codex, Cursor) on a 100-point, 7-category rubric (Navigation, Context Quality, Tribal Knowledge, Dependency Mapping, Verification Gates, Freshness, Agent Outcomes) and produce an HTML dashboard showing where agents get lost. Use this whenever the user asks how AI-ready, agent-ready or Claude-friendly a repo is, wants CLAUDE.md / AGENTS.md / onboarding docs audited or graded for agents, asks why agents keep struggling or going in circles in their codebase, or says things like "AI readiness score", "agent readiness", "AI 준비도", "에이전트 친화도 점수", "이 repo 에이전트가 작업하기 좋은 상태야?". Pass `quick` to skip the live agent probes.
argument-hint: "[path] [quick]"
---

# AI-Readiness Score

Measure how easily an AI coding agent can find its way around a repository, and show the team where agents get lost. Categories 1-6 check the **inputs** an agent relies on (structure, docs, verification). Category 7 checks the **outcome**: fresh, context-free agents try to answer real questions about the repo and we watch where they wander.

| # | Category | Max | Question it answers |
|---|---|---|---|
| 1 | Navigation | 15 | Can an agent find entry points and know where things live? |
| 2 | Context Quality | 20 | Are CLAUDE.md / AGENTS.md / guides dense, specific and correct? |
| 3 | Tribal Knowledge | 20 | Is knowledge that lives in people's heads written down? |
| 4 | Dependency Mapping | 15 | Are module boundaries clear, without cycles or god files? |
| 5 | Verification Gates | 15 | Can an agent check its own work (tests, linters, CI)? |
| 6 | Freshness | 10 | Do the docs still match the code? |
| 7 | Agent Outcomes | 5 | Do fresh agents succeed on the first try? |

Scores must be reproducible and defensible, so: scripts gather the facts and do the arithmetic, you make the judgment calls, and every judgment cites evidence (a path, ideally `path:line`).

## Ground rules

- **Read-only on the target repo.** Never edit, create or delete files in it. Everything this skill writes goes to the report directory (`out_dir` from the scan), which lives outside the repo so later probes can never read old answers.
- **Language.** Write verdicts, findings, fixes and the chat summary in the language the user is speaking. Set `lang` in score.json to `ko` for Korean and `en` otherwise (it switches the dashboard labels).
- **Privacy.** Session logs contain the user's own prompts. Use them to derive questions, but never copy raw prompt text into score.json, the report or the chat; write generalized questions instead.
- **Budget.** For categories 1-6, read the agent docs and READMEs fully, then spot-check at most ~15 other files. The scan already has the facts; don't re-derive them by hand.

## Step 0: Set up

1. **Target and mode.** The target repo is the path argument, or the current working directory if there is none. The mode is `full` unless the user passed `quick` (or asked for a fast check), which skips Step 3.
2. **Python.** Find a Python 3.8+ interpreter once and reuse it. Try `python3 --version`, then `python --version`, then `py -3 --version` (Windows), and use the first one that prints `Python 3.8` or higher. If there is none, tell the user to install Python 3.8+ (python.org, `brew install python`, or `winget install Python.Python.3.12`) and stop.
3. **Paths.** `<SKILL_DIR>` below means this skill's base directory, which is shown when the skill loads. Quote every path, because they may contain spaces. Commands have the form `<python> "<SKILL_DIR>/scripts/x.py" ...`, which works in bash and PowerShell alike.

## Step 1: Scan

```
<python> "<SKILL_DIR>/scripts/scan_repo.py" "<repo>"
```

The scan prints JSON facts and a few fields you will reuse:
- `run_id`: tags this run's probes.
- `skill_version`: copy it into score.json.
- `out_dir`: where the report goes; the scan creates it.
- The evidence sections: `agent_docs`, `docs`, `navigation`, `manifests`, `verification`, `env`, `doc_refs`, `churn`, `sizes`, `dependencies`, `warnings`.

The scan is regex-level. Treat `doc_refs.missing_*` and dependency cycles as candidates you confirm before penalizing. Things like runtime-generated paths, example names and package import paths are false positives.

## Step 2: Score categories 1-6

Read `references/rubric.md`. It has the sub-items, point anchors and the scan fields each one uses. Then read the agent docs and READMEs, spot-check claims against the code, and score each category.

For each category, produce these (see `references/schema.md`):
- `score`
- a one-sentence `verdict`
- `findings` (strengths and gaps, each with `evidence`)
- `fixes` (concrete and small, e.g. "add a 3-line directory map for `plugins/` to CLAUDE.md")

## Step 3: Agent Outcomes probes (full mode only)

Read `references/probes.md` and follow it exactly. It has the probe templates, the subagent prompt, the forbidden paths and the grading rules. In outline:

1. **Collect probe sources.**
   - Log-derived candidates: `<python> "<SKILL_DIR>/scripts/extract_log_tasks.py" "<repo>"`.
   - Human-written custom probes, if `<repo>/.ai-readiness/probes.md` exists.
2. **Build the probe set.** For each of categories 1-5, take 3 base probes. Then add up to 7 extra: custom probes first, then log probes. The number of log probes is k = min(7 − custom, floor(log2(n+1))), where n is that category's usable log candidates.
3. **Fix the expected answers.** Settle the expected answer for every probe before launching anything, and keep the answers in your context only.
4. **Launch the probes.** Launch in batches of up to 10 parallel `Explore` subagents with `model: haiku` and description `probe:<run_id>:<probe_id>`, using the prompt in probes.md. Wait for each batch to finish.
5. **Measure.** Run `<python> "<SKILL_DIR>/scripts/count_probe_steps.py" <run_id> --repo "<repo>"`. It gives each probe's step count, trail, dead ends, revisits and whether it touched a forbidden path.
6. **Judge.** Decide `correct` for each probe. For probes that were not clean passes, write a short `lost_at`.

The renderer, not you, converts `correct` + steps into pass / partial / fail and computes category 7. For path answers it counts the step at which the agent reached the answer, not the total.

## Step 4: Write score.json and render

Write `<out_dir>/score.json` following `references/schema.md`. Leave category 7's `score` and the `total` for the renderer: it grades the probes, computes category 7 as 5 × the mean of the per-category probe means, sums the total, and writes the finalized JSON back.

```
<python> "<SKILL_DIR>/scripts/render_dashboard.py" "<out_dir>/score.json" --open
```

It prints the final numbers and the report path. Use those numbers in the chat; don't recompute them. Drop `--open` if the user is on a remote or headless machine.

## Step 5: Tell the user

Keep the chat short. The dashboard holds the detail. Send:
- the total (and `/95, category 7 N/A` in quick mode)
- a 7-row table: category, score / max, a few words on why
- the top 3 fixes with estimated point impact
- where agents got lost, in one or two sentences (full mode)
- the report path, plus a note that probe results are a trend indicator and move a little between runs

If the scan printed `warnings`, mention the ones that affect scores (for example, "not a git repository: Freshness is estimated").

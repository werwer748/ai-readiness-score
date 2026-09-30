# Rubric for categories 1-6

Each category is split into sub-items with anchored points. Score each sub-item at an anchor, or between two anchors when the evidence sits between them. The category score is the sum of its sub-items. Where a sub-item says "scan:", start from that field of the scan JSON, then confirm by reading.

What the categories share: an agent starts every session with no memory. Anything it needs, it must either find in the repo or rediscover by trial and error, and rediscovery is where time, tokens and correctness are lost. Score from the point of view of a capable engineer who has never seen this repo and has only the files.

Contents: [1 Navigation](#1-navigation-15) · [2 Context Quality](#2-context-quality-20) · [3 Tribal Knowledge](#3-tribal-knowledge-20) · [4 Dependency Mapping](#4-dependency-mapping-15) · [5 Verification Gates](#5-verification-gates-15) · [6 Freshness](#6-freshness-10) · [Log signals](#using-log-signals)

---

## 1 Navigation (15)

**1a Entry points are discoverable (5).** scan: `navigation.entry_points`, `manifests`
- 5: the entry points (app start, CLI, server, plugin manifest, main routes) are named with paths in README or the agent doc.
- 3: not documented, but findable by convention (scan found them) and unambiguous.
- 1: several plausible entry points and nothing says which one is real.
- 0: none identifiable.

**1b Directory map (5).** scan: `files.by_top_dir`, doc headings
- 5: README or the agent doc explains what the top-level directories are for, covering those that hold most of the files.
- 3: partial map, or only the main source dir is explained.
- 0: no map.

**1c Structural clarity (5).** scan: `files`, `sizes`, `navigation.monorepo_signals`
- 5: conventional, consistent layout; names say what things are; each package in a monorepo has its own short README.
- 3: mostly clear, with a dumping ground or two (`utils/`, `misc/`, `common/` holding a large share of the code) or inconsistent naming.
- 1: flat or tangled; you need to read code to know where anything goes.

## 2 Context Quality (20)

**2a Agent doc exists (3).** scan: `agent_docs`
- 3: `CLAUDE.md` or `AGENTS.md` (or an equivalent the team's agent reads) at the repo root.
- 1: only nested or tool-specific rules (`.cursor/rules`, `.claude/rules` without a root doc).
- 0: none.

**2b Actionable commands (6).** scan: `manifests`, `doc_refs.missing_commands`, `verification.ci`

There are four command kinds: install/setup, run/dev, test, lint/typecheck/build. Take 1.5 per kind that is written in an agent doc or README **and** resolves (the script or target exists, or the tool is present). Commands you can see only in CI or package.json don't count here; they count in 5.

**2c Density (5).**
- 5: short, specific, project-only facts (paths, commands, constraints, "do X, not Y because Z"). Every line would change what an agent does.
- 3: useful but padded with generic advice ("write clean code", "follow best practices"), or the same rules repeated across several files.
- 1: mostly generic, or so long (hundreds of undifferentiated lines) that the signal is buried.

**2d Accuracy (6).** scan: `doc_refs.missing_paths`

Pick 5 concrete, checkable claims from the agent docs (paths, commands, "X lives in Y", "we use library Z") and verify each against the code. Take 1.5 off per false claim, with a floor of 0. Confirmed dead references from the scan count as false claims. With no agent doc, score 2d as 0 and say so.

## 3 Tribal Knowledge (20)

**3a Setup prerequisites (5).** scan: `env`, `manifests`
- 5: runtime/toolchain versions, required env vars (an `.env.example` or a documented list), external services and OS quirks are all written down.
- 3: some of these, or they're only implied by config files.
- 0: an agent has to guess.
- If `env.tracked_env_files` is non-empty, add a gap finding about possibly committed secrets. Don't print the values.

**3b Pitfalls and constraints (5).**
- 5: known gotchas, limits and "never do X" rules are documented with reasons.
- 3: a few rules without reasons.
- 0: none.

**3c Design rationale (5).** scan: `adr_like`, docs
- 5: non-obvious architecture choices are explained (ADRs, a decisions doc, or why-comments at the relevant code).
- 3: some rationale scattered in docs or commit history.
- 0: none. An agent can't tell a deliberate choice from an accident.

**3d Domain vocabulary (5).**
- 5: domain terms that appear in code (entity names, business concepts) are defined somewhere: a glossary, doc comments, or a model file with explanations.
- 3: partially.
- 0: undefined jargon.
- Score 5 if the domain is purely technical and needs no glossary.

## 4 Dependency Mapping (15)

**4a Module boundaries (5).**
- 5: clear layers or packages with a documented direction of dependency (or it is obvious from the structure), and a short architecture or data-flow description.
- 3: boundaries exist but are undocumented.
- 1: everything imports everything.

**4b Cycles (5).** scan: `dependencies.cycles_total`, `dependencies.cycles`
- 5: no cycles.
- 3: 1-2 small cycles (2-3 files).
- 1: larger or many cycles.
- Confirm a reported cycle by opening the imports before penalizing it.
- For languages the scan doesn't graph (`files_by_language.other` dominates), judge by sampling imports. Cap the sub-item at 3 and note the limitation.

**4c God files (5).** scan: `sizes.largest`, `sizes.over_800_lines`, `dependencies.top_fan_in`

A god file has more than 800 lines of hand-written logic, or is imported by at least max(10, 15% of code files) and mixes several responsibilities. Generated, vendored, fixture and test-data files don't count; neither does a big test file on its own.
- 5: none.
- 3: 1-2.
- 1: several.

## 5 Verification Gates (15)

**5a Tests exist and run with one command (6).** scan: `verification.test_files`, `test_dirs`, manifests
- 6: meaningful tests covering the main code paths, runnable with one documented command.
- 4: tests exist and are runnable, but are thin or the command is undocumented.
- 2: a handful of tests.
- 0: none.

**5b Static checks (4).** scan: `verification.configs`, `tsconfig_strict`

Take 1.5 for a linter, 1 for a formatter, 1.5 for type checking (TS `strict: true`, mypy or pyright, or a statically typed language). Cap at 4.

**5c CI gate (5).** scan: `verification.ci`
- 5: CI runs tests **and** static checks on pull requests.
- 3: CI runs one of them, or doesn't run on PRs.
- 1: only pre-commit hooks locally.
- 0: nothing automatic.

## 6 Freshness (10)

**6a Doc recency vs code (5).** scan: `agent_docs[].commits_since`, `head`
- 5: the main agent doc changed within the last 20 commits (or the repo has fewer commits than that).
- 3: within the last 100.
- 1: older.
- Non-git (`estimated: true`): judge from mtimes, cap at 3, and say it is estimated.

**6b Reference integrity (5).** scan: `doc_refs`

Start at 5 and take 1 off per **confirmed** dead reference (a path or command in docs that no longer exists), with a floor of 0. First exclude false positives: runtime-generated files, example placeholders (`Foo.tsx`), package import paths, paths inside data formats. Probes that failed because they followed stale docs (`drift: true`) also count here, one point each.

## Using log signals

When `extract_log_tasks.py` returns candidates, look for the same question asked two or more times (e.g., three separate sessions asking how to set up the JDK). Repeated questions are direct evidence that knowledge wasn't externalized. Deduct in the matching sub-item (usually 3a, 3b or 1b) and cite it in generalized form ("setup of the JDK path was asked in 3 separate sessions"). Never quote the prompt.

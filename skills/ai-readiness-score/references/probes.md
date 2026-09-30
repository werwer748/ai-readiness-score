# Agent Outcomes probes

A probe is one question about the repo, answered by a fresh subagent that has nothing but the repo. We measure whether it gets the answer right and how many steps it needs, so the dashboard can show where agents get lost. Probes cover categories 1-5; category 6 shows up as `drift` when a probe is misled by stale docs.

Contents: [1 Build the probe set](#1-build-the-probe-set) · [2 Base templates](#2-base-templates) · [3 Log-derived probes](#3-log-derived-probes) · [4 Expected answers](#4-expected-answers) · [5 Launch](#5-launch) · [6 Measure and judge](#6-measure-and-judge)

## 1 Build the probe set

For each category 1-5:

1. **Base probes: 3.** Use the first three templates below that apply to this repo, in order.
2. **Extra probes: at most 7.** Fill them in this order:
   - **Custom probes** from `<repo>/.ai-readiness/probes.md` for this category (format in `schema.md`). Take them as written.
   - **Log probes**: k = min(7 − custom_count, floor(log2(n + 1))), where n is the number of usable log candidates for the category. So n = 1-2 gives 1 probe, 3-6 gives 2, 7-14 gives 3, 15-30 gives 4, and so on.
3. **Cap: 10 per category.** The total ranges from 15 (no logs, no custom probes) to 50.

Probe IDs are the category letter + number + a sequence: `N1-01` (Navigation), `C2-01` (Context), `T3-01` (Tribal), `D4-01` (Dependency), `V5-01` (Verification). Set `kind` to `base`, `custom` or `log`.

## 2 Base templates

Slots such as `<area>` get filled by rules, not by what is easiest to answer. The point is to test the repo, not to confirm what you already found. Use the scan like this:
- `<area>` = the first `churn.top_areas` entry that is source code, not docs, tests or config. Without churn, use the top-level source directory with the most files.
- `<module>` = the first `dependencies.top_fan_in` path inside `<area>`, or the overall first one.
- `<unit>` = what this repo adds repeatedly: route, page, command, handler, screen, migration, endpoint or plugin. Pick it from the code.

If a template can't apply (e.g., there is no dev server for a library), skip it and use the next one in the list. Write each question as a user would ask it, in the language of the repo's docs (English if unclear).

**1 Navigation**
1. "Where does the application start? Give the entry-point file(s)."
2. "Where is the code for `<area>`, and which file is its main entry point?"
3. "To add a new `<unit>` like the existing ones, which files would you create or edit?"
4. "Which directory holds `<concern>`?" (`<concern>` is one of: database access, UI components, configuration, API clients, whichever exists.)

**2 Context Quality**
1. "What command starts the project locally for development?"
2. "What command builds (or packages) the project?"
3. "What rules does this project say an agent must follow when changing code?" Use this only if the agent docs state rules. The expected answer is the documented rules.
4. "What command runs the linter or type checker?"

**3 Tribal Knowledge**
1. "What has to be installed or configured before the project will build and run locally (runtime versions, env vars, services)?"
2. "What is a known pitfall or constraint to keep in mind when changing `<area>`?" Use this only if one is documented or clearly encoded in code comments. Otherwise skip it.
3. "What does `<domain term>` mean in this codebase, and where is it defined?" `<domain term>` is a domain-specific type or concept name that appears in 3 or more files.
4. "Why is `<non-obvious choice>` done this way?" Use this only if the rationale exists somewhere.

**4 Dependency Mapping**
1. "If you change `<module>`, which files depend on it directly?"
2. "Which internal modules does `<module>` depend on?"
3. "Where is the single source of truth for `<shared concept>` (config, shared types, API client, or DB schema)?"

**5 Verification Gates**
1. "What checks must pass before a change can be merged? How do you run them locally?"
2. "How do you run only the tests for `<area>` (a single file or test)?"
3. "Where are the tests for `<module>`, and which test framework do they use?"

## 3 Log-derived probes

`extract_log_tasks.py` returns candidates: `text` (a user's request, clipped), `touched` (repo files the session worked on), `commands`, and `missing` (touched files that no longer exist). For each candidate:

1. **Discard** it if it isn't about locating or understanding something in the repo (chit-chat, pure config of Claude itself, "commit this"). Also discard it if all its touched files are in `missing`, or if nothing checkable remains.
2. **Classify** it into one category from 1 to 5, by what an agent would need to succeed. "Where is X handled?" is 1. "How do I run the app?" is 2. "Why does build fail on my machine?" is 3. "What breaks if I change X?" is 4. "How do I test X?" is 5.
3. **Rewrite** it as a neutral question, without personal details, names, secrets or pasted content. For example, a request like "fix the thing where xlsx preview is blank for big files" becomes "Where is the size limit that decides whether a spreadsheet preview is rendered?"
4. **Set the expected answer** from the touched files and commands, re-verified against the current code. If the code moved, use the current location. If you can't verify it, discard the candidate.

Count usable candidates per category (n), compute k, and take the k most recent ones, skipping near-duplicates.

## 4 Expected answers

Settle the expected answer for every probe **before** launching any, and keep it in your context only. Don't write it to disk until the probes are done: a probe that finds a file with the answers isn't measuring the repo.

Use this shape:
```json
{"must_include": ["src/app/router.ts"], "any_of": ["npm run dev", "pnpm dev"], "note": "either runner is fine"}
```
- `must_include`: paths or commands the answer has to contain. Keep it to the one to three items that matter.
- `any_of`: acceptable alternatives, where at least one must appear (optional).
- A path matches if either path ends with the other, compared as POSIX paths (`XlsxReader.kt` matches `src/main/kotlin/format/XlsxReader.kt`).
- Commands match if they are equivalent (`npm test` = `npm run test`; `./gradlew test` = `gradlew test`).

## 5 Launch

Launch probes with the Agent tool, in batches of up to 10 in a single message, and wait for the batch to finish before starting the next one.

- `subagent_type`: `Explore`. If it isn't available, use `general-purpose`, keep the same prompt and note the substitution.
- `model`: `haiku`. A lighter model makes the test conservative: if haiku can find it, a stronger agent will too.
- `description`: `probe:<run_id>:<probe_id>`, exactly. The step counter finds transcripts by this string.
- `prompt`: the template below, with `<REPO>`, `<CONFIG_DIR>` (the Claude config dir, normally `~/.claude`) and `<QUESTION>` filled in.

```
You are exploring an unfamiliar code repository to answer one question. Use only what is in the repository.

Repository root: <REPO>
Question: <QUESTION>

Rules:
- Explore read-only, inside the repository root: Read, Glob, Grep, or read-only shell commands (ls, find, cat, head, grep). Do not modify files, install anything, or run builds or tests.
- Do not open or search <REPO>/.ai-readiness/, anything under <CONFIG_DIR>, or any temp/scratchpad directory. They are not part of the project.
- Stop as soon as you are confident. If you have used 20 tool calls, stop and answer with what you have.
- If the repository does not contain the answer, say so. Do not guess.

Reply with only this JSON (keep "answer" under 120 words; paths relative to the repository root):
{"answer": "...", "paths": ["..."], "commands": ["..."], "confidence": "high|medium|low"}
```

Don't hint at the answer, don't mention scoring, and don't add context beyond this prompt. Unhelped discovery is exactly what we are measuring.

Read-only shell commands are allowed on purpose. Real agents use `ls` and `find`, and in testing haiku used them even when told not to. They count as steps like any other tool call.

## 6 Measure and judge

When every batch has finished, run:
```
<python> "<SKILL_DIR>/scripts/count_probe_steps.py" <run_id> --repo "<repo>"
```

For each probe it prints:
- `steps`: tool calls, not counting the final handback
- `trail`: tool and target, in order
- `dead_ends`: steps with empty or error results
- `revisits`: files read more than once
- `invalid`: true if it read or searched a forbidden path (merely excluding it, as in `grep -v .ai-readiness`, is fine)
- `final`: the parsed JSON answer

If it finds no transcripts (for example, a different Claude config dir), fall back to the `tool_uses` count in each agent's completion notification for `steps` and leave `trail` empty.

Then fill in each probe's fields for score.json:
- `correct` (bool): the answer satisfies `must_include` and `any_of` by the matching rules above. If the machine match fails, read the answer text once more for an equivalent answer written differently, but don't be lenient. A partially right answer that is missing a required item is `false`. "Not in the repo" is correct only if that is the truth.
- `steps`, `trail`, `dead_ends`, `revisits`, `invalid`: copy from the counter output.
- `answer`: a one-line summary of what the probe said.
- `lost_at` (for anything that isn't a clean pass, or took over 10 steps): one short phrase naming where it went off course, using step numbers from the trail. For example: "steps 6-8: searched editor/ for the registration that lives in plugin.xml".
- `drift` (bool): true if the probe followed a doc to a path or command that no longer exists or is wrong.

Don't set `result` or `score`; `render_dashboard.py` derives them:

**Cost.** When `expected` contains paths, the renderer computes `answer_step`: the step by which every expected path had been reached, either read or named by a search or listing. That step is the cost. Agents often keep exploring after they have found the answer so they can write a fuller reply, and that extra exploring isn't getting lost. When `expected` is only commands, or a path was never reached, the cost is the total `steps`.

| Result | Rule | Points |
|---|---|---|
| pass | correct, cost ≤ 10 | 1 |
| partial | correct, cost 11-20 | 0.5 |
| fail | wrong, or total steps > 20 | 0 |
| invalid | touched a forbidden path | excluded |

The renderer also computes category 7 as 5 × the mean of the per-category means, so a category with 10 probes doesn't outweigh one with 3.

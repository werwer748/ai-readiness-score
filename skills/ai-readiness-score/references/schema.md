# Formats

## score.json

Write this to `<out_dir>/score.json`, then run `render_dashboard.py` on it. Fields marked *renderer* are filled in by the script, so leave them out or set them to `null`.

```json
{
  "schema_version": 1,
  "skill_version": "0.1.0",
  "repo": "/abs/path/to/repo",
  "repo_name": "repo",
  "generated_at": "2026-09-30T09:00:00+00:00",
  "mode": "full",
  "lang": "en",
  "run_id": "b541fb",
  "probe_model": "haiku",
  "summary": "Two or three sentences: the overall verdict and the single biggest reason.",

  "categories": [
    {
      "id": 1,
      "name": "Navigation",
      "score": 12,
      "verdict": "One sentence.",
      "findings": [
        {"type": "strength", "text": "README maps every top-level directory", "evidence": ["README.md:12"]},
        {"type": "gap", "text": "plugins/ has no explanation", "evidence": ["plugins/"]}
      ],
      "fixes": ["Add a 3-line map of plugins/ to CLAUDE.md"]
    },
    {"id": 7, "name": "Agent Outcomes", "score": null}
  ],

  "probes": [
    {
      "id": "N1-02",
      "category": 1,
      "kind": "base",
      "question": "To add a new file type, which files would you edit?",
      "expected": ["FileTypeAndTabs.kt", "plugin.xml"],
      "answer": "Named SheetPanel.kt and plugin.xml",
      "correct": true,
      "steps": 13,
      "trail": [{"n": 1, "tool": "Read", "target": "README.md"}, {"n": 2, "tool": "Grep", "target": "FileType", "empty": true}],
      "dead_ends": [2],
      "revisits": ["plugin.xml"],
      "invalid": false,
      "lost_at": "steps 6-8: looked for registration in editor/",
      "drift": false
    }
  ],

  "top_fixes": [
    {"title": "Document local setup (JDK, gradle.properties) in docs/setup.md", "category": 3, "impact": "+6",
     "detail": "Asked in 3 separate sessions; probe T3-01 failed after 22 steps."}
  ],
  "notes": ["49 log candidates, 11 used as probes"],

  "total": null,
  "max": null,
  "probe_summary": null
}
```

Field notes:
- **skill_version, run_id:** copy them from the scan output.
- **categories:** all seven, with ids 1-7. `max` is fixed by id, so you don't need to write it. In `quick` mode, give category 7 `"score": null` and leave `probes` empty.
- **findings:** `type` is `strength` or `gap`. Always give `evidence`, as `path` or `path:line`.
- **probes:** `expected` is a flat list of the `must_include` items plus any `any_of` item the answer matched, so readers see what "right" meant. The fields `answer_step`, `result` and `score` come from the renderer.
- **top_fixes:** ordered by points gained per unit of effort, at most 5. `impact` is your estimate of the score change if the fix is done, written like `"+4"`.
- **Renderer fields:** `total`, `max`, `probe_summary`, category 7 `score`, and each probe's `answer_step`, `result` and `score`.
- **expected:** list only paths and commands, never prose. The renderer finds `answer_step` by matching the path items against the trail.
- **Privacy:** no raw log prompts anywhere in this file. Log-derived `question`s must be your generalized rewrite.

## Custom probes: `<repo>/.ai-readiness/probes.md`

Teams can commit their own probe questions: the things new people (and agents) keep getting wrong. Each probe is a level-2 heading with a category number, followed by `expected:` and optionally `any_of:` lines:

```markdown
## 1 Where do we register a new spreadsheet format?
expected: src/main/kotlin/format/SpreadsheetFormat.kt, src/main/resources/META-INF/plugin.xml

## 5 How do I run only the parser tests?
expected: ./gradlew test --tests "*Reader*"
any_of: gradlew test --tests
```

- Category numbers are 1-5.
- Custom probes take the first of the up-to-7 extra slots per category, ahead of log probes.
- Probe subagents are told not to read `.ai-readiness/`. If one does, the counter marks it `invalid` and it is excluded from the score.

"""Tests for the ai-readiness-score helper scripts (stdlib unittest; runs on Windows, macOS, Linux)."""
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "ai-readiness-score" / "scripts"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(SCRIPTS))

import _common  # noqa: E402
import render_dashboard  # noqa: E402

HAS_GIT = shutil.which("git") is not None


def run_script(name, *args, env=None):
    full_env = dict(os.environ, **(env or {}))
    res = subprocess.run([sys.executable, str(SCRIPTS / name)] + [str(a) for a in args],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=full_env)
    if res.returncode != 0:
        raise AssertionError("{} failed: {}".format(name, res.stderr.decode("utf-8", "replace")))
    return json.loads(res.stdout.decode("utf-8"))


def write(path, text, newline="\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline=newline) as fh:
        fh.write(text)


def jsonl(path, records, newline="\n"):
    write(path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), newline=newline)


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.config = self.tmp / "claude-config"
        self.env = {"CLAUDE_CONFIG_DIR": str(self.config)}

    def tearDown(self):
        self._tmp.cleanup()


class CommonTests(TempDirCase):
    def test_is_within(self):
        repo = self.tmp / "repo"
        self.assertTrue(_common.is_within(repo, repo))
        self.assertTrue(_common.is_within(repo / "src" / "a.py", repo))
        self.assertFalse(_common.is_within(self.tmp / "repo-other", repo))

    @unittest.skipUnless(os.name == "nt", "Windows paths are case-insensitive only on Windows")
    def test_is_within_windows_case_and_slashes(self):
        repo = self.tmp / "Repo"
        other = str(repo).upper().replace("\\", "/") + "/src"
        self.assertTrue(_common.is_within(other, repo))

    def test_parse_ts_accepts_z_suffix(self):
        ts = _common.parse_ts("2026-09-30T04:41:51.665Z")
        self.assertEqual(ts.tzinfo, timezone.utc)
        self.assertIsNone(_common.parse_ts("not a date"))

    def test_out_dir_is_stable_and_outside_repo(self):
        repo = self.tmp / "my repo"
        repo.mkdir()
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.config)
        try:
            a, b = _common.out_dir(repo), _common.out_dir(repo)
        finally:
            del os.environ["CLAUDE_CONFIG_DIR"]
        self.assertEqual(a, b)
        self.assertTrue(str(a).startswith(str(self.config)))
        self.assertTrue(a.name.startswith("my repo-"))

    def test_scripts_parse_as_python_38(self):
        for script in SCRIPTS.glob("*.py"):
            with self.subTest(script=script.name):
                ast.parse(script.read_text(encoding="utf-8"), feature_version=(3, 8))


class ExtractLogTasksTests(TempDirCase):
    def make_logs(self, newline="\n"):
        repo = self.tmp / "repo"
        (repo / "src").mkdir(parents=True)
        write(repo / "src" / "parser.py", "x = 1\n")
        now = datetime.now(timezone.utc)
        recent, old = now.isoformat(), (now - timedelta(days=120)).isoformat()
        human = {"type": "user", "origin": {"kind": "human"}, "promptSource": "typed", "isSidechain": False}

        def prompt(text, cwd=repo, ts=recent, **extra):
            rec = dict(human, cwd=str(cwd), timestamp=ts, message={"role": "user", "content": text})
            rec.update(extra)
            return rec

        def tool_use(name, **inp):
            return {"type": "assistant", "cwd": str(repo), "timestamp": recent,
                    "message": {"role": "assistant", "content": [{"type": "tool_use", "name": name, "input": inp}]}}

        projects = self.tmp / "projects"
        jsonl(projects / "p1" / "s1.jsonl", [
            prompt("Where does the parser handle empty sheets?"),
            tool_use("Read", file_path=str(repo / "src" / "parser.py")),
            tool_use("Read", file_path=str(repo / ".claude" / "worktrees" / "wt1" / "src" / "parser.py")),
            tool_use("Bash", command="python -m pytest tests/\necho done"),
            {"type": "user", "cwd": str(repo), "timestamp": recent, "toolUseResult": {},
             "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]}},
            prompt("<command-name>/clear</command-name>"),
            prompt("A background task finished with details", origin={"kind": "task-notification"}),
            prompt("short"),
            prompt("How do I run the old migration scripts?", ts=old),
            prompt("Where does the parser handle empty sheets?"),  # duplicate
            prompt("Explain the build for the sibling project", cwd=self.tmp / "elsewhere"),
            prompt("Subagent chatter should never count here", isSidechain=True),
            prompt("<pasted_content id=\"1\">huge paste</pasted_content>\nWhat does this stack trace mean for parser.py?"),
        ], newline=newline)
        jsonl(projects / "p2" / "s2.jsonl", [
            prompt("Please score this repo for AI readiness"),
            {"type": "assistant", "cwd": str(repo), "timestamp": recent, "message": {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Skill", "input": {"skill": "ai-readiness-score"}}]}},
        ])
        return repo, projects

    def extract(self, repo, projects):
        return run_script("extract_log_tasks.py", repo, "--projects-dir", projects)

    def test_filters_and_touched_files(self):
        repo, projects = self.make_logs()
        out = self.extract(repo, projects)
        texts = [c["text"] for c in out["candidates"]]
        self.assertEqual(texts.count("Where does the parser handle empty sheets?"), 1)
        self.assertIn("What does this stack trace mean for parser.py?", texts)
        for rejected in ("short", "Subagent chatter should never count here", "A background task finished with details",
                         "How do I run the old migration scripts?", "Explain the build for the sibling project"):
            self.assertNotIn(rejected, texts)
        self.assertFalse(any(t.startswith("<") for t in texts))
        first = next(c for c in out["candidates"] if c["text"].startswith("Where does the parser"))
        self.assertEqual(first["touched"], ["src/parser.py"])  # worktree path folded into the repo path
        self.assertEqual(first["commands"], ["python -m pytest tests/"])
        self.assertEqual(first["missing"], [])
        self.assertEqual(out["sessions_skipped_self"], 1)

    def test_crlf_logs(self):
        repo, projects = self.make_logs(newline="\r\n")
        out = self.extract(repo, projects)
        self.assertEqual(len(out["candidates"]), 2)

    @unittest.skipUnless(os.name == "nt", "case-insensitive cwd matching is Windows behaviour")
    def test_windows_cwd_case_insensitive(self):
        repo, projects = self.make_logs()
        rec = {"type": "user", "origin": {"kind": "human"}, "cwd": str(repo).upper(),
               "timestamp": datetime.now(timezone.utc).isoformat(),
               "message": {"role": "user", "content": "Upper-case cwd prompt should still match"}}
        jsonl(projects / "p3" / "s3.jsonl", [rec])
        texts = [c["text"] for c in self.extract(repo, projects)["candidates"]]
        self.assertIn("Upper-case cwd prompt should still match", texts)

    def test_no_logs_dir(self):
        out = run_script("extract_log_tasks.py", self.tmp, "--projects-dir", self.tmp / "missing")
        self.assertEqual(out["candidates"], [])


class ScanRepoTests(TempDirCase):
    def make_repo(self):
        repo = self.tmp / "app"
        write(repo / "README.md", "# App\n\n## Layout\n\n- `src/` code\n- `docs/gone.md` old notes\n\n"
                                  "Make sure you install deps.\n\n```bash\nnpm run dev\nnpm run deploy\n```\n")
        write(repo / "CLAUDE.md", "# Rules\n\nRun `npm test` before committing. Entry: `src/index.ts`.\n", newline="\r\n")
        write(repo / "package.json", json.dumps({"scripts": {"dev": "vite", "test": "vitest"},
                                                 "devDependencies": {"vitest": "1", "eslint": "9", "typescript": "5"}}))
        write(repo / "tsconfig.json", '{ "compilerOptions": { "strict": true } }')
        write(repo / "src" / "index.ts", "import { a } from './a'\nconsole.log(a)\n")
        write(repo / "src" / "a.ts", "import { b } from './b'\nexport const a = b\n")
        write(repo / "src" / "b.ts", "import { a } from './a'\nexport const b = 1\n")
        write(repo / "src" / "a.test.ts", "import { a } from './a'\n")
        write(repo / ".github" / "workflows" / "ci.yml",
              "on: [pull_request]\njobs:\n  t:\n    steps:\n      - run: npm test\n      - run: |\n          npm run lint\n")
        write(repo / "node_modules" / "x" / "index.js", "ignored\n")
        if HAS_GIT:
            for cmd in (["init", "-q"], ["add", "-A"],
                        ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"]):
                subprocess.run(["git", "-C", str(repo)] + cmd, check=True, stdout=subprocess.DEVNULL)
        return repo

    def test_scan_facts(self):
        repo = self.make_repo()
        out = run_script("scan_repo.py", repo, env=self.env)
        self.assertTrue(Path(out["out_dir"]).is_dir())
        self.assertTrue(out["out_dir"].startswith(str(self.config)))
        self.assertEqual(len(out["run_id"]), 6)
        self.assertIn("src/index.ts", out["navigation"]["entry_points"])
        self.assertEqual([d["path"] for d in out["agent_docs"]], ["CLAUDE.md"])
        self.assertIn("## Layout", out["docs"][0]["headings"])
        self.assertFalse(any("node_modules" in f for f in out["files"]["by_top_dir"]))
        missing = [m["ref"] for m in out["doc_refs"]["missing_paths"]]
        self.assertIn("docs/gone.md", missing)
        self.assertNotIn("src/index.ts", missing)
        cmds = [m["cmd"] for m in out["doc_refs"]["missing_commands"]]
        self.assertEqual(cmds, ["npm run deploy"])  # "Make sure" prose is not a make target
        verification = out["verification"]
        self.assertEqual(verification["test_files"], 1)
        self.assertTrue(verification["tsconfig_strict"])
        self.assertIn("npm test", verification["ci"][0]["run_steps"])
        self.assertIn("npm run lint", verification["ci"][0]["run_steps"])
        self.assertTrue(verification["ci"][0]["on_pull_request"])
        deps = out["dependencies"]
        self.assertEqual(deps["cycles_total"], 1)
        self.assertEqual(deps["cycles"][0], ["src/a.ts", "src/b.ts"])
        self.assertEqual(out["is_git"], HAS_GIT)

    def test_not_a_git_repo(self):
        repo = self.tmp / "plain"
        write(repo / "main.py", "import helper\n")
        write(repo / "helper.py", "x = 1\n")
        out = run_script("scan_repo.py", repo, env=self.env)
        if not HAS_GIT or not out["is_git"]:
            self.assertIsNone(out["churn"])
            self.assertTrue(out["warnings"])
        self.assertIn("main.py", out["navigation"]["entry_points"])
        self.assertEqual(out["dependencies"]["edges"], 1)


class CountProbeStepsTests(TempDirCase):
    def make_probe(self, projects, agent, probe_id, repo, blocks_and_results, run_id="r1"):
        sub = projects / "proj" / "session-1" / "subagents"
        write(sub / "{}.meta.json".format(agent), json.dumps(
            {"agentType": "Explore", "description": "probe:{}:{}".format(run_id, probe_id)}))
        records = [{"type": "user", "message": {"role": "user", "content": "question (mentions .ai-readiness rule)"}}]
        for i, (name, inp, result) in enumerate(blocks_and_results):
            tid = "t{}".format(i)
            records.append({"type": "assistant", "message": {
                "role": "assistant", "model": "claude-haiku-4-5",
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}})
            if result == "BLOCKED":
                records.append({"type": "user", "message": {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": tid, "is_error": True,
                     "content": "The server-side auto mode classifier gave no verdict (error)"}]}})
            elif result is not None:
                records.append({"type": "user", "message": {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": tid, "content": result}]}})
        jsonl(sub / "{}.jsonl".format(agent), records)

    def test_counts_trail_and_leaks(self):
        repo = self.tmp / "repo"
        repo.mkdir()
        projects = self.tmp / "projects"
        answer = json.dumps({"answer": "src/main.py", "paths": ["src/main.py"], "commands": []})
        self.make_probe(projects, "agent-a", "N1-01", repo, [
            ("Read", {"file_path": str(repo / "README.md")}, "text"),
            ("Grep", {"pattern": "main", "path": str(repo)}, "No files found"),
            ("Read", {"file_path": str(repo / "src" / "main.py")}, "code"),
            ("Read", {"file_path": str(repo / "src" / "main.py")}, "code"),
            ("Bash", {"command": "ls"}, "BLOCKED"),  # harness refusal: not a step
            ("SubagentHandback", {"message": answer}, None),
        ])
        self.make_probe(projects, "agent-b", "V5-01", repo, [
            ("Read", {"file_path": str(repo / ".ai-readiness" / "probes.md")}, "expected: x"),
        ])
        self.make_probe(projects, "agent-c", "N1-02", repo, [("Read", {"file_path": "x"}, "y")], run_id="other")
        # Excluding the forbidden dir in a shell command is compliance, not access.
        self.make_probe(projects, "agent-d", "C2-01", repo, [
            ("Bash", {"command": 'find {} -name "*.md" | grep -v ".ai-readiness"'.format(repo)}, "README.md"),
            ("Bash", {"command": "ls -la {}/src".format(repo)}, "main.py"),
        ])
        out = run_script("count_probe_steps.py", "r1", "--repo", repo, "--projects-dir", projects, env=self.env)
        self.assertEqual(out["found"], 3)
        c2, n1, v5 = out["probes"]
        self.assertFalse(c2["invalid"])
        self.assertEqual(c2["trail"][1]["target"], "ls -la ./src")
        self.assertEqual(n1["probe_id"], "N1-01")
        self.assertEqual(n1["steps"], 4)  # neither the handback nor the blocked call is a step
        self.assertEqual(n1["blocked"], 1)
        self.assertEqual([t["target"] for t in n1["trail"]][:3], ["README.md", ".", "src/main.py"])
        self.assertEqual(n1["dead_ends"], [2])
        self.assertEqual(n1["revisits"], ["src/main.py"])
        self.assertFalse(n1["invalid"])
        self.assertEqual(n1["final"]["paths"], ["src/main.py"])
        self.assertEqual(n1["model"], "claude-haiku-4-5")
        self.assertTrue(v5["invalid"])

    def test_no_matches_has_note(self):
        out = run_script("count_probe_steps.py", "zzz", "--repo", self.tmp, "--projects-dir", self.tmp, env=self.env)
        self.assertEqual(out["found"], 0)
        self.assertIn("note", out)


class AnswerStepTests(unittest.TestCase):
    trail = [{"n": 1, "tool": "Read", "target": "README.md"},
             {"n": 2, "tool": "Bash", "target": 'find . -name "plugin.xml"'},
             {"n": 3, "tool": "Read", "target": ".claude/rules/limits.md"},
             {"n": 4, "tool": "Read", "target": "src/main/kotlin/FileTypes.kt"},
             {"n": 5, "tool": "Read", "target": "src/preview/Render.kt"}]

    def step(self, expected):
        return render_dashboard.answer_step({"expected": expected, "trail": self.trail})

    def test_named_by_search_counts(self):
        self.assertEqual(self.step(["src/main/resources/META-INF/plugin.xml"]), 2)

    def test_dot_directories_and_suffix_match(self):
        self.assertEqual(self.step([".claude/rules/limits.md"]), 3)
        self.assertEqual(self.step(["FileTypes.kt"]), 4)

    def test_all_paths_must_be_reached(self):
        self.assertEqual(self.step(["plugin.xml", "FileTypes.kt"]), 4)
        self.assertIsNone(self.step(["plugin.xml", "Missing.kt"]))

    def test_directory_and_command_expectations(self):
        self.assertEqual(self.step(["preview/"]), 5)
        self.assertIsNone(self.step(["./gradlew test"]))

    def test_grade_uses_answer_step(self):
        probe = {"correct": True, "steps": 14, "answer_step": 6}
        self.assertEqual(render_dashboard.grade(probe), "pass")
        probe["answer_step"] = None
        self.assertEqual(render_dashboard.grade(probe), "partial")
        probe["steps"] = 21
        self.assertEqual(render_dashboard.grade(probe), "fail")


class RenderDashboardTests(TempDirCase):
    def load(self):
        return json.loads((FIXTURES / "sample_score.json").read_text(encoding="utf-8"))

    def test_finalize_full(self):
        data = self.load()
        warnings = render_dashboard.finalize(data)
        self.assertEqual(warnings, [])
        cats = {c["id"]: c for c in data["categories"]}
        self.assertEqual(cats[7]["score"], 3.9)  # 5 * mean(0.833, 0.5, 1.0)
        self.assertEqual(data["total"], 70.9)
        self.assertEqual(data["max"], 100)
        results = {p["id"]: p["result"] for p in data["probes"]}
        self.assertEqual(results, {"N1-01": "pass", "N1-02": "partial", "N1-03": "pass", "T3-01": "fail",
                                   "T3-02": "pass", "V5-01": "pass", "V5-02": "invalid"})

    def test_finalize_quick(self):
        data = self.load()
        data["mode"], data["probes"] = "quick", []
        render_dashboard.finalize(data)
        self.assertIsNone(data["categories"][6]["score"])
        self.assertEqual(data["max"], 95)
        self.assertEqual(data["total"], 67.0)

    def test_render_cli_escapes_and_writes(self):
        data = self.load()
        data["summary"] = "tricky </script><script>alert(1)</script> 한글"
        src = self.tmp / "out" / "score.json"
        write(src, json.dumps(data, ensure_ascii=False))
        run_script("render_dashboard.py", src)
        html = (src.parent / "report.html").read_text(encoding="utf-8")
        self.assertNotIn("__SCORE_JSON__", html)
        self.assertNotIn("</script><script>alert", html)
        self.assertIn("한글", html)
        final = json.loads(src.read_text(encoding="utf-8"))
        self.assertEqual(final["total"], 70.9)


if __name__ == "__main__":
    unittest.main()

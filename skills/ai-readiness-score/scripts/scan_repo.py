#!/usr/bin/env python3
"""Collect objective facts about a repository for AI-readiness scoring.

Usage: scan_repo.py REPO [--days 90]

Prints JSON. The scoring agent judges quality; this script only gathers evidence
(what exists, sizes, dates, references that no longer resolve, import graph shape).
"""
import argparse
import json
import os
import re
import secrets
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import VERSION, dump, out_dir  # noqa: E402

SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", "target", ".gradle",
    ".idea", ".next", ".nuxt", "vendor", ".tox", ".mypy_cache", ".pytest_cache", "coverage", ".turbo",
}
CODE_EXT = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rs", ".java", ".kt", ".kts", ".swift",
    ".rb", ".php", ".cs", ".c", ".cc", ".cpp", ".h", ".hpp", ".scala", ".dart", ".vue", ".svelte", ".m",
    ".sh", ".lua", ".ex", ".exs", ".clj",
}
JS_EXT = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte")
MAX_FILES, MAX_READ_BYTES, GOD_FILE_LINES = 20000, 1_000_000, 800

AGENT_DOC_NAMES = {"claude.md", "claude.local.md", "agents.md", "gemini.md", ".cursorrules", ".windsurfrules",
                   "copilot-instructions.md"}
AGENT_DOC_DIRS = (".claude/rules/", ".cursor/rules/", ".github/instructions/")
DOC_NAME_RE = re.compile(r"^(readme|contributing|architecture|design|glossary|development|setup|onboarding|"
                         r"conventions|decisions|changelog)\b", re.I)
TEST_RE = re.compile(r"(^|/)(tests?|__tests__|spec|src/test)/|(^|/)test_[^/]+\.py$|_test\.(py|go)$|"
                     r"\.(test|spec)\.[a-z]+$|Tests?\.(kt|java|swift|cs)$", re.I)
ENTRY_STEMS = {"main", "index", "app", "server", "cli", "__main__", "manage", "application", "program"}
# Framework conventions where the entry point is not called main/index.
ENTRY_PATTERNS = re.compile(r"^(src/)?(app/(layout|page)|pages/_app|routes/\+layout|routes/__root)\.[jt]sx?$|"
                            r"^(src/)?(app|pages|routes)/\+?(layout|page)\.svelte$|(^|/)(plugin|AndroidManifest)\.xml$|"
                            r"^cmd/[^/]+/main\.go$|^src/main\.rs$|^src/bin/[^/]+\.rs$")
LINT_FILES = {
    "lint": re.compile(r"^(\.eslintrc.*|eslint\.config\..*|biome\.jsonc?|ruff\.toml|\.ruff\.toml|\.flake8|\.pylintrc|"
                       r"\.golangci\.ya?ml|clippy\.toml|detekt\.ya?ml|\.swiftlint\.ya?ml|\.rubocop\.yml|"
                       r"\.stylelintrc.*|phpstan\.neon.*)$"),
    "format": re.compile(r"^(\.prettierrc.*|prettier\.config\..*|rustfmt\.toml|\.rustfmt\.toml|\.editorconfig|"
                         r"\.clang-format)$"),
    "typecheck": re.compile(r"^(tsconfig\.json|mypy\.ini|\.mypy\.ini|pyrightconfig\.json)$"),
    "precommit": re.compile(r"^(\.pre-commit-config\.yaml|lefthook\.ya?ml|\.lintstagedrc.*)$"),
}
PY_TOOLS = {"ruff": "lint", "flake8": "lint", "pylint": "lint", "black": "format", "isort": "format",
            "mypy": "typecheck", "pyright": "typecheck", "pytest": "test"}
JS_TOOLS = {"eslint": "lint", "@biomejs/biome": "lint", "oxlint": "lint", "prettier": "format",
            "typescript": "typecheck", "jest": "test", "vitest": "test", "mocha": "test", "@playwright/test": "test",
            "cypress": "test", "husky": "precommit", "lint-staged": "precommit"}
CI_FILES = re.compile(r"^(\.github/workflows/[^/]+\.ya?ml|\.gitlab-ci\.yml|\.circleci/config\.yml|azure-pipelines\.yml|"
                      r"Jenkinsfile|bitbucket-pipelines\.yml|\.travis\.yml|\.buildkite/.+\.ya?ml)$")
TOOLCHAIN = {".nvmrc", ".node-version", ".python-version", ".tool-versions", "rust-toolchain", "rust-toolchain.toml",
             ".java-version", ".sdkmanrc", "mise.toml", ".ruby-version"}
RUNNER_RE = re.compile(r"\b(npm run|pnpm(?: run)?|yarn(?: run)?|bun run|make|just)\s+([A-Za-z0-9:_.-]+)")
FILE_EXT_RE = re.compile(r"\.(md|mdc|mdx|json|jsonc|ya?ml|toml|ini|cfg|conf|txt|lock|xml|gradle|kts?|java|py|js|jsx|"
                         r"mjs|cjs|ts|tsx|go|rs|rb|php|swift|cs|c|h|cpp|hpp|sh|ps1|sql|html|css|scss|vue|svelte)$", re.I)
TECH_NAMES = {"node.js", "next.js", "vue.js", "nuxt.js", "express.js", "three.js", "d3.js", "chart.js", "react.js",
              "angular.js", "ember.js", "backbone.js", "socket.io", "nest.js", "deno.js", "p5.js"}


# ---------------------------------------------------------------- helpers

def git(repo, *args, timeout=60):
    try:
        res = subprocess.run(["git", "-C", str(repo)] + list(args), stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return res.stdout.decode("utf-8", "replace") if res.returncode == 0 else None


def read_text(path, limit=MAX_READ_BYTES):
    try:
        with open(path, "rb") as fh:
            data = fh.read(limit + 1)
    except OSError:
        return None
    if len(data) > limit or b"\0" in data[:4096]:
        return None
    return data.decode("utf-8", "replace").replace("\r\n", "\n")


def skipped(rel):
    return any(part in SKIP_DIRS for part in rel.split("/")[:-1]) or rel.startswith(".claude/worktrees/")


def list_files(repo, is_git):
    if is_git:
        out = git(repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
        if out is not None:
            files = sorted({f for f in out.split("\0") if f and not f.endswith("/") and not skipped(f)})
            return [f for f in files if (repo / f).is_file()][:MAX_FILES]
    files = []
    for root, dirs, names in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in names:
            rel = Path(root, name).relative_to(repo).as_posix()
            if not skipped(rel):
                files.append(rel)
            if len(files) >= MAX_FILES:
                return sorted(files)
    return sorted(files)


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


class Dates:
    """Last-modified info per path: git commit dates when available, file mtime otherwise."""

    def __init__(self, repo, is_git):
        self.repo, self.is_git = repo, is_git

    def of(self, rel):
        if self.is_git:
            out = (git(self.repo, "log", "-1", "--format=%H %cI", "--", rel) or "").strip()
            if out:
                sha, date = out.split(" ", 1)
                since = (git(self.repo, "rev-list", "--count", sha + "..HEAD") or "").strip()
                return {"updated": date, "commits_since": int(since) if since.isdigit() else None}
        try:
            return {"updated": iso((self.repo / rel).stat().st_mtime), "commits_since": None, "estimated": True}
        except OSError:
            return {"updated": None, "commits_since": None}


# ---------------------------------------------------------------- sections

def overview(files):
    top = Counter(f.split("/")[0] if "/" in f else "." for f in files)
    ext = Counter(PurePosixPath(f).suffix.lower() or PurePosixPath(f).name for f in files)
    return {"total": len(files), "by_top_dir": dict(top.most_common(15)), "by_ext": dict(ext.most_common(12))}


def doc_entry(repo, rel, dates):
    text = read_text(repo / rel) or ""
    entry = {"path": rel, "lines": text.count("\n") + (1 if text and not text.endswith("\n") else 0),
             "bytes": len(text.encode("utf-8"))}
    entry.update(dates.of(rel))
    if rel.lower().endswith((".md", ".mdc")):
        entry["headings"] = [h[:80] for h in re.findall(r"^#{1,4} .+$", text, re.M)[:25]]
    return entry


def collect_docs(repo, files, dates):
    agent, other = [], []
    for f in files:
        low, name = f.lower(), PurePosixPath(f).name.lower()
        if name in AGENT_DOC_NAMES or low.startswith(AGENT_DOC_DIRS):
            agent.append(f)
        elif low.endswith((".md", ".mdx", ".rst", ".txt")) and (
                "/" not in f or low.startswith("docs/") or DOC_NAME_RE.match(name) or "/adr/" in low
                or "/decisions/" in low):
            other.append(f)
    other.sort(key=lambda f: (f.count("/"), f))
    claude_dir = defaultdict(list)
    for f in files:
        m = re.match(r"^\.claude/(skills|commands|agents|rules|hooks)/(.+)$", f)
        if m:
            claude_dir[m.group(1)].append(m.group(2))
    return {
        "agent_docs": [doc_entry(repo, f, dates) for f in agent[:30]],
        "docs": [doc_entry(repo, f, dates) for f in other[:30]],
        "docs_total": len(other),
        "adr_like": [f for f in other if "/adr/" in f.lower() or "/decisions/" in f.lower()][:10],
        "claude_dir": {k: v[:15] for k, v in claude_dir.items()},
        "claude_settings": [f for f in files if re.match(r"^\.claude/settings(\.local)?\.json$", f)],
    }


def collect_manifests(repo, files):
    manifests, runnables, tools = [], defaultdict(set), defaultdict(set)
    entry_hints, monorepo = [], []
    for f in files:
        name = PurePosixPath(f).name
        if name in ("pnpm-workspace.yaml", "lerna.json", "turbo.json", "nx.json", "go.work"):
            monorepo.append(f)
        if name == "package.json":
            try:
                pkg = json.loads(read_text(repo / f) or "{}")
            except ValueError:
                continue
            scripts = sorted((pkg.get("scripts") or {}).keys())
            runnables[f].update(scripts)
            deps = set(pkg.get("dependencies") or {}) | set(pkg.get("devDependencies") or {})
            for dep, kind in JS_TOOLS.items():
                if dep in deps:
                    tools[kind].add("{} ({})".format(dep, f))
            if pkg.get("workspaces"):
                monorepo.append(f + "#workspaces")
            for key in ("main", "module", "bin"):
                val = pkg.get(key)
                vals = val.values() if isinstance(val, dict) else [val]
                entry_hints += [str(PurePosixPath(f).parent / str(v).lstrip("./")) for v in vals if v]
            manifests.append({"path": f, "kind": "npm", "scripts": scripts[:30]})
        elif name in ("Makefile", "makefile", "GNUmakefile", "justfile", "Justfile"):
            text = read_text(repo / f) or ""
            targets = sorted(set(re.findall(r"^([A-Za-z0-9_.-]+)(?:\s[^:=\n]*)?:(?!=)", text, re.M)) - {".PHONY"})
            runnables[f].update(targets)
            manifests.append({"path": f, "kind": "make" if "make" in name.lower() else "just", "targets": targets[:30]})
        elif name == "pyproject.toml":
            text = read_text(repo / f) or ""
            sections = sorted(set(re.findall(r"^\[tool\.([\w-]+)", text, re.M)))
            for tool in sections:
                if tool in PY_TOOLS:
                    tools[PY_TOOLS[tool]].add("{} ({})".format(tool, f))
            manifests.append({"path": f, "kind": "python", "tool_sections": sections,
                              "has_scripts": "[project.scripts]" in text})
        elif name in ("build.gradle", "build.gradle.kts", "pom.xml"):
            text = read_text(repo / f) or ""
            for tool, kind in (("detekt", "lint"), ("ktlint", "lint"), ("checkstyle", "lint"), ("spotbugs", "lint"),
                               ("spotless", "format"), ("jacoco", "test"), ("kover", "test")):
                if tool in text:
                    tools[kind].add("{} ({})".format(tool, f))
            manifests.append({"path": f, "kind": name})
        elif name in ("go.mod", "Cargo.toml", "composer.json",
                      "Gemfile", "setup.py", "requirements.txt", "Package.swift", "pubspec.yaml", "mix.exs"):
            manifests.append({"path": f, "kind": name})
    return manifests[:25], runnables, tools, entry_hints, monorepo


def collect_entry_points(files, hints):
    found = [h for h in hints if h in set(files)]
    for f in files:
        p = PurePosixPath(f)
        if p.suffix.lower() in CODE_EXT and p.stem.lower() in ENTRY_STEMS and f.count("/") <= 3 and not TEST_RE.search(f):
            found.append(f)
        elif ENTRY_PATTERNS.search(f):
            found.append(f)
    seen, out = set(), []
    for f in sorted(found, key=lambda x: (x.count("/"), x)):
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out[:20]


def collect_verification(repo, files, tools):
    tests = [f for f in files if TEST_RE.search(f) and PurePosixPath(f).suffix.lower() in CODE_EXT]
    configs = defaultdict(list)
    for f in files:
        name = PurePosixPath(f).name
        for kind, rx in LINT_FILES.items():
            if rx.match(name):
                configs[kind].append(f)
        if f.startswith(".husky/"):
            configs["precommit"].append(f)
    for kind, found in tools.items():
        configs[kind] += sorted(found)
    strict = None
    if (repo / "tsconfig.json").is_file():
        strict = bool(re.search(r'"strict"\s*:\s*true', read_text(repo / "tsconfig.json") or ""))
    ci = []
    for f in files:
        if CI_FILES.match(f):
            text = read_text(repo / f) or ""
            steps = []
            for m in re.finditer(r"^(\s*)-?\s*run:\s*(\|[-+]?|>[-+]?)?\s*(.*)$", text, re.M):
                if m.group(3).strip():
                    steps.append(m.group(3).strip()[:120])
                else:  # block scalar: take the following, more-indented lines
                    rest = text[m.end():].split("\n")[1:6]
                    steps += [ln.strip()[:120] for ln in rest if ln.startswith(m.group(1) + "  ") and ln.strip()][:3]
            ci.append({"path": f, "on_pull_request": "pull_request" in text or "merge_request" in text,
                       "run_steps": steps[:12]})
    test_dirs = Counter(re.match(r"^(.*?/)?(tests?|__tests__|spec|src/test)/", f).group(0)
                        for f in tests if re.match(r"^(.*?/)?(tests?|__tests__|spec|src/test)/", f))
    return {
        "test_files": len(tests),
        "test_dirs": [d for d, _ in test_dirs.most_common(8)],
        "sample_tests": tests[:5],
        "configs": {k: sorted(set(v))[:10] for k, v in configs.items()},
        "tsconfig_strict": strict,
        "ci": ci[:10],
    }


def collect_env(repo, files, is_git):
    names = {f: PurePosixPath(f).name for f in files}
    example = [f for f, n in names.items() if re.match(r"^(\.env\.(example|sample|template|dist)|example\.env)$", n)]
    tracked_env = []
    if is_git:
        tracked = set((git(repo, "ls-files", "-z") or "").split("\0"))
        tracked_env = [f for f in tracked if re.match(r"^\.env(\.[\w-]+)?$", PurePosixPath(f).name)
                       and f not in example]
    return {
        "example_files": example[:10],
        "tracked_env_files": sorted(tracked_env)[:10],
        "toolchain_files": [f for f, n in names.items() if n in TOOLCHAIN][:10],
        "containers": [f for f, n in names.items() if n.startswith("Dockerfile") or re.match(
            r"^(docker-)?compose(\.[\w-]+)?\.ya?ml$", n) or f.startswith(".devcontainer/")][:10],
    }


def check_doc_refs(repo, files, docs, runnables):
    """Paths and runner commands mentioned in agent docs / READMEs that no longer resolve."""
    fileset = set(files)
    dirset = {str(PurePosixPath(f).parent) for f in files} | {
        "/".join(f.split("/")[:i]) for f in files for i in range(1, f.count("/") + 1)}
    all_targets = set().union(*runnables.values()) if runnables else set()
    basenames = {PurePosixPath(f).name for f in files}
    missing_paths, missing_cmds, checked = [], [], 0
    for doc in docs:
        text = read_text(repo / doc) or ""
        base = PurePosixPath(doc).parent
        refs = re.findall(r"`([^`\s]{3,120})`", text) + re.findall(r"\]\(([^)\s#]+)\)", text)
        for ref in refs:
            if re.match(r"^[a-z]+:", ref, re.I) or any(ch in ref for ch in "*<>{}$|") or ref.startswith(("-", "@")):
                continue
            ref = ref.strip("'\"").rstrip("/.,:;")
            # Without a slash, only treat it as a path if it looks like a file name (not ".xls", "org.foo.bar", "Node.js").
            if "/" not in ref and (not FILE_EXT_RE.search(ref) or ref.startswith(".") and ref.count(".") == 1
                                   or ref.lower() in TECH_NAMES):
                continue
            ref = ref[2:] if ref.startswith("./") else ref
            if ref.startswith(("/", "~")):
                continue
            checked += 1
            candidates = {ref, str(base / ref)}
            # Docs often cite paths relative to a sub-project, so a suffix match counts as found.
            found = (any(c in fileset or c in dirset for c in candidates)
                     or ("/" not in ref and ref in basenames)
                     or any(x.endswith("/" + ref) for x in fileset | dirset)
                     or (repo / ref).exists())
            if not found:
                missing_paths.append({"doc": doc, "ref": ref})
        # Only look for runner commands inside code, so prose like "make sure" is ignored.
        code = "\n".join(re.findall(r"```[^\n]*\n(.*?)```", text, re.S) + re.findall(r"`([^`\n]+)`", text))
        for m in RUNNER_RE.finditer(code):
            tool, target = m.group(1), m.group(2)
            if target in ("install", "i", "add", "ci", "init", "create", "dlx", "exec", "x", "run", "-C"):
                continue
            checked += 1
            if target not in all_targets:
                missing_cmds.append({"doc": doc, "cmd": "{} {}".format(tool, target)})
    return {"checked": checked, "missing_paths": missing_paths[:20], "missing_commands": missing_cmds[:20]}


def collect_churn(repo, files, days):
    out = git(repo, "log", "--since={}.days".format(days), "--name-only", "--relative", "--format=")
    if out is None:
        return None
    fileset = set(files)
    per_file = Counter(ln for ln in out.splitlines() if ln and ln in fileset)
    per_area = Counter()
    for f, n in per_file.items():
        parts = f.split("/")[:-1]
        per_area["/".join(parts[:2]) or "."] += n
    return {"window_days": days,
            "top_areas": [{"dir": d, "changes": n} for d, n in per_area.most_common(10)],
            "top_files": [{"path": f, "changes": n} for f, n in per_file.most_common(15)]}


def collect_sizes(repo, files):
    sizes = []
    for f in files:
        if PurePosixPath(f).suffix.lower() in CODE_EXT:
            text = read_text(repo / f)
            if text is not None:
                sizes.append((text.count("\n") + 1, f))
    sizes.sort(reverse=True)
    return {"code_files": len(sizes), "over_{}_lines".format(GOD_FILE_LINES): sum(1 for n, _ in sizes if n > GOD_FILE_LINES),
            "largest": [{"path": f, "lines": n} for n, f in sizes[:15]]}


# ---------------------------------------------------------------- import graph

JS_IMPORT_RE = re.compile(r"""(?:\bfrom\s*|\bimport\s*\(\s*|\brequire\s*\(\s*|^\s*import\s+)['"]([^'"]+)['"]""", re.M)
PY_FROM_RE = re.compile(r"^\s*from\s+(\.*[\w.]*)\s+import\s+([\w*, ()]+)", re.M)
PY_IMPORT_RE = re.compile(r"^\s*import\s+([\w.]+(?:\s*,\s*[\w.]+)*)", re.M)
JVM_IMPORT_RE = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+)(?:\s+as\s+\w+)?\s*;?\s*$", re.M)
JVM_PACKAGE_RE = re.compile(r"^\s*package\s+([\w.]+)", re.M)
GO_IMPORT_RE = re.compile(r'^\s*(?:import\s+)?(?:[\w.]+\s+)?"([^"]+)"', re.M)


def build_graph(repo, files):
    fileset = set(files)
    by_lang = Counter()
    edges, unresolved = defaultdict(set), 0
    texts = {}

    def src(f):
        if f not in texts:
            texts[f] = read_text(repo / f) or ""
        return texts[f]

    code = [f for f in files if PurePosixPath(f).suffix.lower() in CODE_EXT][:5000]

    # JVM: map fully-qualified class names to files.
    fqcn = {}
    for f in code:
        if f.endswith((".java", ".kt")):
            m = JVM_PACKAGE_RE.search(src(f))
            fqcn["{}.{}".format(m.group(1), PurePosixPath(f).stem) if m else PurePosixPath(f).stem] = f

    go_dirs = {str(PurePosixPath(f).parent) for f in code if f.endswith(".go")}
    go_module = None
    if "go.mod" in fileset:
        m = re.search(r"^module\s+(\S+)", src("go.mod"), re.M)
        go_module = m.group(1) if m else None

    def resolve_js(f, spec):
        if spec.startswith("."):
            base = PurePosixPath(os.path.normpath(str(PurePosixPath(f).parent / spec)).replace("\\", "/"))
        elif spec.startswith(("@/", "~/")):
            base = PurePosixPath("src") / spec[2:]
        else:
            return "external"
        stem = str(base)
        stem_noext = re.sub(r"\.(m?jsx?|cjs)$", "", stem)
        cands = [stem] + [stem_noext + e for e in JS_EXT] + [stem + "/index" + e for e in JS_EXT]
        if spec.startswith(("@/", "~/")):
            cands += [c[4:] for c in cands]  # alias pointing at repo root instead of src/
        return next((c for c in cands if c in fileset), None)

    def resolve_py(f, mod, names):
        if mod.startswith("."):
            dots = len(mod) - len(mod.lstrip("."))
            pkg = PurePosixPath(f).parent
            for _ in range(dots - 1):
                pkg = pkg.parent
            rest = mod.lstrip(".")
            bases = [str(pkg / rest.replace(".", "/")) if rest else str(pkg)]
        else:
            bases = [mod.replace(".", "/"), "src/" + mod.replace(".", "/")]
        for b in bases:
            for c in (b + ".py", b + "/__init__.py"):
                if c in fileset:
                    return c
            for n in names:
                if n + ".py" in fileset or (b + "/" + n + ".py") in fileset:
                    return b + "/" + n + ".py"
        return None

    for f in code:
        ext = PurePosixPath(f).suffix.lower()
        text = src(f)
        node, targets = f, []
        if ext in JS_EXT:
            by_lang["js/ts"] += 1
            for spec in JS_IMPORT_RE.findall(text):
                targets.append(resolve_js(f, spec))
        elif ext == ".py":
            by_lang["python"] += 1
            for mod, names in PY_FROM_RE.findall(text):
                targets.append(resolve_py(f, mod, [n.strip(" ()") for n in names.split(",")]) or (
                    None if mod.startswith(".") else "external"))
            for group in PY_IMPORT_RE.findall(text):
                for mod in group.split(","):
                    targets.append(resolve_py(f, mod.strip(), []) or "external")
        elif ext in (".java", ".kt"):
            by_lang["jvm"] += 1
            for name in JVM_IMPORT_RE.findall(text):
                targets.append(fqcn.get(name) or "external")
        elif ext == ".go" and go_module:
            by_lang["go"] += 1
            node = str(PurePosixPath(f).parent) + "/"  # Go dependencies are package (directory) level
            block = "\n".join(re.findall(r"^import\s*\(([^)]*)\)", text, re.M | re.S)) + "\n" + "\n".join(
                re.findall(r'^import\s+(?:[\w.]+\s+)?"[^"]+"', text, re.M))
            for spec in GO_IMPORT_RE.findall(block):
                if spec.startswith(go_module + "/"):
                    d = spec[len(go_module) + 1:]
                    targets.append(d + "/" if d in go_dirs else None)
        else:
            by_lang["other"] += 1
            continue
        for t in targets:
            if t is None:
                unresolved += 1
            elif t != "external" and t != node:
                edges[node].add(t)

    fan_in = Counter()
    for src_f, dsts in edges.items():
        for d in dsts:
            fan_in[d] += 1
    cycles = strongly_connected(edges)
    return {
        "files_by_language": dict(by_lang),
        "edges": sum(len(v) for v in edges.values()),
        "unresolved_internal_imports": unresolved,
        "top_fan_in": [{"path": p, "importers": n} for p, n in fan_in.most_common(10)],
        "cycles_total": len(cycles),
        "cycles": [c[:8] for c in cycles[:10]],
        "note": "regex-level analysis; JVM same-package references and non-JS/Python/JVM/Go languages are not graphed",
    }


def strongly_connected(edges):
    """Iterative Tarjan; returns SCCs with more than one node, largest first."""
    index, low, on_stack, stack, result, counter = {}, {}, set(), [], [], [0]
    nodes = set(edges) | {d for ds in edges.values() for d in ds}
    for root in sorted(nodes):
        if root in index:
            continue
        work = [(root, iter(sorted(edges.get(root, ()))))]
        index[root] = low[root] = counter[0]
        counter[0] += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, it = work[-1]
            advanced = False
            for nxt in it:
                if nxt not in index:
                    index[nxt] = low[nxt] = counter[0]
                    counter[0] += 1
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, iter(sorted(edges.get(nxt, ())))))
                    advanced = True
                    break
                if nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                comp = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp.append(w)
                    if w == node:
                        break
                if len(comp) > 1:
                    result.append(sorted(comp))
    return sorted(result, key=len, reverse=True)


# ---------------------------------------------------------------- main

def scan(repo, days=90):
    repo = Path(repo).resolve()
    is_git = git(repo, "rev-parse", "--is-inside-work-tree") is not None
    warnings = []
    if not is_git:
        warnings.append("not a git repository: dates come from file mtimes and churn is unavailable")
    files = list_files(repo, is_git)
    if len(files) >= MAX_FILES:
        warnings.append("file list truncated at {}".format(MAX_FILES))
    dates = Dates(repo, is_git)
    head = None
    if is_git:
        out = (git(repo, "log", "-1", "--format=%h %cI") or "").strip()
        head = dict(zip(("sha", "date"), out.split(" ", 1))) if out else None

    docs = collect_docs(repo, files, dates)
    manifests, runnables, tools, entry_hints, monorepo = collect_manifests(repo, files)
    report_dir = out_dir(repo)
    report_dir.mkdir(parents=True, exist_ok=True)
    ref_docs = [d["path"] for d in docs["agent_docs"]] + [d["path"] for d in docs["docs"]
                                                          if PurePosixPath(d["path"]).name.lower().startswith("readme")]
    return {
        "repo": str(repo),
        "run_id": secrets.token_hex(3),
        "skill_version": VERSION,
        "out_dir": str(report_dir),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "is_git": is_git,
        "head": head,
        "files": overview(files),
        "navigation": {"entry_points": collect_entry_points(files, entry_hints), "monorepo_signals": monorepo},
        **docs,
        "manifests": manifests,
        "verification": collect_verification(repo, files, tools),
        "env": collect_env(repo, files, is_git),
        "doc_refs": check_doc_refs(repo, files, ref_docs, runnables),
        "churn": collect_churn(repo, files, days) if is_git else None,
        "sizes": collect_sizes(repo, files),
        "dependencies": build_graph(repo, files),
        "warnings": warnings,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("repo")
    ap.add_argument("--days", type=int, default=90, help="churn window (default 90)")
    args = ap.parse_args()
    if not Path(args.repo).is_dir():
        sys.exit("not a directory: {}".format(args.repo))
    dump(scan(args.repo, args.days))


if __name__ == "__main__":
    main()

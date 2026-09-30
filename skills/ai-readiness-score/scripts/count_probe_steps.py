#!/usr/bin/env python3
"""Measure probe subagent runs from their Claude Code transcripts.

Usage: count_probe_steps.py RUN_ID --repo REPO [--forbid PATH ...] [--since-hours 24] [--projects-dir DIR]

Probes must be launched with the Agent description "probe:<RUN_ID>:<PROBE_ID>". For each one this
prints the number of exploration steps (tool calls), the ordered trail, dead ends, revisits,
any access to forbidden paths, and the final answer. Never read the transcripts directly:
they are large; use this summary instead.
"""
import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import config_dir, dump, is_within, iter_jsonl  # noqa: E402

NON_STEP_TOOLS = {"SubagentHandback", "StructuredOutput", "TodoWrite"}
TARGET_KEYS = ("file_path", "path", "pattern", "command", "url", "query", "notebook_path")
PATH_KEYS = ("file_path", "path", "notebook_path")
# Shell fragments that *exclude* a path; mentioning a forbidden path this way is compliance, not access.
EXCLUDE_RE = re.compile(r"""(?:(?:-not|!)\s+-(?:path|name|wholename)\s+|grep\s+-v\s+|--exclude(?:-dir)?[=\s]|-prune\b)\S*""")
BLOCKED_RE = re.compile(r"auto mode classifier|haven't granted it yet|doesn't want to proceed with this tool use|"
                        r"permission to use .* has been denied", re.I)
EMPTY_RE = re.compile(r"^\s*(No (files|matches) found|Found 0 |0 matches|File does not exist|<tool_use_error>)", re.I)
MAX_TRAIL = 40


def block_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(block_text(b.get("text") if isinstance(b, dict) and "text" in b else
                                    b.get("content") if isinstance(b, dict) else b) for b in content)
    return "" if content is None else str(content)


def short_target(inp, repo_strings):
    """Main argument of a tool call, with the repo root shortened so trails stay readable."""
    for key in TARGET_KEYS:
        val = inp.get(key)
        if isinstance(val, str) and val:
            val = val.strip().splitlines()[0]
            for root in repo_strings:
                for form in (root, root.replace("\\", "/")):
                    if key in PATH_KEYS:  # a path argument: make it repo-relative
                        val = val.replace(form + "/", "").replace(form + "\\", "").replace(form, ".")
                    else:  # inside a command or pattern: keep it readable as a path
                        val = val.replace(form, ".")
            return val[:140]
    return ""


def touches(name, inp, marker):
    """True if a tool call reads or searches `marker`, rather than merely excluding it."""
    if name == "Bash":
        cmd = str(inp.get("command", ""))
        return marker in EXCLUDE_RE.sub(" ", cmd.replace("\\", "/"))
    values = [str(v) for k, v in inp.items() if k in TARGET_KEYS]
    return any(marker in v or marker in v.replace("\\", "/") for v in values)


def json_in(text):
    """First {...} object in the text, if it parses."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def analyse(transcript, repo_strings, forbid):
    calls, results, final, model, leaked = [], {}, "", None, False
    for rec in iter_jsonl(transcript):
        msg = rec.get("message") or {}
        if rec.get("type") == "user" and isinstance(msg.get("content"), list):
            for b in msg["content"]:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    text = block_text(b.get("content"))
                    results[b.get("tool_use_id")] = (bool(b.get("is_error")), text)
                    # Grep content output shows the answer file's lines as "path:line:" hits.
                    if re.search(r"\.ai-readiness[\\/][^\s:]+:\d*:?", text):
                        leaked = True
        if rec.get("type") != "assistant":
            continue
        model = model or msg.get("model")
        for b in msg.get("content") or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and b.get("text", "").strip():
                final = b["text"]
            if b.get("type") != "tool_use":
                continue
            inp = b.get("input") or {}
            if b.get("name") in NON_STEP_TOOLS:
                if b.get("name") == "SubagentHandback" and inp.get("message"):
                    final = str(inp["message"])
                continue
            calls.append((b.get("id"), b.get("name"), inp))

    trail, forbidden, blocked = [], [], 0
    for call_id, name, inp in calls:
        is_error, text = results.get(call_id, (False, ""))
        # Calls the harness refused (permission checks) say nothing about the repo, so they are not steps.
        if is_error and BLOCKED_RE.search(text):
            blocked += 1
            continue
        step = {"n": len(trail) + 1, "tool": name, "target": short_target(inp, repo_strings)}
        if is_error or EMPTY_RE.match(text):
            step["empty"] = True
        hit = [f for f in forbid if touches(name, inp, f)]
        if hit:
            forbidden.append({"step": step["n"], "tool": name, "matched": hit[0]})
        trail.append(step)

    reads = Counter(t["target"] for t in trail if t["tool"] in ("Read", "NotebookRead") and t["target"])
    parsed = json_in(final)
    out = {
        "model": model,
        "steps": len(trail),
        "trail": trail[:MAX_TRAIL],
        "dead_ends": [t["n"] for t in trail if t.get("empty")],
        "revisits": sorted(p for p, n in reads.items() if n > 1),
        "blocked": blocked,
        "forbidden": forbidden,
        "invalid": bool(forbidden) or leaked,
    }
    if parsed is not None:
        out["final"] = parsed
    else:
        out["final_text"] = final.strip()[:1500]
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run_id")
    ap.add_argument("--repo", required=True, help="target repository (trail paths are shown relative to it)")
    ap.add_argument("--forbid", action="append", default=[], help="extra path marker probes must not touch")
    ap.add_argument("--since-hours", type=float, default=24)
    ap.add_argument("--projects-dir", default=None)
    args = ap.parse_args()

    projects = Path(args.projects_dir) if args.projects_dir else config_dir() / "projects"
    repo = Path(args.repo)
    repo_strings = sorted({os.path.normpath(os.path.abspath(str(repo))), str(repo.resolve())}, key=len, reverse=True)
    cfg = str(config_dir())
    forbid = [".ai-readiness"] + args.forbid
    if not is_within(repo, cfg):  # the Claude config dir holds reports and logs, unless the target lives there
        forbid += [cfg, cfg.replace("\\", "/")]
    forbid = sorted(set(f for f in forbid if f))

    prefix = "probe:{}:".format(args.run_id)
    cutoff = time.time() - args.since_hours * 3600
    probes = []
    for meta_path in projects.glob("*/*/subagents/*.meta.json"):
        try:
            if meta_path.stat().st_mtime < cutoff:
                continue
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        desc = str(meta.get("description", ""))
        if not desc.startswith(prefix):
            continue
        transcript = meta_path.with_name(meta_path.name[: -len(".meta.json")] + ".jsonl")
        entry = {"probe_id": desc[len(prefix):].strip(), "agent_type": meta.get("agentType")}
        if transcript.is_file():
            entry.update(analyse(transcript, repo_strings, forbid))
        else:
            entry["error"] = "transcript not found"
        probes.append(entry)

    probes.sort(key=lambda p: p["probe_id"])
    result = {"run_id": args.run_id, "found": len(probes), "probes": probes}
    if not probes:
        result["note"] = ("no transcripts matched; check the Agent descriptions use '{}<id>' and that this ran "
                          "in the same Claude config dir".format(prefix))
    dump(result)


if __name__ == "__main__":
    main()

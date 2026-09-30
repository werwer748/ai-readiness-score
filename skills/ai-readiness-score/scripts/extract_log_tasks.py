#!/usr/bin/env python3
"""Extract real tasks people asked Claude Code to do in a repo, from local session logs.

Usage: extract_log_tasks.py REPO [--days 90] [--limit 200] [--projects-dir DIR]

Prints JSON candidates for log-derived probes. Output is meant for the scoring agent's
context only; raw prompt text must never be copied into the report.
"""
import argparse
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import config_dir, dump, is_within, iter_jsonl, norm_path, parse_ts  # noqa: E402

SKILL_NAME = "ai-readiness-score"
MIN_LEN, MAX_LEN, CLIP = 15, 2000, 300
MAX_TOUCHED, MAX_COMMANDS = 10, 5

BLOCK_RE = re.compile(r"<(system-reminder|pasted_content)\b[^>]*>.*?</\1>", re.S)
IMAGE_LINE_RE = re.compile(r"^\[Image[^\]]*\]\s*$", re.M)
# Claude Code worktrees are checkouts of the same repo; map their paths back to repo paths.
WORKTREE_RE = re.compile(r"^\.claude/worktrees/[^/]+(?:/|$)")
PATH_KEYS = ("file_path", "path", "notebook_path")


def prompt_text(rec):
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def is_human_turn(rec):
    """A top-level user turn (not a tool result, reminder, notification, or subagent message)."""
    if rec.get("type") != "user" or "toolUseResult" in rec:
        return False
    if rec.get("isMeta") or rec.get("isSidechain"):
        return False
    origin = rec.get("origin")
    if isinstance(origin, dict) and origin.get("kind") not in (None, "human"):
        return False
    return rec.get("promptSource") != "system"


def clean(text):
    text = BLOCK_RE.sub(" ", text)
    text = IMAGE_LINE_RE.sub("", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def ran_this_skill(records):
    for rec in records:
        if rec.get("type") == "assistant":
            for block in (rec.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Skill":
                    skill = str((block.get("input") or {}).get("skill", ""))
                    if skill == SKILL_NAME or skill.endswith(":" + SKILL_NAME):
                        return True
        elif rec.get("type") == "user" and "<command-name>/" + SKILL_NAME in prompt_text(rec):
            return True
    return False


def rel_to(path, roots):
    """Repo-relative POSIX path if `path` is inside one of `roots`, preserving the path's own case."""
    for root in roots:
        if is_within(path, root):
            full = os.path.normpath(os.path.abspath(str(path)))
            rest = full[len(norm_path(root)):]
            rel = rest.lstrip("\\/").replace("\\", "/")
            return WORKTREE_RE.sub("", rel) or "."
    return None


def extract(repo, projects_dir, days, limit):
    # Logs may record either the path as typed or its resolved form (e.g. /tmp vs /private/tmp).
    roots = [Path(os.path.abspath(repo)), Path(repo).resolve()]
    repo = roots[1]
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    stats = {"sessions_scanned": 0, "sessions_matched": 0, "sessions_skipped_self": 0}
    candidates = []

    for session_file in sorted(Path(projects_dir).glob("*/*.jsonl")):
        records = list(iter_jsonl(session_file))
        stats["sessions_scanned"] += 1
        if not any(rel_to(r["cwd"], roots) for r in records if r.get("cwd")):
            continue
        if ran_this_skill(records):
            stats["sessions_skipped_self"] += 1
            continue
        stats["sessions_matched"] += 1

        current = None
        for rec in records:
            if is_human_turn(rec):
                current = None
                cwd, ts = rec.get("cwd"), parse_ts(rec.get("timestamp"))
                cwd_rel = rel_to(cwd, roots) if cwd else None
                if cwd_rel is None or not ts or ts < cutoff:
                    continue
                text = clean(prompt_text(rec))
                if text.startswith("<") or text.startswith("[Request interrupted"):
                    continue
                if not MIN_LEN <= len(text) <= MAX_LEN:
                    continue
                current = {
                    "ts": ts.isoformat(),
                    "session": session_file.stem[:8],
                    "cwd": cwd_rel,
                    "text": text[:CLIP] + ("…" if len(text) > CLIP else ""),
                    "touched": [],
                    "commands": [],
                }
                candidates.append(current)
            elif current is not None and rec.get("type") == "assistant":
                for block in (rec.get("message") or {}).get("content") or []:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    inp = block.get("input") or {}
                    for key in PATH_KEYS:
                        value = inp.get(key)
                        rel = rel_to(value, roots) if isinstance(value, str) and os.path.isabs(value) else None
                        if rel and rel not in current["touched"] and len(current["touched"]) < MAX_TOUCHED:
                            current["touched"].append(rel)
                    cmd = inp.get("command")
                    if isinstance(cmd, str) and cmd.strip() and len(current["commands"]) < MAX_COMMANDS:
                        current["commands"].append(cmd.strip().splitlines()[0][:100])

    # Newest first, drop near-duplicate prompts, cap the list.
    candidates.sort(key=lambda c: c["ts"], reverse=True)
    seen, unique = set(), []
    for cand in candidates:
        key = re.sub(r"\s+", " ", cand["text"].lower())
        if key in seen:
            continue
        seen.add(key)
        cand["missing"] = [t for t in cand["touched"] if not (repo / t).exists()]
        unique.append(cand)
    unique = unique[:limit]
    for i, cand in enumerate(unique, 1):
        cand["id"] = "L{:03d}".format(i)

    return dict(repo=str(repo), window_days=days, candidates=unique, **stats)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("repo")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--projects-dir", default=None, help="defaults to <claude config dir>/projects")
    args = ap.parse_args()
    projects = Path(args.projects_dir) if args.projects_dir else config_dir() / "projects"
    if not projects.is_dir():
        dump({"repo": str(Path(args.repo).resolve()), "candidates": [], "note": "no session logs found"})
        return
    dump(extract(args.repo, projects, args.days, args.limit))


if __name__ == "__main__":
    main()

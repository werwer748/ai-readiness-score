"""Shared helpers for ai-readiness-score scripts (Python 3.8+, stdlib only)."""
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

VERSION = "0.1.0"  # keep in sync with CHANGELOG.md; recorded in every score.json


def use_utf8_stdout():
    """Windows consoles default to a legacy code page; force UTF-8 so non-ASCII text survives."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def config_dir():
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(env).expanduser() if env else Path.home() / ".claude"


def norm_path(p):
    """Comparable form of a path: absolute, normalized separators, case-folded on Windows."""
    return os.path.normcase(os.path.normpath(os.path.abspath(str(p))))


def is_within(child, parent):
    c, p = norm_path(child), norm_path(parent)
    return c == p or c.startswith(p.rstrip(os.sep) + os.sep)


def out_dir(repo):
    """Where reports for `repo` live: outside the repo so probes can never read old answers."""
    repo = Path(repo).resolve()
    digest = hashlib.sha1(norm_path(repo).encode("utf-8")).hexdigest()[:6]
    return config_dir() / "ai-readiness" / "{}-{}".format(repo.name or "root", digest)


def parse_ts(value):
    """Parse ISO-8601 timestamps, including the trailing 'Z' that fromisoformat rejects before 3.11."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def iter_jsonl(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def dump(obj):
    use_utf8_stdout()
    json.dump(obj, sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")

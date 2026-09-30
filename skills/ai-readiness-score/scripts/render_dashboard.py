#!/usr/bin/env python3
"""Finalize score.json (probe grades, category 7, total) and render the HTML dashboard.

Usage: render_dashboard.py SCORE_JSON [-o REPORT_HTML] [--open]

The scoring agent fills categories 1-6 and each probe's `correct`/`steps`/`invalid`;
this script does the arithmetic so the numbers are consistent run to run, writes the
finalized JSON back, and renders a self-contained report.html next to it.
"""
import argparse
import json
import re
import sys
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import VERSION, use_utf8_stdout  # noqa: E402

TEMPLATE = Path(__file__).resolve().parent.parent / "assets" / "dashboard.html"
CATEGORIES = {1: ("Navigation", 15), 2: ("Context Quality", 20), 3: ("Tribal Knowledge", 20),
              4: ("Dependency Mapping", 15), 5: ("Verification Gates", 15), 6: ("Freshness", 10),
              7: ("Agent Outcomes", 5)}
PROBE_POINTS = {"pass": 1.0, "partial": 0.5, "fail": 0.0}
PASS_STEPS, CUTOFF_STEPS = 10, 20


def looks_like_path(item):
    return " " not in item.strip() and ("/" in item or re.search(r"\.[A-Za-z0-9]{1,6}$", item) is not None)


def answer_step(probe):
    """Step by which every expected path had been reached (read, or named by a search/listing).

    Agents often keep exploring after they have the answer (to write a fuller reply), so for path
    answers the cost of finding the answer is measured here rather than by the total step count.
    Returns None when the answer has no paths or a path was never reached in the trail.
    """
    expected = [e.strip().replace("\\", "/").strip("/") for e in probe.get("expected") or [] if looks_like_path(e)]
    if not expected:
        return None
    reached = {}
    for step in probe.get("trail") or []:
        target = str(step.get("target") or "").replace("\\", "/")
        rel = target[2:] if target.startswith("./") else target
        for exp in expected:
            if exp in reached:
                continue
            if step.get("tool") in ("Read", "NotebookRead"):
                hit = (rel == exp or rel.endswith("/" + exp) or exp.endswith("/" + rel)
                       or rel.startswith(exp + "/") or ("/" + exp + "/") in ("/" + rel))
            else:
                hit = exp.rsplit("/", 1)[-1] in target
            if hit:
                reached[exp] = step.get("n")
    return max(reached.values()) if len(reached) == len(expected) else None


def grade(probe):
    if probe.get("invalid"):
        return "invalid"
    total = probe.get("steps")
    if not probe.get("correct") or (total is not None and total > CUTOFF_STEPS):
        return "fail"
    cost = probe.get("answer_step") or total
    if cost is not None and cost > PASS_STEPS:
        return "partial"
    return "pass"


def finalize(data):
    warnings = []
    data.setdefault("skill_version", VERSION)
    cats = {int(c["id"]): c for c in data.get("categories", [])}
    for cid, (name, mx) in CATEGORIES.items():
        cat = cats.setdefault(cid, {"id": cid, "name": name, "max": mx, "score": None})
        cat.setdefault("name", name)
        cat["max"] = mx
        if cid != 7 and cat.get("score") is None:
            warnings.append("category {} has no score".format(cid))
        elif cid != 7 and not 0 <= cat["score"] <= mx:
            warnings.append("category {} score {} clamped to 0..{}".format(cid, cat["score"], mx))
            cat["score"] = min(max(cat["score"], 0), mx)

    probes = data.get("probes") or []
    by_cat = {}
    for p in probes:
        p["answer_step"] = answer_step(p)
        p["result"] = grade(p)
        p["score"] = PROBE_POINTS.get(p["result"])
        by_cat.setdefault(int(p["category"]), []).append(p)

    summary = {}
    for cid, ps in sorted(by_cat.items()):
        valid = [p for p in ps if p["result"] != "invalid"]
        mean = sum(p["score"] for p in valid) / len(valid) if valid else None
        summary[str(cid)] = {"passed": sum(1 for p in valid if p["score"] > 0), "total": len(valid),
                             "invalid": len(ps) - len(valid), "mean": None if mean is None else round(mean, 3)}
    data["probe_summary"] = dict(data.get("probe_summary") or {}, by_category=summary,
                                 total=len(probes), invalid=sum(1 for p in probes if p["result"] == "invalid"))

    # Category 7 = 5 x (mean of per-category means), so categories with more probes don't dominate.
    outcome = cats[7]
    means = [s["mean"] for s in summary.values() if s["mean"] is not None]
    if data.get("mode") == "quick":
        outcome["score"] = None
    elif means:
        outcome["score"] = round(5 * sum(means) / len(means), 1)
    else:
        outcome["score"] = None
        warnings.append("full mode but no valid probes: category 7 is N/A")

    scored = [c for c in cats.values() if c.get("score") is not None]
    data["categories"] = [cats[k] for k in sorted(cats)]
    data["total"] = round(sum(c["score"] for c in scored), 1)
    data["max"] = sum(c["max"] for c in scored)
    return warnings


def render(data, template=TEMPLATE):
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/").replace("<!--", "<\\!--")
    return template.read_text(encoding="utf-8").replace("__SCORE_JSON__", payload, 1)


def main():
    use_utf8_stdout()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("score_json")
    ap.add_argument("-o", "--output", help="default: report.html next to the JSON")
    ap.add_argument("--open", action="store_true", help="open the report in the default browser")
    args = ap.parse_args()

    src = Path(args.score_json)
    data = json.loads(src.read_text(encoding="utf-8"))
    warnings = finalize(data)
    src.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    out = Path(args.output) if args.output else src.with_name("report.html")
    out.write_text(render(data), encoding="utf-8")

    for w in warnings:
        print("warning: " + w, file=sys.stderr)
    print(json.dumps({"total": data["total"], "max": data["max"], "report": str(out.resolve()),
                      "categories": {c["id"]: c.get("score") for c in data["categories"]}}, ensure_ascii=False))
    if args.open:
        webbrowser.open(out.resolve().as_uri())


if __name__ == "__main__":
    main()

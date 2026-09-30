# Changelog

All notable changes to this skill. Versions follow [Semantic Versioning](https://semver.org/): a change to the rubric or scoring math is at least a minor bump, because scores from different versions are not directly comparable.

## [0.1.1] - 2026-09-30

### Fixed
- Windows: probe trail paths were reported with backslashes (`src\main.py`), which broke revisit detection and display. They are now POSIX-style.

## [0.1.0] - 2026-09-30

### Added
- `ai-readiness-score` skill: 7-category, 100-point rubric (Navigation, Context Quality, Tribal Knowledge, Dependency Mapping, Verification Gates, Freshness, Agent Outcomes).
- `scan_repo.py`: repository facts (docs, commands, CI, dead doc references, churn, file sizes, import graph with cycle detection).
- Agent Outcomes probes: 3 base probes per category plus custom (`.ai-readiness/probes.md`) and log-derived probes, run by haiku `Explore` subagents; `extract_log_tasks.py` and `count_probe_steps.py`.
- Self-contained HTML dashboard (light/dark, en/ko) showing per-probe trails: where agents get lost.
- `quick` mode that skips probes.
- Tests and CI on Windows, macOS and Linux.

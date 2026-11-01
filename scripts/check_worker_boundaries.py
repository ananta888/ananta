#!/usr/bin/env python3
"""Detector: hub/worker import boundaries (CLN-029 / CLN-055).

The hub-worker architecture (AGENTS.md) requires workers to execute delegated
work only and to depend on abstractions, not on concrete hub services. Two
directions are gated at module level:

  Rule A — ``worker/`` must not import concrete ``agent.*`` modules. Only
           ``agent.config``, ``agent.auth`` and leaf modules named ``*_ports``
           or ``*_contract`` (plus top-level ``ananta_contracts.*``) are allowed.
  Rule B — ``agent/services/`` must not import ``worker.*``; the hub drives
           workers through task delegation, not by importing their internals.

Tracked legacy coupling lives in ``reports/worker-boundary-baseline.txt`` (one
violating file per line). A NEW violating file fails the check; a baseline
entry without current violations is reported as stale so the baseline can be
pruned as CLN-054 decouples the trees.

Only module-level imports are inspected; lazy function-level imports remain the
documented escape hatch until the ports/contracts migration (CLN-054) lands.

Exit codes:
- 0: no new boundary violations
- 1: violations outside the baseline
- 2: the detector itself failed

Usage:
    python scripts/check_worker_boundaries.py [--json]
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

BASELINE_PATH = Path("reports/worker-boundary-baseline.txt")
WORKER_ROOT = Path("worker")
AGENT_SERVICES_ROOT = Path("agent") / "services"

ALLOWED_AGENT_IMPORTS = frozenset({"agent.config", "agent.auth"})


def _module_level_imports(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except SyntaxError:
        return []
    out: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            out.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append(node.module)
    return out


def _allowed_worker_import(target: str) -> bool:
    if target.startswith("ananta_contracts"):
        return True
    if not target.startswith("agent."):
        return True
    leaf = target.rsplit(".", 1)[-1]
    return target in ALLOWED_AGENT_IMPORTS or leaf.endswith(("_ports", "_contract"))


def rule_a_violations() -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for path in sorted(WORKER_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for target in _module_level_imports(path):
            if target.startswith("agent.") and not _allowed_worker_import(target):
                findings.append({"file": str(path), "rule": "A", "target": target})
    return findings


def rule_b_violations() -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for path in sorted(AGENT_SERVICES_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for target in _module_level_imports(path):
            if target.startswith("worker."):
                findings.append({"file": str(path), "rule": "B", "target": target})
    return findings


def _baseline_files() -> dict[str, str]:
    if not BASELINE_PATH.exists():
        return {}
    out: dict[str, str] = {}
    for raw in BASELINE_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "#" in line:
            name, reason = line.split("#", 1)
        else:
            name, reason = line, ""
        out[name.strip()] = reason.strip()
    return out


def main() -> int:
    as_json = "--json" in sys.argv[1:]

    findings = rule_a_violations() + rule_b_violations()
    baseline = _baseline_files()

    by_file: dict[str, list[dict[str, str]]] = {}
    for finding in findings:
        by_file.setdefault(finding["file"], []).append(finding)

    new_files = sorted(set(by_file) - set(baseline))
    stale_files = sorted(set(baseline) - set(by_file))

    if as_json:
        print(
            json.dumps(
                {
                    "new": {f: by_file[f] for f in new_files},
                    "stale_baseline": stale_files,
                    "baselined_files": len(baseline),
                },
                indent=2,
            )
        )
    else:
        print(f"check_worker_boundaries: {len(findings)} sites in {len(by_file)} files ({len(baseline)} baselined)")
        for file in new_files:
            for finding in by_file[file]:
                print(f"NEW rule {finding['rule']}: {file} imports {finding['target']}")
        for file in stale_files:
            print(f"STALE BASELINE (prune it): {file}")
        if not new_files:
            print("No new hub/worker boundary violations.")

    return 1 if new_files else 0


if __name__ == "__main__":
    sys.exit(main())

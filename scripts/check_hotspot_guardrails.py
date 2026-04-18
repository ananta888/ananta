#!/usr/bin/env python3
"""Report maintainability hotspot size guardrails without failing by default."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

DEFAULT_TARGETS = (
    ("agent/ai_agent.py", 250),
    ("agent/services/service_registry.py", 250),
    ("agent/services/task_orchestration_service.py", 650),
    ("frontend-angular/src/app/components/dashboard.component.ts", 1200),
    ("frontend-angular/src/app/services/agent-api.service.ts", 350),
    ("frontend-angular/src/app/services/auth.interceptor.ts", 250),
)


def build_hotspot_report(targets: tuple[tuple[str, int], ...] = DEFAULT_TARGETS) -> dict[str, object]:
    entries = []
    for raw_path, max_lines in targets:
        path = Path(raw_path)
        exists = path.exists()
        line_count = _line_count(path) if exists else 0
        entries.append(
            {
                "path": raw_path,
                "exists": exists,
                "lines": line_count,
                "max_lines": max_lines,
                "status": "over_budget" if exists and line_count > max_lines else "ok",
            }
        )
    return {
        "version": "v1",
        "entries": entries,
        "over_budget": [entry for entry in entries if entry["status"] == "over_budget"],
    }


def _line_count(path: Path) -> int:
    return sum(1 for _ in path.open(encoding="utf-8", errors="ignore"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Report hotspot size guardrails")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    parser.add_argument("--fail-on-over-budget", action="store_true", help="Exit non-zero when a target is over budget")
    args = parser.parse_args()

    report = build_hotspot_report()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for entry in report["entries"]:
            print(f"{entry['status']}: {entry['path']} lines={entry['lines']} max={entry['max_lines']}")

    return 1 if args.fail_on_over_budget and report["over_budget"] else 0


if __name__ == "__main__":
    raise SystemExit(main())


#!/usr/bin/env python3
"""Detector: test-file line budget (CLN-046).

God tests are unmaintainable (the 4300-line recovery planning suite is the
worst example). Splitting the existing backlog (CLN-039) takes time, so this
gate freezes growth instead: any NEW test file crossing the budget threshold
(1200 lines) fails the check unless it is explicitly allowlisted with a
reason. Shrinking/removing an allowlisted file is reported so the ratchet
prunes naturally.

Existing god tests >=2000 lines stay visible in the report; the workflow
transition / recovery clusters are the primary CLN-039 split candidates.

Exit codes:
- 0: no new over-budget test files
- 1: over-budget file outside the allowlist
- 2: detector failure

Usage:
    python scripts/check_test_line_budget.py [--update]
"""

from __future__ import annotations

import sys
from pathlib import Path

TESTS_ROOT = Path("tests")
BUDGET_LINES = 1200
ALLOWLIST_PATH = Path("reports/test-line-budget-allowlist.json")


def over_budget_files() -> dict[str, int]:
    found: dict[str, int] = {}
    for path in sorted(TESTS_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            count = sum(1 for _ in path.open(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
        if count > BUDGET_LINES:
            found[str(path)] = count
    return found


def _load_allowlist() -> dict[str, dict[str, object]]:
    import json

    if not ALLOWLIST_PATH.exists():
        return {}
    data = json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))
    return {entry["path"]: entry for entry in data.get("allowlist", [])}


def _write_allowlist(current: dict[str, int]) -> None:
    import json

    entries = [
        {
            "path": path,
            "line_count": count,
            "reason": (
                "god test (CLN-039 split candidate)" if count >= 2000 else "over-budget legacy test; split when touched"
            ),
        }
        for path, count in sorted(current.items(), key=lambda kv: -kv[1])
    ]
    ALLOWLIST_PATH.write_text(
        json.dumps(
            {
                "schema": "test_line_budget_allowlist.v1",
                "budget_lines": BUDGET_LINES,
                "policy": "new test files over budget fail; prune entries when files shrink or split",
                "allowlist": entries,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    update = "--update" in sys.argv[1:]
    current = over_budget_files()

    if update or not ALLOWLIST_PATH.exists():
        _write_allowlist(current)
        print(f"test line budget allowlist written: {len(current)} files >{BUDGET_LINES}")
        return 0

    allowlist = _load_allowlist()
    new_files = sorted(set(current) - set(allowlist))
    stale = sorted(set(allowlist) - set(current))
    grown = {
        path: (allowlist[path]["line_count"], current[path])
        for path in set(allowlist) & set(current)
        if current[path] > int(allowlist[path]["line_count"])  # type: ignore[arg-type]
    }

    for path in new_files:
        print(f"NEW over-budget test file ({current[path]} > {BUDGET_LINES}): {path}")
    for path in stale:
        print(f"STALE allowlist entry (prune it): {path}")
    for path, (recorded, actual) in sorted(grown.items()):
        print(f"OVER-BUDGET GROWTH: {path} {recorded} -> {actual}")

    if not new_files and not grown:
        god = sum(1 for c in current.values() if c >= 2000)
        print(f"test line budget OK: {len(current)} files >{BUDGET_LINES} ({god} god tests >=2000 tracked for CLN-039)")
    return 1 if new_files or grown else 0


if __name__ == "__main__":
    sys.exit(main())

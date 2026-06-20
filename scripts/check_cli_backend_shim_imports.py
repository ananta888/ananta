#!/usr/bin/env python3
"""Detector: flag legacy ``from agent.common.sgpt_*`` imports.

After Welle 3 of the SGDEC migration is complete, all consumers must
import the LLM-CLI backend subsystem from the new ``agent.cli_backends.*``
namespace. This script enforces that: it greps the codebase for any
``from agent.common.sgpt_`` import and exits 1 if any are found.

Exit codes:
- 0: zero violations (Welle 3 final state)
- 1: one or more violations found
- 2: this script itself crashed (bug, missing files, etc.)

Usage:
    python scripts/check_cli_backend_shim_imports.py

The detector excludes:
- ``agent/common/sgpt_*.py`` themselves (those are the source files
  that the shim layer re-exports; in Welle 3 they ARE the shims that
  will be deleted)
- ``agent/cli_backends/`` (the new namespace; imports go the other way)
- ``scripts/check_cli_backend_shim_imports.py`` (this file)
- ``.venv``, ``node_modules``, ``__pycache__``, ``.git``, etc.
- ``tests/fixtures/`` (deterministic test fixtures)
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Patterns that count as a violation. We grep for two patterns:
# 1. `from agent.common.sgpt_X import ...`  (multi-line aware)
# 2. `import agent.common.sgpt_X`           (whole-module import)
# Both indicate the legacy namespace is still in use.
VIOLATION_PATTERNS = [
    re.compile(r"^\s*from\s+agent\.common\.sgpt_[A-Za-z_][\w.]*\s+import\b"),
    re.compile(r"^\s*from\s+agent\.common\s+import\s+sgpt_\w"),
    re.compile(r"^\s*import\s+agent\.common\.sgpt_"),
]

# Paths to exclude from the scan.
EXCLUDED_PATH_PARTS = {
    "agent/common/",  # Source / shim files themselves
    "agent/cli_backends/",  # New namespace (imports go the other way)
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".git",
    ".pytest_cache",
    ".claude/",  # Worktrees + caches, not part of the source tree
    "scripts/check_cli_backend_shim_imports.py",  # this file
    "tests/test_cli_backend_shim_deprecation.py",  # tests the re-export contract; must import both paths
    "tests/fixtures/",
    "data/",
    "artifacts/",
    "project-workspaces/",
    "autoimport-state/logs/",
}


def _is_excluded(path: Path) -> bool:
    """Return True if the file should be excluded from the scan."""
    s = str(path)
    for excl in EXCLUDED_PATH_PARTS:
        if excl in s:
            return True
    return False


def _find_violations(root: Path) -> list[tuple[Path, int, str]]:
    """Find all ``from agent.common.sgpt_`` violations in the codebase."""
    violations: list[tuple[Path, int, str]] = []
    for py_file in root.rglob("*.py"):
        if _is_excluded(py_file):
            continue
        try:
            text = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for pattern in VIOLATION_PATTERNS:
                if pattern.search(line):
                    violations.append((py_file, lineno, line.rstrip()))
                    break
    return violations


def main() -> int:
    root = Path.cwd()
    if not (root / "agent").is_dir():
        print(f"ERROR: no agent/ directory at {root}", file=sys.stderr)
        return 2

    violations = _find_violations(root)
    if violations:
        print(f"FAIL: {len(violations)} legacy 'agent.common.sgpt_' imports found:")
        for path, lineno, line in violations:
            rel = path.relative_to(root)
            print(f"  {rel}:{lineno}  {line}")
        print()
        print("Migrate these imports to the new namespace:")
        print("  from agent.common.sgpt_X import Y")
        print("  → from agent.cli_backends.X import Y")
        return 1
    else:
        print("OK: no legacy 'agent.common.sgpt_' imports found.")
        return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Detector: flag ``# noqa: C901`` complexity suppressions.

Ananta keeps every function inside the project's McCabe limit
(``max-complexity = 30`` in ``pyproject.toml``). A ``# noqa: C901`` comment
silences a genuine complexity regression instead of fixing it, so it is not an
accepted production pattern. This detector fails when any tracked Python file
still carries one.

Exit codes:
- 0: no suppressions found
- 1: one or more suppressions found
- 2: the detector itself failed (for example, outside a git work tree)

Usage:
    python scripts/check_complexity_suppressions.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

SUPPRESSION = re.compile(r"#\s*noqa:\s*C901\b")

# Documented exceptions may be named here (repository-relative paths). The
# intended state is an empty set: fix the function instead of suppressing it.
ALLOWED_SUPPRESSIONS: frozenset[str] = frozenset()


def _tracked_python_files(root: Path) -> list[Path]:
    """Return every git-tracked Python file below ``root``."""
    result = subprocess.run(
        ["git", "ls-files", "-z", "*.py"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("not a git work tree")
    return [root / name for name in result.stdout.split("\0") if name]


def find_suppressions(root: Path) -> list[tuple[str, int, str]]:
    """Return ``(relative_path, line_number, line)`` for each suppression."""
    violations: list[tuple[str, int, str]] = []
    for path in _tracked_python_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        relative = path.relative_to(root).as_posix()
        if relative in ALLOWED_SUPPRESSIONS:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if SUPPRESSION.search(line):
                violations.append((relative, lineno, line.strip()))
    return violations


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    try:
        violations = find_suppressions(root)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if not violations:
        print("OK: no '# noqa: C901' complexity suppressions found.")
        return 0

    print(f"FAIL: {len(violations)} '# noqa: C901' complexity suppression(s) found:")
    for relative, lineno, line in violations:
        print(f"  {relative}:{lineno}  {line}")
    print()
    print("Reduce the function below max-complexity (pyproject.toml) instead of suppressing C901.")
    return 1


if __name__ == "__main__":
    sys.exit(main())

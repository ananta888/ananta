#!/usr/bin/env python3
"""Detector: runtime artefacts must never be tracked in git.

The AGENTS.md Output Boundary table classifies paths as Source (commit), Runtime
(never commit), Secret (never commit) or Fixture (commit only when explicitly
approved). This detector enforces the Runtime/Secret side of that contract: it
fails when any tracked file matches a known runtime-artefact or secret pattern.

This keeps the repository free of committed databases, logs, build output,
scratch files and secret material that would otherwise silently re-accumulate.

Exit codes:
- 0: no runtime artefacts are tracked
- 1: one or more forbidden artefacts are tracked
- 2: the detector itself failed (for example, outside a git work tree)

Usage:
    python scripts/check_output_boundary.py
"""

from __future__ import annotations

import fnmatch
import subprocess
import sys

# Exact repository-relative paths that must never be tracked.
FORBIDDEN_PATHS: frozenset[str] = frozenset(
    {
        "test.db",
        "user.json",
        "crash.txt",
        "system_context.txt",
        "running-copilot.chat.txt",
        "ananta.runtime-profile.json",
        "set_llm_config.json",
        "release-verification-report.json",
        "login_payload.json",
    }
)

# Path glob patterns that must never appear anywhere in the tracked index.
FORBIDDEN_GLOBS: tuple[str, ...] = (
    "**/secrets/secret_key",
    "**/secrets/key",
    "*.egg-info/*",
    "test-reports/*",
    "test-results/*",
    "**/*.log",
    "**/nohup.out",
    "**/*.sqlite",
    "**/*.sqlite3",
    "**/__pycache__/*",
)


def _tracked_files() -> list[str]:
    """Return every tracked path in the repository (read-only)."""
    result = subprocess.run(
        ["git", "ls-files"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git ls-files failed: {result.stderr.strip()}")
    return [line for line in result.stdout.splitlines() if line.strip()]


def _matches_forbidden_glob(path: str) -> bool:
    """Return True when *path* matches any forbidden glob pattern."""
    # fnmatch treats ``**`` like ``*``; normalise ``**/`` so nested paths match.
    for pattern in FORBIDDEN_GLOBS:
        norm = pattern.replace("**/", "*")
        if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(path, norm):
            return True
        # Also match when the pattern is meant to match the basename anywhere.
        if "/" not in pattern.rstrip("*") and fnmatch.fnmatch(path.rsplit("/", 1)[-1], pattern):
            return True
    return False


def main() -> int:
    try:
        tracked = _tracked_files()
    except RuntimeError as exc:
        print(f"check_output_boundary: {exc}", file=sys.stderr)
        return 2

    violations: list[str] = []
    for path in tracked:
        if path in FORBIDDEN_PATHS:
            violations.append(f"tracked runtime/secret path: {path}")
            continue
        if _matches_forbidden_glob(path):
            violations.append(f"tracked artefact matching forbidden glob: {path}")

    if violations:
        print("check_output_boundary FAILED — runtime artefacts are tracked:")
        for violation in violations:
            print(f"  - {violation}")
        print(
            "\nRemove them from the index (git rm --cached <path>) and keep them "
            "gitignored. See AGENTS.md Output Boundary."
        )
        return 1

    print(f"check_output_boundary OK — {len(tracked)} tracked files, no artefacts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Detector: .env.example keys must be referenced somewhere tracked (CLN-065).

The env reference is generated (scripts/generate_env_reference.py); the
example file may only document names the repository actually consumes.
A key with zero references outside .env.example fails this check.

Exit codes: 0 clean, 1 orphans found, 2 detector failure.

Usage:
    python scripts/check_env_reference.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

EXAMPLE = Path(".env.example")


def main() -> int:
    keys = []
    for line in EXAMPLE.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Z][A-Z0-9_]+)=", line.strip())
        if m:
            keys.append(m.group(1))
    pattern = "(" + "|".join(re.escape(k) for k in keys) + ")" if keys else None
    if pattern is None:
        return 0
    proc = subprocess.run(
        ["git", "grep", "-h", "-o", "-E", pattern, "--", ".", ":!.env.example", ":!docs/env-reference.generated.md"],
        capture_output=True,
        text=True,
    )
    used = set(proc.stdout.split())
    orphans = sorted(set(keys) - used)
    if orphans:
        print("check_env_reference FAILED — documented but unreferenced env keys:")
        for o in orphans:
            print(f"  - {o}")
        return 1
    print(f"check_env_reference OK — {len(keys)} documented keys all referenced")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Compile Python sources in memory without writing permission-sensitive pyc files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOTS = ("agent", "ananta_contracts", "voice_runtime", "worker", "scripts")


def check(relative_roots: tuple[str, ...] = DEFAULT_ROOTS) -> dict[str, int | bool]:
    files: list[Path] = []
    for relative in relative_roots:
        root = ROOT / relative
        if not root.is_dir():
            raise ValueError("python_build_root_missing")
        files.extend(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)
    unique = sorted(set(files))
    failures = 0
    for path in unique:
        try:
            compile(path.read_bytes(), path.as_posix(), "exec", dont_inherit=True)
        except (OSError, SyntaxError, ValueError):
            failures += 1
    return {"source_count": len(unique), "failure_count": failures, "passed": failures == 0}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("roots", nargs="*")
    args = parser.parse_args()
    result = check(tuple(args.roots) if args.roots else DEFAULT_ROOTS)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

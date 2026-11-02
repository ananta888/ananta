#!/usr/bin/env python3
"""Detector: freeze the growth of hand-rolled service-singleton boilerplate.

CLN-035: agent/services/ carries ~300 hand-written ``get_*_service()``
accessors built from four competing patterns (eager module instance, lazy
global, ``current_app.extensions``, di late-binding). Rewriting them all at
once is not worth the churn, so growth is frozen instead: the current set of
modules that define a ``get_*_service`` accessor is ratcheted in
``reports/singleton-freeze.txt``. A NEW module adding such an accessor fails
this check; removed modules are reported as stale so the ratchet shrinks.

New services should use the canonical pattern instead (see
docs/service-singletons.md): constructor injection at the composition root
(bootstrap) with ``get_repository_registry()`` seams for late binding.

Exit codes:
- 0: no new singleton accessors
- 1: new accessor outside the freeze list
- 2: detector failure

Usage:
    python scripts/check_singleton_freeze.py [--update]
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

FREEZE_PATH = Path("reports/singleton-freeze.txt")
SCAN_ROOTS = ("agent", "worker")


def modules_with_service_accessors() -> set[str]:
    found: set[str] = set()
    for root in SCAN_ROOTS:
        for path in Path(root).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.FunctionDef)
                    and node.name.startswith("get_")
                    and node.name.endswith("_service")
                ):
                    found.add(str(path))
                    break
    return found


def main() -> int:
    update = "--update" in sys.argv[1:]
    current = modules_with_service_accessors()

    if update or not FREEZE_PATH.exists():
        FREEZE_PATH.write_text(
            "# Singleton freeze (CLN-035): modules defining get_*_service accessors.\n"
            "# Do not add entries — new services use constructor injection\n"
            "# (see docs/service-singletons.md). Prune when a module loses its accessor.\n"
            + "\n".join(sorted(current))
            + "\n"
        )
        print(f"singleton freeze written: {len(current)} modules")
        return 0

    frozen = {
        line.strip()
        for line in FREEZE_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    new_modules = sorted(current - frozen)
    stale = sorted(frozen - current)

    for m in new_modules:
        print(f"NEW get_*_service accessor: {m} — use constructor injection instead (docs/service-singletons.md)")
    for m in stale:
        print(f"STALE freeze entry (prune it): {m}")

    if not new_modules:
        print(f"singleton freeze OK: {len(current)} accessor modules, no growth")
    return 1 if new_modules else 0


if __name__ == "__main__":
    sys.exit(main())

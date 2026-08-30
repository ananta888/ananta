#!/usr/bin/env python3
"""Enforce collaboration core/adapter/Worker architecture boundaries."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = [
    ROOT / "ananta_contracts/collaboration_workspace.py",
    *sorted((ROOT / "agent/services").glob("collaboration_*.py")),
]


def main() -> int:
    violations: list[str] = []
    for path in CORE:
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            modules: set[str] = set()
            if isinstance(node, ast.Import):
                modules = {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = {node.module}
            if any(module == "worker" or module.startswith("worker.") for module in modules):
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}:core_worker_import")
            if any("nostr" in module.casefold() or "buzz" in module.casefold() for module in modules):
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}:core_external_bridge_import")
            if {module.split(".")[0] for module in modules} & {"pickle", "cloudpickle", "dill"}:
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}:unsafe_serializer_import")
    if violations:
        print("\n".join(violations))
        return 1
    print("collaboration-workspace-boundaries-ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

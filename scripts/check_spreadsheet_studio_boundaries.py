#!/usr/bin/env python3
"""Enforce spreadsheet Hub/runtime and unsafe document boundaries."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HUB_FILES = [
    ROOT / "ananta_contracts/spreadsheet_studio.py",
    ROOT / "agent/bootstrap/spreadsheet_studio.py",
    ROOT / "agent/routes/spreadsheet_studio.py",
    *sorted((ROOT / "agent/services").glob("spreadsheet_*.py")),
]


def main() -> int:
    violations: list[str] = []
    for path in HUB_FILES:
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            modules: set[str] = set()
            if isinstance(node, ast.Import):
                modules = {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = {node.module}
            roots = {module.split(".")[0] for module in modules}
            if roots & {"uno", "openpyxl", "odf", "torch", "transformers"}:
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}:hub_runtime_import")
            if any(module == "worker" or module.startswith("worker.") for module in modules):
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}:hub_worker_import")
            if roots & {"pickle", "cloudpickle", "dill"}:
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}:unsafe_serializer_import")
    if violations:
        print("\n".join(violations))
        return 1
    print("spreadsheet-studio-boundaries-ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

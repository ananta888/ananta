#!/usr/bin/env python3
"""Reject ML-runtime imports in Hub code and unsafe serialization in the experiment."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HUB_FILES = [
    ROOT / "ananta_contracts/dendritic_memory.py",
    ROOT / "agent/bootstrap/dendritic_memory.py",
    ROOT / "agent/routes/dendritic_memory.py",
    *sorted((ROOT / "agent/services").glob("dendritic_memory_*.py")),
]
EXPERIMENT_FILES = [*HUB_FILES, *sorted((ROOT / "worker/training/dendritic").glob("*.py"))]


def main() -> int:
    violations: list[str] = []
    for path in EXPERIMENT_FILES:
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            imported: set[str] = set()
            if isinstance(node, ast.Import):
                imported = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported = {node.module.split(".")[0]}
            if path in HUB_FILES and imported & {"torch", "transformers", "peft", "safetensors"}:
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}:hub_ml_runtime_import")
            if imported & {"pickle", "cloudpickle", "dill"}:
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}:unsafe_deserializer_import")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in {"pickle", "cloudpickle", "dill"}
                and node.func.attr in {"load", "loads"}
            ):
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}:unsafe_deserializer_call")
    if violations:
        print("\n".join(violations))
        return 1
    print("dendritic-memory-boundaries-ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

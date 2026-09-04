#!/usr/bin/env python3
"""Enforce Hub/Worker and serialization boundaries for research training."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HUB_FILES = [
    *sorted((ROOT / "ananta_contracts").glob("research_training*.py")),
    ROOT / "agent/bootstrap/research_training.py",
    ROOT / "agent/routes/research_training.py",
    *sorted((ROOT / "agent/services").glob("research_training_*.py")),
]
WORKER_FILES = [
    *sorted((ROOT / "worker/training/research").glob("*.py")),
    *sorted((ROOT / "worker/training/tasks").glob("*.py")),
    *sorted((ROOT / "worker/training/tokenizers").glob("*.py")),
]
ALL_FILES = [*HUB_FILES, *WORKER_FILES]
ML_RUNTIMES = {"torch", "transformers", "tokenizers", "accelerate", "deepspeed", "trl"}
UNSAFE_SERIALIZERS = {"pickle", "cloudpickle", "dill"}


def main() -> int:
    violations: list[str] = []
    for path in ALL_FILES:
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            modules: set[str] = set()
            if isinstance(node, ast.Import):
                modules = {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = {node.module}
            roots = {module.split(".")[0] for module in modules}
            if path in HUB_FILES and roots & ML_RUNTIMES:
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}:hub_ml_runtime_import")
            if path in WORKER_FILES and any(module == "agent" or module.startswith("agent.") for module in modules):
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}:worker_hub_import")
            if roots & UNSAFE_SERIALIZERS:
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}:unsafe_serializer_import")
    if violations:
        print("\n".join(violations))
        return 1
    print("research-training-boundaries-ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

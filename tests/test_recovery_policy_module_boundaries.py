from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY_MODULES = (
    ROOT / "agent/services/recovery_dispatch_contract.py",
    ROOT / "agent/services/recovery_task_merge_policy.py",
    ROOT / "agent/services/recovery_task_write_validation.py",
)


def test_recovery_value_and_transition_policies_are_repository_and_flask_free() -> None:
    forbidden_prefixes = ("agent.db_models", "agent.repositories", "flask")
    for path in POLICY_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports.update(
            node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        )
        violations = sorted(
            imported
            for imported in imports
            if imported.startswith(forbidden_prefixes)
        )
        assert violations == [], f"{path.name}: {violations}"

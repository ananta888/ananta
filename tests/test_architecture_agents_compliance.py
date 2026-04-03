from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_IMPORT_PREFIXES = (
    "agent.routes.tasks.autopilot",
    "agent.routes.tasks.orchestration",
    "agent.routes.tasks.orchestration_policy",
)


def _imported_modules(py_path: Path) -> list[str]:
    tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.append(node.module)
    return modules


def test_worker_modules_do_not_import_hub_orchestration_routes():
    worker_service_files = sorted((REPO_ROOT / "agent" / "services").glob("worker_*.py"))
    worker_gateway_file = REPO_ROOT / "agent" / "common" / "gateways" / "worker_gateway.py"
    candidates = [*worker_service_files, worker_gateway_file]

    violations: list[str] = []
    for py_path in candidates:
        for imported in _imported_modules(py_path):
            if imported.startswith(FORBIDDEN_IMPORT_PREFIXES):
                violations.append(f"{py_path.relative_to(REPO_ROOT)} -> {imported}")

    assert not violations, "AGENTS.md violation: worker modules must not import hub orchestration routes:\n" + "\n".join(violations)

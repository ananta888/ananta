#!/usr/bin/env python3
from __future__ import annotations

import ast
from pathlib import Path

ALLOWED_SERVICE_TO_ROUTE_IMPORTS = {
    ("agent.services.agent_registry_service", "agent.routes.tasks.orchestration_policy"),
    ("agent.services.app_runtime_service", "agent.routes.system"),
    ("agent.services.automation_snapshot_service", "agent.routes.tasks.auto_planner"),
    ("agent.services.autopilot_runtime_service", "agent.routes.tasks.autopilot"),
    ("agent.services.planning_service", "agent.routes.tasks.dependency_policy"),
    ("agent.services.task_claim_service", "agent.routes.tasks.orchestration_policy"),
    ("agent.services.task_management_service", "agent.routes.tasks.dependency_policy"),
    ("agent.services.task_management_service", "agent.routes.tasks.orchestration_policy"),
    ("agent.services.task_orchestration_service", "agent.routes.tasks.orchestration_policy"),
    ("agent.services.task_query_service", "agent.routes.tasks.timeline_utils"),
    ("agent.services.task_queue_service", "agent.routes.tasks.orchestration_policy.routing"),
    ("agent.services.task_scoped_execution_service", "agent.routes.tasks.orchestration_policy"),
    ("agent.services.trigger_runtime_service", "agent.routes.tasks.triggers"),
}


def module_name(path: Path) -> str:
    parts = path.with_suffix("").parts
    if "agent" in parts:
        parts = parts[parts.index("agent"):]
    return ".".join(parts)


def imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imports.append(node.module)
    return imports


def is_allowed(module: str, imported: str) -> bool:
    return any(module.startswith(mod) and imported.startswith(imp) for mod, imp in ALLOWED_SERVICE_TO_ROUTE_IMPORTS)


def check_service_boundaries(root: Path = Path("agent/services")) -> list[str]:
    violations: list[str] = []
    for path in sorted(root.rglob("*.py")):
        module = module_name(path)
        for imported in imported_modules(path):
            if imported.startswith("agent.routes") and not is_allowed(module, imported):
                violations.append(f"{module} imports {imported}")
    return violations


def main() -> int:
    violations = check_service_boundaries()
    if violations:
        print("Service boundary violations:")
        for violation in violations:
            print(f"- {violation}")
        return 1
    print("Service boundary check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

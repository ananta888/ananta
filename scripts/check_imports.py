#!/usr/bin/env python3
"""Enforce Python architecture boundaries without hiding legacy debt."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "reports" / "architecture-import-baseline.txt"

# A layer can import itself and the explicitly allowed layers only.
RULES = {
    "agent.routes": [
        "agent.services",
        "agent.common",
        "agent.models",
        "agent.db_models",
        "agent.auth",
        "agent.config",
        "agent.utils",
    ],
    "agent.services": [
        "agent.repositories",
        "agent.common",
        "agent.models",
        "agent.db_models",
        "agent.config",
        "agent.utils",
        "agent.auth",
    ],
    "agent.repositories": [
        "agent.common",
        "agent.models",
        "agent.db_models",
        "agent.config",
        "agent.utils",
    ],
    "agent.common": ["agent.config", "agent.utils"],
    "plugins": [
        "agent.services",
        "agent.common",
        "agent.models",
        "agent.config",
        "agent.utils",
    ],
}

FORBIDDEN_DIRECT = [("agent.routes", "agent.repositories")]

# Narrow compatibility exceptions that predate the baseline mechanism.
EXCEPTIONS = [
    ("agent.common.audit", "agent.services.hub_event_service"),
    ("agent.common.error_handler", "agent.services.log_service"),
    ("agent.common.sgpt", "agent.services.opencode_runtime_service"),
    ("agent.common.sgpt", "agent.services.live_terminal_session_service"),
    ("agent.common.signals", "agent.services.scheduler_service"),
    ("agent.services.agent_registry_service", "agent.routes.tasks.orchestration_policy"),
    ("agent.services.app_runtime_service", "agent.routes.system"),
    ("agent.services.automation_snapshot_service", "agent.routes.tasks.auto_planner"),
    ("agent.services.autopilot_runtime_service", "agent.routes.tasks.autopilot"),
    ("agent.services.planning_service", "agent.routes.tasks.dependency_policy"),
    ("agent.services.task_claim_service", "agent.routes.tasks.orchestration_policy"),
    ("agent.services.task_delegation_services", "agent.routes.tasks.orchestration_policy"),
    ("agent.services.task_management_service", "agent.routes.tasks.dependency_policy"),
    ("agent.services.task_management_service", "agent.routes.tasks.orchestration_policy"),
    ("agent.services.task_orchestration_service", "agent.routes.tasks.orchestration_policy"),
    ("agent.services.task_query_service", "agent.routes.tasks.timeline_utils"),
    ("agent.services.task_queue_service", "agent.routes.tasks.orchestration_policy.routing"),
    ("agent.services.task_scoped_execution_service", "agent.routes.tasks.orchestration_policy"),
    ("agent.services.trigger_runtime_service", "agent.routes.tasks.triggers"),
]


def get_module_name(file_path: Path) -> str:
    return ".".join(file_path.relative_to(ROOT).with_suffix("").parts)


def _imported_modules(tree: ast.AST) -> list[str]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.append(node.module)
    return modules


def check_file_imports(file_path: Path) -> set[str]:
    module_name = get_module_name(file_path)
    current_layer = next(
        (layer for layer in RULES if module_name.startswith(layer)),
        None,
    )
    if current_layer is None:
        return set()

    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    except (OSError, SyntaxError) as exc:
        raise RuntimeError(f"architecture_scan_parse_failed:{file_path}:{exc}") from exc

    violations: set[str] = set()
    for imported_module in _imported_modules(tree):
        if any(
            module_name.startswith(module_prefix)
            and imported_module.startswith(import_prefix)
            for module_prefix, import_prefix in EXCEPTIONS
        ):
            continue
        for other_layer in RULES:
            if (
                imported_module.startswith(other_layer)
                and other_layer != current_layer
                and other_layer not in RULES[current_layer]
            ):
                violations.add(
                    f"Layer violation: {module_name} imports {imported_module} "
                    f"(Layer {current_layer} -> {other_layer} not allowed)"
                )
        for layer, forbidden in FORBIDDEN_DIRECT:
            if module_name.startswith(layer) and imported_module.startswith(forbidden):
                violations.add(
                    f"Forbidden direct import: {module_name} imports {imported_module} "
                    f"(Direct access to {forbidden} from {layer} is prohibited)"
                )
    return violations


def collect_violations() -> set[str]:
    violations: set[str] = set()
    for search_dir in ("agent", "plugins"):
        for path in sorted((ROOT / search_dir).rglob("*.py")):
            violations.update(check_file_imports(path))
    return violations


def load_baseline() -> set[str]:
    if not BASELINE_PATH.is_file():
        raise RuntimeError(f"architecture_baseline_missing:{BASELINE_PATH}")
    return {
        line.strip()
        for line in BASELINE_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }


def main() -> int:
    print("--- Checking Architecture Boundaries (BND-010/BND-011) ---")
    current = collect_violations()
    baseline = load_baseline()
    new_violations = current - baseline
    resolved_violations = baseline - current

    for violation in sorted(new_violations):
        print(f"❌ New architecture violation: {violation}")
    for violation in sorted(resolved_violations):
        print(f"❌ Baseline contains a resolved violation; remove it: {violation}")

    if new_violations or resolved_violations:
        print(
            "\nArchitecture boundary check failed: "
            f"{len(new_violations)} new, {len(resolved_violations)} stale baseline entries."
        )
        return 1

    print(
        "✅ No new architecture boundary violations "
        f"({len(baseline)} legacy violations remain explicitly baselined)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

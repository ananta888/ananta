import os
import sys
import ast
import re
from typing import List, Tuple, Dict

# Architecture Rule Definition:
# layer: [allowed_imports]
# A layer can only import from itself or from allowed_imports.
# It MUST NOT import from layers that are not listed.

RULES = {
    "agent.routes": ["agent.services", "agent.common", "agent.models", "agent.db_models", "agent.auth", "agent.config", "agent.utils"],
    "agent.services": ["agent.repositories", "agent.common", "agent.models", "agent.db_models", "agent.config", "agent.utils", "agent.auth"],
    "agent.repositories": ["agent.common", "agent.models", "agent.db_models", "agent.config", "agent.utils"],
    "agent.common": ["agent.config", "agent.utils"],
    "plugins": ["agent.services", "agent.common", "agent.models", "agent.config", "agent.utils"]
}

# Explicitly forbidden: Routes must not import from repositories directly (they should go through services)
FORBIDDEN_DIRECT = [
    ("agent.routes", "agent.repositories")
]

# Temporary exceptions for existing violations (Technical Debt)
# These should be resolved over time and removed from this list.
EXCEPTIONS = [
    # common -> services
    ("agent.common.audit", "agent.services.hub_event_service"),
    ("agent.common.error_handler", "agent.services.log_service"),
    ("agent.common.sgpt", "agent.services.opencode_runtime_service"),
    ("agent.common.sgpt", "agent.services.live_terminal_session_service"),
    ("agent.common.signals", "agent.services.scheduler_service"),

    # services -> routes (mostly task orchestration policies that should probably be in services)
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
]

def get_module_name(file_path: str) -> str:
    """Converts a file path to a python module name."""
    parts = file_path.replace(".py", "").replace("\\", "/").split("/")
    if parts[0] == ".":
        parts = parts[1:]
    return ".".join(parts)

def check_file_imports(file_path: str) -> List[str]:
    violations = []
    module_name = get_module_name(file_path)

    # Determine the layer of the current file
    current_layer = None
    for layer in RULES:
        if module_name.startswith(layer):
            current_layer = layer
            break

    if not current_layer:
        return []

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=file_path)
    except Exception as e:
        print(f"Error parsing {file_path}: {e}")
        return []

    allowed = RULES[current_layer]

    for node in ast.walk(tree):
        imported_module = None
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_module = alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0: # Absolute import
                imported_module = node.module
            else: # Relative import
                # For simplicity, we skip complex relative import resolution for now
                continue

        if imported_module:
            # Check if this is an allowed exception
            is_exception = False
            for mod_prefix, imp_prefix in EXCEPTIONS:
                if module_name.startswith(mod_prefix) and imported_module.startswith(imp_prefix):
                    is_exception = True
                    break

            if is_exception:
                continue

            # Check if importing from another internal layer
            for other_layer in RULES:
                if imported_module.startswith(other_layer) and other_layer != current_layer:
                    if other_layer not in allowed:
                        violations.append(f"Layer violation: {module_name} imports {imported_module} (Layer {current_layer} -> {other_layer} not allowed)")

            # Check for explicitly forbidden direct imports
            for layer, forbidden in FORBIDDEN_DIRECT:
                if module_name.startswith(layer) and imported_module.startswith(forbidden):
                    violations.append(f"Forbidden direct import: {module_name} imports {imported_module} (Direct access to {forbidden} from {layer} is prohibited)")

    return violations

def main():
    print("--- Checking Architecture Boundaries (BND-010/BND-011) ---")
    all_violations = []

    search_dirs = ["agent", "plugins"]
    for root_dir in search_dirs:
        for root, _, files in os.walk(root_dir):
            for file in files:
                if file.endswith(".py"):
                    path = os.path.join(root, file)
                    violations = check_file_imports(path)
                    if violations:
                        all_violations.extend(violations)

    if all_violations:
        for v in all_violations:
            print(f"❌ {v}")
        print(f"\nFound {len(all_violations)} architecture violations.")
        sys.exit(1)
    else:
        print("✅ No architecture boundary violations found.")
        sys.exit(0)

if __name__ == "__main__":
    main()

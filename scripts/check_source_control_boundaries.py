#!/usr/bin/env python3
"""Static no-bypass gate for Source Control Center boundaries."""

from __future__ import annotations

import argparse
import ast
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

_FORBIDDEN_AGENT_IMPORTS = (
    "worker.core.context_access_policy",
    "worker.retrieval.codecompass_graph_store",
    "worker.retrieval.codecompass_graph_expansion",
    "worker.retrieval.codecompass_architecture_query",
)
_FORBIDDEN_CONNECTOR_IMPORT_PREFIXES = (
    "agent.routes",
    "agent.services.task_",
    "agent.services.planning",
    "worker.",
)
_PUBLIC_ROUTE_FILES = (
    "agent/routes/sources.py",
    "agent/routes/knowledge.py",
    "agent/routes/codecompass_graph.py",
    "agent/routes/codecompass_domain_scope.py",
    "agent/routes/codecompass_reload.py",
    "agent/routes/context_policy.py",
)
_FRONTEND_POLICY_FILES = (
    "frontend-angular/src/app/features/codehug/services/policy.service.ts",
    "frontend-angular/src/app/features/context-access-policy/policy-overview.component.ts",
    "frontend-angular/src/app/services/context-access-policy-api.service.ts",
)
_FORBIDDEN_FRONTEND_SNIPPETS = (
    "default-project",
    "decision || 'allow'",
    'decision || "allow"',
    "decision ?? 'allow'",
    'decision ?? "allow"',
)
_V1_EXTENSION_BLUEPRINTS = {
    "agent/routes/source_control_git_authorizations.py": (
        "agent.routes.source_control_git_authorizations",
        "create_source_control_git_authorizations_blueprint",
        "_access_guard",
    ),
}


@dataclass(frozen=True)
class BoundaryViolation:
    code: str
    path: str
    line: int
    detail: str


def check_source_control_boundaries(root: Path) -> list[BoundaryViolation]:
    violations: list[BoundaryViolation] = []
    violations.extend(_check_agent_imports(root))
    violations.extend(_check_indirect_hub_worker_imports(root))
    violations.extend(_check_connector_imports(root))
    violations.extend(_check_route_authentication(root))
    violations.extend(_check_bootstrap_bypasses(root))
    violations.extend(_check_frontend_fail_open_literals(root))
    return sorted(
        violations,
        key=lambda item: (item.path, item.line, item.code),
    )


def _python_files(root: Path, relative_root: str) -> Iterable[Path]:
    directory = root / relative_root
    if not directory.exists():
        return ()
    return directory.rglob("*.py")


def _imports(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            yield node, str(node.module or "")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield node, alias.name


def _dynamic_imports(tree: ast.AST):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        name = _decorator_name(node.func)
        first = node.args[0]
        if (
            name in {"import_module", "__import__"}
            and isinstance(first, ast.Constant)
            and isinstance(first.value, str)
        ):
            yield node, first.value


def _check_agent_imports(root: Path) -> list[BoundaryViolation]:
    violations: list[BoundaryViolation] = []
    for path in _python_files(root, "agent"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node, module in _imports(tree):
            if module in _FORBIDDEN_AGENT_IMPORTS:
                violations.append(
                    BoundaryViolation(
                        code="hub_imports_worker_implementation",
                        path=str(path.relative_to(root)),
                        line=node.lineno,
                        detail=module,
                    )
                )
    return violations


def _module_name(root: Path, path: Path) -> str:
    relative = path.relative_to(root).with_suffix("")
    parts = list(relative.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _check_indirect_hub_worker_imports(
    root: Path,
) -> list[BoundaryViolation]:
    """Trace Source-Control Hub entrypoints through local imports."""

    modules: dict[str, Path] = {}
    imports: dict[str, list[tuple[ast.AST, str]]] = {}
    for path in _python_files(root, "agent"):
        module = _module_name(root, path)
        modules[module] = path
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports[module] = [
            *list(_imports(tree)),
            *list(_dynamic_imports(tree)),
        ]
    roots = sorted(
        module
        for module, path in modules.items()
        if (
            str(path.relative_to(root))
            == "agent/bootstrap/source_control_api.py"
            or path.name.startswith("source_control")
            and any(
                part in {"routes", "services", "sources"}
                for part in path.parts
            )
        )
    )
    def source_control_owned(module: str) -> bool:
        return (
            module == "agent.bootstrap.source_control_api"
            or module.startswith("agent.routes.source_control")
            or module.startswith("agent.services.source_control")
            or module.startswith("agent.sources.source_control")
        )

    violations: dict[tuple[str, int, str], BoundaryViolation] = {}
    for entrypoint in roots:
        # Follow Source-Control modules transitively. For an adjacent general
        # Hub module, inspect one hop for a direct Worker import but do not
        # attribute its entire dependency graph to Source-Control.
        queue: list[tuple[str, tuple[str, ...], int]] = [
            (entrypoint, (entrypoint,), 1)
        ]
        visited: set[tuple[str, int]] = set()
        while queue:
            module, chain, bridge_budget = queue.pop(0)
            visit_key = (module, bridge_budget)
            if visit_key in visited:
                continue
            visited.add(visit_key)
            for node, dependency in imports.get(module, ()):
                if dependency == "worker" or dependency.startswith("worker."):
                    path = modules[module]
                    key = (
                        str(path.relative_to(root)),
                        int(getattr(node, "lineno", 0)),
                        dependency,
                    )
                    violations[key] = BoundaryViolation(
                        code="hub_indirectly_imports_worker_implementation",
                        path=key[0],
                        line=key[1],
                        detail=" -> ".join((*chain, dependency)),
                    )
                    continue
                candidate = dependency
                while candidate and candidate not in modules:
                    candidate = candidate.rpartition(".")[0]
                if not candidate or candidate in chain:
                    continue
                next_bridge_budget = bridge_budget
                if not source_control_owned(candidate):
                    if bridge_budget == 0:
                        continue
                    next_bridge_budget -= 1
                queue.append(
                    (candidate, (*chain, candidate), next_bridge_budget)
                )
    return list(violations.values())


def _check_connector_imports(root: Path) -> list[BoundaryViolation]:
    violations: list[BoundaryViolation] = []
    for path in _python_files(root, "agent/sources"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node, module in _imports(tree):
            if module.startswith(_FORBIDDEN_CONNECTOR_IMPORT_PREFIXES):
                violations.append(
                    BoundaryViolation(
                        code="connector_orchestrates_or_depends_on_worker",
                        path=str(path.relative_to(root)),
                        line=node.lineno,
                        detail=module,
                    )
                )
    return violations


def _check_route_authentication(root: Path) -> list[BoundaryViolation]:
    violations: list[BoundaryViolation] = []
    route_files = set(_PUBLIC_ROUTE_FILES)
    routes_root = root / "agent/routes"
    if routes_root.exists():
        for path in routes_root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "/api/source-control/v1" in text:
                route_files.add(str(path.relative_to(root)))
    for relative_path in sorted(route_files):
        path = root / relative_path
        if not path.exists():
            violations.append(
                BoundaryViolation(
                    code="public_route_file_missing",
                    path=relative_path,
                    line=0,
                    detail="required route module does not exist",
                )
            )
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            route_decorators = [
                decorator
                for decorator in node.decorator_list
                if _is_route_decorator(decorator)
            ]
            if not route_decorators:
                continue
            decorator_names = {
                _decorator_name(decorator)
                for decorator in node.decorator_list
            }
            if not {"check_auth", "admin_required"} & decorator_names:
                violations.append(
                    BoundaryViolation(
                        code="public_route_missing_auth",
                        path=relative_path,
                        line=node.lineno,
                        detail=node.name,
                    )
                )
    return violations


def _check_bootstrap_bypasses(root: Path) -> list[BoundaryViolation]:
    violations: list[BoundaryViolation] = []
    allowed = {
        "agent/bootstrap/source_control_api.py",
        "agent/services/source_control_api_runtime.py",
        "agent/routes/source_control_v1.py",
    }
    sensitive_symbols = {
        "build_source_control_api_runtime",
        "create_source_control_v1_blueprint",
    }
    for path in _python_files(root, "agent"):
        relative = str(path.relative_to(root))
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if relative not in allowed:
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    name = _decorator_name(node.func)
                    if name in sensitive_symbols:
                        violations.append(
                            BoundaryViolation(
                                code="source_control_bootstrap_bypass",
                                path=relative,
                                line=node.lineno,
                                detail=name,
                            )
                        )
        if relative.startswith("agent/routes/") and relative not in {
            "agent/routes/source_control_v1.py",
            "agent/routes/source_control_operations.py",
        }:
            text = path.read_text(encoding="utf-8")
            if (
                "/api/source-control/v1" in text
                and not _verified_v1_extension(
                    root=root,
                    relative=relative,
                    route_tree=tree,
                )
            ):
                violations.append(
                    BoundaryViolation(
                        code="source_control_v1_route_outside_canonical_bootstrap",
                        path=relative,
                        line=1,
                        detail="/api/source-control/v1",
                    )
                )
    bootstrap = root / "agent/bootstrap/source_control_api.py"
    if bootstrap.exists():
        text = bootstrap.read_text(encoding="utf-8")
        for marker in (
            "SourceControlRuntimeObservability",
            "SourceControlRolloutPolicy",
            "create_source_control_operations_blueprint",
        ):
            if marker not in text:
                violations.append(
                    BoundaryViolation(
                        code="source_control_bootstrap_guard_missing",
                        path=str(bootstrap.relative_to(root)),
                        line=1,
                        detail=marker,
                    )
                )
    return violations


def _verified_v1_extension(
    *,
    root: Path,
    relative: str,
    route_tree: ast.AST,
) -> bool:
    """Verify one explicit extension from route definition to bootstrap."""

    specification = _V1_EXTENSION_BLUEPRINTS.get(relative)
    if specification is None:
        return False
    module_name, factory_name, access_guard = specification
    prefix_verified = any(
        isinstance(node, ast.Call)
        and _decorator_name(node.func) == "Blueprint"
        and any(
            keyword.arg == "url_prefix"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value == "/api/source-control/v1"
            for keyword in node.keywords
        )
        for node in ast.walk(route_tree)
    )
    route_functions = [
        node
        for node in ast.walk(route_tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(_is_route_decorator(item) for item in node.decorator_list)
    ]
    route_guards_verified = bool(route_functions) and all(
        {"check_auth", access_guard}.issubset(
            {
                _decorator_name(decorator)
                for decorator in node.decorator_list
            }
        )
        for node in route_functions
    )
    bootstrap = root / "agent/bootstrap/source_control_api.py"
    if not bootstrap.exists():
        return False
    bootstrap_tree = ast.parse(
        bootstrap.read_text(encoding="utf-8"),
        filename=str(bootstrap),
    )
    import_verified = any(
        isinstance(node, ast.ImportFrom)
        and node.module == module_name
        and any(alias.name == factory_name for alias in node.names)
        for node in ast.walk(bootstrap_tree)
    )
    registration_verified = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "register_blueprint"
        and bool(node.args)
        and isinstance(node.args[0], ast.Call)
        and _decorator_name(node.args[0].func) == factory_name
        for node in ast.walk(bootstrap_tree)
    )
    return all(
        (
            prefix_verified,
            route_guards_verified,
            import_verified,
            registration_verified,
        )
    )


def _is_route_decorator(node: ast.expr) -> bool:
    if not isinstance(node, ast.Call):
        return False
    function = node.func
    return (
        isinstance(function, ast.Attribute)
        and function.attr in {"route", "get", "post", "put", "patch", "delete"}
    )


def _decorator_name(node: ast.expr) -> str:
    target = node.func if isinstance(node, ast.Call) else node
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return ""


def _check_frontend_fail_open_literals(
    root: Path,
) -> list[BoundaryViolation]:
    violations: list[BoundaryViolation] = []
    for relative_path in _FRONTEND_POLICY_FILES:
        path = root / relative_path
        if not path.exists():
            continue
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            for snippet in _FORBIDDEN_FRONTEND_SNIPPETS:
                if snippet in line:
                    violations.append(
                        BoundaryViolation(
                            code="frontend_fail_open_literal",
                            path=relative_path,
                            line=line_number,
                            detail=snippet,
                        )
                    )
    return violations


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    violations = check_source_control_boundaries(args.root.resolve())
    payload = {
        "schema": "ananta.source-control.no-bypass-report.v1",
        "status": "passed" if not violations else "failed",
        "violation_count": len(violations),
        "violations": [asdict(item) for item in violations],
    }
    serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(serialized, encoding="utf-8")
    else:
        print(serialized, end="")
    return 0 if not violations else 1


if __name__ == "__main__":
    raise SystemExit(main())

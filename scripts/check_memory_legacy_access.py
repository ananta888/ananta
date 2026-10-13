#!/usr/bin/env python3
"""Prevent known raw memory repository ports from reappearing in consumers.

This is an AST architecture guard, not a Python sandbox or dynamic-flow proof.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW_PORT_MODULES = frozenset({
    "agent/repositories/memory.py", "agent/repositories/memory_migration.py",
    "agent/services/result_memory_service.py", "agent/services/legacy_memory_access.py",
})
METHODS = frozenset({"get_by_task", "get_by_goal", "get_by_id", "get_by_namespace", "save"})


def legacy_access_violations(path, source):
    if path in RAW_PORT_MODULES:
        return []
    tree = ast.parse(source, filename=path)
    raw_names, constructors, scoped_names, repository_modules = set(), set(), set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "memory_entry_repo":
                    raw_names.add(alias.asname or alias.name)
                if alias.name == "MemoryEntryRepository":
                    constructors.add(alias.asname or alias.name)
                if node.module == "agent.repositories" and alias.name == "memory":
                    repository_modules.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in {"agent.repository", "agent.repositories.memory"}:
                    repository_modules.add(alias.asname or alias.name)

    def qualified_name(node):
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parent = qualified_name(node.value)
            return f"{parent}.{node.attr}" if parent else None
        return None

    def label(node):
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self":
            return "self." + node.attr
        return None

    def is_raw(node):
        name = label(node)
        if name in scoped_names:
            return False
        if name in raw_names:
            return True
        if isinstance(node, ast.Attribute) and node.attr == "memory_entry_repo":
            return True
        if isinstance(node, ast.Call):
            function = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            module_constructor = (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "MemoryEntryRepository"
                and qualified_name(node.func.value) in repository_modules
            )
            return function in constructors or function == "get_memory_entry_repository" or module_constructor
        if isinstance(node, ast.BoolOp):
            return any(is_raw(item) for item in node.values)
        return False

    # Resolve aliases without executing source; loops are bounded by assignment count.
    assignments = [node for node in ast.walk(tree) if isinstance(node, (ast.Assign, ast.AnnAssign))]
    for _ in range(len(assignments) + 1):
        before = (frozenset(raw_names), frozenset(scoped_names))
        for node in assignments:
            targets = [label(target) for target in
                       (node.targets if isinstance(node, ast.Assign) else [node.target])]
            function = node.value.func if isinstance(node.value, ast.Call) else None
            if isinstance(function, ast.Name) and function.id == "scoped_memory_reader":
                scoped_names.update(target for target in targets if target)
            elif is_raw(node.value):
                raw_names.update(target for target in targets if target)
        if before == (frozenset(raw_names), frozenset(scoped_names)):
            break
    return [f"{path}:{node.lineno}:unscoped_memory_port:{node.attr}"
            for node in ast.walk(tree) if isinstance(node, ast.Attribute)
            and node.attr in METHODS and is_raw(node.value)]


def main():
    failures = []
    for root in ("agent", "worker", "client_surfaces"):
        for path in sorted((ROOT / root).rglob("*.py")):
            failures.extend(legacy_access_violations(path.relative_to(ROOT).as_posix(), path.read_text()))
    if failures:
        print("\n".join(failures))
        return 1
    print("memory-legacy-access-ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

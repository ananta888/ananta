#!/usr/bin/env python3
"""Detector: module-level import cycles inside the hub/worker source trees.

Pure standard library (no networkx — the previous implementation never ran
because networkx was not a declared dependency). The check walks ``agent/``
and ``worker/`` (including cross-tree edges, e.g. ``agent.services`` <->
``worker.core``), resolves absolute and relative imports to real modules and
reports strongly connected components as cycles.

Known, tracked cycles live in ``reports/import-cycle-baseline.txt`` (one line
per cycle group: comma-separated sorted module names). A cycle NOT in the
baseline fails the check; a baseline entry that no longer exists is reported
so the baseline can be pruned. New cycles must be broken or explicitly
baselined with a justification — never silently tolerated.

Function-level (lazy) imports are intentionally NOT tracked: they are the
sanctioned escape hatch for breaking module-level cycles.

Exit codes:
- 0: no new module-level import cycles
- 1: one or more cycles outside the baseline
- 2: the detector itself failed

Usage:
    python scripts/check_cycles.py [--json]
"""

from __future__ import annotations

import ast
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOTS = ("agent", "worker")
BASELINE_PATH = Path("reports/import-cycle-baseline.txt")


def _module_name(path: Path, root: Path) -> str:
    rel = path.relative_to(root.parent)
    parts = list(rel.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _collect_modules(root: Path) -> dict[str, Path]:
    modules: dict[str, Path] = {}
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        modules[_module_name(path, root)] = path
    return modules


def _resolve_relative(module: str, level: int, imported: str | None) -> str | None:
    parts = module.split(".")
    # For a module ``a.b.c`` (a file), level 1 = package ``a.b``.
    pkg = parts[: len(parts) - (1 if parts else 0)]
    if level - 1 > len(pkg):
        return None
    base = pkg[: len(pkg) - (level - 1)]
    if imported:
        base = base + imported.split(".")
    return ".".join(base) if base else None


def _module_level_imports(path: Path) -> list[tuple[int, str | None, list[str]]]:
    """Return (level, module, names) for every module-level import statement."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except SyntaxError:
        return []
    out: list[tuple[int, str | None, list[str]]] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            out.append((0, None, [alias.name for alias in node.names]))
        elif isinstance(node, ast.ImportFrom):
            out.append((node.level, node.module, [alias.name for alias in node.names]))
    return out


def collect_modules(roots: tuple[Path, ...] | list[Path]) -> dict[str, Path]:
    modules: dict[str, Path] = {}
    for root in roots:
        modules.update(_collect_modules(root))
    return modules


def build_graph(
    root: Path | None = None,
    modules: dict[str, Path] | None = None,
) -> dict[str, set[str]]:
    """Map module -> set of internal modules imported at module level.

    Pass ``modules`` (built from ALL roots) so cross-tree edges (agent <->
    worker) resolve; passing only ``root`` limits resolution to that tree.
    """
    if modules is None:
        assert root is not None
        modules = _collect_modules(root)
    graph: dict[str, set[str]] = defaultdict(set)
    for module, path in modules.items():
        edges = graph[module]
        for level, imported, names in _module_level_imports(path):
            if level:
                target = _resolve_relative(module, level, imported)
                if target:
                    if target in modules:
                        edges.add(target)
                    for name in names:
                        cand = f"{target}.{name}"
                        if cand in modules:
                            edges.add(cand)
            else:
                for name in names:
                    if imported is not None:
                        # from X import a, b — X plus any submodule a/b
                        if imported in modules:
                            edges.add(imported)
                        cand = f"{imported}.{name}"
                        if cand in modules:
                            edges.add(cand)
                    else:
                        # import a.b.c — full name, and parents are bound too
                        if name in modules:
                            edges.add(name)
        edges.discard(module)
    return dict(graph)


def _sccs(graph: dict[str, set[str]]) -> list[list[str]]:
    """Iterative Tarjan SCC; returns components with more than one member or a
    self-loop (self-loops were discarded in build_graph, so only size > 1)."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    counter = 0
    result: list[list[str]] = []

    for start in graph:
        if start in index:
            continue
        work: list[tuple[str, object]] = [(start, iter(sorted(graph.get(start, ()))))]
        index[start] = low[start] = counter
        counter += 1
        stack.append(start)
        on_stack.add(start)
        while work:
            node, it = work[-1]
            advanced = False
            for succ in it:
                if succ not in graph:
                    continue
                if succ not in index:
                    index[succ] = low[succ] = counter
                    counter += 1
                    stack.append(succ)
                    on_stack.add(succ)
                    work.append((succ, iter(sorted(graph.get(succ, ())))))
                    advanced = True
                    break
                if succ in on_stack:
                    low[node] = min(low[node], index[succ])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                comp: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    comp.append(member)
                    if member == node:
                        break
                if len(comp) > 1:
                    result.append(sorted(comp))
    return result


def _representative_cycle(graph: dict[str, set[str]], component: list[str]) -> list[str]:
    """One concrete cycle path inside a component (BFS from the first member)."""
    start = component[0]
    members = set(component)
    parent: dict[str, str] = {}
    queue = [start]
    seen = {start}
    while queue:
        node = queue.pop(0)
        for succ in sorted(graph.get(node, ())):
            if succ not in members:
                continue
            if succ == start:
                path = [node]
                while path[-1] != start:
                    path.append(parent[path[-1]])
                path.reverse()
                return path + [start]
            if succ not in seen:
                seen.add(succ)
                parent[succ] = node
                queue.append(succ)
    return component + [component[0]]


def _baseline_keys() -> dict[str, str]:
    if not BASELINE_PATH.exists():
        return {}
    out: dict[str, str] = {}
    for raw in BASELINE_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "#" in line:
            members, reason = line.split("#", 1)
        else:
            members, reason = line, ""
        out[members.strip()] = reason.strip()
    return out


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--json":
        as_json = True
    else:
        as_json = False

    roots = []
    for root_name in ROOTS:
        root = Path(root_name)
        if not root.exists():
            print(f"check_cycles: root {root_name} missing", file=sys.stderr)
            return 2
        roots.append(root)
    modules = collect_modules(roots)
    graph = dict(build_graph(modules=modules))

    components = _sccs(graph)
    baseline = _baseline_keys()

    new_cycles = []
    for comp in components:
        key = ",".join(comp)
        if key not in baseline:
            new_cycles.append(
                {
                    "members": comp,
                    "example": " -> ".join(_representative_cycle(graph, comp)),
                }
            )

    stale = sorted(set(baseline) - {",".join(c) for c in components})

    if as_json:
        print(json.dumps({"new_cycles": new_cycles, "stale_baseline": stale}, indent=2))
    else:
        print(
            f"check_cycles: {len(graph)} modules, {sum(len(v) for v in graph.values())} internal edges, "
            f"{len(components)} cycle groups ({len(baseline)} baselined)"
        )
        for entry in new_cycles:
            print(f"NEW CYCLE: {entry['example']}")
        for key in stale:
            print(f"STALE BASELINE (prune it): {key}")
        if not new_cycles:
            print("No new import cycles.")

    return 1 if new_cycles else 0


if __name__ == "__main__":
    sys.exit(main())

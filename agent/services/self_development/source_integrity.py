"""Removed-symbol import integrity for the publication verify chain.

A change set may delete a top-level name that the rest of the tree still
imports. The behavioural test profile can pass while the next import of the
module fails (observed: ``split_ordered``/``pack_parts`` removed while
``long_context_coordinator`` kept importing them), so publication refuses such
a change set before it lands.

SRP: the pure core answers "which removed names are still module-referenced";
the glue reads before/after sources and raises one precise reason. Wired into
the coordinator verify chain, which the publisher re-runs for every action.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Mapping


def _toplevel_names(source: str) -> set[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _dotted_chain(node: ast.AST) -> list[str] | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        parts.reverse()
        return parts
    return None


def _module_aliases(tree: ast.AST, module_dotted: str) -> set[str]:
    """Local names bound to the module itself (``import m as x``, parent imports)."""
    tail = module_dotted.rsplit(".", 1)[-1]
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == module_dotted or f"{node.module or ''}.{tail}" == module_dotted:
                for alias in node.names:
                    if alias.name == tail:
                        aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == module_dotted and alias.asname:
                    aliases.add(alias.asname)
    return aliases


def _references_module_name(tree: ast.AST, module_dotted: str, name: str) -> bool:
    aliases = _module_aliases(tree, module_dotted)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == module_dotted:
            for alias in node.names:
                if alias.name in {"*", name}:
                    return True
        elif isinstance(node, ast.Attribute) and node.attr == name:
            chain = _dotted_chain(node.value)
            if not chain:
                continue
            if ".".join(chain) == module_dotted or (len(chain) == 1 and chain[0] in aliases):
                return True
    return False


def removed_imported_symbols(
    before: str,
    after: str,
    module_dotted: str,
    tree_sources: Mapping[str, str],
) -> list[str]:
    """Removed top-level names of ``module_dotted`` still imported anywhere in the tree."""
    removed = sorted(_toplevel_names(before) - _toplevel_names(after))
    if not removed:
        return []
    tail = module_dotted.rsplit(".", 1)[-1]
    candidates: list[ast.AST] = []
    for source in tree_sources.values():
        if tail not in source:
            continue
        # A star import references every removed name without naming it, so it
        # must survive the cheap name prefilter.
        if not any(name in source for name in removed) and "import *" not in source:
            continue
        try:
            candidates.append(ast.parse(source))
        except SyntaxError:
            continue
    hits: list[str] = []
    for name in removed:
        if any(_references_module_name(tree, module_dotted, name) for tree in candidates):
            hits.append(name)
    return hits


def assert_removed_symbols_unreferenced(policy: Any, item: Mapping[str, Any]) -> None:
    """Raise ``removed_symbol_still_imported:<file>:<name>`` when the change set breaks imports.

    Before-state comes from the admitted revision in the private checkout,
    after-state from the sealed change set (overlaid onto the checkout view so
    sibling changed files are judged by their new content). Unresolvable
    before-states (added files) pass: there is nothing removed to reference.
    """
    from agent.services.self_development.workspace import git, read_source

    hashes = dict(item.get("hashes") or {})
    rels = [rel for rel in hashes if str(rel).endswith(".py")]
    revision = str(item.get("revision") or "").strip()
    if not rels or not revision:
        return
    checkout = Path(policy.checkout)
    seal = Path(policy.root) / "sealed" / str(item.get("task_id") or "")
    tree_sources: dict[str, str] = {}
    for path in checkout.rglob("*.py"):
        if ".git" in path.parts:
            continue
        try:
            tree_sources[str(path.relative_to(checkout))] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
    for rel in rels:
        try:
            tree_sources[rel] = read_source(seal, rel).decode("utf-8", errors="replace")
        except (ValueError, OSError):
            continue
    for rel in rels:
        try:
            before = git(checkout, "show", f"{revision}:{rel}")
        except ValueError:
            continue
        after = tree_sources.get(rel, "")
        module_dotted = str(rel)[: -len(".py")].replace("/", ".")
        for name in removed_imported_symbols(before, after, module_dotted, tree_sources):
            raise ValueError(f"removed_symbol_still_imported:{rel}:{name}")

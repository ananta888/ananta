"""Path-focused ranking helpers for the repository map engine."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping, Sequence


def resolve_path_focus(
    query: str,
    paths: list[str],
    *,
    aliases: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, object] | None:
    query_label = _normalize_label(query)
    if not query_label:
        return None
    candidate_roots: dict[str, int] = {}
    for rel_path in paths:
        parts = [part for part in str(rel_path or "").replace("\\", "/").split("/") if part]
        for depth in (1, 2):
            if len(parts) < depth:
                continue
            root = "/".join(parts[:depth])
            label = _normalize_label(root)
            basename_label = _normalize_label(parts[depth - 1])
            if not label or len(basename_label) < 4:
                continue
            if label in query_label or basename_label in query_label:
                candidate_roots[root] = max(candidate_roots.get(root, 0), len(label))

    alias_roots_set: set[str] = set()
    for alias_keyword, alias_roots in dict(aliases or {}).items():
        if alias_keyword not in query_label:
            continue
        for raw_root in list(alias_roots or []):
            alias_root = str(raw_root)
            alias_label = _normalize_label(alias_root)
            if alias_root not in candidate_roots:
                candidate_roots[alias_root] = len(alias_label)
            alias_roots_set.add(alias_root)
    if not candidate_roots:
        return None

    roots = sorted(candidate_roots, key=lambda item: (-candidate_roots[item], item))
    preferred = [root for root in roots if "/" in root] or roots[:1]
    all_anchor_paths = _anchor_paths(roots, paths)
    alias_root_prefixes = tuple(f"{root.rstrip('/')}/" for root in alias_roots_set)
    alias_anchor_paths = [
        path
        for path in all_anchor_paths
        if any(path.startswith(prefix) for prefix in alias_root_prefixes) or path in alias_roots_set
    ]
    return {
        "id": "query-path-focus",
        "paths": tuple(f"{root.rstrip('/')}/" for root in roots),
        "preferred_paths": tuple(f"{root.rstrip('/')}/" for root in preferred),
        "anchor_paths": tuple(all_anchor_paths),
        "alias_anchor_paths": tuple(alias_anchor_paths),
        "min_results": min(4, max(2, len(preferred) + 1)),
    }


def path_is_in_focus(path: str, focus: dict[str, object] | None, *, preferred_only: bool = False) -> bool:
    if not focus:
        return False
    prefixes = list(focus.get("preferred_paths") or []) if preferred_only else list(focus.get("paths") or [])
    normalized = str(path or "").replace("\\", "/")
    return any(normalized == str(prefix).rstrip("/") or normalized.startswith(str(prefix)) for prefix in prefixes)


def _normalize_label(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")


def _anchor_paths(roots: list[str], paths: list[str]) -> list[str]:
    path_set = set(paths)
    anchors: list[str] = []
    entrypoint_names = {
        "__init__.py",
        "cli.py",
        "main.py",
        "app.py",
        "index.ts",
        "index.js",
        "README.md",
        "readme.md",
    }
    for root in roots:
        root_prefix = f"{root.rstrip('/')}/"
        in_root = sorted(path for path in path_set if path.startswith(root_prefix))
        direct = [path for path in in_root if "/" not in path[len(root_prefix):].strip("/")]
        prioritized = [
            path for path in direct if Path(path).name in entrypoint_names or Path(path).stem == Path(root).name
        ]
        for path in [*prioritized, *direct, *in_root]:
            if path not in anchors:
                anchors.append(path)
            if len(anchors) >= 4:
                return anchors
    return anchors

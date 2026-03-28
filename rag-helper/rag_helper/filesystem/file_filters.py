from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path


def should_include_file(
    path: Path,
    root: Path,
    extensions: set[str],
    excluded_parts: set[str],
    include_globs: list[str] | None = None,
    exclude_globs: list[str] | None = None,
) -> bool:
    if any(part in excluded_parts for part in path.parts):
        return False

    ext = path.suffix.lower().lstrip(".")
    if ext not in extensions:
        return False

    rel_path = path.relative_to(root).as_posix()
    candidates = _match_candidates(rel_path)

    if include_globs and not any(_matches_any_glob(candidates, pattern) for pattern in include_globs):
        return False
    if exclude_globs and any(_matches_any_glob(candidates, pattern) for pattern in exclude_globs):
        return False
    return True


def _matches_any_glob(candidates: list[str], pattern: str) -> bool:
    normalized_pattern = pattern.replace("\\", "/")
    return any(fnmatch(candidate, normalized_pattern) for candidate in candidates)


def _match_candidates(rel_path: str) -> list[str]:
    normalized = rel_path.replace("\\", "/")
    parts = normalized.split("/")
    candidates = {normalized, parts[-1]}
    for index in range(len(parts)):
        candidates.add("/".join(parts[index:]))
    return sorted(candidates)

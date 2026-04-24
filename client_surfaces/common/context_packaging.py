from __future__ import annotations

import re
from pathlib import Path
from typing import Any


_SECRET_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"api[_-]?key",
        r"secret",
        r"private[_-]?key",
        r"password",
        r"token",
    )
]


def _clean_text(value: Any, *, max_chars: int) -> str:
    text = str(value or "").strip()
    return text[: max(1, int(max_chars))]


def _path_is_within_root(path: str, root: str) -> bool:
    try:
        path_obj = Path(path).resolve()
        root_obj = Path(root).resolve()
        path_obj.relative_to(root_obj)
        return True
    except (RuntimeError, OSError, ValueError):
        return False


def package_editor_context(
    *,
    file_path: str | None,
    project_root: str | None,
    selection_text: str | None,
    extra_paths: list[str] | None = None,
    max_selection_chars: int = 2000,
    max_paths: int = 15,
) -> dict[str, Any]:
    selection = _clean_text(selection_text, max_chars=max_selection_chars)
    clipped = len(str(selection_text or "")) > max_selection_chars
    allowed_paths: list[str] = []
    rejected_paths: list[str] = []

    for raw_path in list(extra_paths or [])[: max(1, int(max_paths))]:
        candidate = _clean_text(raw_path, max_chars=400)
        if not candidate:
            continue
        if project_root and not _path_is_within_root(candidate, project_root):
            rejected_paths.append(candidate)
            continue
        allowed_paths.append(candidate)

    warnings = []
    for pattern in _SECRET_PATTERNS:
        if pattern.search(selection):
            warnings.append("selection_may_contain_secret")
            break

    return {
        "schema": "client_bounded_context_payload_v1",
        "file_path": _clean_text(file_path, max_chars=400) or None,
        "project_root": _clean_text(project_root, max_chars=400) or None,
        "selection_text": selection or None,
        "selection_clipped": clipped,
        "extra_paths": allowed_paths,
        "rejected_paths": rejected_paths,
        "provenance": {
            "has_selection": bool(selection),
            "has_file_path": bool(file_path),
            "has_project_root": bool(project_root),
            "extra_paths_count": len(allowed_paths),
        },
        "warnings": warnings,
        "bounded": True,
        "implicit_unrelated_paths_included": False,
    }


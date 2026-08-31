"""Path confinement for the SGPT source-preview endpoint."""

from __future__ import annotations

from pathlib import Path

SOURCE_ALLOWED_EXTENSIONS = {
    ".py", ".md", ".txt", ".log", ".json", ".jsonl", ".yaml", ".yml", ".toml",
    ".ini", ".ts", ".tsx", ".js", ".jsx",
}


def resolve_source_preview_path(source_path: str, *, repo_root: str | Path) -> Path:
    repo_root = Path(repo_root).resolve()
    requested = (repo_root / source_path).resolve()
    requested.relative_to(repo_root)
    if requested.suffix.lower() not in SOURCE_ALLOWED_EXTENSIONS:
        raise ValueError("Source file type is not allowed")
    return requested

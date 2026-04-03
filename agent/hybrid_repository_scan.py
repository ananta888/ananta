from __future__ import annotations

import logging
import os
from pathlib import Path

try:
    from git import Repo
except Exception:  # pragma: no cover - optional dependency
    Repo = None


def tracked_code_files(*, repo_root: Path, code_extensions: set[str], max_files: int) -> list[Path]:
    if Repo is not None:
        try:
            repo = Repo(repo_root, search_parent_directories=True)
            root = Path(repo.working_tree_dir or repo_root)
            files = [
                (root / rel).resolve()
                for rel in repo.git.ls_files().splitlines()
                if Path(rel).suffix.lower() in code_extensions
            ]
            return files[:max_files]
        except Exception as e:
            logging.debug(f"Git ls-files failed, falling back to os.walk: {e}")

    files: list[Path] = []
    for current_root, dirs, file_names in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in {".git", ".venv", "node_modules", "__pycache__", ".mypy_cache"}]
        for name in file_names:
            path = Path(current_root) / name
            if path.suffix.lower() in code_extensions:
                files.append(path.resolve())
                if len(files) >= max_files:
                    return files
    return files

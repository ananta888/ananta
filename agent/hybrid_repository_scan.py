from __future__ import annotations

import logging
import os
from pathlib import Path

from agent.config import settings

try:
    from git import Repo
except Exception:  # pragma: no cover - optional dependency
    Repo = None


def _configured_exclude_dirs() -> set[str]:
    raw = str(getattr(settings, "rag_scan_exclude_dirs", "") or "")
    return {item.strip() for item in raw.split(",") if item.strip()}


# Extensions that may not be git-tracked but should be scanned from disk
_DATA_EXTENSIONS = frozenset({".json", ".jsonl"})

# File names to skip when scanning data extensions (build artefacts / lock files)
_DATA_FILE_BLOCKLIST = frozenset({
    "package-lock.json", "yarn.lock.json", "composer.lock",
    "angular.json", "tsconfig.json", "tsconfig.app.json",
    "tsconfig.spec.json", "tsconfig.eslint.json", ".eslintrc.json",
})

# Directories to skip during the extra data-file walk (in addition to _configured_exclude_dirs)
_DATA_EXTRA_SKIP_DIRS = frozenset({
    "node_modules", "dist", "build", ".eggs", ".tox",
    "ananta.egg-info", "ci-artifacts", "test-results.root-owned.backup.1780499293",
    "archiv", "benchmarks", "fixtures", "data_test",
    ".rag", "project-workspaces", "venv", ".venv", "myvenv",
})


def tracked_code_files(*, repo_root: Path, code_extensions: set[str], max_files: int) -> list[Path]:
    data_exts = _DATA_EXTENSIONS & code_extensions

    code_files: list[Path] = []
    code_seen: set[str] = set()

    # 1. Git-tracked files (code + any data files that happen to be tracked)
    git_ok = False
    if Repo is not None:
        try:
            repo = Repo(repo_root, search_parent_directories=True)
            root = Path(repo.working_tree_dir or repo_root)
            for rel in repo.git.ls_files().splitlines():
                ext = Path(rel).suffix.lower()
                if ext in code_extensions:
                    p = (root / rel).resolve()
                    s = str(p)
                    if s not in code_seen:
                        code_seen.add(s)
                        code_files.append(p)
            git_ok = True
        except Exception as e:
            logging.debug(f"Git ls-files failed, falling back to os.walk: {e}")

    if not git_ok:
        # Full os.walk fallback when git is unavailable
        excluded_dirs = _configured_exclude_dirs()
        for current_root, dirs, file_names in os.walk(repo_root):
            dirs[:] = [d for d in dirs if d not in excluded_dirs]
            for name in file_names:
                path = Path(current_root) / name
                if path.suffix.lower() in code_extensions:
                    r = path.resolve()
                    if str(r) not in code_seen:
                        code_seen.add(str(r))
                        code_files.append(r)
                if len(code_files) >= max_files:
                    return code_files
        return code_files[:max_files]

    # 2. Always scan for data files via os.walk (todos, configs, etc. often not git-tracked)
    data_files: list[Path] = []
    if data_exts:
        excluded_dirs = _configured_exclude_dirs() | _DATA_EXTRA_SKIP_DIRS
        max_data = min(500, max_files)
        for current_root, dirs, file_names in os.walk(repo_root):
            dirs[:] = [d for d in dirs if d not in excluded_dirs]
            for name in file_names:
                if name in _DATA_FILE_BLOCKLIST:
                    continue
                path = Path(current_root) / name
                if path.suffix.lower() in data_exts:
                    r = path.resolve()
                    s = str(r)
                    if s not in code_seen:
                        code_seen.add(s)
                        data_files.append(r)
                if len(data_files) >= max_data:
                    break
            if len(data_files) >= max_data:
                break

    # Merge: code files first, data files appended (data always included up to max_data)
    combined = code_files + data_files
    return combined[:max_files]

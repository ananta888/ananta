from __future__ import annotations

import logging
import os
from collections import defaultdict, deque
from pathlib import Path
from typing import TYPE_CHECKING

from agent.config import settings

if TYPE_CHECKING:
    from ananta_contracts.file_type_support import FileTypeSupportRegistry

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

# Directories to skip in the os.walk fallback for CODE files (when git is unavailable).
# Must exclude node_modules/dist/etc. to avoid hitting max_files before reaching source dirs.
_CODE_EXTRA_SKIP_DIRS = frozenset({
    "node_modules", ".git", "__pycache__", "dist", "build",
    ".eggs", ".tox", ".cache", "ananta.egg-info",
    "ci-artifacts", "project-workspaces", "venv", ".venv", "myvenv",
    "test-results.root-owned.backup.1780499293",
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
        excluded_dirs = _configured_exclude_dirs() | _CODE_EXTRA_SKIP_DIRS
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


def tracked_registry_files(
    *,
    repo_root: Path,
    registry: "FileTypeSupportRegistry",
    pipeline: str,
    max_files: int,
    include_untracked: bool = True,
) -> list[Path]:
    """Select repository files through the neutral file-type registry.

    This function performs discovery only. It preserves path/security excludes,
    applies identical classification to tracked and allowed untracked files, and
    selects a deterministic family-balanced subset when ``max_files`` is hit.
    """

    from ananta_contracts.file_type_classifier import FileTypeClassifier

    root = Path(repo_root).resolve()
    classifier = FileTypeClassifier(registry)
    candidates: dict[str, tuple[Path, str, str]] = {}
    tracked_paths: list[str] = []
    untracked_paths: list[str] = []

    if Repo is not None:
        try:
            repo = Repo(root, search_parent_directories=True)
            worktree = Path(repo.working_tree_dir or root).resolve()
            if worktree != root and root not in worktree.parents:
                worktree = root
            tracked_paths = repo.git.ls_files().splitlines()
            if include_untracked:
                untracked_paths = list(repo.untracked_files)
        except Exception as exc:
            logging.debug("Registry git scan failed, falling back to os.walk: %s", exc)

    if not tracked_paths:
        excluded_dirs = _configured_exclude_dirs() | _CODE_EXTRA_SKIP_DIRS
        for current_root, dirs, file_names in os.walk(root):
            dirs[:] = [name for name in dirs if name not in excluded_dirs]
            for name in file_names:
                path = Path(current_root) / name
                try:
                    tracked_paths.append(path.relative_to(root).as_posix())
                except ValueError:
                    continue

    for relative in sorted(set([*tracked_paths, *untracked_paths])):
        normalized = str(relative).replace("\\", "/").lstrip("/")
        if not normalized or _registry_path_excluded(normalized):
            continue
        candidate_path = root / normalized
        path = _safe_repository_file(candidate_path, root)
        if path is None:
            continue
        first_line, is_text = _bounded_text_probe(path)
        classification = classifier.classify(normalized, first_line=first_line, is_text=is_text)
        if classification is None:
            continue
        support = classification.descriptor.support_for(pipeline)
        if not classification.descriptor.enabled or not support.indexed.configured:
            continue
        candidates[normalized] = (
            path,
            classification.descriptor.priority,
            classification.descriptor.family,
        )

    ordered = _fair_registry_order(candidates)
    return [candidates[relative][0] for relative in ordered[: max(0, int(max_files))]]


def _fair_registry_order(candidates: dict[str, tuple[Path, str, str]]) -> list[str]:
    priority_order = {"P0": 0, "P1": 1, "P2": 2}
    by_priority_family: dict[int, dict[str, deque[str]]] = defaultdict(lambda: defaultdict(deque))
    for relative, (_path, priority, family) in sorted(candidates.items()):
        by_priority_family[priority_order.get(str(priority).upper(), 99)][str(family)].append(relative)

    result: list[str] = []
    for priority in sorted(by_priority_family):
        queues = by_priority_family[priority]
        families = sorted(queues)
        while any(queues[family] for family in families):
            for family in families:
                if queues[family]:
                    result.append(queues[family].popleft())
    return result


def _registry_path_excluded(relative: str) -> bool:
    parts = Path(relative).parts
    if any(part in (_configured_exclude_dirs() | _CODE_EXTRA_SKIP_DIRS | {"secrets"}) for part in parts[:-1]):
        return True
    name = parts[-1].lower() if parts else ""
    if name == ".env" or (name.startswith(".env.") and name not in {".env.example", ".env.sample"}):
        return True
    if name in {"id_rsa", "id_ed25519", "credentials", "credentials.json"}:
        return True
    return Path(name).suffix in {".pem", ".key", ".p12", ".pfx"}


def _safe_repository_file(path: Path, root: Path) -> Path | None:
    """Return a resolved regular file while rejecting links before resolution.

    Checking ``is_symlink`` after ``resolve`` loses the link identity and can
    accidentally admit a repository symlink.  Discovery is deliberately
    fail-closed because parsers must never follow repository-controlled links.
    """

    if path.is_symlink():
        return None
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return None
    return resolved if resolved.is_file() else None


def _bounded_text_probe(path: Path) -> tuple[str | None, bool]:
    try:
        with path.open("rb") as handle:
            raw = handle.read(4096)
    except OSError:
        return None, False
    if b"\0" in raw:
        return None, False
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, False
    return (text.splitlines()[0] if text else "", True)

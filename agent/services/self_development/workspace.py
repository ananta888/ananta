"""Git-free worker exports and immutable, bounded publication snapshots."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

from agent.services.self_development.contracts import source_path


def git(root: Path, *args: str) -> str:
    result = subprocess.run(['git', '-c', 'safe.directory=' + str(root), *args],
                            cwd=root, capture_output=True, timeout=90, check=False)
    if result.returncode:
        raise ValueError('self_development_git_command_failed:' + args[0])
    return result.stdout.decode('utf-8', errors='strict').strip()


def read_source(root: Path, relative: str) -> bytes:
    path = root / relative
    if any(parent.is_symlink() for parent in [path, *path.parents] if parent != root.parent):
        raise ValueError('source_symlink_denied')
    if not path.is_file() or path.stat().st_size > 1_000_000:
        raise ValueError('source_file_invalid')
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError('source_escape_denied')
    return path.read_bytes()


def fingerprint(root: Path, paths: list[str]) -> dict:
    return {p: hashlib.sha256(read_source(root, source_path(p))).hexdigest() for p in paths}


def export_checkout(checkout: Path, destination: Path) -> dict:
    """Copy tracked regular source only. No Git config, hooks or credentials."""
    if destination.exists():
        raise ValueError('workspace_already_exists')
    destination.mkdir(parents=True, mode=0o700)
    names = git(checkout, 'ls-files', '-z').split('\0')
    baseline = {}
    for name in filter(None, names):
        path = checkout / name
        if path.is_symlink() or not path.is_file():
            continue
        content = path.read_bytes()
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        target.chmod(0o755 if os.access(path, os.X_OK) else 0o644)
        baseline[name] = hashlib.sha256(content).hexdigest()
    return baseline


def write_scope_manifest(workspace: Path, files: list[str]) -> None:
    """Declare the closed writable scope so the worker mutation policy allows edits.

    Without this the policy treats every write as ``not_in_materialized_scope``
    (``no_materialization_manifest``), so the model can only read and never
    converge to a change.
    """
    bundle = workspace / '.ananta'
    bundle.mkdir(parents=True, exist_ok=True)
    rows = [{'workspace_path': source_path(relative),
             'allowed_operations': ['write', 'patch', 'replace']} for relative in files]
    (bundle / 'materialization-manifest.json').write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')


def seal_changes(workspace: Path, baseline: dict, allowed: list[str], seal: Path) -> dict:
    """Reject any undeclared source edit, then copy exact approved bytes privately.

    Worker context/runtime metadata is excluded, never copied or published.
    Unrelated source changes, missing files and symlinks fail closed.
    """
    current = {}
    for root, dirs, files in os.walk(workspace, followlinks=False):
        relative = Path(root).relative_to(workspace)
        root_metadata = {'.ananta', '.pytest_cache', 'rag_helper'} if relative == Path('.') else set()
        dirs[:] = [d for d in dirs if d != '__pycache__' and d not in root_metadata]
        for directory in dirs:
            if (Path(root) / directory).is_symlink():
                raise ValueError('source_symlink_denied')
        for name in files:
            path = (relative / name).as_posix()
            if path == 'AGENTS.md':
                continue  # Existing worker context writer owns this generated file.
            actual = Path(root) / name
            if actual.is_symlink() or not actual.is_file():
                raise ValueError('source_symlink_denied')
            with actual.open('rb') as stream:
                current[path] = hashlib.file_digest(stream, 'sha256').hexdigest()
    changed = {p for p in set(baseline) | set(current)
               if p != 'AGENTS.md' and baseline.get(p) != current.get(p)}
    if not changed or not changed.issubset(set(allowed)):
        raise ValueError('empty_or_out_of_scope_changes')
    if any(p not in current for p in changed):
        raise ValueError('deletion_requires_separate_policy')
    seal.mkdir(parents=True, mode=0o700)
    for path in sorted(changed):
        target = seal / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(read_source(workspace, path))
        target.chmod(0o400)
    hashes = fingerprint(seal, sorted(changed))
    if any(hashes[p] != current[p] for p in hashes):
        raise ValueError('worker_snapshot_changed')
    return hashes

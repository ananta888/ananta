"""AWTCL-013: deterministic repo tools for the ananta-worker tool loop.

``repo.list_files``, ``repo.read_file_range`` and ``repo.grep`` work only
inside the resolved workspace root; path traversal and absolute paths are
rejected. Outputs are bounded, sorted and deterministic. ``git.status``
and ``git.diff_readonly`` run fixed read-only git argv (no shell).
"""
from __future__ import annotations

import fnmatch
import hashlib
import os
import re
import subprocess
from collections import deque
from pathlib import Path
from typing import Any

from agent.services.tools._evidence import (
    EVIDENCE_KIND_FILE_EXCERPT,
    EVIDENCE_KIND_FILE_LIST,
    EVIDENCE_KIND_GREP_MATCH,
    build_evidence_entry,
    build_tool_result,
)

_IGNORED_SEGMENTS = {".git", ".ananta", "__pycache__", "node_modules", "artifacts"}
_MAX_LIST_LIMIT = 500
_MAX_GREP_LIMIT = 100
_MAX_READ_LINES = 400
_MAX_FILE_BYTES = 2 * 1024 * 1024
_MAX_GREP_CONTEXT = 20


class WorkspacePathError(ValueError):
    pass


def resolve_workspace_path(workspace_dir: str | Path, rel_path: str | None) -> Path:
    """Resolve ``rel_path`` inside the workspace; reject traversal/absolute paths."""
    root = Path(workspace_dir).resolve()
    raw = str(rel_path or "").strip()
    if not raw:
        raise WorkspacePathError("path_required")
    candidate = Path(raw)
    if candidate.is_absolute() or raw.startswith("~"):
        raise WorkspacePathError("absolute_path_blocked")
    resolved = (root / candidate).resolve()
    try:
        if os.path.commonpath([str(resolved), str(root)]) != str(root):
            raise WorkspacePathError("path_traversal_blocked")
    except ValueError as exc:
        raise WorkspacePathError("path_traversal_blocked") from exc
    return resolved


def _iter_files(root: Path):
    for current_root, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in _IGNORED_SEGMENTS)
        current = Path(current_root)
        for name in sorted(filenames):
            path = current / name
            rel = str(path.relative_to(root)).replace("\\", "/")
            if any(part in _IGNORED_SEGMENTS for part in Path(rel).parts):
                continue
            yield path, rel


def repo_list_files(*, workspace_dir: str, arguments: dict[str, Any], tool_call_id: str) -> dict[str, Any]:
    root = Path(workspace_dir).resolve()
    glob = str((arguments or {}).get("path_glob") or "").strip()
    limit = max(1, min(int((arguments or {}).get("limit") or 200), _MAX_LIST_LIMIT))
    rows: list[str] = []
    truncated = False
    for _, rel in _iter_files(root):
        if glob and not fnmatch.fnmatch(rel, glob):
            continue
        if len(rows) >= limit:
            truncated = True
            break
        rows.append(rel)
    entry, _ = build_evidence_entry(
        kind=EVIDENCE_KIND_FILE_LIST,
        path=glob or ".",
        excerpt="\n".join(rows),
        max_excerpt_chars=8000,
    )
    return build_tool_result(
        tool_name="repo.list_files",
        tool_call_id=tool_call_id,
        status="ok",
        evidence=[entry],
        data={"file_count": len(rows), "truncated": truncated},
        warnings=(["result_truncated_at_limit"] if truncated else []),
    )


def repo_read_file_range(*, workspace_dir: str, arguments: dict[str, Any], tool_call_id: str) -> dict[str, Any]:
    args = arguments or {}
    try:
        path = resolve_workspace_path(workspace_dir, args.get("path"))
    except WorkspacePathError as exc:
        return build_tool_result(
            tool_name="repo.read_file_range", tool_call_id=tool_call_id, status="error", error=str(exc)
        )
    if not path.is_file():
        return build_tool_result(
            tool_name="repo.read_file_range", tool_call_id=tool_call_id, status="error", error="file_not_found"
        )
    if path.stat().st_size > _MAX_FILE_BYTES:
        return build_tool_result(
            tool_name="repo.read_file_range", tool_call_id=tool_call_id, status="error", error="file_too_large"
        )
    start = max(1, int(args.get("line_start") or 1))
    requested_end = int(args.get("line_end") or (start + 80))
    end = max(start, min(requested_end, start + _MAX_READ_LINES - 1))
    selected: list[str] = []
    total_lines = 0
    file_hash = hashlib.sha256()
    range_hash = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                total_lines = line_number
                file_hash.update(raw_line)
                if line_number < start or line_number > end:
                    continue
                selected.append(raw_line.decode("utf-8", errors="replace").rstrip("\n\r"))
                range_hash.update(raw_line)
    except OSError as exc:
        return build_tool_result(
            tool_name="repo.read_file_range", tool_call_id=tool_call_id, status="error", error=f"read_failed:{exc}"
        )
    end = min(end, total_lines) if total_lines else start
    excerpt = "\n".join(selected)
    rel = str(path.relative_to(Path(workspace_dir).resolve())).replace("\\", "/")
    entry, _ = build_evidence_entry(
        kind=EVIDENCE_KIND_FILE_EXCERPT,
        path=rel,
        line_start=start,
        line_end=end,
        excerpt=excerpt,
        max_excerpt_chars=12000,
    )
    return build_tool_result(
        tool_name="repo.read_file_range",
        tool_call_id=tool_call_id,
        status="ok",
        evidence=[entry],
        data={
            "total_lines": total_lines,
            "file_sha256": file_hash.hexdigest(),
            "range_sha256": range_hash.hexdigest(),
            "line_start": start,
            "line_end": end,
            "truncated_to_max_lines": (requested_end - start + 1) > _MAX_READ_LINES,
        },
    )


def repo_grep(*, workspace_dir: str, arguments: dict[str, Any], tool_call_id: str) -> dict[str, Any]:
    args = arguments or {}
    pattern = str(args.get("pattern") or "").strip()
    if not pattern:
        return build_tool_result(tool_name="repo.grep", tool_call_id=tool_call_id, status="error", error="pattern_required")
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        return build_tool_result(
            tool_name="repo.grep", tool_call_id=tool_call_id, status="error", error=f"invalid_pattern:{exc}"
        )
    globs = [str(item or "").strip() for item in list(args.get("path_globs") or []) if str(item or "").strip()]
    limit = max(1, min(int(args.get("limit") or 50), _MAX_GREP_LIMIT))
    context_before = max(0, min(int(args.get("context_before") or 0), _MAX_GREP_CONTEXT))
    context_after = max(0, min(int(args.get("context_after") or 0), _MAX_GREP_CONTEXT))
    root = Path(workspace_dir).resolve()
    evidence: list[dict[str, Any]] = []
    truncated = False
    for path, rel in _iter_files(root):
        if globs and not any(fnmatch.fnmatch(rel, glob) for glob in globs):
            continue
        if path.stat().st_size > _MAX_FILE_BYTES:
            continue
        before: deque[tuple[int, str]] = deque(maxlen=context_before)
        try:
            handle = path.open("rb")
        except OSError:
            continue
        with handle:
            for line_no, raw_line in enumerate(handle, start=1):
                if b"\x00" in raw_line:
                    break
                line = raw_line.decode("utf-8", errors="replace").rstrip("\n\r")
                previous = list(before)
                if context_before:
                    before.append((line_no, line))
                if not compiled.search(line):
                    continue
                if len(evidence) >= limit:
                    truncated = True
                    break
                context_lines = [text for _, text in previous] + [line]
                context_end = line_no
                if context_after:
                    for offset in range(context_after):
                        next_raw = handle.readline()
                        if not next_raw:
                            break
                        if b"\x00" in next_raw:
                            break
                        context_end = line_no + offset + 1
                        next_line = next_raw.decode("utf-8", errors="replace").rstrip("\n\r")
                        context_lines.append(next_line)
                        if context_before:
                            before.append((context_end, next_line))
                entry, _ = build_evidence_entry(
                    kind=EVIDENCE_KIND_GREP_MATCH,
                    path=rel,
                    line_start=previous[0][0] if previous else line_no,
                    line_end=context_end,
                    excerpt="\n".join(context_lines).strip(),
                    max_excerpt_chars=1200 if (context_before or context_after) else 400,
                )
                evidence.append(entry)
        if truncated:
            break
    return build_tool_result(
        tool_name="repo.grep",
        tool_call_id=tool_call_id,
        status="ok",
        evidence=evidence,
        data={
            "match_count": len(evidence),
            "truncated": truncated,
            "context_before": context_before,
            "context_after": context_after,
        },
        warnings=(["result_truncated_at_limit"] if truncated else []),
    )


def _run_git_readonly(workspace_dir: str, argv: list[str]) -> tuple[int, str, str]:
    try:
        result = subprocess.run(  # noqa: S603 - fixed read-only argv, no shell
            ["git", "-C", str(workspace_dir), *argv],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        return result.returncode, result.stdout, result.stderr
    except (subprocess.TimeoutExpired, OSError) as exc:
        return -1, "", str(exc)


def git_status(*, workspace_dir: str, arguments: dict[str, Any], tool_call_id: str) -> dict[str, Any]:
    rc, out, err = _run_git_readonly(workspace_dir, ["status", "--porcelain=v1", "--no-renames"])
    if rc != 0:
        return build_tool_result(
            tool_name="git.status", tool_call_id=tool_call_id, status="error", error=err.strip() or f"git_rc_{rc}"
        )
    entry, _ = build_evidence_entry(kind="git_status", path=".", excerpt=out.strip() or "(clean)", max_excerpt_chars=4000)
    return build_tool_result(tool_name="git.status", tool_call_id=tool_call_id, status="ok", evidence=[entry])


def git_diff_readonly(*, workspace_dir: str, arguments: dict[str, Any], tool_call_id: str) -> dict[str, Any]:
    args = arguments or {}
    argv = ["diff", "--no-color"]
    rel = str(args.get("path") or "").strip()
    if rel:
        try:
            resolve_workspace_path(workspace_dir, rel)
        except WorkspacePathError as exc:
            return build_tool_result(
                tool_name="git.diff_readonly", tool_call_id=tool_call_id, status="error", error=str(exc)
            )
        argv += ["--", rel]
    rc, out, err = _run_git_readonly(workspace_dir, argv)
    if rc != 0:
        return build_tool_result(
            tool_name="git.diff_readonly", tool_call_id=tool_call_id, status="error", error=err.strip() or f"git_rc_{rc}"
        )
    entry, _ = build_evidence_entry(kind="diff", path=rel or ".", excerpt=out.strip() or "(no diff)", max_excerpt_chars=10000)
    return build_tool_result(tool_name="git.diff_readonly", tool_call_id=tool_call_id, status="ok", evidence=[entry])

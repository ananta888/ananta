"""AWTCL-013: deterministic repo tools for the ananta-worker tool loop.

``repo.list_files``, ``repo.read_file_range`` and ``repo.grep`` work only
inside the resolved workspace root; path traversal and absolute paths are
rejected. Outputs are bounded, sorted and deterministic. ``git.status``
and ``git.diff_readonly`` run fixed read-only git argv (no shell).
"""
from __future__ import annotations

import fnmatch
import os
import re
import subprocess
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
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    start = max(1, int(args.get("line_start") or 1))
    end = int(args.get("line_end") or (start + 80))
    end = max(start, min(end, start + _MAX_READ_LINES - 1, len(lines)))
    excerpt = "\n".join(lines[start - 1 : end])
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
        data={"total_lines": len(lines)},
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
    root = Path(workspace_dir).resolve()
    evidence: list[dict[str, Any]] = []
    truncated = False
    for path, rel in _iter_files(root):
        if globs and not any(fnmatch.fnmatch(rel, glob) for glob in globs):
            continue
        try:
            if path.stat().st_size > _MAX_FILE_BYTES:
                continue
            payload = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in payload:
            continue
        text = payload.decode("utf-8", errors="replace")
        for line_no, line in enumerate(text.splitlines(), start=1):
            if not compiled.search(line):
                continue
            if len(evidence) >= limit:
                truncated = True
                break
            entry, _ = build_evidence_entry(
                kind=EVIDENCE_KIND_GREP_MATCH,
                path=rel,
                line_start=line_no,
                line_end=line_no,
                excerpt=line.strip(),
                max_excerpt_chars=400,
            )
            evidence.append(entry)
        if truncated:
            break
    return build_tool_result(
        tool_name="repo.grep",
        tool_call_id=tool_call_id,
        status="ok",
        evidence=evidence,
        data={"match_count": len(evidence), "truncated": truncated},
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

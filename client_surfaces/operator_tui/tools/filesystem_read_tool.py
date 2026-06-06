"""SCTR-003: FilesystemReadTool for SnakeChat.

Safe read-only filesystem access for the operator TUI chat surface.
All reads are policy-checked via SnakeChatSecurityPolicy.
"""
from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from client_surfaces.operator_tui.snake_chat_security_policy import (
    SnakeChatSecurityPolicy,
    check_path_allowed,
)


@dataclass
class DirEntry:
    name: str
    path: str
    is_dir: bool
    size_bytes: int = 0
    extension: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "is_dir": self.is_dir,
            "size_bytes": self.size_bytes,
            "extension": self.extension,
        }


@dataclass
class FilesystemReadResult:
    ok: bool
    data: Any = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "data": self.data, "error": self.error}


class FilesystemReadTool:
    """
    Read-only filesystem operations for the SnakeChat surface.

    All paths are resolved relative to workspace_root.
    Policy violations return FilesystemReadResult(ok=False, error=...).
    """

    def __init__(
        self,
        workspace_root: str | Path,
        policy: SnakeChatSecurityPolicy | None = None,
    ) -> None:
        self._root = Path(str(workspace_root or "")).resolve()
        self._policy = policy or SnakeChatSecurityPolicy(workspace_root=str(self._root))

    def _safe_resolve(self, relative_path: str) -> Path | None:
        """Resolve path within workspace; return None on traversal."""
        candidate = (self._root / relative_path).resolve()
        if not str(candidate).startswith(str(self._root)):
            return None
        return candidate

    def list_dir(self, relative_path: str = "") -> FilesystemReadResult:
        """
        List contents of a directory relative to workspace_root.
        Returns list of DirEntry dicts.
        """
        abs_path = self._safe_resolve(relative_path or "")
        if abs_path is None:
            return FilesystemReadResult(ok=False, error="path_traversal_denied")

        if not abs_path.exists():
            return FilesystemReadResult(ok=False, error=f"not_found:{relative_path!r}")
        if not abs_path.is_dir():
            return FilesystemReadResult(ok=False, error=f"not_a_directory:{relative_path!r}")

        entries: list[dict[str, Any]] = []
        try:
            for entry in sorted(abs_path.iterdir(), key=lambda e: (not e.is_dir(), e.name)):
                rel = str(entry.relative_to(self._root))
                allowed, _ = check_path_allowed(rel, policy=self._policy)
                if not allowed and not entry.is_dir():
                    continue
                try:
                    size = entry.stat().st_size if entry.is_file() else 0
                except OSError:
                    size = 0
                ext = entry.suffix.lower() if entry.is_file() else ""
                entries.append(DirEntry(
                    name=entry.name,
                    path=rel,
                    is_dir=entry.is_dir(),
                    size_bytes=size,
                    extension=ext,
                ).as_dict())
        except PermissionError as exc:
            return FilesystemReadResult(ok=False, error=f"permission_denied:{exc}")

        return FilesystemReadResult(ok=True, data={
            "path": str(relative_path or "."),
            "entries": entries,
            "count": len(entries),
        })

    def list_root_files(self, pattern: str = "**/*.py") -> FilesystemReadResult:
        """
        Glob from workspace_root. Returns at most 200 matched relative paths.
        """
        try:
            matched = sorted(
                str(p.relative_to(self._root))
                for p in self._root.glob(pattern)
                if p.is_file()
            )[:200]
        except Exception as exc:
            return FilesystemReadResult(ok=False, error=f"glob_error:{exc}")

        # Filter by policy
        allowed_paths = [
            p for p in matched
            if check_path_allowed(p, policy=self._policy)[0]
        ]
        return FilesystemReadResult(ok=True, data={
            "pattern": pattern,
            "paths": allowed_paths,
            "count": len(allowed_paths),
        })

    def read_file(self, relative_path: str) -> FilesystemReadResult:
        """
        Read a file's content. Policy-checked; max 64 KB for chat display.
        """
        allowed, reason = check_path_allowed(relative_path, policy=self._policy)
        if not allowed:
            return FilesystemReadResult(ok=False, error=reason)

        abs_path = self._safe_resolve(relative_path)
        if abs_path is None:
            return FilesystemReadResult(ok=False, error="path_traversal_denied")

        if not abs_path.exists():
            return FilesystemReadResult(ok=False, error=f"not_found:{relative_path!r}")
        if abs_path.is_dir():
            return FilesystemReadResult(ok=False, error=f"is_a_directory:{relative_path!r}")

        try:
            raw = abs_path.read_bytes()
        except PermissionError:
            return FilesystemReadResult(ok=False, error=f"permission_denied:{relative_path!r}")

        truncated = False
        max_bytes = self._policy.max_file_bytes_display
        if len(raw) > max_bytes:
            raw = raw[:max_bytes]
            truncated = True

        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            return FilesystemReadResult(ok=False, error=f"binary_file:{relative_path!r}")

        if truncated:
            content += f"\n\n[truncated — showing first {max_bytes // 1024} KB]"

        return FilesystemReadResult(ok=True, data={
            "path": relative_path,
            "content": content,
            "byte_count": len(raw),
            "line_count": content.count("\n") + (1 if content else 0),
            "truncated": truncated,
        })

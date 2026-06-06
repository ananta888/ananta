"""
ContextFileReaderService — CWFH-005

Policy-checked file reader for worker context handoff.
Enforces: path traversal protection, allowed_extensions, denied_globs,
max_bytes per file, and workspace boundary checks.
"""
from __future__ import annotations

import fnmatch
import hashlib
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class FileReadPolicy:
    workspace_root: str | Path = ""
    allowed_extensions: list[str] = field(default_factory=lambda: [
        ".py", ".ts", ".tsx", ".js", ".jsx",
        ".go", ".rs", ".java", ".cs", ".cpp", ".c", ".h",
        ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
        ".md", ".txt", ".sh", ".bash",
        ".sql", ".html", ".css", ".scss",
    ])
    denied_globs: list[str] = field(default_factory=lambda: [
        "**/.env", "**/.env.*",
        "**/secrets.*", "**/secret.*",
        "**/*.key", "**/*.pem", "**/*.p12", "**/*.pfx",
        "**/id_rsa", "**/id_ed25519", "**/id_ecdsa",
        "**/.ssh/**",
        "**/credentials.json", "**/credentials.yaml",
        "**/token.json", "**/token.txt",
        "**/__pycache__/**", "**/*.pyc",
        "**/.git/**",
    ])
    max_bytes_per_file: int = 256 * 1024  # 256 KB
    allow_binary: bool = False


@dataclass
class FileReadResult:
    path: str
    content: str
    sha256: str
    byte_count: int
    line_count: int
    read_at: float
    policy_applied: str = "allowed"
    error: str | None = None

    def as_context_file_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "content": self.content,
            "sha256": self.sha256,
            "byte_count": self.byte_count,
            "line_count": self.line_count,
            "redaction_status": "not_redacted",
            "read_at": self.read_at,
            "provenance": "context_file_reader_service",
        }


class ContextFileReaderService:
    """
    Reads files from disk with policy enforcement.

    All paths are resolved relative to `policy.workspace_root`.
    Raises ValueError on policy violations (traversal, denied ext, denied glob).
    Returns FileReadResult with content or an error message on soft failures.
    """

    def __init__(self, policy: FileReadPolicy | None = None):
        self._policy = policy or FileReadPolicy()

    def _resolve_safe(self, relative_path: str) -> Path:
        root = Path(str(self._policy.workspace_root or "")).resolve()
        candidate = (root / relative_path).resolve()
        if root.parts and not str(candidate).startswith(str(root)):
            raise ValueError(f"Path traversal blocked: {relative_path!r}")
        return candidate

    def _check_extension(self, path: Path) -> None:
        ext = path.suffix.lower()
        allowed = [e.lower() for e in (self._policy.allowed_extensions or [])]
        if allowed and ext not in allowed:
            raise ValueError(f"Extension {ext!r} not in allowed_extensions for {path.name!r}")

    def _check_denied_globs(self, relative_path: str) -> None:
        normalized = relative_path.replace("\\", "/").lstrip("/")
        filename = normalized.split("/")[-1]
        for pattern in self._policy.denied_globs or []:
            if fnmatch.fnmatch(normalized, pattern):
                raise ValueError(f"Path {relative_path!r} matches denied pattern {pattern!r}")
            # Also match filename-only patterns (e.g. "*.key" or ".env") against the basename
            last_seg = pattern.split("/")[-1]
            if last_seg and last_seg not in {"**", "*"} and fnmatch.fnmatch(filename, last_seg):
                raise ValueError(f"Path {relative_path!r} matches denied pattern {pattern!r}")

    def read_file(self, relative_path: str) -> FileReadResult:
        """
        Read a single file by relative path (relative to workspace_root).
        Returns FileReadResult. Policy violations raise ValueError.
        Soft I/O errors return result with error field set.
        """
        self._check_denied_globs(relative_path)
        abs_path = self._resolve_safe(relative_path)
        self._check_extension(abs_path)

        read_at = time.time()
        try:
            raw = abs_path.read_bytes()
        except FileNotFoundError:
            return FileReadResult(
                path=relative_path, content="", sha256="", byte_count=0,
                line_count=0, read_at=read_at, policy_applied="file_not_found",
                error=f"File not found: {relative_path!r}",
            )
        except PermissionError:
            return FileReadResult(
                path=relative_path, content="", sha256="", byte_count=0,
                line_count=0, read_at=read_at, policy_applied="permission_denied",
                error=f"Permission denied: {relative_path!r}",
            )

        # Size check
        if len(raw) > self._policy.max_bytes_per_file:
            truncated = raw[: self._policy.max_bytes_per_file]
            content = truncated.decode("utf-8", errors="replace") + "\n[truncated]"
            policy_applied = "truncated"
        else:
            if not self._policy.allow_binary:
                try:
                    content = raw.decode("utf-8")
                except UnicodeDecodeError:
                    return FileReadResult(
                        path=relative_path, content="", sha256="", byte_count=len(raw),
                        line_count=0, read_at=read_at, policy_applied="binary_denied",
                        error=f"Binary file skipped: {relative_path!r}",
                    )
            else:
                content = raw.decode("utf-8", errors="replace")
            policy_applied = "allowed"

        sha256 = hashlib.sha256(raw).hexdigest()
        line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)

        return FileReadResult(
            path=relative_path,
            content=content,
            sha256=sha256,
            byte_count=len(raw),
            line_count=line_count,
            read_at=read_at,
            policy_applied=policy_applied,
        )

    def read_files(
        self,
        relative_paths: list[str],
        *,
        skip_errors: bool = True,
    ) -> list[FileReadResult]:
        """
        Read multiple files. With skip_errors=True (default), policy violations
        return error results rather than raising.
        """
        results: list[FileReadResult] = []
        for rp in relative_paths:
            try:
                results.append(self.read_file(rp))
            except ValueError as exc:
                if not skip_errors:
                    raise
                results.append(FileReadResult(
                    path=rp, content="", sha256="", byte_count=0,
                    line_count=0, read_at=time.time(),
                    policy_applied="policy_violation",
                    error=str(exc),
                ))
        return results

    def read_required_files(
        self,
        candidate_files: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Read all candidates where requires_read=True.
        Returns list of ContextFile dicts (CWFH-003 schema).
        """
        to_read = [c["path"] for c in candidate_files if c.get("requires_read")]
        results = self.read_files(to_read)
        return [r.as_context_file_dict() for r in results if not r.error]

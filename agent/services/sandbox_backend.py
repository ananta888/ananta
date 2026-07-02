"""Sandbox Backend Port — COSMOS-005

SandboxBackend Protocol, FakeSandbox (in-memory stub for tests),
and SandboxAuditService. Keine Produktions-Sandbox-Implementierung hier —
nur das Port-Interface und der Test-Stub.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class SandboxConfig:
    max_cpu_seconds: int = 30
    max_memory_mb: int = 512
    network: str = "none"   # "none" | "restricted" | "allowed"
    allowed_paths: list[str] = field(default_factory=list)
    env_allowlist: list[str] = field(default_factory=lambda: ["PATH", "HOME", "LANG"])
    working_dir: str = "/workspace"


@dataclass
class ExecResult:
    stdout: str
    stderr: str
    exit_code: int
    timeout: bool
    duration_ms: int
    sandbox_id: str
    cmd_hash: str      # sha256[:16] of JSON-serialised command list


@dataclass
class FileDiff:
    path: str
    status: str             # "added" | "modified" | "deleted"
    diff_text: str | None   # unified diff or None for binary


# ── Protocol ──────────────────────────────────────────────────────────────────

_VALID_NETWORK_VALUES = frozenset({"none", "restricted", "allowed"})


@runtime_checkable
class SandboxBackend(Protocol):
    """Port interface for sandbox backends (local, Docker, FakeSandbox, …)."""

    def start(self, config: SandboxConfig) -> str:
        """Start sandbox; returns opaque sandbox_id."""

    def exec(self, sandbox_id: str, cmd: list[str], timeout: int = 30) -> ExecResult:
        """Execute command in sandbox. Never use shell=True internally."""

    def copy_in(self, sandbox_id: str, src: Path, dst: str) -> None:
        """Copy file from host into sandbox."""

    def copy_out(self, sandbox_id: str, src: str, dst: Path) -> None:
        """Copy file from sandbox to host."""

    def diff(self, sandbox_id: str, baseline_ref: str) -> list[FileDiff]:
        """Return file changes since baseline snapshot."""

    def stop(self, sandbox_id: str) -> None:
        """Stop sandbox cleanly (processes terminated, state preserved)."""

    def cleanup(self, sandbox_id: str) -> None:
        """Remove sandbox and all temporary resources."""


# ── FakeSandbox ───────────────────────────────────────────────────────────────

class FakeSandbox:
    """In-memory sandbox for tests. Never executes real commands.

    Satisfies SandboxBackend via structural subtyping (isinstance check passes
    because all protocol methods are present).
    """

    def __init__(self) -> None:
        self._sandboxes: dict[str, dict[str, Any]] = {}
        self._exec_log: list[dict[str, Any]] = []

    def start(self, config: SandboxConfig) -> str:
        sandbox_id = f"fake-{uuid.uuid4().hex[:8]}"
        self._sandboxes[sandbox_id] = {
            "config": config,
            "files": {},
            "stopped": False,
        }
        return sandbox_id

    def exec(self, sandbox_id: str, cmd: list[str], timeout: int = 30) -> ExecResult:
        """Record exec call, return deterministic fake output. Raises if sandbox unknown."""
        if sandbox_id not in self._sandboxes:
            raise KeyError(f"Unknown sandbox: {sandbox_id}")
        cmd_hash = hashlib.sha256(json.dumps(cmd).encode()).hexdigest()[:16]
        self._exec_log.append(
            {"sandbox_id": sandbox_id, "cmd": cmd, "timeout": timeout}
        )
        return ExecResult(
            stdout=f"fake_output_for_{cmd[0]}",
            stderr="",
            exit_code=0,
            timeout=False,
            duration_ms=1,
            sandbox_id=sandbox_id,
            cmd_hash=cmd_hash,
        )

    def copy_in(self, sandbox_id: str, src: Path, dst: str) -> None:
        if sandbox_id not in self._sandboxes:
            raise KeyError(f"Unknown sandbox: {sandbox_id}")
        self._sandboxes[sandbox_id]["files"][dst] = str(src)

    def copy_out(self, sandbox_id: str, src: str, dst: Path) -> None:
        if sandbox_id not in self._sandboxes:
            raise KeyError(f"Unknown sandbox: {sandbox_id}")
        # No-op in fake; real backends copy actual bytes.

    def diff(self, sandbox_id: str, baseline_ref: str) -> list[FileDiff]:
        return []

    def stop(self, sandbox_id: str) -> None:
        if sandbox_id in self._sandboxes:
            self._sandboxes[sandbox_id]["stopped"] = True

    def cleanup(self, sandbox_id: str) -> None:
        self._sandboxes.pop(sandbox_id, None)

    def get_exec_log(self) -> list[dict[str, Any]]:
        """Return copy of recorded exec calls (test helper)."""
        return list(self._exec_log)


# ── Audit service ─────────────────────────────────────────────────────────────

class SandboxAuditService:
    """Audit helper for sandbox actions.

    cmd text is never stored in the audit record — only the cmd_hash.
    """

    def audit_exec(
        self,
        *,
        sandbox_id: str,
        cmd: list[str],
        result: ExecResult,
    ) -> dict[str, Any]:
        """Build and return an audit record for an exec call."""
        return {
            "sandbox_id": sandbox_id,
            "cmd_hash": result.cmd_hash,
            "exit_code": result.exit_code,
            "timeout": result.timeout,
            "duration_ms": result.duration_ms,
            "created_at": time.time(),
        }

    def check_network_policy(self, config: SandboxConfig) -> bool:
        """Return True iff config.network is a recognised value."""
        return config.network in _VALID_NETWORK_VALUES

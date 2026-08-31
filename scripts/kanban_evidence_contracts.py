"""Contracts shared by the Kanban release-evidence producer."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol, Sequence


class EvidenceProducerError(RuntimeError):
    """Raised when evidence cannot be produced safely."""


class EvidenceBlocked(EvidenceProducerError):
    """A fail-closed precondition that prevents suite execution."""

    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class CommandSpec:
    argv: tuple[str, ...]
    cwd: str = "."
    env: tuple[tuple[str, str], ...] = ()
    timeout_seconds: int = 600
    validator: str = "pytest"
    minimum_passed: int = 1
    result_path: str | None = None


@dataclass(frozen=True, slots=True)
class SuiteSpec:
    suite: str
    commands: tuple[CommandSpec, ...]
    inputs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    exit_code: int | None
    stdout: bytes = b""
    stderr: bytes = b""
    failure_code: str | None = None


class CommandExecutorPort(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env_overrides: Mapping[str, str],
        timeout_seconds: int,
    ) -> ExecutionResult: ...


class CandidateSourcePort(Protocol):
    def current_commit(self) -> str: ...
    def path_exists(self, commit_sha: str, relative_path: str) -> bool: ...
    def read_path(
        self,
        commit_sha: str,
        relative_path: str,
        *,
        max_bytes: int,
    ) -> bytes | None: ...

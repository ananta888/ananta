from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    not_found: bool = False
    truncated: bool = False


class CommandRunner:
    """Small subprocess boundary for hub-side Ops services."""

    def __init__(self, *, timeout_seconds: int = 10, max_output_bytes: int = 64_000) -> None:
        self._timeout_seconds = timeout_seconds
        self._max_output_bytes = max_output_bytes

    def exists(self, binary: str) -> bool:
        return shutil.which(binary) is not None

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        timeout_seconds: int | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        safe_env = self._safe_env(env)
        try:
            proc = subprocess.run(
                list(args),
                cwd=str(cwd) if cwd else None,
                capture_output=True,
                text=True,
                timeout=timeout_seconds or self._timeout_seconds,
                env=safe_env,
            )
        except FileNotFoundError:
            return CommandResult(returncode=127, stdout="", stderr="binary not found", not_found=True)
        except subprocess.TimeoutExpired as exc:
            stdout, out_truncated = self._cap(str(exc.stdout or ""))
            stderr, err_truncated = self._cap(str(exc.stderr or ""))
            return CommandResult(
                returncode=124,
                stdout=stdout,
                stderr=stderr,
                timed_out=True,
                truncated=out_truncated or err_truncated,
            )
        stdout, out_truncated = self._cap(proc.stdout or "")
        stderr, err_truncated = self._cap(proc.stderr or "")
        return CommandResult(
            returncode=proc.returncode,
            stdout=stdout,
            stderr=stderr,
            truncated=out_truncated or err_truncated,
        )

    def _cap(self, value: str) -> tuple[str, bool]:
        raw = value.encode("utf-8", errors="replace")
        if len(raw) <= self._max_output_bytes:
            return value, False
        clipped = raw[: self._max_output_bytes].decode("utf-8", errors="replace")
        return clipped, True

    def _safe_env(self, env: Mapping[str, str] | None) -> dict[str, str]:
        allowed = {
            "HOME",
            "PATH",
            "LANG",
            "LC_ALL",
            "GIT_CONFIG_GLOBAL",
            "GIT_CONFIG_NOSYSTEM",
            "GIT_LFS_SKIP_SMUDGE",
            "GIT_LFS_SKIP_PUSH",
            "GIT_SSL_NO_VERIFY",
            "GIT_TERMINAL_PROMPT",
            "DOCKER_HOST",
            "DOCKER_CONTEXT",
            "COMPOSE_PROFILES",
        }
        merged = {k: v for k, v in os.environ.items() if k in allowed}
        for key, value in dict(env or {}).items():
            if key in allowed:
                merged[key] = str(value)
        return merged


_default_runner: CommandRunner | None = None


def get_default_command_runner() -> CommandRunner:
    global _default_runner
    if _default_runner is None:
        _default_runner = CommandRunner()
    return _default_runner

"""Infrastructure adapters of the Kanban release-evidence producer.

``SubprocessCommandExecutor`` runs allowlisted argv without a shell and with a
minimal environment; ``GitCandidateSource`` reads candidate blobs through
argv-only Git plumbing. Both implement the ports in
``kanban_evidence_contracts``.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Mapping, Sequence

if __package__:
    from scripts.kanban_evidence_contracts import EvidenceBlocked, ExecutionResult
else:
    from kanban_evidence_contracts import EvidenceBlocked, ExecutionResult  # type: ignore

SHA_PATTERN = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")


class SubprocessCommandExecutor:
    """Execute fixed argv without a shell and without inherited test options."""

    _PASSTHROUGH = (
        "PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "TMPDIR",
        "TMP",
        "TEMP",
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "XDG_RUNTIME_DIR",
        "DBUS_SESSION_BUS_ADDRESS",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
    )

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env_overrides: Mapping[str, str],
        timeout_seconds: int,
    ) -> ExecutionResult:
        environment = {
            name: os.environ[name]
            for name in self._PASSTHROUGH
            if name in os.environ
        }
        environment.update(
            {
                "CI": "true",
                "NO_COLOR": "1",
                "PYTHONHASHSEED": "0",
                **env_overrides,
            }
        )
        try:
            completed = subprocess.run(
                list(argv),
                cwd=str(cwd),
                env=environment,
                capture_output=True,
                check=False,
                shell=False,
                timeout=timeout_seconds,
            )
        except FileNotFoundError:
            return ExecutionResult(
                exit_code=None,
                failure_code="command_executable_missing",
            )
        except subprocess.TimeoutExpired as exc:
            return ExecutionResult(
                exit_code=None,
                stdout=bytes(exc.stdout or b""),
                stderr=bytes(exc.stderr or b""),
                failure_code="command_timeout",
            )
        return ExecutionResult(
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


class GitCandidateSource:
    """Read candidate blobs with argv-only Git plumbing commands."""

    def __init__(self, root: Path):
        self._root = root
        executable = shutil.which("git")
        if executable is None:
            raise EvidenceBlocked("candidate_git_unavailable")
        self._git = executable

    def _run(
        self,
        *arguments: str,
        allow_failure: bool = False,
    ) -> subprocess.CompletedProcess[bytes]:
        completed = subprocess.run(
            [self._git, "-C", str(self._root), *arguments],
            capture_output=True,
            check=False,
            shell=False,
        )
        if completed.returncode != 0 and not allow_failure:
            raise EvidenceBlocked("candidate_git_read_failed")
        return completed

    def current_commit(self) -> str:
        value = self._run("rev-parse", "--verify", "HEAD").stdout.decode(
            "ascii",
            errors="strict",
        ).strip()
        if not SHA_PATTERN.fullmatch(value):
            raise EvidenceBlocked("candidate_checkout_sha_invalid")
        return value

    def path_exists(self, commit_sha: str, relative_path: str) -> bool:
        return (
            self._run(
                "cat-file",
                "-e",
                f"{commit_sha}:{relative_path}",
                allow_failure=True,
            ).returncode
            == 0
        )

    def read_path(
        self,
        commit_sha: str,
        relative_path: str,
        *,
        max_bytes: int,
    ) -> bytes | None:
        object_name = f"{commit_sha}:{relative_path}"
        size_result = self._run(
            "cat-file",
            "-s",
            object_name,
            allow_failure=True,
        )
        if size_result.returncode != 0:
            return None
        try:
            size = int(size_result.stdout.decode("ascii").strip())
        except (UnicodeDecodeError, ValueError) as exc:
            raise EvidenceBlocked("candidate_blob_size_invalid") from exc
        if size < 0 or size > max_bytes:
            raise EvidenceBlocked("candidate_blob_oversized")
        content = self._run("show", object_name).stdout
        if len(content) != size:
            raise EvidenceBlocked("candidate_blob_size_mismatch")
        return content

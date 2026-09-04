"""Container-only adapter for untrusted generated-code evaluation."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

_IMAGE = re.compile(r"^[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}$")


class ContainerCodeEvaluationSandbox:
    def __init__(
        self,
        *,
        image: str,
        runtime: str = "docker",
        executor: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        if _IMAGE.fullmatch(image) is None or runtime not in {"docker", "podman"}:
            raise ValueError("research_code_sandbox_config_invalid")
        self._image = image
        self._runtime = runtime
        self._executor = executor

    def run(
        self,
        *,
        workspace: str | Path,
        command: Sequence[str],
        timeout_seconds: int,
        memory_bytes: int,
    ) -> dict[str, Any]:
        root = Path(workspace).resolve()
        if not root.is_dir() or root.is_symlink():
            raise ValueError("research_code_sandbox_workspace_invalid")
        if (
            not command
            or len(command) > 32
            or command[0] not in {"python", "python3", "pytest"}
            or any(not isinstance(item, str) or not item or len(item) > 256 for item in command)
            or not 1 <= timeout_seconds <= 300
            or not 16 * 1024 * 1024 <= memory_bytes <= 8 * 1024**3
        ):
            raise ValueError("research_code_sandbox_request_invalid")
        argv = [
            self._runtime,
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--pids-limit",
            "32",
            "--memory",
            str(memory_bytes),
            "--cpus",
            "1",
            "--user",
            "65532:65532",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=16m,uid=65532,gid=65532",
            "--mount",
            f"type=bind,src={root},dst=/work,readonly",
            "--workdir",
            "/work",
            self._image,
            *command,
        ]
        try:
            completed = self._executor(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
            )
        except subprocess.TimeoutExpired:
            return self._result("blocked", "research_code_sandbox_timeout", None, "", "")
        stdout = completed.stdout[-16_384:]
        stderr = completed.stderr[-16_384:]
        return self._result(
            "passed" if completed.returncode == 0 else "failed",
            "research_code_sandbox_passed" if completed.returncode == 0 else "research_code_sandbox_failed",
            completed.returncode,
            stdout,
            stderr,
        )

    @staticmethod
    def _result(status: str, reason: str, exit_code: int | None, stdout: str, stderr: str) -> dict[str, Any]:
        return {
            "schema": "ananta.research-training-code-sandbox-result.v1",
            "status": status,
            "reason_code": reason,
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "network": "none",
            "human_intervention_required": False,
        }


__all__ = ["ContainerCodeEvaluationSandbox"]

"""Hub-authorized, allowlisted provisioning for Worker-local CLI backends."""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence


@dataclass(frozen=True)
class CliBackendPackage:
    backend_id: str
    package: str
    version: str
    binary: str


PROVISIONABLE_CLI_BACKENDS: Mapping[str, CliBackendPackage] = {
    "codex": CliBackendPackage(
        backend_id="codex",
        package="@openai/codex",
        version="0.145.0",
        binary="codex",
    ),
    "claude_code": CliBackendPackage(
        backend_id="claude_code",
        package="@anthropic-ai/claude-code",
        version="2.1.220",
        binary="claude",
    ),
}
_PROVISIONING_LOCKS = {
    backend_id: threading.RLock() for backend_id in PROVISIONABLE_CLI_BACKENDS
}


class CliBackendProvisioningError(RuntimeError):
    """A bounded provisioning operation could not be completed."""


RunCommand = Callable[..., subprocess.CompletedProcess[str]]


class CliBackendProvisioner:
    """Installs a fixed catalog entry into a Worker-owned persistent prefix.

    The caller chooses only a backend id. Package names, versions, executable
    names and command flags are controlled by this module.
    """

    def __init__(
        self,
        *,
        base_dir: Path | None = None,
        run_command: RunCommand = subprocess.run,
    ) -> None:
        configured = str(os.environ.get("ANANTA_CLI_BACKENDS_DIR") or "").strip()
        self._base_dir = (
            Path(configured).expanduser()
            if base_dir is None and configured
            else base_dir
            or Path.home() / ".local" / "share" / "ananta" / "cli-backends"
        )
        self._run_command = run_command

    def status(self, backend_id: str) -> dict:
        package = self._package(backend_id)
        with _PROVISIONING_LOCKS[package.backend_id]:
            binary_path = self.binary_path(backend_id)
            installed = binary_path.is_file() and os.access(binary_path, os.X_OK)
            result = {
                "backend": package.backend_id,
                "package": package.package,
                "version": package.version,
                "binary": package.binary,
                "binary_path": str(binary_path),
                "installed": installed,
                "status": "ready" if installed else "not_installed",
            }
            if installed:
                probe = self._run(
                    [str(binary_path), "--version"],
                    timeout=30,
                )
                result["version_probe"] = self._public_process_result(probe)
                if probe.returncode != 0:
                    result["status"] = "error"
            return result

    def install(self, backend_id: str) -> dict:
        package = self._package(backend_id)
        with _PROVISIONING_LOCKS[package.backend_id]:
            npm = shutil.which("npm")
            if not npm:
                raise CliBackendProvisioningError("npm_not_available")

            prefix = self.install_prefix(backend_id)
            prefix.mkdir(parents=True, exist_ok=True, mode=0o700)
            result = self._run(
                [
                    npm,
                    "install",
                    "--prefix",
                    str(prefix),
                    "--no-audit",
                    "--no-fund",
                    "--save=false",
                    f"{package.package}@{package.version}",
                ],
                timeout=600,
            )
            if result.returncode != 0:
                raise CliBackendProvisioningError("npm_install_failed")

            status = self.status(backend_id)
            if not status["installed"] or status["status"] != "ready":
                raise CliBackendProvisioningError(
                    "installed_binary_verification_failed"
                )
            return status

    def install_prefix(self, backend_id: str) -> Path:
        package = self._package(backend_id)
        return self._base_dir / package.backend_id / package.version

    def binary_path(self, backend_id: str) -> Path:
        package = self._package(backend_id)
        suffix = ".cmd" if os.name == "nt" else ""
        return (
            self.install_prefix(backend_id)
            / "node_modules"
            / ".bin"
            / f"{package.binary}{suffix}"
        )

    def _package(self, backend_id: str) -> CliBackendPackage:
        normalized = str(backend_id or "").strip().lower()
        package = PROVISIONABLE_CLI_BACKENDS.get(normalized)
        if package is None:
            raise CliBackendProvisioningError("backend_not_provisionable")
        return package

    def _run(self, command: Sequence[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
        try:
            return self._run_command(
                list(command),
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise CliBackendProvisioningError(type(exc).__name__) from exc

    @staticmethod
    def _public_process_result(result: subprocess.CompletedProcess[str]) -> dict:
        return {
            "rc": int(result.returncode),
            "stdout": (result.stdout or "")[:1000],
            "stderr": (result.stderr or "")[:1000],
        }


def resolve_provisioned_backend_binary(backend_id: str) -> str | None:
    """Return the pinned Worker-local binary when it is already installed."""

    if backend_id not in PROVISIONABLE_CLI_BACKENDS:
        return None
    path = CliBackendProvisioner().binary_path(backend_id)
    return str(path) if path.is_file() and os.access(path, os.X_OK) else None


def get_cli_backend_provisioner() -> CliBackendProvisioner:
    return CliBackendProvisioner()

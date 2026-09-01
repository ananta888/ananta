"""Explicit lifecycle control for the external Webcrawler process/container."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Protocol

from agent.providers.interfaces import ProviderHealthReport

from .backend_provider import AnantaWebcrawlerBackendProvider
from .config import AnantaWebcrawlerProviderConfig


class WebcrawlerRuntimeError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class ManagedProcessPort(Protocol):
    def start(self, argv: tuple[str, ...], *, cwd: Path) -> None: ...
    def stop(self) -> None: ...


class ComposeLifecyclePort(Protocol):
    def up(self, *, compose_file: Path, service: str) -> None: ...
    def stop(self, *, compose_file: Path, service: str) -> None: ...
    def restart(self, *, compose_file: Path, service: str) -> None: ...


class SubprocessManagedProcess:
    def __init__(self) -> None:
        self._process: subprocess.Popen[bytes] | None = None

    def start(self, argv: tuple[str, ...], *, cwd: Path) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        self._process = subprocess.Popen(  # noqa: S603 - validated argv, explicit repo cwd
            list(argv),
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def stop(self) -> None:
        process = self._process
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


class DockerComposeLifecycle:
    @staticmethod
    def _run(*args: str) -> None:
        completed = subprocess.run(  # noqa: S603,S607 - fixed docker argv, no shell
            ["docker", "compose", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if completed.returncode != 0:
            raise WebcrawlerRuntimeError("webcrawler_compose_command_failed")

    def up(self, *, compose_file: Path, service: str) -> None:
        self._run("-f", str(compose_file), "up", "-d", service)

    def stop(self, *, compose_file: Path, service: str) -> None:
        self._run("-f", str(compose_file), "stop", service)

    def restart(self, *, compose_file: Path, service: str) -> None:
        self._run("-f", str(compose_file), "restart", service)


class WebcrawlerRuntimeManager:
    """Health is read-only; lifecycle always requires Hub policy authorization."""

    def __init__(
        self,
        config: AnantaWebcrawlerProviderConfig,
        provider: AnantaWebcrawlerBackendProvider,
        *,
        process: ManagedProcessPort | None = None,
        compose: ComposeLifecyclePort | None = None,
        monotonic=time.monotonic,
        sleeper=time.sleep,
    ) -> None:
        self._config = config
        self._provider = provider
        self._process = process or SubprocessManagedProcess()
        self._compose = compose or DockerComposeLifecycle()
        self._monotonic = monotonic
        self._sleep = sleeper

    def health(self) -> ProviderHealthReport:
        return self._provider.health()

    def start(self, *, lifecycle_authorized: bool) -> ProviderHealthReport:
        self._require_lifecycle(lifecycle_authorized)
        if self._config.mode == "external_url":
            return self._wait_until_healthy()
        if self._config.mode == "managed_process":
            repo = self._required_directory(self._config.repo_path)
            self._process.start(self._config.startup_command, cwd=repo)
        elif self._config.mode == "managed_docker_compose":
            compose_file = self._required_file(self._config.docker_compose_file)
            self._compose.up(
                compose_file=compose_file,
                service=str(self._config.docker_compose_service),
            )
        else:
            raise WebcrawlerRuntimeError("webcrawler_runtime_disabled")
        return self._wait_until_healthy()

    def stop(self, *, lifecycle_authorized: bool) -> None:
        self._require_lifecycle(lifecycle_authorized)
        if self._config.mode == "managed_process":
            self._process.stop()
        elif self._config.mode == "managed_docker_compose":
            self._compose.stop(
                compose_file=self._required_file(self._config.docker_compose_file),
                service=str(self._config.docker_compose_service),
            )
        elif self._config.mode != "external_url":
            raise WebcrawlerRuntimeError("webcrawler_runtime_disabled")

    def restart(self, *, lifecycle_authorized: bool) -> ProviderHealthReport:
        self._require_lifecycle(lifecycle_authorized)
        if self._config.mode == "managed_docker_compose":
            self._compose.restart(
                compose_file=self._required_file(self._config.docker_compose_file),
                service=str(self._config.docker_compose_service),
            )
            return self._wait_until_healthy()
        self.stop(lifecycle_authorized=True)
        return self.start(lifecycle_authorized=True)

    def _wait_until_healthy(self) -> ProviderHealthReport:
        deadline = self._monotonic() + self._config.startup_timeout_seconds
        last = self.health()
        while last.status != "healthy" and self._monotonic() < deadline:
            self._sleep(self._config.health_poll_seconds)
            last = self.health()
        if last.status != "healthy":
            raise WebcrawlerRuntimeError("webcrawler_startup_timeout")
        return last

    def _require_lifecycle(self, authorized: bool) -> None:
        if not self._config.managed_lifecycle_enabled or authorized is not True:
            raise WebcrawlerRuntimeError("webcrawler_lifecycle_policy_blocked")

    @staticmethod
    def _required_directory(value: str | None) -> Path:
        path = Path(value or "")
        if not path.is_absolute() or not path.is_dir():
            raise WebcrawlerRuntimeError("webcrawler_repo_path_unavailable")
        return path

    @staticmethod
    def _required_file(value: str | None) -> Path:
        path = Path(value or "")
        if not path.is_absolute() or not path.is_file() or path.is_symlink():
            raise WebcrawlerRuntimeError("webcrawler_compose_file_unavailable")
        return path

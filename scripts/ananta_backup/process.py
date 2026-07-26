"""Small subprocess port used by infrastructure adapters."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Protocol, Sequence

from .errors import BackupError


class CommandRunner(Protocol):
    """Command execution port with explicit binary input/output boundaries."""

    def capture(self, command: Sequence[str]) -> bytes:
        """Run a command and return stdout."""

    def to_file(self, command: Sequence[str], output_path: Path) -> None:
        """Run a command and write stdout to an exclusive private file."""

    def from_file(self, command: Sequence[str], input_path: Path) -> bytes:
        """Run a command with a file connected to stdin and return stdout."""

    def run(self, command: Sequence[str]) -> None:
        """Run a command without capturing normal output."""


class SystemCommandRunner:
    """Fail-closed subprocess implementation which never invokes a shell."""

    @staticmethod
    def _failure(command: Sequence[str], exc: subprocess.CalledProcessError) -> BackupError:
        stderr = (exc.stderr or b"").decode("utf-8", errors="replace").strip()
        executable = Path(command[0]).name if command else "command"
        detail = stderr[-1200:] if stderr else f"exit status {exc.returncode}"
        return BackupError(f"{executable} failed: {detail}")

    def capture(self, command: Sequence[str]) -> bytes:
        try:
            completed = subprocess.run(
                list(command),
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            if isinstance(exc, subprocess.CalledProcessError):
                raise self._failure(command, exc) from exc
            raise BackupError(f"Cannot execute {Path(command[0]).name}: {exc}") from exc
        return completed.stdout

    def to_file(self, command: Sequence[str], output_path: Path) -> None:
        descriptor = os.open(
            output_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "wb") as output:
                try:
                    subprocess.run(
                        list(command),
                        check=True,
                        stdout=output,
                        stderr=subprocess.PIPE,
                    )
                except (OSError, subprocess.CalledProcessError) as exc:
                    if isinstance(exc, subprocess.CalledProcessError):
                        raise self._failure(command, exc) from exc
                    raise BackupError(
                        f"Cannot execute {Path(command[0]).name}: {exc}"
                    ) from exc
                output.flush()
                os.fsync(output.fileno())
        except Exception:
            output_path.unlink(missing_ok=True)
            raise

    def from_file(self, command: Sequence[str], input_path: Path) -> bytes:
        try:
            with input_path.open("rb") as source:
                completed = subprocess.run(
                    list(command),
                    check=True,
                    stdin=source,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
        except (OSError, subprocess.CalledProcessError) as exc:
            if isinstance(exc, subprocess.CalledProcessError):
                raise self._failure(command, exc) from exc
            raise BackupError(f"Cannot execute {Path(command[0]).name}: {exc}") from exc
        return completed.stdout

    def run(self, command: Sequence[str]) -> None:
        try:
            subprocess.run(
                list(command),
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            if isinstance(exc, subprocess.CalledProcessError):
                raise self._failure(command, exc) from exc
            raise BackupError(f"Cannot execute {Path(command[0]).name}: {exc}") from exc

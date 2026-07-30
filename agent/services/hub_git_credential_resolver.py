"""Secret-contained command capability for Hub-side Git transports."""

from __future__ import annotations

import os
import resource
import shlex
import shutil
import stat
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import ContextManager, Iterator, Mapping, Protocol, Sequence

from agent.services.git_remote_policy_service import hardened_git_environment


class HubGitCredentialError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


class GitSecretValueResolverPort(Protocol):
    def resolve(self, reference: str) -> str: ...


@dataclass(frozen=True, repr=False)
class GitCommandResult:
    returncode: int
    stdout: bytes
    timed_out: bool = False
    output_truncated: bool = False

    def __repr__(self) -> str:
        return (
            "GitCommandResult("
            f"returncode={self.returncode!r}, "
            f"timed_out={self.timed_out!r}, "
            f"output_truncated={self.output_truncated!r}, "
            "stdout=<redacted>)"
        )


class AuthorizedGitCommandSessionPort(Protocol):
    def run(
        self,
        arguments: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: float,
        maximum_file_bytes: int | None = None,
    ) -> GitCommandResult: ...


class GitCredentialCommandResolverPort(Protocol):
    def open_session(
        self,
        *,
        credential_ref: str | None,
        credential_username: str | None,
        scheme: str,
    ) -> ContextManager[AuthorizedGitCommandSessionPort]: ...


class _SubprocessGitCommandSession(AuthorizedGitCommandSessionPort):
    def __init__(
        self,
        *,
        environment: Mapping[str, str],
        ssh_identity_path: Path | None,
        maximum_output_bytes: int,
    ) -> None:
        self._environment = dict(environment)
        self._ssh_identity_path = ssh_identity_path
        self._maximum_output_bytes = maximum_output_bytes

    def __repr__(self) -> str:
        return (
            "_SubprocessGitCommandSession("
            "environment=<redacted>, ssh_identity_path=<redacted>)"
        )

    def run(
        self,
        arguments: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: float,
        maximum_file_bytes: int | None = None,
    ) -> GitCommandResult:
        safe_arguments = [str(item) for item in arguments]
        if self._ssh_identity_path is not None:
            safe_arguments = self._with_ssh_identity(safe_arguments)
        try:
            file_limit = (
                max(1, int(maximum_file_bytes))
                if maximum_file_bytes is not None
                else None
            )

            def apply_file_limit() -> None:
                if file_limit is not None:
                    resource.setrlimit(
                        resource.RLIMIT_FSIZE,
                        (file_limit, file_limit),
                    )

            completed = subprocess.run(
                ["git", *safe_arguments],
                cwd=str(cwd),
                env=dict(self._environment),
                capture_output=True,
                text=False,
                timeout=max(0.1, float(timeout_seconds)),
                check=False,
                preexec_fn=apply_file_limit if file_limit else None,
            )
        except subprocess.TimeoutExpired:
            return GitCommandResult(
                returncode=124,
                stdout=b"",
                timed_out=True,
            )
        except (OSError, ValueError):
            return GitCommandResult(returncode=127, stdout=b"")
        stdout = bytes(completed.stdout or b"")
        truncated = len(stdout) > self._maximum_output_bytes
        if truncated:
            stdout = stdout[: self._maximum_output_bytes]
        return GitCommandResult(
            returncode=int(completed.returncode),
            stdout=stdout,
            output_truncated=truncated,
        )

    def _with_ssh_identity(self, arguments: list[str]) -> list[str]:
        marker = "core.sshCommand="
        for index, value in enumerate(arguments):
            if value.startswith(marker):
                arguments[index] = (
                    value
                    + " -o IdentitiesOnly=yes -i "
                    + shlex.quote(str(self._ssh_identity_path))
                )
                return arguments
        raise HubGitCredentialError(
            "git_transport_ssh_pinning_configuration_missing"
        )


class SubprocessGitCredentialCommandResolver(
    GitCredentialCommandResolverPort
):
    """Resolve a reference only inside a short-lived command capability."""

    def __init__(
        self,
        *,
        secret_resolver: GitSecretValueResolverPort,
        credential_root: Path,
        base_environment: Mapping[str, str] | None = None,
        maximum_output_bytes: int = 32 * 1024 * 1024,
    ) -> None:
        self._secret_resolver = secret_resolver
        self._credential_root = Path(credential_root)
        self._base_environment = (
            dict(base_environment)
            if base_environment is not None
            else {
                key: value
                for key, value in os.environ.items()
                if key in {"HOME", "LANG", "LC_ALL", "PATH"}
            }
        )
        self._maximum_output_bytes = max(4096, int(maximum_output_bytes))

    def __repr__(self) -> str:
        return (
            "SubprocessGitCredentialCommandResolver("
            "secret_resolver=<redacted>, credential_root=<redacted>)"
        )

    @contextmanager
    def open_session(
        self,
        *,
        credential_ref: str | None,
        credential_username: str | None,
        scheme: str,
    ) -> Iterator[AuthorizedGitCommandSessionPort]:
        normalized_scheme = str(scheme or "").strip().lower()
        if normalized_scheme not in {"https", "ssh"}:
            raise HubGitCredentialError(
                "git_credential_scheme_unsupported"
            )
        self._prepare_root()
        directory = Path(
            tempfile.mkdtemp(
                prefix="ananta-git-credential-",
                dir=str(self._credential_root),
            )
        )
        try:
            environment = dict(self._base_environment)
            environment.update(hardened_git_environment())
            identity_path: Path | None = None
            if credential_ref:
                try:
                    secret = self._secret_resolver.resolve(credential_ref)
                except Exception:
                    raise HubGitCredentialError(
                        "git_credential_resolution_failed"
                    ) from None
                if not isinstance(secret, str) or not secret:
                    raise HubGitCredentialError(
                        "git_credential_resolution_failed"
                    )
                if normalized_scheme == "https":
                    askpass = self._write_askpass(directory)
                    environment.update(
                        {
                            "ANANTA_GIT_USERNAME": str(
                                credential_username or "x-access-token"
                            ),
                            "ANANTA_GIT_SECRET": secret,
                            "GIT_ASKPASS": str(askpass),
                            "SSH_ASKPASS": str(askpass),
                        }
                    )
                else:
                    identity_path = directory / "identity"
                    identity_path.write_text(secret, encoding="utf-8")
                    identity_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
                secret = ""
            yield _SubprocessGitCommandSession(
                environment=environment,
                ssh_identity_path=identity_path,
                maximum_output_bytes=self._maximum_output_bytes,
            )
        finally:
            self._erase_tree(directory)

    def _prepare_root(self) -> None:
        if self._credential_root.is_symlink():
            raise HubGitCredentialError(
                "git_credential_root_invalid"
            )
        self._credential_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if (
            not self._credential_root.is_dir()
            or self._credential_root.is_symlink()
        ):
            raise HubGitCredentialError(
                "git_credential_root_invalid"
            )

    @staticmethod
    def _write_askpass(directory: Path) -> Path:
        target = directory / "askpass"
        target.write_text(
            "#!/bin/sh\n"
            "case \"$1\" in\n"
            "  *sername*) printf '%s\\n' \"$ANANTA_GIT_USERNAME\" ;;\n"
            "  *) printf '%s\\n' \"$ANANTA_GIT_SECRET\" ;;\n"
            "esac\n",
            encoding="ascii",
        )
        target.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        return target

    @staticmethod
    def _erase_tree(directory: Path) -> None:
        if not directory.exists():
            return
        for root, directories, files in os.walk(
            directory,
            topdown=False,
            followlinks=False,
        ):
            for name in files:
                path = Path(root) / name
                try:
                    if not path.is_symlink():
                        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
                        size = path.stat().st_size
                        if size:
                            with path.open("r+b", buffering=0) as handle:
                                handle.write(b"\x00" * min(size, 1024 * 1024))
                                handle.truncate(0)
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            for name in directories:
                path = Path(root) / name
                try:
                    if path.is_symlink():
                        path.unlink(missing_ok=True)
                    else:
                        path.chmod(0o700)
                        path.rmdir()
                except OSError:
                    pass
        shutil.rmtree(directory, ignore_errors=True)
        if directory.exists():
            raise HubGitCredentialError(
                "git_credential_cleanup_failed"
            )


__all__ = [
    "AuthorizedGitCommandSessionPort",
    "GitCommandResult",
    "GitCredentialCommandResolverPort",
    "GitSecretValueResolverPort",
    "HubGitCredentialError",
    "SubprocessGitCredentialCommandResolver",
]

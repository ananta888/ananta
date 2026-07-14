"""Secure, container-neutral readers for file-managed service credentials."""

from __future__ import annotations

import os
import stat
from pathlib import Path


class FileCredentialConfigurationError(ValueError):
    """Raised when a configured credential file cannot be trusted."""


def read_file_managed_bytes(
    raw_path: str,
    *,
    description: str,
    max_bytes: int,
) -> bytes:
    """Read a bounded regular file without following links or accepting races."""

    path = Path(str(raw_path or ""))
    if not path.is_absolute():
        raise FileCredentialConfigurationError(f"{description} reference must be absolute")

    no_follow = getattr(os, "O_NOFOLLOW", None)
    if not isinstance(no_follow, int) or no_follow == 0:
        raise FileCredentialConfigurationError(f"{description} secure open is unsupported")
    flags = os.O_RDONLY | no_follow
    flags |= int(getattr(os, "O_CLOEXEC", 0))
    flags |= int(getattr(os, "O_NONBLOCK", 0))
    try:
        descriptor = os.open(path, flags)
    except (OSError, ValueError) as exc:
        raise FileCredentialConfigurationError(
            f"{description} cannot be opened securely"
        ) from exc

    try:
        try:
            before = os.fstat(descriptor)
            _validate_metadata(before, description=description, max_bytes=max_bytes)
            raw = _bounded_read(descriptor, max_bytes=max_bytes)
            after = os.fstat(descriptor)
        except FileCredentialConfigurationError:
            raise
        except OSError as exc:
            raise FileCredentialConfigurationError(
                f"{description} cannot be read securely"
            ) from exc
        if _metadata_fingerprint(before) != _metadata_fingerprint(after):
            raise FileCredentialConfigurationError(
                f"{description} changed while being read"
            )
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass

    if not raw or len(raw) > max_bytes:
        raise FileCredentialConfigurationError(f"{description} size is invalid")
    return raw


def read_file_managed_token(
    raw_path: str,
    *,
    description: str = "service token file",
    min_bytes: int = 32,
    max_bytes: int = 16_384,
) -> str:
    """Read and validate one whitespace-free UTF-8 bearer token."""

    raw = read_file_managed_bytes(
        raw_path,
        description=description,
        max_bytes=max_bytes,
    )
    try:
        token = raw.decode("utf-8").strip()
    except UnicodeError as exc:
        raise FileCredentialConfigurationError(
            f"{description} encoding is invalid"
        ) from exc
    token_bytes = token.encode("utf-8")
    if (
        len(token_bytes) < min_bytes
        or len(token_bytes) > max_bytes
        or "\x00" in token
        or any(character.isspace() for character in token)
    ):
        raise FileCredentialConfigurationError(f"{description} value is invalid")
    return token


def _validate_metadata(
    metadata: os.stat_result,
    *,
    description: str,
    max_bytes: int,
) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise FileCredentialConfigurationError(f"{description} must be a regular file")
    if int(metadata.st_nlink) != 1:
        raise FileCredentialConfigurationError(f"{description} link count is unsafe")
    effective_uid = getattr(os, "geteuid", None)
    if not callable(effective_uid):
        raise FileCredentialConfigurationError(
            f"{description} owner cannot be verified"
        )
    if metadata.st_uid not in {0, effective_uid()}:
        raise FileCredentialConfigurationError(f"{description} owner is unsafe")
    if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise FileCredentialConfigurationError(
            f"{description} permissions are unsafe"
        )
    if metadata.st_size < 1 or metadata.st_size > max_bytes:
        raise FileCredentialConfigurationError(f"{description} size is invalid")


def _metadata_fingerprint(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _bounded_read(descriptor: int, *, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    consumed = 0
    limit = max_bytes + 1
    while consumed < limit:
        chunk = os.read(descriptor, min(8192, limit - consumed))
        if not chunk:
            break
        chunks.append(chunk)
        consumed += len(chunk)
    return b"".join(chunks)


__all__ = [
    "FileCredentialConfigurationError",
    "read_file_managed_bytes",
    "read_file_managed_token",
]

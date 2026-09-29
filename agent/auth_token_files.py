"""Resolution of configured service credentials (agent and registration tokens).

Inline tokens come from the Flask/app config; file-managed tokens are read
through a bounded, no-follow, metadata-checked open so secret rotation takes
effect without caching secret material in process-global state.
"""

import os
import secrets
import stat
from pathlib import Path
from typing import Any, Mapping

from flask import current_app

from agent.config import settings
from ananta_contracts.file_credentials import (
    FileCredentialConfigurationError,
    read_file_managed_token,
)

_AGENT_TOKEN_FILE_MIN_BYTES = 32
_AGENT_TOKEN_FILE_MAX_BYTES = 16_384


class AgentTokenConfigurationError(RuntimeError):
    """Raised when a configured file-managed service token is unsafe."""


def _validate_agent_token_file_metadata(metadata: os.stat_result) -> None:
    """Validate security properties from an already-open file descriptor."""

    if not stat.S_ISREG(metadata.st_mode):
        raise AgentTokenConfigurationError("agent token file must be a regular file")
    if int(metadata.st_nlink) != 1:
        raise AgentTokenConfigurationError("agent token file link count is unsafe")

    effective_uid = getattr(os, "geteuid", None)
    if not callable(effective_uid):
        raise AgentTokenConfigurationError("agent token file owner cannot be verified")
    if metadata.st_uid not in {0, effective_uid()}:
        raise AgentTokenConfigurationError("agent token file owner is unsafe")

    if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise AgentTokenConfigurationError("agent token file permissions are unsafe")
    if metadata.st_size < 1 or metadata.st_size > _AGENT_TOKEN_FILE_MAX_BYTES:
        raise AgentTokenConfigurationError("agent token file size is invalid")


def _agent_token_file_metadata_fingerprint(metadata: os.stat_result) -> tuple[int, ...]:
    """Return the mutation-sensitive metadata that must remain stable while reading."""

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


def _read_agent_token_descriptor(file_descriptor: int) -> bytes:
    """Read at most one byte beyond the configured secret-size boundary."""

    chunks: list[bytes] = []
    bytes_read = 0
    read_limit = _AGENT_TOKEN_FILE_MAX_BYTES + 1
    while bytes_read < read_limit:
        chunk = os.read(file_descriptor, min(8192, read_limit - bytes_read))
        if not chunk:
            break
        chunks.append(chunk)
        bytes_read += len(chunk)
    return b"".join(chunks)


def _agent_token_file_reference(config: Mapping[str, Any] | None = None) -> str:
    source = config if config is not None else current_app.config
    configured = source.get("AGENT_TOKEN_FILE")
    return str(configured or os.environ.get("AGENT_TOKEN_FILE") or "").strip()


def resolve_configured_agent_token(
    config: Mapping[str, Any] | None = None,
) -> str | None:
    """Resolve the agent token, preferring a bounded read-only file secret.

    The file is intentionally read for every authentication attempt so an
    atomic secret-file replacement becomes effective without caching secret
    material in process-global state.
    """

    source = config if config is not None else current_app.config
    inline_token = str(source.get("AGENT_TOKEN") or "")
    raw_path = _agent_token_file_reference(source)
    if not raw_path:
        return inline_token or None

    path = Path(raw_path)
    if not path.is_absolute():
        raise AgentTokenConfigurationError("agent token file reference must be absolute")

    no_follow = getattr(os, "O_NOFOLLOW", None)
    if not isinstance(no_follow, int) or no_follow == 0:
        raise AgentTokenConfigurationError("agent token file secure open is unsupported")

    open_flags = os.O_RDONLY | no_follow
    open_flags |= int(getattr(os, "O_CLOEXEC", 0))
    open_flags |= int(getattr(os, "O_NONBLOCK", 0))
    try:
        file_descriptor = os.open(path, open_flags)
    except (OSError, ValueError) as exc:
        raise AgentTokenConfigurationError("agent token file cannot be opened securely") from exc

    try:
        try:
            metadata_before = os.fstat(file_descriptor)
            _validate_agent_token_file_metadata(metadata_before)
            raw_token = _read_agent_token_descriptor(file_descriptor)
            metadata_after = os.fstat(file_descriptor)
        except AgentTokenConfigurationError:
            raise
        except OSError as exc:
            raise AgentTokenConfigurationError("agent token file cannot be read securely") from exc
        if _agent_token_file_metadata_fingerprint(metadata_before) != _agent_token_file_metadata_fingerprint(
            metadata_after
        ):
            raise AgentTokenConfigurationError("agent token file changed while being read")
    finally:
        try:
            os.close(file_descriptor)
        except OSError:
            pass

    if not raw_token or len(raw_token) > _AGENT_TOKEN_FILE_MAX_BYTES:
        raise AgentTokenConfigurationError("agent token file size is invalid")
    try:
        token = raw_token.decode("utf-8").strip()
    except UnicodeError as exc:
        raise AgentTokenConfigurationError("agent token file encoding is invalid") from exc
    token_bytes = token.encode("utf-8")
    if (
        len(token_bytes) < _AGENT_TOKEN_FILE_MIN_BYTES
        or len(token_bytes) > _AGENT_TOKEN_FILE_MAX_BYTES
        or "\x00" in token
        or any(character.isspace() for character in token)
    ):
        raise AgentTokenConfigurationError("agent token file value is invalid")
    if inline_token and not secrets.compare_digest(inline_token.encode("utf-8"), token_bytes):
        raise AgentTokenConfigurationError("inline and file-managed agent tokens conflict")
    return token


def resolve_configured_registration_token(
    config: Mapping[str, Any] | None = None,
) -> str | None:
    """Resolve the bootstrap-only registration credential.

    ``REGISTRATION_TOKEN_FILE`` is deliberately independent from
    ``AGENT_TOKEN_FILE`` so a Worker service bearer cannot mint or overwrite
    another Worker identity. Inline ``REGISTRATION_TOKEN`` remains available
    for non-strict legacy deployments.
    """

    source = config if config is not None else current_app.config
    inline = str(
        source.get("REGISTRATION_TOKEN")
        or getattr(settings, "registration_token", None)
        or ""
    )
    raw_path = str(
        source.get("REGISTRATION_TOKEN_FILE")
        or os.environ.get("REGISTRATION_TOKEN_FILE")
        or ""
    ).strip()
    if not raw_path:
        return inline or None
    try:
        token = read_file_managed_token(
            raw_path,
            description="registration token file",
            min_bytes=_AGENT_TOKEN_FILE_MIN_BYTES,
            max_bytes=_AGENT_TOKEN_FILE_MAX_BYTES,
        )
    except FileCredentialConfigurationError as exc:
        raise AgentTokenConfigurationError(str(exc)) from exc
    if inline and not secrets.compare_digest(inline, token):
        raise AgentTokenConfigurationError(
            "inline and file-managed registration tokens conflict"
        )
    return token

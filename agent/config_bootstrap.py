"""Fail-closed runtime secret and legacy-token bootstrap for application settings."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import shutil
from pathlib import Path
from typing import Any

from ananta_contracts.file_credentials import (
    FileCredentialConfigurationError,
    read_file_managed_token,
)

_LOG = logging.getLogger("agent.config")


class SecretKeyConfigurationError(RuntimeError):
    """Raised when a file-managed Flask/JWT signing key is unsafe."""


def resolve_file_managed_secret_key(
    *,
    inline_secret_key: str,
    secret_key_file: str,
) -> str:
    try:
        file_secret_key = read_file_managed_token(
            secret_key_file,
            description="SECRET_KEY file",
            min_bytes=32,
            max_bytes=16_384,
        )
    except FileCredentialConfigurationError as exc:
        raise SecretKeyConfigurationError("SECRET_KEY file is invalid") from exc
    if inline_secret_key and inline_secret_key != file_secret_key:
        raise SecretKeyConfigurationError("inline SECRET_KEY conflicts with file-managed SECRET_KEY")
    return file_secret_key


def initialize_settings(settings: Any) -> Any:
    if settings.secret_key_file:
        settings.secret_key = resolve_file_managed_secret_key(
            inline_secret_key=settings.secret_key,
            secret_key_file=settings.secret_key_file,
        )
        _LOG.info("SECRET_KEY loaded from the configured file-managed secret")

    if not settings.secret_key:
        secret_key_path = Path(settings.secrets_dir) / "secret_key"
        if secret_key_path.exists():
            try:
                settings.secret_key = secret_key_path.read_text().strip()
                _LOG.info("SECRET_KEY loaded from %s", secret_key_path)
            except Exception as exc:
                _LOG.error("Could not read SECRET_KEY from %s: %s", secret_key_path, exc)
        if not settings.secret_key:
            settings.secret_key = secrets.token_urlsafe(32)
            _LOG.warning("SECRET_KEY was not set. A random key has been generated.")
            try:
                os.makedirs(settings.secrets_dir, exist_ok=True)
                secret_key_path.write_text(settings.secret_key)
                _LOG.info("Generated SECRET_KEY persisted to %s", secret_key_path)
            except Exception as exc:
                _LOG.error("Could not persist generated SECRET_KEY to %s: %s", secret_key_path, exc)

    if settings.secret_key and len(settings.secret_key.encode("utf-8")) < 32:
        short_length = len(settings.secret_key.encode("utf-8"))
        settings.secret_key = hashlib.sha256(settings.secret_key.encode("utf-8")).hexdigest()
        _LOG.warning(
            "SECRET_KEY length (%s bytes) is below 32; using deterministic SHA-256 derived key at runtime.",
            short_length,
        )

    if not settings.mfa_encryption_key:
        mfa_key_path = Path(settings.secrets_dir) / "mfa_encryption_key"
        if mfa_key_path.exists():
            try:
                settings.mfa_encryption_key = mfa_key_path.read_text().strip()
                _LOG.info("MFA_ENCRYPTION_KEY loaded from %s", mfa_key_path)
            except Exception as exc:
                _LOG.error("Could not read MFA_ENCRYPTION_KEY from %s: %s", mfa_key_path, exc)

    token_path = Path(settings.token_path)
    old_token_path = Path(settings.data_dir) / "token.json"
    if old_token_path.exists() and not token_path.exists():
        try:
            os.makedirs(settings.secrets_dir, exist_ok=True)
            shutil.move(str(old_token_path), str(token_path))
            _LOG.info("Migrated agent token from %s to %s", old_token_path, token_path)
        except Exception as exc:
            _LOG.error("Failed to migrate agent token: %s", exc)

    if token_path.exists():
        try:
            with token_path.open(encoding="utf-8") as handle:
                token_data = json.load(handle)
            if not settings.agent_token and not settings.agent_token_file:
                settings.agent_token = token_data.get("agent_token")
                if settings.agent_token:
                    _LOG.info("Agent token loaded from %s", token_path)
        except Exception as exc:
            _LOG.error("Could not read agent token from %s: %s", token_path, exc)
    return settings

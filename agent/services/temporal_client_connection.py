"""Hub-side Temporal client security configuration.

The dedicated Temporal worker has its own process configuration.  The Hub
must not import that worker module, so this focused adapter independently
resolves only the credentials needed by the Hub SDK client.  Secrets remain
file references and are read lazily for each new connection, which also makes
atomic secret rotation effective without a process-global credential cache.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


class TemporalHubClientConfigurationError(ValueError):
    """Fail-closed Temporal Hub-client configuration error."""


def _boolean(source: Mapping[str, str], name: str, default: bool = False) -> bool:
    raw = str(source.get(name) or ("true" if default else "false")).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise TemporalHubClientConfigurationError(f"{name} must be a boolean")


@dataclass(frozen=True)
class TemporalHubClientSecurity:
    identity: str = "ananta-hub"
    tls_enabled: bool = False
    tls_server_name: str = ""
    tls_ca_file: str = ""
    tls_cert_file: str = ""
    tls_key_file: str = ""
    api_key_file: str = ""

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
    ) -> "TemporalHubClientSecurity":
        source = os.environ if env is None else env
        value = cls(
            identity=str(source.get("ANANTA_TEMPORAL_HUB_IDENTITY") or "ananta-hub").strip(),
            tls_enabled=_boolean(source, "ANANTA_TEMPORAL_TLS_ENABLED"),
            tls_server_name=str(source.get("ANANTA_TEMPORAL_TLS_SERVER_NAME") or "").strip(),
            tls_ca_file=str(source.get("ANANTA_TEMPORAL_TLS_CA_FILE") or "").strip(),
            tls_cert_file=str(source.get("ANANTA_TEMPORAL_TLS_CERT_FILE") or "").strip(),
            tls_key_file=str(source.get("ANANTA_TEMPORAL_TLS_KEY_FILE") or "").strip(),
            api_key_file=str(source.get("ANANTA_TEMPORAL_API_KEY_FILE") or "").strip(),
        )
        value.validate()
        return value

    def validate(self) -> None:
        if not self.identity or len(self.identity) > 256 or "\x00" in self.identity:
            raise TemporalHubClientConfigurationError("Temporal Hub identity is invalid")
        referenced_tls_files = (self.tls_ca_file, self.tls_cert_file, self.tls_key_file)
        if any(referenced_tls_files) and not self.tls_enabled:
            raise TemporalHubClientConfigurationError(
                "Temporal TLS credential references require ANANTA_TEMPORAL_TLS_ENABLED"
            )
        if bool(self.tls_cert_file) != bool(self.tls_key_file):
            raise TemporalHubClientConfigurationError(
                "Temporal mTLS certificate and private key must be configured together"
            )
        for path in (*referenced_tls_files, self.api_key_file):
            if path and not Path(path).is_absolute():
                raise TemporalHubClientConfigurationError(
                    "Temporal credential references must be absolute paths"
                )

    def client_kwargs(self) -> dict[str, Any]:
        """Return kwargs accepted by ``temporalio.client.Client.connect``."""

        kwargs: dict[str, Any] = {
            "identity": self.identity,
            "api_key": _read_text_secret(self.api_key_file) if self.api_key_file else None,
            "tls": None,
        }
        if self.tls_enabled:
            from temporalio.client import TLSConfig

            kwargs["tls"] = TLSConfig(
                server_root_ca_cert=_read_binary_file(self.tls_ca_file) if self.tls_ca_file else None,
                domain=self.tls_server_name or None,
                client_cert=_read_binary_file(self.tls_cert_file) if self.tls_cert_file else None,
                client_private_key=_read_binary_file(self.tls_key_file) if self.tls_key_file else None,
            )
        return kwargs


def _checked_regular_file(path_text: str, *, maximum_bytes: int) -> Path:
    path = Path(path_text)
    try:
        metadata = path.stat()
    except OSError as exc:
        raise TemporalHubClientConfigurationError(
            "Temporal credential file cannot be inspected"
        ) from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0 or metadata.st_size > maximum_bytes:
        raise TemporalHubClientConfigurationError("Temporal credential file is invalid")
    if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise TemporalHubClientConfigurationError("Temporal credential file permissions are unsafe")
    return path


def _read_text_secret(path_text: str) -> str:
    path = _checked_regular_file(path_text, maximum_bytes=16_384)
    try:
        value = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise TemporalHubClientConfigurationError("Temporal API-key file cannot be read") from exc
    if len(value) < 16 or "\x00" in value or any(character.isspace() for character in value):
        raise TemporalHubClientConfigurationError("Temporal API-key file value is invalid")
    return value


def _read_binary_file(path_text: str) -> bytes:
    path = _checked_regular_file(path_text, maximum_bytes=1_048_576)
    try:
        return path.read_bytes()
    except OSError as exc:
        raise TemporalHubClientConfigurationError("Temporal TLS file cannot be read") from exc


__all__ = [
    "TemporalHubClientConfigurationError",
    "TemporalHubClientSecurity",
]

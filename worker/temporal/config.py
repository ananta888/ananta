"""Environment configuration for the isolated Temporal worker process."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from ananta_contracts.file_credentials import (
    FileCredentialConfigurationError,
    read_file_managed_bytes,
    read_file_managed_token,
)


class TemporalWorkerConfigError(ValueError):
    pass


def _text(env: Mapping[str, str], name: str, default: str = "") -> str:
    return str(env.get(name) or default).strip()


def _integer(env: Mapping[str, str], name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(_text(env, name, str(default)))
    except ValueError as exc:
        raise TemporalWorkerConfigError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise TemporalWorkerConfigError(f"{name} must be between {minimum} and {maximum}")
    return value


def _boolean(env: Mapping[str, str], name: str, default: bool = False) -> bool:
    raw = _text(env, name, "1" if default else "0").lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise TemporalWorkerConfigError(f"{name} must be a boolean")


@dataclass(frozen=True)
class TemporalWorkerConfig:
    address: str = "temporal:7233"
    namespace: str = "default"
    task_queue: str = "ananta-workflows-v1"
    build_id: str = "ananta-temporal-v1"
    identity: str = ""
    health_host: str = "0.0.0.0"
    health_port: int = 8088
    graceful_shutdown_seconds: int = 30
    max_concurrent_workflow_tasks: int = 20
    max_concurrent_activities: int = 20
    use_worker_versioning: bool = False
    tls_enabled: bool = False
    tls_server_name: str = ""
    tls_ca_file: str = ""
    tls_cert_file: str = ""
    tls_key_file: str = ""
    api_key_file: str = ""
    authorization_keyring_file: str = ""
    allow_legacy_hmac_keyring: bool = False
    hub_url: str = ""
    hub_token_file: str = ""
    workflow_service_id: str = ""
    hub_poll_seconds: int = 2
    hub_activity_timeout_seconds: int = 1_800

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "TemporalWorkerConfig":
        source = dict(os.environ if env is None else env)
        allow_legacy_hmac = _boolean(
            source,
            "ANANTA_WORKFLOW_ALLOW_LEGACY_HMAC_KEYRING",
        )
        verification_keyring_file = _text(source, "ANANTA_TEMPORAL_AUTH_VERIFICATION_KEYRING_FILE") or _text(
            source, "ANANTA_WORKFLOW_AUTH_VERIFICATION_KEYRING_FILE"
        )
        legacy_keyring_file = _text(source, "ANANTA_TEMPORAL_AUTH_KEYRING_FILE") or _text(
            source, "ANANTA_WORKFLOW_AUTH_KEYRING_FILE"
        )
        if legacy_keyring_file and not verification_keyring_file and not allow_legacy_hmac:
            raise TemporalWorkerConfigError("legacy HMAC authorization keyring is disabled")
        identity = _text(source, "ANANTA_TEMPORAL_IDENTITY") or f"ananta-temporal-worker@{socket.gethostname()}"
        config = cls(
            address=_text(source, "ANANTA_TEMPORAL_ADDRESS", "temporal:7233"),
            namespace=_text(source, "ANANTA_TEMPORAL_NAMESPACE", "default"),
            task_queue=_text(source, "ANANTA_TEMPORAL_TASK_QUEUE", "ananta-workflows-v1"),
            build_id=_text(source, "ANANTA_TEMPORAL_BUILD_ID", "ananta-temporal-v1"),
            identity=identity,
            health_host=_text(source, "ANANTA_TEMPORAL_HEALTH_HOST", "0.0.0.0"),
            health_port=_integer(source, "ANANTA_TEMPORAL_HEALTH_PORT", 8088, minimum=1, maximum=65_535),
            graceful_shutdown_seconds=_integer(
                source, "ANANTA_TEMPORAL_GRACEFUL_SHUTDOWN_SECONDS", 30, minimum=1, maximum=600
            ),
            max_concurrent_workflow_tasks=_integer(
                source, "ANANTA_TEMPORAL_MAX_WORKFLOW_TASKS", 20, minimum=1, maximum=1_000
            ),
            max_concurrent_activities=_integer(source, "ANANTA_TEMPORAL_MAX_ACTIVITIES", 20, minimum=1, maximum=1_000),
            use_worker_versioning=_boolean(source, "ANANTA_TEMPORAL_USE_WORKER_VERSIONING"),
            tls_enabled=_boolean(source, "ANANTA_TEMPORAL_TLS_ENABLED"),
            tls_server_name=_text(source, "ANANTA_TEMPORAL_TLS_SERVER_NAME"),
            tls_ca_file=_text(source, "ANANTA_TEMPORAL_TLS_CA_FILE"),
            tls_cert_file=_text(source, "ANANTA_TEMPORAL_TLS_CERT_FILE"),
            tls_key_file=_text(source, "ANANTA_TEMPORAL_TLS_KEY_FILE"),
            api_key_file=_text(source, "ANANTA_TEMPORAL_API_KEY_FILE"),
            authorization_keyring_file=(
                verification_keyring_file or (legacy_keyring_file if allow_legacy_hmac else "")
            ),
            allow_legacy_hmac_keyring=allow_legacy_hmac,
            hub_url=_text(source, "ANANTA_TEMPORAL_HUB_URL"),
            hub_token_file=_text(source, "ANANTA_TEMPORAL_HUB_TOKEN_FILE"),
            workflow_service_id=_text(source, "ANANTA_WORKFLOW_SERVICE_ID"),
            hub_poll_seconds=_integer(source, "ANANTA_TEMPORAL_HUB_POLL_SECONDS", 2, minimum=1, maximum=60),
            hub_activity_timeout_seconds=_integer(
                source, "ANANTA_TEMPORAL_HUB_ACTIVITY_TIMEOUT_SECONDS", 1_800, minimum=10, maximum=86_400
            ),
        )
        config.validate()
        return config

    def validate(self) -> None:
        for name, value in (
            ("address", self.address),
            ("namespace", self.namespace),
            ("task_queue", self.task_queue),
            ("build_id", self.build_id),
            ("identity", self.identity),
        ):
            if not value or len(value) > 256 or "\x00" in value:
                raise TemporalWorkerConfigError(f"{name} is invalid")
        if self.tls_enabled:
            if bool(self.tls_cert_file) != bool(self.tls_key_file):
                raise TemporalWorkerConfigError("mTLS certificate and key must be configured together")
            for path in (self.tls_ca_file, self.tls_cert_file, self.tls_key_file):
                if path and not Path(path).is_absolute():
                    raise TemporalWorkerConfigError("TLS credential references must be absolute paths")
        for path in (self.api_key_file, self.authorization_keyring_file, self.hub_token_file):
            if path and not Path(path).is_absolute():
                raise TemporalWorkerConfigError("credential references must be absolute paths")
        if bool(self.hub_url) != bool(self.hub_token_file):
            raise TemporalWorkerConfigError("hub URL and token-file reference must be configured together")
        if self.hub_url and not self.workflow_service_id:
            raise TemporalWorkerConfigError(
                "workflow service identity is required with the Hub gateway"
            )
        if (
            len(self.workflow_service_id.encode("utf-8")) > 256
            or "\x00" in self.workflow_service_id
            or "\r" in self.workflow_service_id
            or "\n" in self.workflow_service_id
        ):
            raise TemporalWorkerConfigError("workflow service identity is invalid")
        if self.hub_url and not self.hub_url.startswith(("http://", "https://")):
            raise TemporalWorkerConfigError("hub URL must use HTTP or HTTPS")

    @property
    def productive_gateway_configured(self) -> bool:
        return bool(
            self.hub_url
            and self.hub_token_file
            and self.workflow_service_id
        )

    def read_api_key(self) -> str | None:
        return _read_token_secret_file(self.api_key_file, "Temporal API key")

    def read_hub_token(self) -> str | None:
        return _read_token_secret_file(self.hub_token_file, "hub service token")

    def read_authorization_keyring(self) -> dict[str, object] | None:
        if not self.authorization_keyring_file:
            return None
        import json

        raw = _read_text_secret_file(
            self.authorization_keyring_file,
            "authorization keyring",
        )
        try:
            decoded = json.loads(str(raw))
        except json.JSONDecodeError as exc:
            raise TemporalWorkerConfigError("authorization keyring file is not valid JSON") from exc
        if not isinstance(decoded, dict):
            raise TemporalWorkerConfigError("authorization keyring file must contain an object")
        from ananta_contracts.runtime_authorization_crypto import (
            ED25519_VERIFICATION_KEYRING_SCHEMA,
            Ed25519VerificationKeyRing,
            RuntimeAuthorizationCryptoError,
        )

        if str(decoded.get("schema") or "") == ED25519_VERIFICATION_KEYRING_SCHEMA:
            try:
                Ed25519VerificationKeyRing.from_mapping(decoded)
            except RuntimeAuthorizationCryptoError as exc:
                raise TemporalWorkerConfigError(exc.reason_code) from exc
            return {str(key): value for key, value in decoded.items()}
        if not self.allow_legacy_hmac_keyring:
            raise TemporalWorkerConfigError("legacy HMAC authorization keyring is disabled")
        keys = decoded.get("keys")
        active_key_id = str(decoded.get("active_key_id") or "")
        if not isinstance(keys, dict) or not active_key_id or active_key_id not in keys:
            raise TemporalWorkerConfigError("authorization keyring file is incomplete")
        if any(not isinstance(value, str) or len(value) < 16 for value in keys.values()):
            raise TemporalWorkerConfigError("authorization keyring contains an invalid key")
        result: dict[str, object] = {"active_key_id": active_key_id, "keys": dict(keys)}
        for field_name in ("revoked_key_ids", "revoked_envelope_ids"):
            values = decoded.get(field_name)
            if values is None:
                continue
            if (
                not isinstance(values, list)
                or len(values) > 10_000
                or any(not isinstance(value, str) or not value or len(value) > 256 for value in values)
            ):
                raise TemporalWorkerConfigError(f"authorization keyring {field_name} is invalid")
            result[field_name] = list(dict.fromkeys(values))
        return result

    def temporal_tls_config(self):
        """Build the SDK TLS object lazily outside deterministic workflow code."""

        if not self.tls_enabled:
            return None
        from temporalio.client import TLSConfig

        return TLSConfig(
            server_root_ca_cert=_read_optional_bytes(self.tls_ca_file),
            domain=self.tls_server_name or None,
            client_cert=_read_optional_bytes(self.tls_cert_file),
            client_private_key=_read_optional_bytes(self.tls_key_file),
        )


def _read_token_secret_file(path: str, label: str) -> str | None:
    if not path:
        return None
    try:
        return read_file_managed_token(
            path,
            description=f"{label} file",
            min_bytes=1,
            max_bytes=16_384,
        )
    except FileCredentialConfigurationError as exc:
        raise TemporalWorkerConfigError(f"{label} file is invalid") from exc


def _read_text_secret_file(path: str, label: str) -> str:
    try:
        raw = read_file_managed_bytes(
            path,
            description=f"{label} file",
            max_bytes=16_384,
        )
        return raw.decode("utf-8")
    except (FileCredentialConfigurationError, UnicodeError) as exc:
        raise TemporalWorkerConfigError(f"{label} file is invalid") from exc


def _read_optional_bytes(path: str) -> bytes | None:
    if not path:
        return None
    try:
        return read_file_managed_bytes(
            path,
            description="TLS credential file",
            max_bytes=1_048_576,
        )
    except FileCredentialConfigurationError as exc:
        raise TemporalWorkerConfigError("TLS credential file is invalid") from exc


__all__ = ["TemporalWorkerConfig", "TemporalWorkerConfigError"]

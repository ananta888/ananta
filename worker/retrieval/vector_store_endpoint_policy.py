from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol, Sequence, runtime_checkable
from urllib.parse import unquote, urlsplit


class VectorStoreEndpointPolicyError(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = str(reason or "invalid_vector_store_endpoint")
        super().__init__(self.reason)


class VectorStoreSecretError(RuntimeError):
    def __init__(self, reason: str) -> None:
        self.reason = str(reason or "vector_store_secret_error")
        super().__init__(self.reason)


@dataclass(frozen=True, slots=True)
class NormalizedEndpoint:
    origin: str
    scheme: str
    host: str
    port: int
    secure: bool
    local: bool


def _is_local_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def normalize_endpoint(value: str, *, transport: str | None = None) -> NormalizedEndpoint:
    raw = str(value or "").strip()
    if not raw:
        raise VectorStoreEndpointPolicyError("missing_vector_store_endpoint")
    parsed = urlsplit(raw)
    scheme = parsed.scheme.lower()
    allowed_schemes = {"http", "https"} if transport == "rest" else {"grpc", "grpcs"}
    if transport is None:
        allowed_schemes = {"http", "https", "grpc", "grpcs"}
    if scheme not in allowed_schemes:
        raise VectorStoreEndpointPolicyError("unsupported_vector_store_endpoint_scheme")
    if parsed.username is not None or parsed.password is not None:
        raise VectorStoreEndpointPolicyError("vector_store_endpoint_userinfo_forbidden")
    if parsed.query:
        raise VectorStoreEndpointPolicyError("vector_store_endpoint_query_forbidden")
    if parsed.fragment:
        raise VectorStoreEndpointPolicyError("vector_store_endpoint_fragment_forbidden")
    if parsed.path not in {"", "/"}:
        raise VectorStoreEndpointPolicyError("vector_store_endpoint_path_forbidden")
    host = str(parsed.hostname or "").strip().lower()
    if not host:
        raise VectorStoreEndpointPolicyError("missing_vector_store_endpoint_host")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise VectorStoreEndpointPolicyError("invalid_vector_store_endpoint_host") from exc
    default_ports = {"http": 80, "https": 443, "grpc": 6334, "grpcs": 6334}
    try:
        port = int(parsed.port or default_ports[scheme])
    except ValueError as exc:
        raise VectorStoreEndpointPolicyError("invalid_vector_store_endpoint_port") from exc
    if not 1 <= port <= 65535:
        raise VectorStoreEndpointPolicyError("invalid_vector_store_endpoint_port")
    display_host = f"[{host}]" if ":" in host else host
    return NormalizedEndpoint(
        origin=f"{scheme}://{display_host}:{port}",
        scheme=scheme,
        host=host,
        port=port,
        secure=scheme in {"https", "grpcs"},
        local=_is_local_host(host),
    )


def normalize_allowed_origins(values: Sequence[str]) -> tuple[str, ...]:
    normalized = {normalize_endpoint(value).origin for value in values}
    if not normalized:
        raise VectorStoreEndpointPolicyError("vector_store_allowed_origins_required")
    return tuple(sorted(normalized))


def validate_endpoint_access(
    value: str,
    *,
    transport: str,
    allowed_origins: Sequence[str],
    external_calls_allowed: bool,
) -> NormalizedEndpoint:
    endpoint = normalize_endpoint(value, transport=transport)
    normalized_allowed = normalize_allowed_origins(allowed_origins)
    if endpoint.origin not in normalized_allowed:
        raise VectorStoreEndpointPolicyError("vector_store_endpoint_not_allowlisted")
    if not endpoint.local and not bool(external_calls_allowed):
        raise VectorStoreEndpointPolicyError("vector_store_external_calls_not_allowed")
    return endpoint


_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class SecretReference:
    scheme: str
    locator: str

    @classmethod
    def parse(cls, value: str) -> "SecretReference":
        raw = str(value or "").strip()
        parsed = urlsplit(raw)
        if parsed.query or parsed.fragment or parsed.username is not None or parsed.password is not None:
            raise VectorStoreEndpointPolicyError("invalid_vector_store_secret_ref")
        if parsed.scheme == "env":
            name = str(parsed.netloc or "").strip()
            if parsed.path or not _ENV_NAME.fullmatch(name):
                raise VectorStoreEndpointPolicyError("invalid_vector_store_env_secret_ref")
            return cls(scheme="env", locator=name)
        if parsed.scheme == "file":
            if parsed.netloc not in {"", "localhost"}:
                raise VectorStoreEndpointPolicyError("invalid_vector_store_file_secret_ref")
            path = Path(unquote(parsed.path))
            if not path.is_absolute():
                raise VectorStoreEndpointPolicyError("vector_store_secret_path_must_be_absolute")
            return cls(scheme="file", locator=str(path))
        raise VectorStoreEndpointPolicyError("unsupported_vector_store_secret_ref")

    def as_uri(self) -> str:
        if self.scheme == "env":
            return f"env://{self.locator}"
        return f"file://{self.locator}"


@runtime_checkable
class SecretResolver(Protocol):
    def resolve(self, reference: SecretReference) -> str: ...


class EnvFileSecretResolver:
    """Narrow resolver for injected environment values and mounted secret files."""

    def __init__(
        self,
        *,
        environ: Mapping[str, str] | None = None,
        allowed_file_roots: Sequence[str | Path] = (Path("/run/secrets"),),
        maximum_bytes: int = 16 * 1024,
    ) -> None:
        self._environ = environ if environ is not None else os.environ
        self._allowed_roots = tuple(Path(root).resolve(strict=False) for root in allowed_file_roots)
        self._maximum_bytes = max(1, int(maximum_bytes))
        if not self._allowed_roots:
            raise ValueError("vector_store_secret_roots_required")

    def resolve(self, reference: SecretReference) -> str:
        if reference.scheme == "env":
            value = str(self._environ.get(reference.locator) or "").strip()
            if not value:
                raise VectorStoreSecretError("vector_store_secret_not_found")
            return value
        if reference.scheme != "file":
            raise VectorStoreSecretError("unsupported_vector_store_secret_ref")
        source = Path(reference.locator)
        if source.is_symlink():
            raise VectorStoreSecretError("vector_store_secret_symlink_forbidden")
        try:
            resolved = source.resolve(strict=True)
        except OSError as exc:
            raise VectorStoreSecretError("vector_store_secret_not_found") from exc
        if not any(resolved.is_relative_to(root) for root in self._allowed_roots):
            raise VectorStoreSecretError("vector_store_secret_path_not_allowed")
        if not resolved.is_file():
            raise VectorStoreSecretError("vector_store_secret_not_regular_file")
        try:
            with resolved.open("rb") as handle:
                raw = handle.read(self._maximum_bytes + 1)
        except OSError as exc:
            raise VectorStoreSecretError("vector_store_secret_unreadable") from exc
        if len(raw) > self._maximum_bytes:
            raise VectorStoreSecretError("vector_store_secret_too_large")
        value = raw.decode("utf-8").strip()
        if not value:
            raise VectorStoreSecretError("vector_store_secret_empty")
        return value


def redact_sensitive_text(value: object, *, secrets: Sequence[str] = ()) -> str:
    redacted = str(value)
    for secret in secrets:
        clean = str(secret or "")
        if clean:
            redacted = redacted.replace(clean, "[REDACTED]")
    redacted = re.sub(r"(?i)(api[_-]?key|authorization)(\s*[:=]\s*)([^\s,;]+)", r"\1\2[REDACTED]", redacted)
    return redacted


__all__ = [
    "EnvFileSecretResolver",
    "NormalizedEndpoint",
    "SecretReference",
    "SecretResolver",
    "VectorStoreEndpointPolicyError",
    "VectorStoreSecretError",
    "normalize_allowed_origins",
    "normalize_endpoint",
    "redact_sensitive_text",
    "validate_endpoint_access",
]

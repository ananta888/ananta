"""Transport limits and stable data contracts for Unsloth Studio."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from agent.services.jmap_endpoint_policy import ValidatedJmapEndpoint

MAX_CONNECT_TIMEOUT_SECONDS = 2.0
MAX_TOTAL_TIMEOUT_SECONDS = 10.0
MAX_DECOMPRESSED_RESPONSE_BYTES = 1024 * 1024
MAX_IDEMPOTENT_RETRIES = 1


class UnslothStudioTransportError(RuntimeError):
    def __init__(self, reason_code: str, *, retryable: bool = False) -> None:
        self.reason_code = str(reason_code or "unsloth_studio_transport_failed")
        self.retryable = bool(retryable)
        super().__init__(self.reason_code)


@dataclass(frozen=True, slots=True)
class UnslothStudioTransportConfig:
    base_url: str
    credential_secret_ref: str
    expected_studio_version: str
    allowed_hosts: tuple[str, ...]
    allowed_ip_cidrs: tuple[str, ...]
    external_network_enabled: bool = False
    local_network_enabled: bool = False
    allow_plaintext_internal: bool = False
    connect_timeout_seconds: float = MAX_CONNECT_TIMEOUT_SECONDS
    total_timeout_seconds: float = MAX_TOTAL_TIMEOUT_SECONDS
    maximum_response_bytes: int = MAX_DECOMPRESSED_RESPONSE_BYTES
    maximum_request_bytes: int = MAX_DECOMPRESSED_RESPONSE_BYTES
    maximum_idempotent_retries: int = MAX_IDEMPOTENT_RETRIES
    retry_backoff_seconds: float = 0.05

    def __post_init__(self) -> None:
        if not str(self.base_url or "").strip():
            raise ValueError("unsloth_studio_base_url_required")
        if not str(self.credential_secret_ref or "").strip():
            raise ValueError("unsloth_studio_credential_secret_ref_required")
        if not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}",
            str(self.expected_studio_version or ""),
        ):
            raise ValueError("unsloth_studio_expected_version_invalid")
        if not self.allowed_hosts:
            raise ValueError("unsloth_studio_host_allowlist_required")
        if not self.allowed_ip_cidrs:
            raise ValueError("unsloth_studio_ip_allowlist_required")
        if not 0 < float(self.connect_timeout_seconds) <= MAX_CONNECT_TIMEOUT_SECONDS:
            raise ValueError("unsloth_studio_connect_timeout_invalid")
        if not 0 < float(self.total_timeout_seconds) <= MAX_TOTAL_TIMEOUT_SECONDS:
            raise ValueError("unsloth_studio_total_timeout_invalid")
        if float(self.connect_timeout_seconds) > float(self.total_timeout_seconds):
            raise ValueError("unsloth_studio_timeout_order_invalid")
        if not 0 < int(self.maximum_response_bytes) <= MAX_DECOMPRESSED_RESPONSE_BYTES:
            raise ValueError("unsloth_studio_response_limit_invalid")
        if not 0 < int(self.maximum_request_bytes) <= MAX_DECOMPRESSED_RESPONSE_BYTES:
            raise ValueError("unsloth_studio_request_limit_invalid")
        if not 0 <= int(self.maximum_idempotent_retries) <= MAX_IDEMPOTENT_RETRIES:
            raise ValueError("unsloth_studio_retry_limit_invalid")
        if not 0 <= float(self.retry_backoff_seconds) <= 1.0:
            raise ValueError("unsloth_studio_retry_backoff_invalid")


@dataclass(frozen=True, slots=True)
class UnslothStudioHttpRequest:
    method: str
    url: str
    headers: Mapping[str, str]
    body: bytes | None
    endpoint: ValidatedJmapEndpoint
    connect_timeout_seconds: float
    total_timeout_seconds: float
    maximum_decompressed_bytes: int


@dataclass(frozen=True, slots=True)
class UnslothStudioHttpResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes


class UnslothStudioHttpAdapter(Protocol):
    def send(self, request: UnslothStudioHttpRequest) -> UnslothStudioHttpResponse: ...

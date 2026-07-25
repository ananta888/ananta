from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping, Protocol

if TYPE_CHECKING:
    from agent.services.mail_provider_ports import MailProviderResult


@dataclass(frozen=True, slots=True)
class JmapRuntimeLimits:
    connect_timeout_seconds: float = 5.0
    read_timeout_seconds: float = 20.0
    maximum_request_bytes: int = 8 * 1024 * 1024
    maximum_json_response_bytes: int = 8 * 1024 * 1024
    maximum_blob_bytes: int = 25 * 1024 * 1024
    maximum_redirects: int = 3
    maximum_safe_retries: int = 2
    maximum_retry_after_seconds: float = 30.0
    maximum_calls_per_request: int = 32
    maximum_objects_per_get: int = 500
    maximum_objects_per_set: int = 128
    maximum_concurrent_requests: int = 4
    maximum_queued_requests: int = 32
    request_queue_timeout_seconds: float = 5.0
    discovery_cache_ttl_seconds: float = 300.0
    maximum_change_pages: int = 20
    maximum_rebuild_objects: int = 5_000
    maximum_query_page_size: int = 500
    polling_interval_seconds: int = 300
    push_ping_seconds: int = 300

    def __post_init__(self) -> None:
        positive = (
            self.connect_timeout_seconds,
            self.read_timeout_seconds,
            self.maximum_request_bytes,
            self.maximum_json_response_bytes,
            self.maximum_blob_bytes,
            self.maximum_calls_per_request,
            self.maximum_objects_per_get,
            self.maximum_objects_per_set,
            self.maximum_concurrent_requests,
            self.maximum_queued_requests,
            self.request_queue_timeout_seconds,
            self.discovery_cache_ttl_seconds,
            self.maximum_change_pages,
            self.maximum_rebuild_objects,
            self.maximum_query_page_size,
            self.polling_interval_seconds,
            self.push_ping_seconds,
        )
        if any(float(value) <= 0 for value in positive):
            raise ValueError("jmap_runtime_limit_must_be_positive")
        if self.maximum_redirects < 0 or self.maximum_safe_retries < 0:
            raise ValueError("jmap_runtime_limit_must_not_be_negative")
        if self.maximum_retry_after_seconds < 0:
            raise ValueError("jmap_retry_after_limit_must_not_be_negative")


@dataclass(frozen=True, slots=True)
class MailFeaturePolicy:
    mail_enabled: bool = True
    jmap_enabled: bool = True
    imap_fallback_enabled: bool = True
    protocol_autodiscovery_enabled: bool = True
    external_network_enabled: bool = False
    local_endpoint_policy_enabled: bool = False
    limits: JmapRuntimeLimits = JmapRuntimeLimits()

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "MailFeaturePolicy":
        value = dict(raw or {})
        raw_limits = dict(value.get("limits") or {})
        allowed_limit_names = set(JmapRuntimeLimits.__dataclass_fields__)
        unknown_limits = sorted(set(raw_limits) - allowed_limit_names)
        if unknown_limits:
            raise ValueError("unknown_jmap_runtime_limit")
        limits = JmapRuntimeLimits(**raw_limits)
        allowed_names = set(cls.__dataclass_fields__) - {"limits"}
        unknown = sorted(set(value) - allowed_names - {"limits"})
        if unknown:
            raise ValueError("unknown_mail_feature_policy")
        return cls(**{name: bool(value[name]) for name in allowed_names if name in value}, limits=limits)

    def network_reason(self, *, account_enabled: bool) -> str:
        if not self.mail_enabled:
            return "mail_disabled"
        if not self.jmap_enabled:
            return "jmap_disabled"
        if not account_enabled:
            return "mail_account_disabled"
        return "ok"


@dataclass(frozen=True, slots=True)
class MailOperationEvent:
    provider: str
    phase: str
    outcome: str
    reason_code: str
    retryable: bool = False
    duration_ms: int = 0


class MailOperationObserver(Protocol):
    def record(self, event: MailOperationEvent) -> None:
        ...


class MailRuntimeAvailabilityPolicy(Protocol):
    def evaluate(
        self,
        *,
        account_id: str,
        provider: str,
        operation: str,
    ) -> MailProviderResult[None]:
        ...

    def record_success(
        self,
        *,
        account_id: str,
        provider: str,
        operation: str,
    ) -> None:
        ...

    def record_failure(
        self,
        *,
        account_id: str,
        provider: str,
        operation: str,
        retryable: bool,
    ) -> None:
        ...


class NullMailOperationObserver:
    def record(self, event: MailOperationEvent) -> None:
        del event


__all__ = [
    "JmapRuntimeLimits",
    "MailRuntimeAvailabilityPolicy",
    "MailFeaturePolicy",
    "MailOperationEvent",
    "MailOperationObserver",
    "NullMailOperationObserver",
]

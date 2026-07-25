from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from agent.services.jmap_auth_service import (
    JmapAuthService,
    JmapAuthorizationProvider,
    StaticJmapAuthorizationProvider,
)
from agent.services.jmap_client_service import JmapClient
from agent.services.jmap_contract_service import JmapContractError, JmapSessionDocument, normalize_jmap_session
from agent.services.jmap_endpoint_policy import JmapEndpointPolicy, JmapEndpointPolicyError
from agent.services.jmap_http_transport import JmapHttpTransport, JmapTransportError
from agent.services.jmap_request_scheduler import JmapRequestScheduler
from agent.services.mail_contract_service import MailAccountV2
from agent.services.mail_feature_policy import MailFeaturePolicy
from agent.services.mail_provider_ports import (
    MailAuthMaterial,
    MailProviderCapabilities,
    MailProviderResult,
    MailProviderSession,
)


@dataclass(frozen=True, slots=True)
class _DiscoveryCacheEntry:
    document: JmapSessionDocument
    expires_at: float


class JmapDiscoveryCache:
    """Bounded metadata-only cache. Authorization material is never accepted."""

    def __init__(
        self,
        *,
        ttl_seconds: float,
        maximum_entries: int = 256,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl_seconds = max(0.01, float(ttl_seconds))
        self._maximum_entries = max(1, int(maximum_entries))
        self._clock = clock
        self._lock = threading.RLock()
        self._entries: OrderedDict[str, _DiscoveryCacheEntry] = OrderedDict()

    def get(self, key: str) -> JmapSessionDocument | None:
        now = self._clock()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None or entry.expires_at <= now:
                return None
            self._entries.move_to_end(key)
            return entry.document

    def put(self, key: str, document: JmapSessionDocument) -> str:
        with self._lock:
            previous = self._entries.get(key)
            self._entries[key] = _DiscoveryCacheEntry(
                document=document,
                expires_at=self._clock() + self._ttl_seconds,
            )
            self._entries.move_to_end(key)
            while len(self._entries) > self._maximum_entries:
                self._entries.popitem(last=False)
            return previous.document.state if previous is not None else ""

    def invalidate_account(self, account_id: str) -> None:
        prefix = f"{str(account_id)}\x1f"
        with self._lock:
            for key in tuple(self._entries):
                if key.startswith(prefix):
                    self._entries.pop(key, None)


class JmapDiscoveryService:
    def __init__(
        self,
        *,
        transport: JmapHttpTransport,
        endpoint_policy: JmapEndpointPolicy,
        auth_service: JmapAuthService,
        feature_policy: MailFeaturePolicy,
        cache: JmapDiscoveryCache | None = None,
        session_state_observer: Callable[[str, str, str], None] | None = None,
    ) -> None:
        self._transport = transport
        self._endpoint_policy = endpoint_policy
        self._auth_service = auth_service
        self._feature_policy = feature_policy
        self._cache = cache or JmapDiscoveryCache(
            ttl_seconds=feature_policy.limits.discovery_cache_ttl_seconds
        )
        self._session_state_observer = session_state_observer

    def discover(
        self,
        *,
        account: MailAccountV2,
        auth: MailAuthMaterial,
    ) -> MailProviderResult[JmapSessionDocument]:
        reason = self._feature_policy.network_reason(account_enabled=account.enabled)
        if reason != "ok":
            return MailProviderResult(ok=False, reason_code=reason)
        config = dict(account.provider_config or {})
        try:
            session_url = self._session_url(config)
            self._endpoint_policy.validate_initial(session_url, purpose="session")
        except (JmapEndpointPolicyError, ValueError) as exc:
            code = getattr(exc, "reason_code", str(exc) or "jmap_discovery_config_invalid")
            return MailProviderResult(ok=False, reason_code=code)
        authorization = self._auth_service.bind(account=account, auth=auth)
        if not authorization.ok or authorization.value is None:
            return MailProviderResult(
                ok=False,
                reason_code=authorization.reason_code,
                retryable=authorization.retryable,
                retry_after_ms=authorization.retry_after_ms,
                details=authorization.details,
            )
        cache_key = _cache_key(
            account_id=account.account_id,
            session_url=session_url,
            provider_account_id=str(config.get("provider_account_id") or ""),
        )
        cached = self._cache.get(cache_key)
        if cached is not None:
            return MailProviderResult(ok=True, reason_code="jmap_discovery_cached", value=cached)
        try:
            payload, response = self._request_session(
                session_url=session_url,
                authorization=authorization.value,
            )
            final_origin = _origin(response.final_url)
            session = normalize_jmap_session(
                payload,
                session_url=response.final_url,
                trusted_origin=final_origin,
                preferred_provider_account_id=str(config.get("provider_account_id") or ""),
                local_maximum_request_bytes=self._feature_policy.limits.maximum_request_bytes,
                local_maximum_calls_per_request=self._feature_policy.limits.maximum_calls_per_request,
                local_maximum_objects_per_get=self._feature_policy.limits.maximum_objects_per_get,
                local_maximum_objects_per_set=self._feature_policy.limits.maximum_objects_per_set,
                local_maximum_concurrent_requests=self._feature_policy.limits.maximum_concurrent_requests,
                local_maximum_queued_requests=self._feature_policy.limits.maximum_queued_requests,
                request_queue_timeout_seconds=self._feature_policy.limits.request_queue_timeout_seconds,
            )
            self._endpoint_policy.validate_related(
                session.api_url,
                trusted_origin=final_origin,
                purpose="api",
            )
            self._endpoint_policy.validate_template(
                session.download_url_template,
                trusted_origin=final_origin,
                purpose="download",
            )
            self._endpoint_policy.validate_template(
                session.upload_url_template,
                trusted_origin=final_origin,
                purpose="upload",
            )
            if session.event_source_url_template:
                self._endpoint_policy.validate_template(
                    session.event_source_url_template,
                    trusted_origin=final_origin,
                    purpose="event_source",
                )
        except (JmapTransportError, JmapContractError, JmapEndpointPolicyError) as exc:
            return MailProviderResult(
                ok=False,
                reason_code=exc.reason_code,
                retryable=bool(getattr(exc, "retryable", False)),
                retry_after_ms=getattr(exc, "retry_after_ms", None),
            )
        previous_state = self._cache.put(cache_key, session)
        if (
            previous_state
            and previous_state != session.state
            and self._session_state_observer is not None
        ):
            try:
                self._session_state_observer(account.account_id, previous_state, session.state)
            except Exception:
                self._cache.invalidate_account(account.account_id)
                return MailProviderResult(ok=False, reason_code="jmap_session_invalidation_failed")
        return MailProviderResult(ok=True, reason_code="ok", value=session)

    def _request_session(
        self,
        *,
        session_url: str,
        authorization: JmapAuthorizationProvider,
    ):
        force_refresh = False
        while True:
            headers = authorization.headers(force_refresh=force_refresh)
            if not headers.ok or headers.value is None:
                raise JmapTransportError(
                    headers.reason_code,
                    retryable=headers.retryable,
                    retry_after_ms=headers.retry_after_ms,
                )
            try:
                return self._transport.request_json(
                    method="GET",
                    url=session_url,
                    headers=headers.value,
                    purpose="session",
                    allow_redirects=True,
                    retry_safe=True,
                )
            except JmapTransportError as exc:
                if (
                    exc.reason_code == "jmap_authentication_failed"
                    and authorization.supports_rotation
                    and not force_refresh
                ):
                    force_refresh = True
                    continue
                raise

    def _session_url(self, config: Mapping[str, Any]) -> str:
        explicit = str(config.get("session_url") or "").strip()
        if explicit:
            return explicit
        if not self._feature_policy.protocol_autodiscovery_enabled:
            raise ValueError("jmap_autodiscovery_disabled")
        domain = str(config.get("discovery_domain") or "").strip().rstrip(".")
        if not domain or any(char in domain for char in "/:@?#"):
            raise ValueError("jmap_discovery_domain_invalid")
        try:
            ascii_domain = domain.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError("jmap_discovery_domain_invalid") from exc
        return f"https://{ascii_domain}/.well-known/jmap"


@dataclass(frozen=True, slots=True)
class _SessionContext:
    account_id: str
    document: JmapSessionDocument
    authorization: JmapAuthorizationProvider
    scheduler: JmapRequestScheduler[Any]


class JmapSessionRegistry:
    """Ephemeral per-binding context; public sessions contain only an opaque id."""

    def __init__(self, *, transport: JmapHttpTransport) -> None:
        self._transport = transport
        self._lock = threading.RLock()
        self._contexts: dict[str, _SessionContext] = {}

    def register(
        self,
        *,
        account_id: str,
        document: JmapSessionDocument,
        authorization_provider: JmapAuthorizationProvider | None = None,
        authorization_headers: Mapping[str, str] | None = None,
    ) -> MailProviderSession:
        authorization = authorization_provider
        if authorization is None:
            authorization = StaticJmapAuthorizationProvider(
                headers=dict(authorization_headers or {})
            )
        session_id = f"jmap-session-{uuid.uuid4().hex}"
        scheduler: JmapRequestScheduler[Any] = JmapRequestScheduler(
            maximum_concurrent_requests=document.limits.maximum_concurrent_requests,
            maximum_queued_requests=document.limits.maximum_queued_requests,
            queue_timeout_seconds=document.limits.request_queue_timeout_seconds,
        )
        with self._lock:
            self._contexts[session_id] = _SessionContext(
                account_id=str(account_id),
                document=document,
                authorization=authorization,
                scheduler=scheduler,
            )
        return MailProviderSession(
            session_id=session_id,
            account_id=account_id,
            protocol="jmap",
            provider_account_id=document.provider_account_id,
        )

    def client(self, session: MailProviderSession) -> MailProviderResult[JmapClient]:
        context = self._resolve(session)
        if not context.ok or context.value is None:
            return MailProviderResult(ok=False, reason_code=context.reason_code)
        value = context.value
        return MailProviderResult(
            ok=True,
            reason_code="ok",
            value=JmapClient(
                session=value.document,
                transport=self._transport,
                authorization_provider=value.authorization,
                scheduler=value.scheduler,
            ),
        )

    def document(self, session: MailProviderSession) -> MailProviderResult[JmapSessionDocument]:
        context = self._resolve(session)
        if not context.ok or context.value is None:
            return MailProviderResult(ok=False, reason_code=context.reason_code)
        return MailProviderResult(ok=True, reason_code="ok", value=context.value.document)

    def remove(self, session: MailProviderSession) -> bool:
        with self._lock:
            context = self._contexts.get(session.session_id)
            if context is None or not _session_matches(session, context):
                return False
            self._contexts.pop(session.session_id, None)
            return True

    def invalidate_account_state(
        self,
        account_id: str,
        previous_state: str,
        new_state: str,
    ) -> None:
        del new_state
        with self._lock:
            for session_id, context in tuple(self._contexts.items()):
                if context.account_id == account_id and context.document.state == previous_state:
                    self._contexts.pop(session_id, None)

    def _resolve(self, session: MailProviderSession) -> MailProviderResult[_SessionContext]:
        with self._lock:
            context = self._contexts.get(session.session_id)
        if context is None:
            return MailProviderResult(ok=False, reason_code="jmap_session_not_found")
        if not _session_matches(session, context):
            return MailProviderResult(ok=False, reason_code="jmap_session_mismatch")
        return MailProviderResult(ok=True, reason_code="ok", value=context)


class JmapLifecycleService:
    def __init__(
        self,
        *,
        discovery: JmapDiscoveryService,
        auth_service: JmapAuthService,
        registry: JmapSessionRegistry,
    ) -> None:
        self._discovery = discovery
        self._auth_service = auth_service
        self._registry = registry

    def connect(
        self,
        account: MailAccountV2,
        auth: MailAuthMaterial,
    ) -> MailProviderResult[MailProviderSession]:
        discovered = self._discovery.discover(account=account, auth=auth)
        if not discovered.ok or discovered.value is None:
            return MailProviderResult(
                ok=False,
                reason_code=discovered.reason_code,
                retryable=discovered.retryable,
                retry_after_ms=discovered.retry_after_ms,
            )
        authorization = self._auth_service.bind(account=account, auth=auth)
        if not authorization.ok or authorization.value is None:
            return MailProviderResult(ok=False, reason_code=authorization.reason_code)
        return MailProviderResult(
            ok=True,
            reason_code="ok",
            value=self._registry.register(
                account_id=account.account_id,
                document=discovered.value,
                authorization_provider=authorization.value,
            ),
        )

    def disconnect(self, session: MailProviderSession) -> MailProviderResult[None]:
        if not self._registry.remove(session):
            return MailProviderResult(ok=False, reason_code="jmap_session_not_found")
        return MailProviderResult(ok=True, reason_code="ok")


class JmapCapabilitiesService:
    def __init__(self, *, registry: JmapSessionRegistry) -> None:
        self._registry = registry

    def capabilities(
        self,
        session: MailProviderSession,
    ) -> MailProviderResult[MailProviderCapabilities]:
        resolved = self._registry.document(session)
        if not resolved.ok or resolved.value is None:
            return MailProviderResult(ok=False, reason_code=resolved.reason_code)
        document = resolved.value
        return MailProviderResult(
            ok=True,
            reason_code="ok",
            value=MailProviderCapabilities(
                provider="jmap",
                using=tuple(sorted(document.server_capabilities)),
                features=frozenset({"metadata", "body", "attachments", "sync", "mutations"}),
                limits={
                    "max_request_bytes": document.limits.maximum_request_bytes,
                    "max_calls": document.limits.maximum_calls_per_request,
                    "max_objects_get": document.limits.maximum_objects_per_get,
                    "max_objects_set": document.limits.maximum_objects_per_set,
                    "max_concurrent_requests": document.limits.maximum_concurrent_requests,
                    "max_queued_requests": document.limits.maximum_queued_requests,
                },
            ),
        )


def _session_matches(session: MailProviderSession, context: _SessionContext) -> bool:
    return (
        session.protocol == "jmap"
        and session.account_id == context.account_id
        and session.provider_account_id == context.document.provider_account_id
    )


def _cache_key(*, account_id: str, session_url: str, provider_account_id: str) -> str:
    return "\x1f".join((str(account_id), str(session_url), str(provider_account_id)))


def _origin(value: str) -> str:
    parsed = urlsplit(value)
    host = str(parsed.hostname or "").lower()
    port = int(parsed.port or (443 if parsed.scheme.lower() == "https" else 80))
    display = f"[{host}]" if ":" in host else host
    return f"{parsed.scheme.lower()}://{display}:{port}"


__all__ = [
    "JmapCapabilitiesService",
    "JmapDiscoveryCache",
    "JmapDiscoveryService",
    "JmapLifecycleService",
    "JmapSessionRegistry",
]

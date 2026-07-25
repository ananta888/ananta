from __future__ import annotations

import itertools
import json
import random
import time
from typing import Any, Callable, Mapping, Sequence

from agent.services.jmap_auth_service import (
    JmapAuthorizationProvider,
    StaticJmapAuthorizationProvider,
)
from agent.services.jmap_contract_service import (
    JMAP_CORE_CAPABILITY,
    JMAP_MAIL_CAPABILITY,
    JmapContractError,
    JmapMethodCall,
    JmapMethodResponse,
    JmapSessionDocument,
    normalize_method_calls,
    parse_method_responses,
)
from agent.services.jmap_http_transport import JmapHttpTransport, JmapTransportError
from agent.services.jmap_request_scheduler import (
    JmapCancellationSignal,
    JmapRequestScheduler,
)
from agent.services.mail_provider_ports import MailProviderResult


_MUTATING_SUFFIXES = ("/set", "/copy", "/import")


class JmapClient:
    def __init__(
        self,
        *,
        session: JmapSessionDocument,
        transport: JmapHttpTransport,
        authorization_headers: Mapping[str, str] | None = None,
        authorization_provider: JmapAuthorizationProvider | None = None,
        scheduler: JmapRequestScheduler[Any] | None = None,
        maximum_method_retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        if authorization_provider is not None and authorization_headers is not None:
            raise ValueError("jmap_authorization_source_conflict")
        self._session = session
        self._transport = transport
        self._authorization = authorization_provider or StaticJmapAuthorizationProvider(
            headers=dict(authorization_headers or {})
        )
        self._scheduler: JmapRequestScheduler[Any] = scheduler or JmapRequestScheduler(
            maximum_concurrent_requests=session.limits.maximum_concurrent_requests,
            maximum_queued_requests=session.limits.maximum_queued_requests,
            queue_timeout_seconds=session.limits.request_queue_timeout_seconds,
        )
        self._counter = itertools.count(1)
        self._maximum_method_retries = max(0, int(maximum_method_retries))
        self._sleep = sleep
        self._jitter = jitter

    @property
    def session(self) -> JmapSessionDocument:
        return self._session

    def call(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        call_id: str = "",
        cancellation: JmapCancellationSignal | None = None,
    ) -> MailProviderResult[JmapMethodResponse]:
        clean_id = str(call_id or f"c{next(self._counter)}")
        result = self.call_many(
            (JmapMethodCall(str(name), dict(arguments), clean_id),),
            cancellation=cancellation,
        )
        if not result.ok or result.value is None:
            return MailProviderResult(
                ok=False,
                reason_code=result.reason_code,
                retryable=result.retryable,
                retry_after_ms=result.retry_after_ms,
                details=result.details,
            )
        response = result.value[0]
        if response.is_error:
            return MailProviderResult(
                ok=False,
                reason_code=_method_error_reason(response.error_type),
                retryable=response.error_type == "rateLimit",
                details={"method_error_type": response.error_type},
            )
        return MailProviderResult(ok=True, reason_code="ok", value=response)

    def call_many(
        self,
        calls: Sequence[JmapMethodCall],
        *,
        cancellation: JmapCancellationSignal | None = None,
    ) -> MailProviderResult[tuple[JmapMethodResponse, ...]]:
        if len(calls) > self._session.limits.maximum_calls_per_request:
            return MailProviderResult(ok=False, reason_code="jmap_max_calls_exceeded")
        normalized = tuple(calls)
        if not normalized:
            return MailProviderResult(ok=False, reason_code="jmap_method_calls_required")
        call_ids = [call.call_id for call in normalized]
        if any(not value for value in call_ids) or len(set(call_ids)) != len(call_ids):
            return MailProviderResult(ok=False, reason_code="jmap_call_id_invalid")
        try:
            normalized = normalize_method_calls(normalized)
        except JmapContractError as exc:
            return MailProviderResult(ok=False, reason_code=exc.reason_code)
        using = {JMAP_CORE_CAPABILITY}
        if any(call.name.startswith(("Email/", "Mailbox/", "Thread/")) for call in normalized):
            using.add(JMAP_MAIL_CAPABILITY)
        if not using.issubset(self._session.server_capabilities):
            return MailProviderResult(ok=False, reason_code="jmap_capability_missing")
        payload = {
            "using": sorted(using),
            "methodCalls": [
                [call.name, dict(call.arguments), call.call_id]
                for call in normalized
            ],
        }
        encoded_size = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        if encoded_size > self._session.limits.maximum_request_bytes:
            return MailProviderResult(ok=False, reason_code="jmap_request_too_large")
        retry_safe = not any(call.name.endswith(_MUTATING_SUFFIXES) for call in normalized)
        return self._scheduler.execute(
            lambda: self._perform_calls(
                normalized=normalized,
                payload=payload,
                retry_safe=retry_safe,
                cancellation=cancellation,
            ),
            cancellation=cancellation,
        )

    def _perform_calls(
        self,
        *,
        normalized: tuple[JmapMethodCall, ...],
        payload: Mapping[str, Any],
        retry_safe: bool,
        cancellation: JmapCancellationSignal | None,
    ) -> MailProviderResult[tuple[JmapMethodResponse, ...]]:
        method_attempt = 0
        force_refresh = False
        while True:
            headers = self._authorization.headers(force_refresh=force_refresh)
            if not headers.ok or headers.value is None:
                return MailProviderResult(
                    ok=False,
                    reason_code=headers.reason_code,
                    retryable=headers.retryable,
                    retry_after_ms=headers.retry_after_ms,
                    details=headers.details,
                )
            try:
                raw, _response = self._transport.request_json(
                    method="POST",
                    url=self._session.api_url,
                    headers=headers.value,
                    payload=payload,
                    purpose="api",
                    trusted_origin=self._session.trusted_origin,
                    allow_redirects=False,
                    retry_safe=retry_safe,
                    cancellation=cancellation,
                )
                responses = parse_method_responses(raw, expected_calls=normalized)
            except (JmapTransportError, JmapContractError) as exc:
                if (
                    isinstance(exc, JmapTransportError)
                    and exc.reason_code == "jmap_authentication_failed"
                    and self._authorization.supports_rotation
                    and not force_refresh
                ):
                    force_refresh = True
                    continue
                return MailProviderResult(
                    ok=False,
                    reason_code=exc.reason_code,
                    retryable=bool(getattr(exc, "retryable", False)),
                    retry_after_ms=getattr(exc, "retry_after_ms", None),
                )
            force_refresh = False
            rate_limited = any(
                response.is_error and response.error_type == "rateLimit"
                for response in responses
            )
            if not rate_limited:
                return MailProviderResult(ok=True, reason_code="ok", value=responses)
            if not retry_safe or method_attempt >= self._maximum_method_retries:
                return MailProviderResult(
                    ok=False,
                    reason_code="jmap_method_ratelimit",
                    retryable=retry_safe,
                )
            delay = 0.25 * (2**method_attempt) * (
                0.8 + 0.4 * min(1.0, max(0.0, float(self._jitter())))
            )
            if cancellation is not None:
                if cancellation.wait(delay):
                    return MailProviderResult(ok=False, reason_code="jmap_request_cancelled")
            else:
                self._sleep(delay)
            method_attempt += 1

    def get_objects(
        self,
        *,
        object_type: str,
        provider_account_id: str,
        ids: Sequence[str],
        properties: Sequence[str],
        extra_arguments: Mapping[str, Any] | None = None,
        cancellation: JmapCancellationSignal | None = None,
    ) -> MailProviderResult[tuple[Mapping[str, Any], ...]]:
        clean_ids = tuple(dict.fromkeys(str(value) for value in ids if str(value)))
        if not clean_ids:
            return MailProviderResult(ok=True, reason_code="ok", value=())
        limit = self._session.limits.maximum_objects_per_get
        collected: list[Mapping[str, Any]] = []
        for offset in range(0, len(clean_ids), limit):
            chunk = clean_ids[offset : offset + limit]
            arguments: dict[str, Any] = {
                "accountId": provider_account_id,
                "ids": list(chunk),
                "properties": list(properties),
            }
            arguments.update(dict(extra_arguments or {}))
            response = self.call(
                f"{object_type}/get",
                arguments,
                cancellation=cancellation,
            )
            if not response.ok or response.value is None:
                return MailProviderResult(
                    ok=False,
                    reason_code=response.reason_code,
                    retryable=response.retryable,
                    retry_after_ms=response.retry_after_ms,
                    details=response.details,
                )
            rows = response.value.arguments.get("list")
            if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
                return MailProviderResult(ok=False, reason_code="jmap_get_list_invalid")
            if len(rows) > len(chunk) or len(rows) > limit:
                return MailProviderResult(ok=False, reason_code="jmap_get_object_limit_exceeded")
            response_ids = [str(row.get("id") or "") for row in rows]
            if (
                any(not value for value in response_ids)
                or len(set(response_ids)) != len(response_ids)
                or not set(response_ids).issubset(set(chunk))
            ):
                return MailProviderResult(ok=False, reason_code="jmap_get_response_ids_invalid")
            collected.extend(dict(row) for row in rows)
        return MailProviderResult(ok=True, reason_code="ok", value=tuple(collected))


def _method_error_reason(value: str) -> str:
    clean = str(value or "unknown").replace("_", "-")
    return f"jmap_method_{clean.lower()}"


__all__ = ["JmapClient"]

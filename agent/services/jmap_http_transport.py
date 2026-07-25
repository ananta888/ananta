from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import ssl
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlsplit, urlunsplit

from agent.services.jmap_endpoint_policy import (
    JmapEndpointPolicy,
    JmapEndpointPolicyError,
    ValidatedJmapEndpoint,
)
from agent.services.jmap_request_scheduler import JmapCancellationSignal
from agent.services.mail_feature_policy import (
    JmapRuntimeLimits,
    MailOperationEvent,
    MailOperationObserver,
    NullMailOperationObserver,
)


class JmapTransportError(RuntimeError):
    def __init__(
        self,
        reason_code: str,
        *,
        retryable: bool = False,
        retry_after_ms: int | None = None,
    ) -> None:
        self.reason_code = str(reason_code or "jmap_transport_failed")
        self.retryable = bool(retryable)
        self.retry_after_ms = retry_after_ms
        super().__init__(self.reason_code)


@dataclass(frozen=True, slots=True)
class JmapHttpRequest:
    method: str
    url: str
    headers: Mapping[str, str]
    body: bytes | None
    connect_timeout_seconds: float
    read_timeout_seconds: float
    maximum_response_bytes: int
    endpoint: ValidatedJmapEndpoint | None = None
    cancellation: JmapCancellationSignal | None = None


@dataclass(frozen=True, slots=True)
class JmapHttpResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes
    final_url: str


class JmapHttpAdapter(Protocol):
    def send(self, request: JmapHttpRequest) -> JmapHttpResponse:
        ...


class RequestsJmapHttpAdapter:
    """Direct HTTP adapter pinned to endpoint-policy IPs; no proxy or second DNS lookup."""

    def send(self, request: JmapHttpRequest) -> JmapHttpResponse:
        endpoint = request.endpoint
        if endpoint is None or endpoint.url != request.url:
            raise JmapTransportError("jmap_validated_endpoint_required")
        parsed = urlsplit(request.url)
        try:
            parsed_host = str(parsed.hostname or "").lower().encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise JmapTransportError("jmap_validated_endpoint_mismatch") from exc
        if (
            parsed_host != endpoint.host
            or int(parsed.port or (443 if parsed.scheme.lower() == "https" else 80)) != endpoint.port
            or parsed.scheme.lower() != endpoint.scheme
        ):
            raise JmapTransportError("jmap_validated_endpoint_mismatch")
        if _cancelled(request.cancellation):
            raise JmapTransportError("jmap_request_cancelled")
        connection: http.client.HTTPConnection | None = None
        try:
            pinned_socket = _connect_validated_endpoint(
                endpoint,
                connect_timeout_seconds=request.connect_timeout_seconds,
                read_timeout_seconds=request.read_timeout_seconds,
                cancellation=request.cancellation,
            )
            connection = http.client.HTTPConnection(
                endpoint.host,
                endpoint.port,
                timeout=request.read_timeout_seconds,
            )
            connection.sock = pinned_socket
            target = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
            headers = {str(key): str(value) for key, value in request.headers.items()}
            headers.setdefault("Host", _host_header(endpoint))
            headers.setdefault("Accept-Encoding", "identity")
            connection.request(
                request.method,
                target,
                body=request.body,
                headers=headers,
            )
            response = connection.getresponse()
            response_headers = {str(key).lower(): str(value) for key, value in response.getheaders()}
            declared = response_headers.get("content-length")
            if declared:
                try:
                    if int(declared) > request.maximum_response_bytes:
                        raise JmapTransportError("jmap_response_too_large")
                except ValueError:
                    pass
            body = bytearray()
            while True:
                if _cancelled(request.cancellation):
                    raise JmapTransportError("jmap_request_cancelled")
                chunk = response.read(min(64 * 1024, request.maximum_response_bytes + 1 - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > request.maximum_response_bytes:
                    raise JmapTransportError("jmap_response_too_large")
            return JmapHttpResponse(
                status_code=int(response.status),
                headers=response_headers,
                body=bytes(body),
                final_url=request.url,
            )
        except (socket.timeout, TimeoutError) as exc:
            raise JmapTransportError("jmap_request_timeout", retryable=True) from exc
        except ssl.SSLCertVerificationError as exc:
            raise JmapTransportError("jmap_tls_verification_failed") from exc
        except JmapTransportError:
            raise
        except (ssl.SSLError, OSError, http.client.HTTPException) as exc:
            raise JmapTransportError("jmap_connection_failed", retryable=True) from exc
        finally:
            if connection is not None:
                connection.close()


def _json_without_duplicate_keys(raw: bytes) -> Any:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise ValueError("duplicate_json_member")
            result[key] = value
        return result

    return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)


class JmapHttpTransport:
    _REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

    def __init__(
        self,
        *,
        endpoint_policy: JmapEndpointPolicy,
        adapter: JmapHttpAdapter | None = None,
        limits: JmapRuntimeLimits = JmapRuntimeLimits(),
        observer: MailOperationObserver | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._endpoint_policy = endpoint_policy
        self._adapter = adapter or RequestsJmapHttpAdapter()
        self._limits = limits
        self._observer = observer or NullMailOperationObserver()
        self._sleep = sleep
        self._clock = clock

    def request_json(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str] | None = None,
        payload: Mapping[str, Any] | None = None,
        purpose: str,
        trusted_origin: str | None = None,
        allow_redirects: bool = False,
        retry_safe: bool = True,
        cancellation: JmapCancellationSignal | None = None,
    ) -> tuple[Mapping[str, Any], JmapHttpResponse]:
        body = None
        request_headers = {str(k): str(v) for k, v in dict(headers or {}).items()}
        request_headers.setdefault("Accept", "application/json")
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(body) > self._limits.maximum_request_bytes:
                raise JmapTransportError("jmap_request_too_large")
            request_headers.setdefault("Content-Type", "application/json")
        response = self._request(
            method=method,
            url=url,
            headers=request_headers,
            body=body,
            purpose=purpose,
            trusted_origin=trusted_origin,
            allow_redirects=allow_redirects,
            retry_safe=retry_safe,
            maximum_response_bytes=self._limits.maximum_json_response_bytes,
            cancellation=cancellation,
        )
        content_type = str(response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
        if content_type not in {"application/json", "application/problem+json"}:
            raise JmapTransportError("jmap_response_content_type_invalid")
        try:
            decoded = _json_without_duplicate_keys(response.body)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise JmapTransportError("jmap_response_json_invalid") from exc
        if not isinstance(decoded, Mapping):
            raise JmapTransportError("jmap_response_object_required")
        return dict(decoded), response

    def request_bytes(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str] | None = None,
        purpose: str,
        trusted_origin: str,
        maximum_response_bytes: int | None = None,
        cancellation: JmapCancellationSignal | None = None,
    ) -> JmapHttpResponse:
        return self._request(
            method=method,
            url=url,
            headers=dict(headers or {}),
            body=None,
            purpose=purpose,
            trusted_origin=trusted_origin,
            allow_redirects=False,
            retry_safe=True,
            maximum_response_bytes=min(
                self._limits.maximum_blob_bytes,
                int(maximum_response_bytes or self._limits.maximum_blob_bytes),
            ),
            cancellation=cancellation,
        )

    def _request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        purpose: str,
        trusted_origin: str | None,
        allow_redirects: bool,
        retry_safe: bool,
        maximum_response_bytes: int,
        cancellation: JmapCancellationSignal | None,
    ) -> JmapHttpResponse:
        upper_method = str(method).upper()
        if upper_method not in {"GET", "POST"}:
            raise JmapTransportError("jmap_http_method_forbidden")
        current_url = str(url)
        current_headers = dict(headers)
        if trusted_origin is None:
            initial = self._endpoint_policy.validate_initial(current_url, purpose=purpose)
            root_origin = initial.origin
        else:
            initial = self._endpoint_policy.validate_related(
                current_url,
                trusted_origin=trusted_origin,
                purpose=purpose,
            )
            root_origin = trusted_origin
        del initial
        redirects = 0
        attempt = 0
        while True:
            if _cancelled(cancellation):
                raise JmapTransportError("jmap_request_cancelled")
            try:
                if trusted_origin is None and redirects == 0:
                    validated = self._endpoint_policy.validate_initial(current_url, purpose=purpose)
                else:
                    validated = self._endpoint_policy.validate_related(
                        current_url,
                        trusted_origin=root_origin,
                        purpose=purpose,
                    )
                response = self._adapter.send(
                    JmapHttpRequest(
                        method=upper_method,
                        url=current_url,
                        headers=current_headers,
                        body=body,
                        connect_timeout_seconds=self._limits.connect_timeout_seconds,
                        read_timeout_seconds=self._limits.read_timeout_seconds,
                        maximum_response_bytes=maximum_response_bytes,
                        endpoint=validated,
                        cancellation=cancellation,
                    )
                )
                if len(response.body) > maximum_response_bytes:
                    raise JmapTransportError("jmap_response_too_large")
            except JmapEndpointPolicyError as exc:
                raise JmapTransportError(exc.reason_code) from exc
            except JmapTransportError as exc:
                if retry_safe and exc.retryable and attempt < self._limits.maximum_safe_retries:
                    delay = min(0.25 * (2**attempt), self._limits.maximum_retry_after_seconds)
                    self._record("transport", "retry", exc.reason_code, True)
                    if cancellation is None:
                        self._sleep(delay)
                    elif _wait_cancelled(cancellation, delay):
                        raise JmapTransportError("jmap_request_cancelled") from exc
                    attempt += 1
                    continue
                self._record("transport", "failed", exc.reason_code, exc.retryable)
                raise
            status = response.status_code
            if status in self._REDIRECT_STATUSES:
                if not allow_redirects or upper_method != "GET":
                    raise JmapTransportError("jmap_redirect_forbidden")
                if redirects >= self._limits.maximum_redirects:
                    raise JmapTransportError("jmap_redirect_limit_exceeded")
                location = str(response.headers.get("location") or "")
                try:
                    target = self._endpoint_policy.validate_redirect(
                        location,
                        current_url=current_url,
                        trusted_origin=root_origin,
                    )
                except JmapEndpointPolicyError as exc:
                    raise JmapTransportError(exc.reason_code) from exc
                if _origin(current_url) != target.origin:
                    current_headers = {
                        key: value for key, value in current_headers.items() if key.lower() != "authorization"
                    }
                current_url = target.url
                redirects += 1
                continue
            if 200 <= status < 300:
                self._record("transport", "ok", "ok", False)
                return JmapHttpResponse(
                    status_code=response.status_code,
                    headers=response.headers,
                    body=response.body,
                    final_url=current_url,
                )
            if status == 401:
                raise JmapTransportError("jmap_authentication_failed")
            if status == 403:
                raise JmapTransportError("jmap_authorization_failed")
            if status in {429, 503}:
                reason = "jmap_rate_limited" if status == 429 else "jmap_service_unavailable"
                delay = self._retry_after(response.headers.get("retry-after"), attempt)
                if retry_safe and attempt < self._limits.maximum_safe_retries:
                    self._record("transport", "retry", reason, True)
                    if cancellation is None:
                        self._sleep(delay)
                    elif _wait_cancelled(cancellation, delay):
                        raise JmapTransportError("jmap_request_cancelled")
                    attempt += 1
                    continue
                raise JmapTransportError(
                    reason,
                    retryable=True,
                    retry_after_ms=int(delay * 1000),
                )
            if 400 <= status < 500:
                if status == 404 and purpose == "session":
                    raise JmapTransportError("jmap_discovery_not_found")
                raise JmapTransportError("jmap_http_client_error")
            raise JmapTransportError("jmap_http_server_error", retryable=retry_safe)

    def _retry_after(self, raw: str | None, attempt: int) -> float:
        value = str(raw or "").strip()
        delay: float
        try:
            delay = float(value)
        except ValueError:
            try:
                parsed = parsedate_to_datetime(value)
                delay = max(0.0, parsed.timestamp() - self._clock())
            except (TypeError, ValueError, OverflowError):
                delay = 0.25 * (2**attempt)
        return min(max(0.0, delay), self._limits.maximum_retry_after_seconds)

    def _record(self, phase: str, outcome: str, reason_code: str, retryable: bool) -> None:
        self._observer.record(
            MailOperationEvent(
                provider="jmap",
                phase=phase,
                outcome=outcome,
                reason_code=reason_code,
                retryable=retryable,
            )
        )


def _origin(value: str) -> str:
    parsed = urlsplit(value)
    host = str(parsed.hostname or "").lower()
    port = int(parsed.port or (443 if parsed.scheme.lower() == "https" else 80))
    display = f"[{host}]" if ":" in host else host
    return f"{parsed.scheme.lower()}://{display}:{port}"


def _connect_validated_endpoint(
    endpoint: ValidatedJmapEndpoint,
    *,
    connect_timeout_seconds: float,
    read_timeout_seconds: float,
    cancellation: JmapCancellationSignal | None,
) -> socket.socket:
    deadline = time.monotonic() + max(0.01, float(connect_timeout_seconds))
    last_error: OSError | None = None
    for address in endpoint.addresses:
        if _cancelled(cancellation):
            raise JmapTransportError("jmap_request_cancelled")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        parsed_address = ipaddress.ip_address(address)
        family = socket.AF_INET6 if parsed_address.version == 6 else socket.AF_INET
        raw_socket = socket.socket(family, socket.SOCK_STREAM)
        connected: socket.socket = raw_socket
        try:
            raw_socket.settimeout(remaining)
            target = (address, endpoint.port, 0, 0) if family == socket.AF_INET6 else (address, endpoint.port)
            raw_socket.connect(target)
            peer = str(raw_socket.getpeername()[0]).split("%", 1)[0]
            if ipaddress.ip_address(peer) != parsed_address:
                raise JmapTransportError("jmap_peer_address_mismatch")
            if endpoint.scheme == "https":
                context = ssl.create_default_context()
                connected = context.wrap_socket(raw_socket, server_hostname=endpoint.host)
                tls_peer = str(connected.getpeername()[0]).split("%", 1)[0]
                if ipaddress.ip_address(tls_peer) != parsed_address:
                    raise JmapTransportError("jmap_peer_address_mismatch")
            connected.settimeout(max(0.01, float(read_timeout_seconds)))
            return connected
        except ssl.SSLCertVerificationError:
            connected.close()
            raise
        except JmapTransportError:
            connected.close()
            raise
        except OSError as exc:
            last_error = exc
            connected.close()
    if isinstance(last_error, (socket.timeout, TimeoutError)) or time.monotonic() >= deadline:
        raise JmapTransportError("jmap_request_timeout", retryable=True) from last_error
    raise JmapTransportError("jmap_connection_failed", retryable=True) from last_error


def _host_header(endpoint: ValidatedJmapEndpoint) -> str:
    host = f"[{endpoint.host}]" if ":" in endpoint.host else endpoint.host
    default_port = 443 if endpoint.scheme == "https" else 80
    return host if endpoint.port == default_port else f"{host}:{endpoint.port}"


def _cancelled(signal: JmapCancellationSignal | None) -> bool:
    return bool(signal is not None and signal.is_cancelled())


def _wait_cancelled(signal: JmapCancellationSignal | None, delay: float) -> bool:
    return bool(signal is not None and signal.wait(max(0.0, float(delay))))


__all__ = [
    "JmapHttpAdapter",
    "JmapHttpRequest",
    "JmapHttpResponse",
    "JmapHttpTransport",
    "JmapTransportError",
    "RequestsJmapHttpAdapter",
]

"""Bounded, authenticated transport for an allowlisted Unsloth Studio endpoint."""

from __future__ import annotations

import http.client
import ipaddress
import json
import queue
import re
import socket
import ssl
import threading
import time
import zlib
from collections.abc import (
    Callable,
    Mapping,
    MutableMapping,
    Sequence,
)
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import unquote, urlsplit, urlunsplit

from ananta_contracts.unsloth_studio import (
    IncompatibleUnslothStudioContract,
    compose_studio_probe,
    validate_studio_token,
)
from agent.services.jmap_endpoint_policy import (
    JmapEndpointPolicy,
    JmapEndpointPolicyConfig,
    JmapEndpointPolicyError,
    ValidatedJmapEndpoint,
)
from agent.services.opaque_secret_reference_service import (
    OpaqueSecretReferenceError,
    OpaqueSecretReferenceService,
    opaque_secret_reference_service,
)

MAX_CONNECT_TIMEOUT_SECONDS = 2.0
MAX_TOTAL_TIMEOUT_SECONDS = 10.0
MAX_DECOMPRESSED_RESPONSE_BYTES = 1024 * 1024
MAX_IDEMPOTENT_RETRIES = 1

_IDEMPOTENT_METHODS = frozenset({"GET"})
_RETRYABLE_STATUSES = frozenset({429, 502, 503, 504})
_FORBIDDEN_REQUEST_HEADERS = frozenset(
    {
        "authorization",
        "connection",
        "content-length",
        "host",
        "proxy-authorization",
        "transfer-encoding",
        "accept-encoding",
    }
)
_IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")


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
    def send(self, request: UnslothStudioHttpRequest) -> UnslothStudioHttpResponse:
        ...


class DeadlineDnsResolver:
    """Bound DNS lookups without allowing timed-out calls to exhaust threads."""

    def __init__(
        self,
        *,
        lookup: Callable[[str, int], Sequence[str]] | None = None,
        clock: Callable[[], float] = time.monotonic,
        maximum_inflight: int = 1,
    ) -> None:
        if int(maximum_inflight) != 1:
            raise ValueError("unsloth_studio_dns_inflight_limit_invalid")
        self._lookup = lookup or _system_dns_lookup
        self._clock = clock
        self._slot = threading.BoundedSemaphore(value=1)

    def resolve(
        self,
        host: str,
        port: int,
        *,
        deadline: float,
    ) -> tuple[str, ...]:
        timeout = self._remaining(deadline)
        if not self._slot.acquire(timeout=timeout):
            raise JmapEndpointPolicyError("jmap_endpoint_dns_timeout")
        result_queue: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)

        def run_lookup() -> None:
            try:
                result_queue.put_nowait(
                    (True, tuple(str(value) for value in self._lookup(host, port)))
                )
            except Exception as exc:
                result_queue.put_nowait((False, exc))
            finally:
                self._slot.release()

        thread = threading.Thread(
            target=run_lookup,
            name="unsloth-studio-dns",
            daemon=True,
        )
        thread.start()
        try:
            ok, value = result_queue.get(timeout=self._remaining(deadline))
        except queue.Empty as exc:
            raise JmapEndpointPolicyError("jmap_endpoint_dns_timeout") from exc
        if not ok:
            if isinstance(value, JmapEndpointPolicyError):
                raise value
            raise JmapEndpointPolicyError("jmap_endpoint_dns_failed") from (
                value if isinstance(value, Exception) else None
            )
        return tuple(value) if isinstance(value, tuple) else ()

    def _remaining(self, deadline: float) -> float:
        value = float(deadline) - float(self._clock())
        if value <= 0:
            raise JmapEndpointPolicyError("jmap_endpoint_dns_timeout")
        return value


class UnslothStudioEndpointPolicy:
    """Studio-specific host/IP allowlists composed with the shared JMAP SSRF policy."""

    def __init__(
        self,
        *,
        config: UnslothStudioTransportConfig,
        resolver: Callable[[str, int], Sequence[str]] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        try:
            self._allowed_hosts = frozenset(_normalize_host(value) for value in config.allowed_hosts)
            self._allowed_networks = tuple(
                ipaddress.ip_network(str(value), strict=True) for value in config.allowed_ip_cidrs
            )
        except (UnicodeError, ValueError) as exc:
            raise ValueError("unsloth_studio_allowlist_invalid") from exc
        if "" in self._allowed_hosts:
            raise ValueError("unsloth_studio_allowlist_invalid")
        self._clock = clock
        self._default_dns_timeout_seconds = float(config.total_timeout_seconds)
        self._resolver = DeadlineDnsResolver(lookup=resolver, clock=clock)
        self._allow_plaintext_internal = bool(config.allow_plaintext_internal)
        self._shared_config = JmapEndpointPolicyConfig(
            external_network_enabled=bool(config.external_network_enabled),
            local_endpoints_enabled=bool(config.local_network_enabled),
            allowed_local_hosts=tuple(sorted(self._allowed_hosts)),
            allowed_local_cidrs=tuple(str(value) for value in self._allowed_networks),
        )

    def validate(
        self,
        url: str,
        *,
        deadline: float | None = None,
    ) -> ValidatedJmapEndpoint:
        effective_deadline = (
            float(deadline)
            if deadline is not None
            else self._clock() + self._default_dns_timeout_seconds
        )
        shared_policy = JmapEndpointPolicy(
            config=self._shared_config,
            resolver=lambda host, port: self._resolver.resolve(
                host,
                port,
                deadline=effective_deadline,
            ),
        )
        try:
            endpoint = shared_policy.validate_initial(url, purpose="api")
        except JmapEndpointPolicyError as exc:
            raise UnslothStudioTransportError(
                exc.reason_code.replace("jmap_", "unsloth_studio_", 1)
            ) from exc
        if endpoint.host not in self._allowed_hosts:
            raise UnslothStudioTransportError("unsloth_studio_host_not_allowlisted")
        addresses = tuple(ipaddress.ip_address(value) for value in endpoint.addresses)
        if not addresses or not all(
            any(address in network for network in self._allowed_networks) for address in addresses
        ):
            raise UnslothStudioTransportError("unsloth_studio_ip_not_allowlisted")
        if endpoint.scheme != "https" and not (
            endpoint.local and self._allow_plaintext_internal
        ):
            raise UnslothStudioTransportError("unsloth_studio_tls_required")
        return endpoint


class PinnedUnslothStudioHttpAdapter:
    """Connect only to validated IPs and verify TLS against the allowlisted hostname."""

    def __init__(
        self,
        *,
        ssl_context: ssl.SSLContext | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ssl_context = ssl_context or ssl.create_default_context()
        self._clock = clock

    def send(self, request: UnslothStudioHttpRequest) -> UnslothStudioHttpResponse:
        _assert_request_matches_endpoint(request)
        deadline = self._clock() + float(request.total_timeout_seconds)
        connect_deadline = min(
            deadline,
            self._clock() + float(request.connect_timeout_seconds),
        )
        connection: http.client.HTTPConnection | None = None
        try:
            pinned_socket = self._connect(request.endpoint, connect_deadline)
            _set_socket_timeout(pinned_socket, deadline, self._clock)
            connection = http.client.HTTPConnection(
                request.endpoint.host,
                request.endpoint.port,
                timeout=_remaining(deadline, self._clock),
            )
            connection.sock = pinned_socket
            parsed = urlsplit(request.url)
            target = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
            headers = {str(key): str(value) for key, value in request.headers.items()}
            headers.setdefault("Host", _host_header(request.endpoint))
            connection.request(request.method, target, body=request.body, headers=headers)
            _set_socket_timeout(pinned_socket, deadline, self._clock)
            response = connection.getresponse()
            response_headers = {
                str(key).lower(): str(value) for key, value in response.getheaders()
            }
            body = _read_bounded_response(
                response,
                headers=response_headers,
                maximum_bytes=request.maximum_decompressed_bytes,
                deadline=deadline,
                clock=self._clock,
                connection_socket=pinned_socket,
            )
            return UnslothStudioHttpResponse(
                status_code=int(response.status),
                headers=response_headers,
                body=body,
            )
        except UnslothStudioTransportError:
            raise
        except (socket.timeout, TimeoutError) as exc:
            raise UnslothStudioTransportError(
                "unsloth_studio_request_timeout",
                retryable=True,
            ) from exc
        except ssl.SSLCertVerificationError as exc:
            raise UnslothStudioTransportError(
                "unsloth_studio_tls_verification_failed"
            ) from exc
        except ssl.SSLError as exc:
            raise UnslothStudioTransportError("unsloth_studio_tls_failed") from exc
        except (OSError, http.client.HTTPException) as exc:
            raise UnslothStudioTransportError(
                "unsloth_studio_connection_failed",
                retryable=True,
            ) from exc
        finally:
            if connection is not None:
                connection.close()

    def _connect(
        self,
        endpoint: ValidatedJmapEndpoint,
        deadline: float,
    ) -> socket.socket:
        last_error: OSError | None = None
        for address in endpoint.addresses:
            try:
                raw_socket = socket.create_connection(
                    (address, endpoint.port),
                    timeout=_remaining(deadline, self._clock),
                )
                if endpoint.scheme == "https":
                    _set_socket_timeout(raw_socket, deadline, self._clock)
                    return self._ssl_context.wrap_socket(
                        raw_socket,
                        server_hostname=endpoint.host,
                    )
                return raw_socket
            except ssl.SSLCertVerificationError:
                raise
            except OSError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise UnslothStudioTransportError("unsloth_studio_ip_allowlist_empty")


class UnslothStudioTransport:
    def __init__(
        self,
        *,
        config: UnslothStudioTransportConfig,
        endpoint_policy: UnslothStudioEndpointPolicy | None = None,
        adapter: UnslothStudioHttpAdapter | None = None,
        secret_resolver: OpaqueSecretReferenceService = opaque_secret_reference_service,
        resolver: Callable[[str, int], Sequence[str]] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._config = config
        self._policy = endpoint_policy or UnslothStudioEndpointPolicy(
            config=config,
            resolver=resolver,
            clock=clock,
        )
        self._adapter = adapter or PinnedUnslothStudioHttpAdapter(clock=clock)
        self._secret_resolver = secret_resolver
        self._clock = clock
        self._sleep = sleep
        self._session_lock = threading.RLock()
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._base_endpoint = self._policy.validate(config.base_url)
        parsed = urlsplit(config.base_url)
        if parsed.query or parsed.fragment:
            raise ValueError("unsloth_studio_base_url_invalid")
        self._base_url = str(config.base_url).rstrip("/")

    def request_json(
        self,
        *,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        service_bearer_secret_ref: str | None = None,
        idempotency_key: str | None = None,
        response_headers: MutableMapping[str, str] | None = None,
    ) -> Mapping[str, Any]:
        upper_method = str(method or "").upper()
        if upper_method not in {"GET", "POST"}:
            raise UnslothStudioTransportError("unsloth_studio_http_method_forbidden")
        if upper_method == "GET" and payload is not None:
            raise UnslothStudioTransportError("unsloth_studio_get_body_forbidden")
        url = self._build_url(path)
        request_headers = self._normalize_headers(headers)
        request_headers.setdefault("Accept", "application/json")
        request_headers["Accept-Encoding"] = "gzip, identity"
        body: bytes | None = None
        if payload is not None:
            body = json.dumps(
                dict(payload),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            if len(body) > self._config.maximum_request_bytes:
                raise UnslothStudioTransportError("unsloth_studio_request_too_large")
            request_headers["Content-Type"] = "application/json"
        if idempotency_key is not None:
            if _IDEMPOTENCY_KEY_RE.fullmatch(str(idempotency_key)) is None:
                raise UnslothStudioTransportError(
                    "unsloth_studio_idempotency_key_invalid"
                )
            request_headers["Idempotency-Key"] = str(idempotency_key)

        deadline = self._clock() + float(self._config.total_timeout_seconds)
        if service_bearer_secret_ref is not None:
            if not (path == "/mcp" or path.startswith("/mcp/")):
                raise UnslothStudioTransportError(
                    "unsloth_studio_service_bearer_scope_invalid"
                )
            access_token = self._resolve_service_bearer(
                service_bearer_secret_ref
            )
            jwt_authentication = False
        else:
            access_token = self._authenticated_access_token(deadline)
            jwt_authentication = True
        authentication_retried = False
        attempt = 0
        while True:
            endpoint = self._policy.validate(url, deadline=deadline)
            if endpoint.origin != self._base_endpoint.origin:
                raise UnslothStudioTransportError(
                    "unsloth_studio_origin_changed"
                )
            attempt_headers = dict(request_headers)
            attempt_headers["Authorization"] = (
                f"Bearer {access_token}"
            )
            try:
                response = self._adapter.send(
                    UnslothStudioHttpRequest(
                        method=upper_method,
                        url=url,
                        headers=attempt_headers,
                        body=body,
                        endpoint=endpoint,
                        connect_timeout_seconds=min(
                            float(self._config.connect_timeout_seconds),
                            _remaining(deadline, self._clock),
                        ),
                        total_timeout_seconds=_remaining(deadline, self._clock),
                        maximum_decompressed_bytes=int(
                            self._config.maximum_response_bytes
                        ),
                    )
                )
            except UnslothStudioTransportError as exc:
                if self._may_retry(upper_method, attempt, exc.retryable):
                    self._sleep_before_retry(deadline)
                    attempt += 1
                    continue
                raise

            if 300 <= response.status_code < 400:
                raise UnslothStudioTransportError(
                    "unsloth_studio_redirect_forbidden"
                )
            if response.status_code in _RETRYABLE_STATUSES:
                if self._may_retry(upper_method, attempt, True):
                    self._sleep_before_retry(deadline)
                    attempt += 1
                    continue
                raise UnslothStudioTransportError(
                    "unsloth_studio_upstream_unavailable",
                    retryable=True,
                )
            if response.status_code == 401:
                if jwt_authentication and not authentication_retried:
                    access_token = self._refresh_or_login(
                        failed_access_token=access_token,
                        deadline=deadline,
                    )
                    authentication_retried = True
                    continue
                raise UnslothStudioTransportError(
                    "unsloth_studio_authentication_failed"
                )
            if response.status_code == 403:
                raise UnslothStudioTransportError(
                    "unsloth_studio_authorization_failed"
                )
            if not 200 <= response.status_code < 300:
                raise UnslothStudioTransportError(
                    "unsloth_studio_upstream_rejected"
                )
            if response_headers is not None:
                response_headers.clear()
                response_headers.update(
                    {
                        str(name).lower(): str(value)
                        for name, value in response.headers.items()
                    }
                )
            if not response.body:
                return {}
            content_type = (
                str(response.headers.get("content-type") or "")
                .split(";", 1)[0]
                .strip()
                .lower()
            )
            if content_type not in {
                "application/json",
                "application/problem+json",
                "text/event-stream",
            }:
                raise UnslothStudioTransportError(
                    "unsloth_studio_response_content_type_invalid"
                )
            try:
                decoded = (
                    _decode_sse_json(response.body)
                    if content_type == "text/event-stream"
                    else json.loads(
                        response.body.decode("utf-8"),
                        object_pairs_hook=(
                            _reject_duplicate_json_members
                        ),
                    )
                )
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                raise UnslothStudioTransportError(
                    "unsloth_studio_response_json_invalid"
                ) from exc
            if not isinstance(decoded, Mapping):
                raise UnslothStudioTransportError(
                    "unsloth_studio_response_object_required"
                )
            return dict(decoded)

    def probe(self) -> Mapping[str, Any]:
        """Validate authenticated health, version and JWT readiness."""

        try:
            auth_status = self.request_json(
                method="GET",
                path="/api/auth/status",
            )
            health = self.request_json(method="GET", path="/api/health")
            return compose_studio_probe(
                auth_status=auth_status,
                health=health,
                expected_studio_version=self._config.expected_studio_version,
            )
        except IncompatibleUnslothStudioContract as exc:
            raise UnslothStudioTransportError(
                "incompatible_upstream_contract"
            ) from exc
        except UnslothStudioTransportError as exc:
            if exc.reason_code.startswith(
                "unsloth_studio_response_"
            ):
                raise UnslothStudioTransportError(
                    "incompatible_upstream_contract"
                ) from exc
            raise

    def _build_url(self, path: str) -> str:
        raw = str(path or "").strip()
        parsed = urlsplit(raw)
        if (
            not raw.startswith("/")
            or raw.startswith("//")
            or parsed.scheme
            or parsed.netloc
            or parsed.query
            or parsed.fragment
        ):
            raise UnslothStudioTransportError("unsloth_studio_path_invalid")
        decoded_path = unquote(parsed.path)
        if "\\" in decoded_path or any(
            segment in {".", ".."} for segment in decoded_path.split("/")
        ):
            raise UnslothStudioTransportError("unsloth_studio_path_invalid")
        return f"{self._base_url}/{raw.lstrip('/')}"

    @staticmethod
    def _normalize_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for key, value in dict(headers or {}).items():
            name = str(key).strip()
            raw_value = str(value)
            if (
                not name
                or name.lower() in _FORBIDDEN_REQUEST_HEADERS
                or "\r" in name
                or "\n" in name
                or "\r" in raw_value
                or "\n" in raw_value
            ):
                raise UnslothStudioTransportError(
                    "unsloth_studio_request_header_forbidden"
                )
            normalized[name] = raw_value
        return normalized

    def _resolve_service_bearer(self, reference: str) -> str:
        reference = str(reference or "").strip()
        if not reference:
            raise UnslothStudioTransportError(
                "unsloth_studio_service_bearer_secret_ref_required"
            )
        try:
            value = self._secret_resolver.resolve(reference)
        except OpaqueSecretReferenceError as exc:
            raise UnslothStudioTransportError(
                "unsloth_studio_service_bearer_secret_unavailable"
            ) from exc
        if len(value) < 16 or len(value) > 4096 or "\r" in value or "\n" in value:
            raise UnslothStudioTransportError(
                "unsloth_studio_service_bearer_secret_invalid"
            )
        return value

    def _authenticated_access_token(self, deadline: float) -> str:
        with self._session_lock:
            if self._access_token is None:
                self._login_locked(deadline)
            assert self._access_token is not None
            return self._access_token

    def _refresh_or_login(
        self,
        *,
        failed_access_token: str,
        deadline: float,
    ) -> str:
        with self._session_lock:
            if (
                self._access_token is not None
                and self._access_token != failed_access_token
            ):
                return self._access_token
            if self._refresh_token is not None:
                try:
                    token = validate_studio_token(
                        self._auth_exchange(
                            path="/api/auth/refresh",
                            payload={"refresh_token": self._refresh_token},
                            deadline=deadline,
                        )
                    )
                    self._accept_token_locked(token)
                    assert self._access_token is not None
                    return self._access_token
                except IncompatibleUnslothStudioContract as exc:
                    self._access_token = None
                    self._refresh_token = None
                    raise UnslothStudioTransportError(
                        "incompatible_upstream_contract"
                    ) from exc
                except UnslothStudioTransportError as exc:
                    if exc.reason_code.startswith(
                        "unsloth_studio_response_"
                    ):
                        self._access_token = None
                        self._refresh_token = None
                        raise UnslothStudioTransportError(
                            "incompatible_upstream_contract"
                        ) from exc
                    if exc.reason_code != "unsloth_studio_authentication_failed":
                        raise
            self._login_locked(deadline)
            assert self._access_token is not None
            return self._access_token

    def _login_locked(self, deadline: float) -> None:
        try:
            password = self._secret_resolver.resolve(
                self._config.credential_secret_ref
            )
        except OpaqueSecretReferenceError as exc:
            raise UnslothStudioTransportError(
                "unsloth_studio_credentials_unavailable"
            ) from exc
        if (
            not 8 <= len(password) <= 1024
            or "\r" in password
            or "\n" in password
        ):
            raise UnslothStudioTransportError(
                "unsloth_studio_credentials_invalid"
            )
        try:
            token = validate_studio_token(
                self._auth_exchange(
                    path="/api/auth/login",
                    payload={"username": "unsloth", "password": password},
                    deadline=deadline,
                )
            )
        except IncompatibleUnslothStudioContract as exc:
            raise UnslothStudioTransportError(
                "incompatible_upstream_contract"
            ) from exc
        except UnslothStudioTransportError as exc:
            if exc.reason_code.startswith(
                "unsloth_studio_response_"
            ):
                raise UnslothStudioTransportError(
                    "incompatible_upstream_contract"
                ) from exc
            raise
        self._accept_token_locked(token)

    def _accept_token_locked(self, token: Mapping[str, Any]) -> None:
        if token.get("must_change_password") is not False:
            self._access_token = None
            self._refresh_token = None
            raise UnslothStudioTransportError(
                "unsloth_studio_password_change_required"
            )
        self._access_token = str(token["access_token"])
        self._refresh_token = str(token["refresh_token"])

    def _auth_exchange(
        self,
        *,
        path: str,
        payload: Mapping[str, Any],
        deadline: float,
    ) -> Mapping[str, Any]:
        url = self._build_url(path)
        endpoint = self._policy.validate(url, deadline=deadline)
        if endpoint.origin != self._base_endpoint.origin:
            raise UnslothStudioTransportError("unsloth_studio_origin_changed")
        body = json.dumps(
            dict(payload),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if len(body) > self._config.maximum_request_bytes:
            raise UnslothStudioTransportError("unsloth_studio_request_too_large")
        response = self._adapter.send(
            UnslothStudioHttpRequest(
                method="POST",
                url=url,
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip, identity",
                    "Content-Type": "application/json",
                },
                body=body,
                endpoint=endpoint,
                connect_timeout_seconds=min(
                    float(self._config.connect_timeout_seconds),
                    _remaining(deadline, self._clock),
                ),
                total_timeout_seconds=_remaining(deadline, self._clock),
                maximum_decompressed_bytes=int(
                    self._config.maximum_response_bytes
                ),
            )
        )
        if 300 <= response.status_code < 400:
            raise UnslothStudioTransportError(
                "unsloth_studio_redirect_forbidden"
            )
        if response.status_code == 401:
            raise UnslothStudioTransportError(
                "unsloth_studio_authentication_failed"
            )
        if response.status_code == 429 or response.status_code in _RETRYABLE_STATUSES:
            raise UnslothStudioTransportError(
                "unsloth_studio_upstream_unavailable",
                retryable=True,
            )
        if not 200 <= response.status_code < 300:
            raise UnslothStudioTransportError(
                "unsloth_studio_upstream_rejected"
            )
        content_type = (
            str(response.headers.get("content-type") or "")
            .split(";", 1)[0]
            .strip()
            .lower()
        )
        if content_type != "application/json":
            raise UnslothStudioTransportError(
                "unsloth_studio_response_content_type_invalid"
            )
        try:
            decoded = json.loads(
                response.body.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_json_members,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise UnslothStudioTransportError(
                "unsloth_studio_response_json_invalid"
            ) from exc
        if not isinstance(decoded, Mapping):
            raise UnslothStudioTransportError(
                "unsloth_studio_response_object_required"
            )
        return dict(decoded)

    def _may_retry(self, method: str, attempt: int, retryable: bool) -> bool:
        return bool(
            retryable
            and method in _IDEMPOTENT_METHODS
            and attempt < int(self._config.maximum_idempotent_retries)
        )

    def _sleep_before_retry(self, deadline: float) -> None:
        delay = min(
            float(self._config.retry_backoff_seconds),
            _remaining(deadline, self._clock),
        )
        if delay > 0:
            self._sleep(delay)
        _remaining(deadline, self._clock)


def _normalize_host(value: str) -> str:
    raw = str(value or "").strip().lower()
    if not raw or raw.endswith("."):
        return ""
    return raw.encode("idna").decode("ascii")


def _system_dns_lookup(host: str, port: int) -> tuple[str, ...]:
    rows = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return tuple(
        sorted({str(row[4][0]).split("%", 1)[0] for row in rows})
    )


def _assert_request_matches_endpoint(request: UnslothStudioHttpRequest) -> None:
    parsed = urlsplit(request.url)
    try:
        host = _normalize_host(str(parsed.hostname or ""))
        port = int(parsed.port or (443 if parsed.scheme.lower() == "https" else 80))
    except (UnicodeError, ValueError) as exc:
        raise UnslothStudioTransportError(
            "unsloth_studio_validated_endpoint_mismatch"
        ) from exc
    endpoint = request.endpoint
    if (
        request.url != endpoint.url
        or host != endpoint.host
        or port != endpoint.port
        or parsed.scheme.lower() != endpoint.scheme
    ):
        raise UnslothStudioTransportError(
            "unsloth_studio_validated_endpoint_mismatch"
        )


def _host_header(endpoint: ValidatedJmapEndpoint) -> str:
    host = f"[{endpoint.host}]" if ":" in endpoint.host else endpoint.host
    default_port = 443 if endpoint.scheme == "https" else 80
    return host if endpoint.port == default_port else f"{host}:{endpoint.port}"


def _remaining(deadline: float, clock: Callable[[], float]) -> float:
    value = float(deadline) - float(clock())
    if value <= 0:
        raise UnslothStudioTransportError(
            "unsloth_studio_request_timeout",
            retryable=True,
        )
    return value


def _set_socket_timeout(
    connection_socket: socket.socket,
    deadline: float,
    clock: Callable[[], float],
) -> None:
    connection_socket.settimeout(_remaining(deadline, clock))


def _read_bounded_response(
    response: http.client.HTTPResponse,
    *,
    headers: Mapping[str, str],
    maximum_bytes: int,
    deadline: float,
    clock: Callable[[], float],
    connection_socket: socket.socket,
) -> bytes:
    encoding = str(headers.get("content-encoding") or "identity").strip().lower()
    if encoding not in {"", "identity", "gzip"}:
        raise UnslothStudioTransportError(
            "unsloth_studio_response_encoding_forbidden"
        )
    declared = str(headers.get("content-length") or "").strip()
    if encoding in {"", "identity"} and declared:
        try:
            if int(declared) > maximum_bytes:
                raise UnslothStudioTransportError(
                    "unsloth_studio_response_too_large"
                )
        except ValueError:
            pass
    output = bytearray()
    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding == "gzip" else None
    try:
        while True:
            _set_socket_timeout(connection_socket, deadline, clock)
            chunk = response.read(64 * 1024)
            if not chunk:
                break
            if decompressor is None:
                output.extend(chunk)
            else:
                remaining = maximum_bytes - len(output)
                expanded = decompressor.decompress(chunk, remaining + 1)
                output.extend(expanded)
                if decompressor.unconsumed_tail:
                    raise UnslothStudioTransportError(
                        "unsloth_studio_response_too_large"
                    )
            if len(output) > maximum_bytes:
                raise UnslothStudioTransportError(
                    "unsloth_studio_response_too_large"
                )
        if decompressor is not None:
            remaining = maximum_bytes - len(output)
            output.extend(decompressor.flush(remaining + 1))
            if not decompressor.eof:
                raise UnslothStudioTransportError(
                    "unsloth_studio_response_encoding_invalid"
                )
            if len(output) > maximum_bytes:
                raise UnslothStudioTransportError(
                    "unsloth_studio_response_too_large"
                )
    except zlib.error as exc:
        raise UnslothStudioTransportError(
            "unsloth_studio_response_encoding_invalid"
        ) from exc
    return bytes(output)


def _decode_sse_json(body: bytes) -> Mapping[str, Any]:
    text = body.decode("utf-8")
    messages: list[Mapping[str, Any]] = []
    for event in re.split(r"\r?\n\r?\n", text):
        data = "\n".join(
            line[5:].lstrip()
            for line in event.splitlines()
            if line.startswith("data:")
        )
        if not data or data == "[DONE]":
            continue
        decoded = json.loads(
            data,
            object_pairs_hook=_reject_duplicate_json_members,
        )
        if not isinstance(decoded, Mapping):
            raise ValueError("mcp_sse_message_invalid")
        messages.append(decoded)
    if len(messages) != 1:
        raise ValueError("mcp_sse_message_invalid")
    return dict(messages[0])


def _reject_duplicate_json_members(values: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in values:
        if key in result:
            raise ValueError("duplicate_json_member")
        result[key] = value
    return result


__all__ = [
    "MAX_CONNECT_TIMEOUT_SECONDS",
    "MAX_DECOMPRESSED_RESPONSE_BYTES",
    "MAX_IDEMPOTENT_RETRIES",
    "MAX_TOTAL_TIMEOUT_SECONDS",
    "DeadlineDnsResolver",
    "PinnedUnslothStudioHttpAdapter",
    "UnslothStudioEndpointPolicy",
    "UnslothStudioHttpAdapter",
    "UnslothStudioHttpRequest",
    "UnslothStudioHttpResponse",
    "UnslothStudioTransport",
    "UnslothStudioTransportConfig",
    "UnslothStudioTransportError",
]

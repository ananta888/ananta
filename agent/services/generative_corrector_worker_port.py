"""Secure Hub transport for the isolated generative transcript corrector."""

from __future__ import annotations

import ipaddress
import json
import re
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from ananta_contracts.voice_corrector_worker import (
    CONTRACT_VERSION,
    VoiceCorrectorWorkerRequest,
    VoiceCorrectorWorkerResponse,
)

_WORKER_PATH = "/internal/v1/voice-corrector"
_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
AddressResolver = Callable[[str, int], Sequence[str]]


class GenerativeCorrectorWorkerTransportError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise GenerativeCorrectorWorkerTransportError(
            "worker_redirect_forbidden",
            "generative corrector worker transport refused a redirect",
        )


class HttpGenerativeCorrectorWorkerPort:
    """Authenticated bounded HTTP port pinned to a private resolved address."""

    def __init__(
        self,
        *,
        endpoint: str,
        allowed_endpoints: tuple[str, ...],
        bearer_token: str,
        hub_origin: str,
        timeout_ms: int = 30_000,
        max_response_bytes: int = 256 * 1024,
        resolver: AddressResolver | None = None,
        opener: Any | None = None,
    ) -> None:
        normalized = _normalize_endpoint(endpoint)
        allowed = {_normalize_endpoint(item) for item in allowed_endpoints}
        if normalized not in allowed:
            raise ValueError("generative corrector worker endpoint is not exactly allowlisted")
        token = str(bearer_token or "").strip()
        if len(token) < 24:
            raise ValueError("generative corrector bearer token must contain at least 24 characters")
        if not 1 <= int(timeout_ms) <= 120_000:
            raise ValueError("generative corrector timeout is outside its bounds")
        if not 1_024 <= int(max_response_bytes) <= 2 * 1024 * 1024:
            raise ValueError("generative corrector response limit is outside its bounds")
        self._endpoint = normalized
        self._parsed = urlsplit(normalized)
        self._token = token
        self._hub_origin = _normalize_origin(hub_origin)
        self._timeout_ms = int(timeout_ms)
        self._max_response_bytes = int(max_response_bytes)
        self._resolver = resolver or _resolve_addresses
        self._opener = opener or urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirectHandler(),
        )

    def execute(self, request: VoiceCorrectorWorkerRequest) -> VoiceCorrectorWorkerResponse:
        if not isinstance(request, VoiceCorrectorWorkerRequest):
            raise TypeError("request must be a VoiceCorrectorWorkerRequest")
        remaining_ms = request.deadline_epoch_ms - time.time_ns() // 1_000_000
        if remaining_ms <= 0:
            raise GenerativeCorrectorWorkerTransportError(
                "timeout",
                "corrector worker deadline expired before dispatch",
            )
        address = self._private_pinned_address()
        netloc = f"[{address}]:{self._parsed.port}" if ":" in address else f"{address}:{self._parsed.port}"
        pinned_endpoint = urlunsplit(("http", netloc, self._parsed.path, "", ""))
        body = json.dumps(request.to_dict(), separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if len(body) > 1024 * 1024:
            raise GenerativeCorrectorWorkerTransportError(
                "request_too_large",
                "corrector request exceeds its byte limit",
            )
        http_request = urllib.request.Request(
            pinned_endpoint,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Host": self._parsed.netloc,
                "Origin": self._hub_origin,
            },
        )
        timeout_seconds = min(self._timeout_ms, remaining_ms) / 1000.0
        try:
            response = self._opener.open(http_request, timeout=timeout_seconds)
            payload = self._read_response(response)
        except urllib.error.HTTPError as exc:
            payload = self._read_response(exc)
        except GenerativeCorrectorWorkerTransportError:
            raise
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            raise GenerativeCorrectorWorkerTransportError(
                "generative_corrector_worker_unavailable",
                "generative corrector worker is unavailable",
            ) from exc
        worker_response = VoiceCorrectorWorkerResponse.from_dict(payload)
        worker_response.validate_for(request)
        return worker_response

    def health(self, *, timeout_ms: int = 500) -> Mapping[str, object]:
        """Read a bounded readiness document from the same pinned worker origin."""

        bounded_timeout_ms = max(1, min(int(timeout_ms), 5_000))
        address = self._private_pinned_address()
        netloc = f"[{address}]:{self._parsed.port}" if ":" in address else f"{address}:{self._parsed.port}"
        endpoint = urlunsplit(("http", netloc, "/health", "", ""))
        health_request = urllib.request.Request(
            endpoint,
            method="GET",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
                "Host": self._parsed.netloc,
                "Origin": self._hub_origin,
            },
        )
        try:
            response = self._opener.open(health_request, timeout=bounded_timeout_ms / 1000.0)
            payload = self._read_response(response, max_bytes=min(self._max_response_bytes, 64 * 1024))
        except GenerativeCorrectorWorkerTransportError:
            raise
        except (TimeoutError, urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
            raise GenerativeCorrectorWorkerTransportError(
                "generative_corrector_worker_unavailable",
                "generative corrector worker health is unavailable",
            ) from exc
        required = {
            "service",
            "status",
            "contract_version",
            "auth_configured",
            "origin_allowlist_configured",
            "engine_configured",
            "model_ids",
        }
        if (
            set(payload) != required
            or payload.get("service") != "generative-corrector-worker"
            or payload.get("contract_version") != CONTRACT_VERSION
            or payload.get("status") not in {"ready", "degraded"}
            or not isinstance(payload.get("model_ids"), list)
            or not all(
                isinstance(payload.get(field), bool)
                for field in (
                    "auth_configured",
                    "origin_allowlist_configured",
                    "engine_configured",
                )
            )
        ):
            raise GenerativeCorrectorWorkerTransportError(
                "invalid_worker_response",
                "generative corrector worker health response is invalid",
            )
        model_ids_value = payload.get("model_ids")
        assert isinstance(model_ids_value, list)
        model_ids = model_ids_value
        if (
            len(model_ids) > 64
            or len(set(model_ids)) != len(model_ids)
            or any(not isinstance(item, str) or not _MODEL_ID_RE.fullmatch(item) for item in model_ids)
            or (
                payload.get("status") == "ready"
                and not all(
                    payload.get(field) is True
                    for field in (
                        "auth_configured",
                        "origin_allowlist_configured",
                        "engine_configured",
                    )
                )
            )
        ):
            raise GenerativeCorrectorWorkerTransportError(
                "invalid_worker_response",
                "generative corrector worker health model metadata is invalid",
            )
        return payload

    def _private_pinned_address(self) -> str:
        hostname = str(self._parsed.hostname or "")
        port = int(self._parsed.port or 0)
        try:
            addresses = tuple(dict.fromkeys(str(value) for value in self._resolver(hostname, port)))
        except OSError as exc:
            raise GenerativeCorrectorWorkerTransportError(
                "generative_corrector_worker_unavailable",
                "generative corrector worker name resolution failed",
            ) from exc
        if not addresses:
            raise GenerativeCorrectorWorkerTransportError(
                "generative_corrector_worker_unavailable",
                "generative corrector worker resolved to no address",
            )
        parsed_addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
        for value in addresses:
            try:
                address = ipaddress.ip_address(value)
            except ValueError as exc:
                raise GenerativeCorrectorWorkerTransportError(
                    "worker_address_forbidden",
                    "generative corrector worker resolved to an invalid address",
                ) from exc
            if (
                not address.is_private
                or address.is_loopback
                or address.is_link_local
                or address.is_multicast
                or address.is_unspecified
            ):
                raise GenerativeCorrectorWorkerTransportError(
                    "worker_address_forbidden",
                    "generative corrector worker must resolve only to a private container address",
                )
            parsed_addresses.append(address)
        return str(min(parsed_addresses, key=lambda item: (item.version, int(item))))

    def _read_response(
        self,
        response: Any,
        *,
        max_bytes: int | None = None,
    ) -> Mapping[str, object]:
        content_type = str(response.headers.get("Content-Type") or "")
        media_type = content_type.partition(";")[0].strip().lower()
        if media_type != "application/json":
            raise GenerativeCorrectorWorkerTransportError(
                "invalid_worker_response",
                "generative corrector worker response must be JSON",
            )
        response_limit = self._max_response_bytes if max_bytes is None else int(max_bytes)
        raw = response.read(response_limit + 1)
        if len(raw) > response_limit:
            raise GenerativeCorrectorWorkerTransportError(
                "worker_response_too_large",
                "generative corrector worker response exceeds its byte limit",
            )
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise GenerativeCorrectorWorkerTransportError(
                "invalid_worker_response",
                "generative corrector worker returned invalid JSON",
            ) from exc
        if not isinstance(payload, Mapping):
            raise GenerativeCorrectorWorkerTransportError(
                "invalid_worker_response",
                "generative corrector worker response must be an object",
            )
        return payload


def _normalize_endpoint(value: str) -> str:
    parsed = urlsplit(str(value or "").strip())
    if (
        parsed.scheme != "http"
        or not parsed.hostname
        or parsed.port is None
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path != _WORKER_PATH
    ):
        raise ValueError("generative corrector endpoint must be the explicit internal HTTP endpoint")
    hostname = parsed.hostname.casefold()
    host = f"[{hostname}]" if ":" in hostname else hostname
    return urlunsplit(("http", f"{host}:{parsed.port}", _WORKER_PATH, "", ""))


def _normalize_origin(value: str) -> str:
    parsed = urlsplit(str(value or "").strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.port is None
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Hub origin must be an explicit HTTP(S) origin")
    hostname = parsed.hostname.casefold()
    host = f"[{hostname}]" if ":" in hostname else hostname
    return urlunsplit((parsed.scheme, f"{host}:{parsed.port}", "", "", ""))


def _resolve_addresses(hostname: str, port: int) -> tuple[str, ...]:
    try:
        return (str(ipaddress.ip_address(hostname)),)
    except ValueError:
        return tuple(
            dict.fromkeys(
                str(item[4][0])
                for item in socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
            )
        )

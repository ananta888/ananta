"""Secure Hub transport for the isolated generative-judge worker."""

from __future__ import annotations

import ipaddress
import json
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from ananta_contracts.generative_judge_worker import (
    GenerativeJudgeWorkerRequest,
    GenerativeJudgeWorkerResponse,
)

_WORKER_PATH = "/internal/v1/generative-judge"
AddressResolver = Callable[[str, int], Sequence[str]]


class GenerativeJudgeWorkerTransportError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise GenerativeJudgeWorkerTransportError(
            "worker_redirect_forbidden",
            "generative judge worker transport refused a redirect",
        )


class HttpGenerativeJudgeWorkerPort:
    """Authenticated bounded HTTP port pinned to a private resolved address."""

    def __init__(
        self,
        *,
        endpoint: str,
        allowed_endpoints: tuple[str, ...],
        bearer_token: str,
        hub_origin: str,
        timeout_ms: int = 2_000,
        max_response_bytes: int = 64 * 1024,
        resolver: AddressResolver | None = None,
        opener: Any | None = None,
    ) -> None:
        normalized = _normalize_endpoint(endpoint)
        allowed = {_normalize_endpoint(item) for item in allowed_endpoints}
        if normalized not in allowed:
            raise ValueError("generative judge worker endpoint is not exactly allowlisted")
        token = str(bearer_token or "").strip()
        if len(token) < 24:
            raise ValueError("generative judge worker bearer token must contain at least 24 characters")
        if not 1 <= int(timeout_ms) <= 60_000:
            raise ValueError("generative judge worker timeout is outside its bounds")
        if not 1024 <= int(max_response_bytes) <= 1024 * 1024:
            raise ValueError("generative judge response limit is outside its bounds")
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

    def execute(self, request: GenerativeJudgeWorkerRequest) -> GenerativeJudgeWorkerResponse:
        if not isinstance(request, GenerativeJudgeWorkerRequest):
            raise TypeError("request must be a GenerativeJudgeWorkerRequest")
        now_ms = time.time_ns() // 1_000_000
        remaining_ms = request.deadline_epoch_ms - now_ms
        if remaining_ms <= 0:
            raise GenerativeJudgeWorkerTransportError("timeout", "judge worker deadline expired before dispatch")
        address = self._private_pinned_address()
        netloc = f"[{address}]:{self._parsed.port}" if ":" in address else f"{address}:{self._parsed.port}"
        pinned_endpoint = urlunsplit(("http", netloc, self._parsed.path, "", ""))
        body = json.dumps(request.to_dict(), separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if len(body) > 1024 * 1024:
            raise GenerativeJudgeWorkerTransportError("request_too_large", "judge request exceeds its byte limit")
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
        except GenerativeJudgeWorkerTransportError:
            raise
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            raise GenerativeJudgeWorkerTransportError(
                "generative_judge_worker_unavailable",
                "generative judge worker is unavailable",
            ) from exc
        worker_response = GenerativeJudgeWorkerResponse.from_dict(payload)
        worker_response.validate_for(request)
        return worker_response

    def _private_pinned_address(self) -> str:
        hostname = str(self._parsed.hostname or "")
        port = int(self._parsed.port or 0)
        try:
            addresses = tuple(dict.fromkeys(str(value) for value in self._resolver(hostname, port)))
        except OSError as exc:
            raise GenerativeJudgeWorkerTransportError(
                "generative_judge_worker_unavailable",
                "generative judge worker name resolution failed",
            ) from exc
        if not addresses:
            raise GenerativeJudgeWorkerTransportError(
                "generative_judge_worker_unavailable",
                "generative judge worker resolved to no address",
            )
        parsed_addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
        for value in addresses:
            try:
                address = ipaddress.ip_address(value)
            except ValueError as exc:
                raise GenerativeJudgeWorkerTransportError(
                    "worker_address_forbidden",
                    "generative judge worker resolved to an invalid address",
                ) from exc
            if (
                not address.is_private
                or address.is_loopback
                or address.is_link_local
                or address.is_multicast
                or address.is_unspecified
            ):
                raise GenerativeJudgeWorkerTransportError(
                    "worker_address_forbidden",
                    "generative judge worker must resolve only to a private container address",
                )
            parsed_addresses.append(address)
        return str(min(parsed_addresses, key=lambda item: (item.version, int(item))))

    def _read_response(self, response: Any) -> Mapping[str, object]:
        content_type = str(response.headers.get("Content-Type") or "").lower()
        if "application/json" not in content_type:
            raise GenerativeJudgeWorkerTransportError(
                "invalid_worker_response",
                "generative judge worker response must be JSON",
            )
        raw = response.read(self._max_response_bytes + 1)
        if len(raw) > self._max_response_bytes:
            raise GenerativeJudgeWorkerTransportError(
                "worker_response_too_large",
                "generative judge worker response exceeds its byte limit",
            )
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise GenerativeJudgeWorkerTransportError(
                "invalid_worker_response",
                "generative judge worker returned invalid JSON",
            ) from exc
        if not isinstance(payload, Mapping):
            raise GenerativeJudgeWorkerTransportError(
                "invalid_worker_response",
                "generative judge worker response must be an object",
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
        raise ValueError("generative judge worker endpoint must be the explicit internal HTTP endpoint")
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

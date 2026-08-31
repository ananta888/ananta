"""Bounded HTTP transport for explicitly admitted local model runtimes."""

from __future__ import annotations

import ipaddress
import json
import socket
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests


class LocalRuntimeTransportError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LocalRuntimeEndpointPolicy:
    allowed_origins: frozenset[str]
    allow_loopback: bool = True
    allow_private: bool = False
    maximum_response_bytes: int = 2 * 1024 * 1024

    def admit(self, url: str) -> str:
        parsed = urlsplit(str(url or ""))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise LocalRuntimeTransportError("local_runtime_endpoint_invalid")
        if parsed.query or parsed.fragment:
            raise LocalRuntimeTransportError("local_runtime_endpoint_invalid")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        origin = f"{parsed.scheme}://{parsed.hostname.lower()}:{port}"
        if origin not in self.allowed_origins:
            raise LocalRuntimeTransportError("local_runtime_origin_denied")
        try:
            addresses = {
                ipaddress.ip_address(item[4][0])
                for item in socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
            }
        except (OSError, ValueError) as exc:
            raise LocalRuntimeTransportError("local_runtime_dns_unavailable") from exc
        if not addresses:
            raise LocalRuntimeTransportError("local_runtime_dns_unavailable")
        for address in addresses:
            if address.is_link_local or address.is_multicast or address.is_unspecified:
                raise LocalRuntimeTransportError("local_runtime_address_denied")
            if address.is_loopback and not self.allow_loopback:
                raise LocalRuntimeTransportError("local_runtime_loopback_denied")
            if address.is_private and not address.is_loopback and not self.allow_private:
                raise LocalRuntimeTransportError("local_runtime_private_address_denied")
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", "", ""))


class LocalRuntimeHttpClient:
    def __init__(self, policy: LocalRuntimeEndpointPolicy, *, session: requests.Session | None = None) -> None:
        self._policy = policy
        self._session = session or requests.Session()
        self._session.trust_env = False

    def request_json(
        self,
        method: str,
        base_url: str,
        path: str,
        *,
        timeout_seconds: float,
        payload: Mapping[str, Any] | None = None,
        authorization: str | None = None,
    ) -> Mapping[str, Any]:
        base = self._policy.admit(base_url.rstrip("/") + "/")
        url = self._policy.admit(urljoin(base, path.lstrip("/")))
        headers = {"Accept": "application/json"}
        if authorization:
            headers["Authorization"] = authorization
        try:
            response = self._session.request(
                method.upper(),
                url,
                headers=headers,
                json=dict(payload) if payload is not None else None,
                timeout=(min(3.0, timeout_seconds), timeout_seconds),
                allow_redirects=False,
                stream=True,
            )
            if 300 <= response.status_code < 400:
                raise LocalRuntimeTransportError("local_runtime_redirect_forbidden")
            response.raise_for_status()
            declared = response.headers.get("Content-Length")
            if declared is not None and int(declared) > self._policy.maximum_response_bytes:
                raise LocalRuntimeTransportError("local_runtime_response_too_large")
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                size += len(chunk)
                if size > self._policy.maximum_response_bytes:
                    raise LocalRuntimeTransportError("local_runtime_response_too_large")
                chunks.append(chunk)
            decoded = json.loads(b"".join(chunks))
        except LocalRuntimeTransportError:
            raise
        except (requests.RequestException, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LocalRuntimeTransportError("local_runtime_request_failed") from exc
        if not isinstance(decoded, Mapping):
            raise LocalRuntimeTransportError("local_runtime_response_invalid")
        return decoded


__all__ = ["LocalRuntimeEndpointPolicy", "LocalRuntimeHttpClient", "LocalRuntimeTransportError"]

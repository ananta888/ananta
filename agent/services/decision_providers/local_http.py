"""Local endpoint and redirect policy shared by optional inference adapters."""

from __future__ import annotations

import ipaddress
import urllib.request
from collections.abc import Sequence
from urllib.parse import urlsplit

from agent.services.decision_providers.types import DecisionProviderError


def validate_local_endpoint(url: str, allowed_hosts: Sequence[str], *, require_port: bool = True) -> None:
    parsed = urlsplit(url)
    try:
        port = parsed.port
    except ValueError:
        raise DecisionProviderError("base_url_invalid") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or (require_port and not port)
        or parsed.hostname not in allowed_hosts
    ):
        raise DecisionProviderError("endpoint_not_allowed")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return  # explicit administrative DNS allowlist, never request/model input
    if not (address.is_loopback or address.is_private) or address.is_unspecified or address.is_multicast:
        raise DecisionProviderError("endpoint_not_local")


def default_local_hosts(url: str) -> tuple[str, ...]:
    host = urlsplit(url).hostname or ""
    if host == "localhost" or "." not in host or ":" in host:
        return (host,)
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return ()
    return (host,)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise DecisionProviderError("redirect_not_allowed")

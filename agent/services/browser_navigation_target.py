"""Strict HTTP target admission; no DNS resolution or browser egress claim."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_NUMERIC_LABEL = re.compile(r"(?:[0-9]+|0x[0-9a-f]*)\Z")


@dataclass(frozen=True)
class BrowserNavigationTarget:
    hostname: str
    address: IPAddress | None

    @classmethod
    def parse(cls, url: str) -> BrowserNavigationTarget:
        if not isinstance(url, str) or not 1 <= len(url) <= 8192:
            raise ValueError("browser_policy_invalid_url")
        # urllib strips some controls; browsers also reinterpret backslashes.
        if any(ord(char) <= 32 or ord(char) == 127 or char == "\\" for char in url):
            raise ValueError("browser_policy_invalid_url")
        try:
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"}:
                raise ValueError
            if not parsed.netloc or "@" in parsed.netloc or "%" in parsed.netloc:
                raise ValueError
            if parsed.port is not None and not 1 <= parsed.port <= 65535:
                raise ValueError
            hostname = canonical_host(parsed.hostname or "")
            address = _address(hostname)
            # Brackets must surround IPv6 only, with no ignored authority tail.
            hostpart = parsed.netloc.rsplit(":", 1)[0] if parsed.port is not None else parsed.netloc
            if parsed.netloc.endswith(":"):
                raise ValueError
            if isinstance(address, ipaddress.IPv6Address):
                if hostpart.lower() != f"[{parsed.hostname}]".lower():
                    raise ValueError
            elif "[" in parsed.netloc or "]" in parsed.netloc:
                raise ValueError
        except (ValueError, UnicodeError):
            raise ValueError("browser_policy_invalid_url") from None
        return cls(hostname, address)


def _address(hostname: str) -> IPAddress | None:
    try:
        return ipaddress.ip_address(hostname)
    except ValueError:
        return None


def canonical_host(value: str) -> str:
    """ASCII DNS/standard IP only; never guess legacy browser numeric hosts."""
    if not isinstance(value, str) or not value.isascii():
        raise ValueError("browser_policy_invalid_host")
    host = value.lower().removesuffix(".")
    if not host or len(host) > 253 or "%" in host:
        raise ValueError("browser_policy_invalid_host")
    address = _address(host)
    if address is not None:
        return str(address)
    labels = host.split(".")
    if any(not _LABEL.fullmatch(label) for label in labels) or _NUMERIC_LABEL.fullmatch(labels[-1]):
        # WHATWG may reinterpret shortened, octal, hex or integer IPv4 input.
        # Require the normal dotted-decimal spelling instead of reimplementing it.
        raise ValueError("browser_policy_invalid_host")
    return host


def matches_host(hostname: str, pattern: str, *, subdomains: bool = True) -> bool:
    try:
        base = canonical_host(pattern.removeprefix("*.") if subdomains else pattern)
    except (AttributeError, ValueError):
        return False
    if hostname == base:
        return True
    return subdomains and _address(base) is None and hostname.endswith(f".{base}")


def restricted_address(address: IPAddress) -> bool:
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return restricted_address(address.ipv4_mapped)
        # Translation/tunnel scopes are not admitted as public browser literals.
        if address.sixtofour is not None or address.teredo is not None:
            return True
        if address in ipaddress.ip_network("64:ff9b::/96"):
            return True
    return not address.is_global or address.is_multicast or address.is_reserved

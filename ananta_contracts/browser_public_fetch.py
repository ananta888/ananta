"""Closed, unauthenticated public-document fetch projection; not an authority grant."""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from ananta_contracts.browser_navigation_target import BrowserNavigationTarget, restricted_address

MAX_DOCUMENT_BYTES = 524288
MAX_FETCH_WIRE_BYTES = 710000
_ESCAPE = re.compile(r"%(?![0-9a-fA-F]{2})")
_SENSITIVE = re.compile(
    r"(?:^|[/&?=_.-])(?:token|password|passwd|secret|credential|authorization|"
    r"session|login|logout|oauth|signin|signout)(?:$|[/&?=_.-])",
    re.I,
)


@dataclass(frozen=True)
class PublicFetchTarget:
    hostname: str
    port: int
    scheme: str
    origin: str
    path: str

    @classmethod
    def parse(cls, url: str) -> PublicFetchTarget:
        target = BrowserNavigationTarget.parse(url)
        parsed = urlsplit(url)
        if (
            len(url) > 2048
            or not url.isascii()
            or "#" in url
            or _ESCAPE.search(url)
            or (target.address is not None and restricted_address(target.address))
            or parsed.port not in {None, 80 if parsed.scheme == "http" else 443}
        ):
            raise ValueError("browser_public_target_denied")
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        decoded = unquote(path, errors="strict")
        if (
            any(ord(char) < 32 or ord(char) == 127 or char == "\\" for char in decoded)
            or "%" in decoded  # No recursively encoded authority/control ambiguity.
            or _SENSITIVE.search(decoded)
        ):
            raise ValueError("browser_public_target_denied")
        host = f"[{target.hostname}]" if ":" in target.hostname else target.hostname
        return cls(
            target.hostname, 80 if parsed.scheme == "http" else 443, parsed.scheme, f"{parsed.scheme}://{host}", path
        )


def validate_fetch_request(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {"schema", "url", "allowed_origins"}:
        raise ValueError("browser_public_request_invalid")
    if value["schema"] != "ananta.browser-public-fetch.v1":
        raise ValueError("browser_public_request_invalid")
    origins = value["allowed_origins"]
    if not isinstance(origins, list) or not 1 <= len(origins) <= 8:
        raise ValueError("browser_public_request_invalid")
    for origin in origins:
        if PublicFetchTarget.parse(origin).origin != origin:
            raise ValueError("browser_public_origin_invalid")
    if len(set(origins)) != len(origins) or PublicFetchTarget.parse(value["url"]).origin not in origins:
        raise ValueError("browser_public_origin_denied")
    return {"schema": value["schema"], "url": value["url"], "allowed_origins": list(origins)}


def document_result(content: bytes) -> dict:
    if not isinstance(content, bytes) or not 0 < len(content) <= MAX_DOCUMENT_BYTES:
        raise ValueError("browser_public_document_invalid")
    content.decode("utf-8", errors="strict")
    return {
        "schema": "ananta.browser-public-document.v1",
        "content_base64": base64.b64encode(content).decode("ascii"),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def decode_document(value: object) -> str:
    if not isinstance(value, dict) or set(value) != {"schema", "content_base64", "sha256"}:
        raise ValueError("browser_public_document_invalid")
    encoded = value["content_base64"]
    if not isinstance(encoded, str) or not 0 < len(encoded) <= MAX_FETCH_WIRE_BYTES:
        raise ValueError("browser_public_document_invalid")
    try:
        content = base64.b64decode(encoded, validate=True)
        if document_result(content) != value:
            raise ValueError()
        return content.decode("utf-8")
    except (ValueError, UnicodeError, binascii.Error):
        raise ValueError("browser_public_document_invalid") from None

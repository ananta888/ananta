"""Safe display projections for Hub-owned Agent endpoints."""

from __future__ import annotations

import hashlib
from urllib.parse import urlsplit, urlunsplit


def safe_agent_endpoint_for_display(value: object) -> str:
    """Return a credential-free endpoint or an opaque stable reference.

    Agent URLs are legacy primary keys, so read models cannot simply drop the
    field.  This projection preserves already-safe identifiers and strips URL
    components that may carry credentials from historical rows.
    """

    raw = str(value or "").strip()
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError:
        return _opaque_agent_ref(raw)
    if (
        not raw
        or parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or any(character.isspace() for character in raw)
    ):
        return _opaque_agent_ref(raw)

    if parsed.username is None and parsed.password is None and not parsed.query and not parsed.fragment:
        return raw

    hostname = parsed.hostname
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = f"{hostname}:{port}" if port is not None else hostname
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def _opaque_agent_ref(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
    return f"agent-ref:{digest}"


__all__ = ["safe_agent_endpoint_for_display"]

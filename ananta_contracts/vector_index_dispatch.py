"""Shared value-object validation for Hub-to-Worker Vector dispatches."""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

_DNS_LABEL = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)


def canonicalize_vector_index_worker_audience(
    value: object,
) -> str:
    """Return one canonical HTTP(S) origin without URL side channels."""

    candidate = str(value or "").strip()
    if (
        not candidate
        or len(candidate.encode("utf-8")) > 512
        or any(
            character.isspace()
            or ord(character) < 32
            or ord(character) == 127
            for character in candidate
        )
    ):
        raise ValueError("vector_index_task_audience_invalid")
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(
            "vector_index_task_audience_invalid"
        ) from exc
    scheme = str(parsed.scheme or "").lower()
    if (
        scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or parsed.netloc.endswith(":")
        or (port is not None and port < 1)
    ):
        raise ValueError("vector_index_task_audience_invalid")
    raw_hostname = str(parsed.hostname or "").rstrip(".").lower()
    if not raw_hostname or "%" in raw_hostname:
        raise ValueError("vector_index_task_audience_invalid")
    try:
        address = ipaddress.ip_address(raw_hostname)
    except ValueError:
        try:
            hostname = raw_hostname.encode("idna").decode(
                "ascii"
            )
        except UnicodeError as exc:
            raise ValueError(
                "vector_index_task_audience_invalid"
            ) from exc
        if (
            len(hostname) > 253
            or any(
                _DNS_LABEL.fullmatch(label) is None
                for label in hostname.split(".")
            )
        ):
            raise ValueError(
                "vector_index_task_audience_invalid"
            )
        origin_host = hostname
    else:
        origin_host = (
            f"[{address.compressed}]"
            if address.version == 6
            else address.compressed
        )
    default_port = 80 if scheme == "http" else 443
    port_suffix = (
        ""
        if port is None or port == default_port
        else f":{port}"
    )
    return f"{scheme}://{origin_host}{port_suffix}"


__all__ = ["canonicalize_vector_index_worker_audience"]

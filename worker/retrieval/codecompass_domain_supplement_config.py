"""Worker-local disk policy for complete CodeCompass domain supplements."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Final

CODECOMPASS_DOMAIN_SUPPLEMENT_SOURCE_BYTES_ENV: Final = (
    "ANANTA_CODECOMPASS_DOMAIN_SUPPLEMENT_SOURCE_MAX_BYTES"
)
MIN_CODECOMPASS_DOMAIN_SUPPLEMENT_SOURCE_BYTES: Final = 64 * 1024
DEFAULT_CODECOMPASS_DOMAIN_SUPPLEMENT_SOURCE_BYTES: Final = (
    2 * 1024 * 1024 * 1024
)
MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_SOURCE_BYTES: Final = (
    4 * 1024 * 1024 * 1024
)


def configured_domain_supplement_source_bytes(
    environ: Mapping[str, str] | None = None,
) -> int:
    """Resolve and validate the Worker-owned temporary source-store budget."""

    values = os.environ if environ is None else environ
    raw = str(
        values.get(CODECOMPASS_DOMAIN_SUPPLEMENT_SOURCE_BYTES_ENV) or ""
    ).strip()
    if not raw:
        return DEFAULT_CODECOMPASS_DOMAIN_SUPPLEMENT_SOURCE_BYTES
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(
            "codecompass_domain_supplement_source_limit_invalid"
        ) from exc
    return validate_domain_supplement_source_bytes(value)


def validate_domain_supplement_source_bytes(value: object) -> int:
    """Enforce the non-client-controlled Worker disk safety envelope."""

    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < MIN_CODECOMPASS_DOMAIN_SUPPLEMENT_SOURCE_BYTES
        or value > MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_SOURCE_BYTES
    ):
        raise ValueError("codecompass_domain_supplement_source_limit_invalid")
    return value


__all__ = [
    "CODECOMPASS_DOMAIN_SUPPLEMENT_SOURCE_BYTES_ENV",
    "DEFAULT_CODECOMPASS_DOMAIN_SUPPLEMENT_SOURCE_BYTES",
    "MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_SOURCE_BYTES",
    "MIN_CODECOMPASS_DOMAIN_SUPPLEMENT_SOURCE_BYTES",
    "configured_domain_supplement_source_bytes",
    "validate_domain_supplement_source_bytes",
]

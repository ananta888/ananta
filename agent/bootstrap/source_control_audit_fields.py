"""Bounded audit-field normalization for source-control API events."""

from __future__ import annotations

import hashlib
import re

_BOUNDED_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")
_BOUNDED_REASON = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,63}$")


def bounded_id(value: object, *, fallback: str) -> str:
    text = str(value or "")
    if _BOUNDED_ID.fullmatch(text):
        return text
    if not text:
        return fallback
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]
    return f"{fallback}-{digest}"


def bounded_reason(value: object, *, fallback: str) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    return normalized if _BOUNDED_REASON.fullmatch(normalized) else fallback

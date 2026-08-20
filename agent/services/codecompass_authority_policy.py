"""Fail-closed validation for untrusted CodeCompass tool arguments."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


FORBIDDEN_CLIENT_AUTHORITY_FIELDS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "capability",
        "collection",
        "collection_name",
        "credential",
        "credentials",
        "qdrant_collection",
        "qdrant_url",
        "secret",
        "token",
    }
)


def contains_client_authority(value: Any) -> bool:
    """Return whether a nested request tries to supply server-owned authority."""

    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in FORBIDDEN_CLIENT_AUTHORITY_FIELDS:
                return True
            if contains_client_authority(nested):
                return True
        return False
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(contains_client_authority(item) for item in value)
    return False


def assert_no_client_authority(value: Any) -> None:
    if contains_client_authority(value):
        raise ValueError("client_authority_forbidden")

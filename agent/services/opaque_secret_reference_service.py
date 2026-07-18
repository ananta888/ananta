"""Resolve the deliberately small set of opaque secret references used by the Hub.

Persisted domain objects carry references, never credential values.  Keeping the
resolver behind a focused service avoids teaching visual-process adapters about
environment access and leaves room for a vault-backed implementation later.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping


class OpaqueSecretReferenceError(ValueError):
    """A secret reference is invalid, unsupported, or not configured."""


class OpaqueSecretReferenceService:
    """Resolve allowlisted ``env://NAME`` references without exposing the value."""

    _ENV_REFERENCE = re.compile(r"^env://([A-Z][A-Z0-9_]{1,127})$")

    def __init__(self, environment: Mapping[str, str] | None = None) -> None:
        self._environment = environment if environment is not None else os.environ

    def resolve(self, reference: str) -> str:
        match = self._ENV_REFERENCE.fullmatch(str(reference or "").strip())
        if match is None:
            raise OpaqueSecretReferenceError("unsupported_secret_reference")
        value = str(self._environment.get(match.group(1)) or "")
        if not value:
            raise OpaqueSecretReferenceError("secret_reference_not_configured")
        return value


opaque_secret_reference_service = OpaqueSecretReferenceService()


__all__ = [
    "OpaqueSecretReferenceError",
    "OpaqueSecretReferenceService",
    "opaque_secret_reference_service",
]

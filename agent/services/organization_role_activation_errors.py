"""Stable failure of the Organization role activation read model."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class OrganizationRoleActivationReadError(ValueError):
    """Stable read-model failure without leaking persistence details."""

    def __init__(self, reason_code: str, *, details: Mapping[str, Any] | None = None) -> None:
        self.reason_code = reason_code
        self.details = dict(details or {})
        super().__init__(reason_code)


__all__ = [
    "OrganizationRoleActivationReadError",
]

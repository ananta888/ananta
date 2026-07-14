"""Persistence-neutral outcomes for atomic OIDC identity provisioning."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class OidcIdentityProvisioningStatus(str, Enum):
    CREATED = "created"
    IDENTITY_ALREADY_LINKED = "identity_already_linked"
    USERNAME_CONFLICT = "username_conflict"
    LINK_CONFLICT = "link_conflict"
    PERSISTENCE_UNAVAILABLE = "persistence_unavailable"


@dataclass(frozen=True)
class OidcIdentityProvisioningResult:
    status: OidcIdentityProvisioningStatus
    username: str | None = None


__all__ = [
    "OidcIdentityProvisioningResult",
    "OidcIdentityProvisioningStatus",
]
